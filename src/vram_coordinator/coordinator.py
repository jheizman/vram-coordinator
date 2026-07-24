import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from .config import CoordinatorMode, Settings
from .gpu import query_vram
from .models import AcquireRequest, AcquireResponse, ReleaseRequest, ReleaseResponse

log = logging.getLogger(__name__)


@dataclass
class Lease:
    lease_id: str
    caller_id: str
    vram_mb: int
    tier: int
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class PendingAcquire:
    request_id: str
    caller_id: str
    vram_mb: int
    tier: int
    enqueue_seq: int
    enqueued_at: float
    deadline_at: float
    future: asyncio.Future


class Coordinator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._leases: Dict[str, Lease] = {}
        self._pending: Dict[str, PendingAcquire] = {}
        self._decisions = {"permit": 0, "deny": 0, "shed": 0}
        self._decision_reasons: Dict[str, int] = {}
        self._wait_ms_total = 0.0
        self._wait_ms_count = 0
        self._ttl_task: Optional[asyncio.Task] = None
        self._queue_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._seq = 0

    async def start(self):
        self._ttl_task = asyncio.create_task(self._ttl_loop())
        self._queue_task = asyncio.create_task(self._queue_loop())
        log.info(json.dumps({"event": "startup", "mode": self.settings.coordinator_mode.value}))

    async def stop(self):
        for task in (self._ttl_task, self._queue_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        log.info(json.dumps({"event": "shutdown"}))

    def _mark_reason(self, reason: str) -> None:
        self._decision_reasons[reason] = self._decision_reasons.get(reason, 0) + 1

    async def _ttl_loop(self):
        while True:
            await asyncio.sleep(5)
            async with self._lock:
                self._expire_leases_locked(time.monotonic())

    async def _queue_loop(self):
        while True:
            await asyncio.sleep(0.5)
            async with self._lock:
                now = time.monotonic()
                self._expire_pending_locked(now)
                self._drain_queue_locked(now)

    def _expire_leases_locked(self, now: float) -> None:
        expired = [
            lid for lid, lease in self._leases.items()
            if now - lease.created_at > self.settings.lease_ttl_seconds
        ]
        for lid in expired:
            lease = self._leases.pop(lid)
            log.warning(json.dumps({
                "event": "lease_expired",
                "lease_id": lid,
                "caller_id": lease.caller_id,
                "vram_mb": lease.vram_mb,
            }))

    def _expire_pending_locked(self, now: float) -> None:
        expired_ids = [
            req_id for req_id, pending in self._pending.items()
            if pending.deadline_at <= now
        ]
        for req_id in expired_ids:
            pending = self._pending.pop(req_id)
            if not pending.future.done():
                pending.future.set_result(("shed", None, "deadline exceeded"))
            self._decisions["shed"] += 1
            self._mark_reason("deadline_exceeded")
            log.warning(json.dumps({
                "event": "acquire_timeout",
                "request_id": pending.request_id,
                "caller_id": pending.caller_id,
                "vram_mb": pending.vram_mb,
                "tier": pending.tier,
                "result": "shed",
                "reason": "deadline_exceeded",
            }))

    def _queue_depth_by_tier_locked(self) -> dict[str, int]:
        counts = {"high": 0, "normal": 0, "low": 0}
        for pending in self._pending.values():
            if pending.tier == 1:
                counts["high"] += 1
            elif pending.tier == 2:
                counts["normal"] += 1
            else:
                counts["low"] += 1
        return counts

    def _tier_queue_depth_locked(self, tier: int) -> int:
        return sum(1 for pending in self._pending.values() if pending.tier == tier)

    def _queue_depth_locked(self) -> int:
        return len(self._pending)

    def _committed_mb_locked(self) -> int:
        return sum(lease.vram_mb for lease in self._leases.values())

    def _gpu_snapshot(self) -> Optional[tuple[int, int, int]]:
        info = query_vram()
        if info is None:
            return None
        total = int(info["total_mb"])
        used = int(info["used_mb"])
        available = total - used - self.settings.safety_overhead_mb
        return total, used, max(available, 0)

    def _can_grant_locked(self, vram_mb: int, tier: int, available: int) -> tuple[bool, str]:
        projected = available - vram_mb
        if projected < self.settings.hard_floor_mb:
            return False, "hard_floor"
        if (
            tier >= 3
            and self.settings.low_tier_shed_under_soft_pressure
            and projected < self.settings.soft_floor_mb
        ):
            return False, "soft_floor_low_tier"
        return True, "ok"

    def _grant_locked(self, pending: PendingAcquire, mode: str, available: int, now: float) -> None:
        lease_id = str(uuid.uuid4())
        self._leases[lease_id] = Lease(
            lease_id=lease_id,
            caller_id=pending.caller_id,
            vram_mb=pending.vram_mb,
            tier=pending.tier,
        )
        waited_ms = max((now - pending.enqueued_at) * 1000.0, 0.0)
        self._wait_ms_total += waited_ms
        self._wait_ms_count += 1
        self._decisions["permit"] += 1
        self._mark_reason("permit")

        if not pending.future.done():
            pending.future.set_result(("permit", lease_id, f"granted ({mode})"))

        log.info(json.dumps({
            "event": "acquire",
            "request_id": pending.request_id,
            "caller_id": pending.caller_id,
            "vram_mb": pending.vram_mb,
            "tier": pending.tier,
            "result": "permit",
            "mode": mode,
            "vram_available_mb": available,
            "wait_ms": round(waited_ms, 2),
            "lease_id": lease_id,
        }))

    def _drain_queue_locked(self, now: float) -> None:
        if not self._pending:
            return

        snapshot = self._gpu_snapshot()
        if snapshot is None:
            return

        _total, _used, available = snapshot
        ordered = sorted(self._pending.values(), key=lambda item: (item.tier, item.enqueue_seq))

        for pending in ordered:
            if pending.request_id not in self._pending:
                continue
            can_grant, reason = self._can_grant_locked(pending.vram_mb, pending.tier, available)
            if can_grant:
                self._pending.pop(pending.request_id, None)
                self._grant_locked(pending, "enforce", available, now)
                available -= pending.vram_mb
                continue

            if reason == "soft_floor_low_tier":
                self._pending.pop(pending.request_id, None)
                self._decisions["shed"] += 1
                self._mark_reason(reason)
                if not pending.future.done():
                    pending.future.set_result(("shed", None, "soft pressure low-tier shedding"))
                log.warning(json.dumps({
                    "event": "acquire",
                    "request_id": pending.request_id,
                    "caller_id": pending.caller_id,
                    "vram_mb": pending.vram_mb,
                    "tier": pending.tier,
                    "result": "shed",
                    "reason": reason,
                }))

    async def acquire(self, req: AcquireRequest, request_id: str) -> AcquireResponse:
        now = time.monotonic()
        tier = int(req.tier)
        deadline_seconds = self.settings.deadline_for_tier(tier, req.deadline_seconds)

        async with self._lock:
            self._expire_leases_locked(now)

            if self.settings.coordinator_mode == CoordinatorMode.observe:
                snapshot = self._gpu_snapshot()
                available = snapshot[2] if snapshot else 0
                pending = PendingAcquire(
                    request_id=request_id,
                    caller_id=req.caller_id,
                    vram_mb=req.vram_mb,
                    tier=tier,
                    enqueue_seq=self._seq,
                    enqueued_at=now,
                    deadline_at=now + deadline_seconds,
                    future=asyncio.get_running_loop().create_future(),
                )
                self._seq += 1
                self._grant_locked(pending, "observe", available, now)
                result, lease_id, message = pending.future.result()
                return AcquireResponse(
                    lease_id=lease_id,
                    result=result,
                    vram_mb=req.vram_mb if lease_id else 0,
                    message=message,
                    request_id=request_id,
                )

            snapshot = self._gpu_snapshot()
            if snapshot is None:
                # fail-open by design
                pending = PendingAcquire(
                    request_id=request_id,
                    caller_id=req.caller_id,
                    vram_mb=req.vram_mb,
                    tier=tier,
                    enqueue_seq=self._seq,
                    enqueued_at=now,
                    deadline_at=now + deadline_seconds,
                    future=asyncio.get_running_loop().create_future(),
                )
                self._seq += 1
                self._mark_reason("gpu_query_fail_open")
                self._grant_locked(pending, "enforce_fail_open", 0, now)
                result, lease_id, message = pending.future.result()
                return AcquireResponse(
                    lease_id=lease_id,
                    result=result,
                    vram_mb=req.vram_mb if lease_id else 0,
                    message=message,
                    request_id=request_id,
                )

            _total, _used, available = snapshot
            if available <= 0:
                self._decisions["deny"] += 1
                self._mark_reason("no_headroom")
                return AcquireResponse(
                    lease_id=None,
                    result="deny",
                    vram_mb=0,
                    message="no VRAM headroom available",
                    request_id=request_id,
                )

            if self._queue_depth_locked() >= self.settings.max_queue_depth:
                self._decisions["shed"] += 1
                self._mark_reason("queue_full_global")
                return AcquireResponse(
                    lease_id=None,
                    result="shed",
                    vram_mb=0,
                    message="queue is full",
                    request_id=request_id,
                )

            if self._tier_queue_depth_locked(tier) >= self.settings.tier_queue_limit(tier):
                self._decisions["shed"] += 1
                self._mark_reason("queue_full_tier")
                return AcquireResponse(
                    lease_id=None,
                    result="shed",
                    vram_mb=0,
                    message="tier queue is full",
                    request_id=request_id,
                )

            loop = asyncio.get_running_loop()
            pending = PendingAcquire(
                request_id=request_id,
                caller_id=req.caller_id,
                vram_mb=req.vram_mb,
                tier=tier,
                enqueue_seq=self._seq,
                enqueued_at=now,
                deadline_at=now + deadline_seconds,
                future=loop.create_future(),
            )
            self._seq += 1
            self._pending[pending.request_id] = pending
            self._drain_queue_locked(now)

        timeout = max(deadline_seconds, 0.1)
        try:
            result, lease_id, message = await asyncio.wait_for(pending.future, timeout=timeout)
        except asyncio.TimeoutError:
            async with self._lock:
                existed = self._pending.pop(request_id, None)
                if existed is not None:
                    self._decisions["shed"] += 1
                    self._mark_reason("deadline_exceeded")
            return AcquireResponse(
                lease_id=None,
                result="shed",
                vram_mb=0,
                message="deadline exceeded",
                request_id=request_id,
            )

        if result == "permit":
            return AcquireResponse(
                lease_id=lease_id,
                result="permit",
                vram_mb=req.vram_mb,
                message=message,
                request_id=request_id,
            )
        if result == "deny":
            return AcquireResponse(
                lease_id=None,
                result="deny",
                vram_mb=0,
                message=message,
                request_id=request_id,
            )
        return AcquireResponse(
            lease_id=None,
            result="shed",
            vram_mb=0,
            message=message,
            request_id=request_id,
        )

    async def release(self, req: ReleaseRequest, request_id: str) -> ReleaseResponse:
        async with self._lock:
            lease = self._leases.pop(req.lease_id, None)
            if lease is None:
                self._mark_reason("release_already_released")
                log.info(json.dumps({
                    "event": "release",
                    "request_id": request_id,
                    "lease_id": req.lease_id,
                    "caller_id": req.caller_id,
                    "result": "already_released",
                }))
                return ReleaseResponse(released=True, message="already released", request_id=request_id)

            log.info(json.dumps({
                "event": "release",
                "request_id": request_id,
                "lease_id": req.lease_id,
                "caller_id": req.caller_id,
                "vram_mb": lease.vram_mb,
                "result": "ok",
            }))
            self._drain_queue_locked(time.monotonic())

        return ReleaseResponse(released=True, message="ok", request_id=request_id)

    async def stats(self) -> dict:
        async with self._lock:
            self._expire_leases_locked(time.monotonic())
            snapshot = self._gpu_snapshot()
            if snapshot is None:
                total = 0
                available = 0
            else:
                total = snapshot[0]
                available = snapshot[2]

            return {
                "mode": self.settings.coordinator_mode.value,
                "vram_total_mb": total,
                "vram_available_mb": available,
                "vram_committed_mb": self._committed_mb_locked(),
                "soft_floor_mb": self.settings.soft_floor_mb,
                "hard_floor_mb": self.settings.hard_floor_mb,
                "active_leases": len(self._leases),
                "queue_depth": self._queue_depth_locked(),
                "queue_depth_by_tier": self._queue_depth_by_tier_locked(),
                "decision_reasons": dict(self._decision_reasons),
                "decisions": dict(self._decisions),
                "wait_ms_total": round(self._wait_ms_total, 2),
                "wait_ms_count": self._wait_ms_count,
            }