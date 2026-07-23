import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from .config import Settings, CoordinatorMode
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


class Coordinator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._leases: Dict[str, Lease] = {}
        self._decisions = {"permit": 0, "deny": 0, "shed": 0}
        self._queue_depth = 0
        self._ttl_task: Optional[asyncio.Task] = None

    async def start(self):
        self._ttl_task = asyncio.create_task(self._ttl_loop())
        log.info(json.dumps({"event": "startup", "mode": str(self.settings.coordinator_mode)}))

    async def stop(self):
        if self._ttl_task:
            self._ttl_task.cancel()
            try:
                await self._ttl_task
            except asyncio.CancelledError:
                pass
        log.info(json.dumps({"event": "shutdown"}))

    async def _ttl_loop(self):
        while True:
            await asyncio.sleep(30)
            self._expire_leases()

    def _expire_leases(self):
        now = time.monotonic()
        expired = [
            lid for lid, lease in self._leases.items()
            if now - lease.created_at > self.settings.lease_ttl_seconds
        ]
        for lid in expired:
            lease = self._leases.pop(lid)
            log.warning(json.dumps({
                "event": "lease_expired",
                "caller_id": lease.caller_id,
                "lease_id": lid,
                "vram_mb": lease.vram_mb,
            }))

    def _vram_available(self) -> Optional[int]:
        info = query_vram()
        if info is None:
            return None
        return info["total_mb"] - info["used_mb"] - self.settings.safety_overhead_mb

    def _committed_mb(self) -> int:
        return sum(l.vram_mb for l in self._leases.values())

    def acquire(self, req: AcquireRequest) -> AcquireResponse:
        available = self._vram_available()
        mode = self.settings.coordinator_mode

        if mode == CoordinatorMode.observe:
            lease_id = str(uuid.uuid4())
            self._leases[lease_id] = Lease(
                lease_id=lease_id, caller_id=req.caller_id,
                vram_mb=req.vram_mb, tier=int(req.tier),
            )
            self._decisions["permit"] += 1
            log.info(json.dumps({
                "event": "acquire", "caller_id": req.caller_id,
                "vram_mb": req.vram_mb, "tier": int(req.tier),
                "result": "permit", "mode": "observe",
                "vram_available_mb": available, "lease_id": lease_id,
            }))
            return AcquireResponse(
                lease_id=lease_id, result="permit",
                vram_mb=req.vram_mb,
                message=f"observe mode — would permit (available={available} MB)",
            )

        # enforce mode
        if available is None:
            log.error(json.dumps({"event": "acquire", "error": "gpu_query_failed",
                                  "caller_id": req.caller_id, "result": "permit"}))
            lease_id = str(uuid.uuid4())
            self._leases[lease_id] = Lease(
                lease_id=lease_id, caller_id=req.caller_id,
                vram_mb=req.vram_mb, tier=int(req.tier),
            )
            self._decisions["permit"] += 1
            return AcquireResponse(
                lease_id=lease_id, result="permit", vram_mb=req.vram_mb,
                message="gpu_query_failed: permit (fail-safe)",
            )

        projected = available - req.vram_mb
        if projected < self.settings.hard_floor_mb:
            self._decisions["deny"] += 1
            log.info(json.dumps({
                "event": "acquire", "caller_id": req.caller_id,
                "vram_mb": req.vram_mb, "tier": int(req.tier),
                "result": "deny", "mode": "enforce",
                "vram_available_mb": available, "projected_mb": projected,
                "hard_floor_mb": self.settings.hard_floor_mb,
            }))
            return AcquireResponse(
                lease_id=None, result="deny", vram_mb=0,
                message=(f"hard floor breached: {available} MB available, "
                         f"need {req.vram_mb} MB, floor {self.settings.hard_floor_mb} MB"),
            )

        lease_id = str(uuid.uuid4())
        self._leases[lease_id] = Lease(
            lease_id=lease_id, caller_id=req.caller_id,
            vram_mb=req.vram_mb, tier=int(req.tier),
        )
        self._decisions["permit"] += 1

        soft_pressure = projected < self.settings.soft_floor_mb
        log.info(json.dumps({
            "event": "acquire", "caller_id": req.caller_id,
            "vram_mb": req.vram_mb, "tier": int(req.tier),
            "result": "permit", "mode": "enforce",
            "vram_available_mb": available, "projected_mb": projected,
            "soft_pressure": soft_pressure, "lease_id": lease_id,
        }))
        return AcquireResponse(
            lease_id=lease_id, result="permit", vram_mb=req.vram_mb,
            message="granted" + (" (soft pressure)" if soft_pressure else ""),
        )

    def release(self, req: ReleaseRequest) -> ReleaseResponse:
        lease = self._leases.pop(req.lease_id, None)
        if lease is None:
            log.warning(json.dumps({
                "event": "release", "result": "not_found",
                "lease_id": req.lease_id, "caller_id": req.caller_id,
            }))
            return ReleaseResponse(released=False, message="lease not found")
        log.info(json.dumps({
            "event": "release", "result": "ok",
            "lease_id": req.lease_id, "caller_id": req.caller_id,
            "vram_mb": lease.vram_mb,
        }))
        return ReleaseResponse(released=True, message="ok")

    def stats(self) -> dict:
        info = query_vram()
        available = self._vram_available() if info else 0
        return {
            "mode": self.settings.coordinator_mode,
            "vram_total_mb": info["total_mb"] if info else 0,
            "vram_available_mb": available or 0,
            "vram_committed_mb": self._committed_mb(),
            "soft_floor_mb": self.settings.soft_floor_mb,
            "hard_floor_mb": self.settings.hard_floor_mb,
            "active_leases": len(self._leases),
            "queue_depth": self._queue_depth,
            "decisions": dict(self._decisions),
        }