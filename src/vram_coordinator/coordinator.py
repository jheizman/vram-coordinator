import asyncio
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

    def start(self):
        try:
            self._ttl_task = asyncio.get_event_loop().create_task(self._ttl_loop())
        except RuntimeError:
            pass
        log.info("coordinator started mode=%s", self.settings.coordinator_mode)

    def stop(self):
        if self._ttl_task:
            self._ttl_task.cancel()

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
            log.warning("lease expired caller_id=%s lease_id=%s vram_mb=%d",
                        lease.caller_id, lid, lease.vram_mb)

    def _vram_available(self) -> Optional[int]:
        info = query_vram()
        if info is None:
            return None
        committed = sum(l.vram_mb for l in self._leases.values())
        return info["total_mb"] - info["used_mb"] - self.settings.safety_overhead_mb

    def _committed_mb(self) -> int:
        return sum(l.vram_mb for l in self._leases.values())

    def acquire(self, req: AcquireRequest) -> AcquireResponse:
        available = self._vram_available()
        committed = self._committed_mb()
        mode = self.settings.coordinator_mode

        if mode == CoordinatorMode.observe:
            lease_id = str(uuid.uuid4())
            lease = Lease(lease_id=lease_id, caller_id=req.caller_id,
                          vram_mb=req.vram_mb, tier=req.tier)
            self._leases[lease_id] = lease
            self._decisions["permit"] += 1
            log.info(
                json_event("acquire", req, "permit", mode, available, lease_id)
            )
            return AcquireResponse(
                lease_id=lease_id, result="permit",
                vram_mb=req.vram_mb,
                message=f"observe mode — would permit (available={available} MB)"
            )

        # enforce mode
        if available is None:
            # GPU query failed — fall back to observe/permit with warning
            log.error("gpu_query_failed: falling back to permit caller_id=%s", req.caller_id)
            lease_id = str(uuid.uuid4())
            self._leases[lease_id] = Lease(lease_id=lease_id, caller_id=req.caller_id,
                                           vram_mb=req.vram_mb, tier=req.tier)
            self._decisions["permit"] += 1
            return AcquireResponse(lease_id=lease_id, result="permit",
                                   vram_mb=req.vram_mb,
                                   message="gpu_query_failed: permit (fail-safe)")

        if available - req.vram_mb < self.settings.hard_floor_mb:
            self._decisions["deny"] += 1
            log.info(json_event("acquire", req, "deny", mode, available, None))
            return AcquireResponse(
                lease_id=None, result="deny", vram_mb=0,
                message=f"hard floor breached: {available} MB available, "
                        f"{self.settings.hard_floor_mb} MB floor"
            )

        lease_id = str(uuid.uuid4())
        self._leases[lease_id] = Lease(lease_id=lease_id, caller_id=req.caller_id,
                                       vram_mb=req.vram_mb, tier=req.tier)
        self._decisions["permit"] += 1

        pressure = ""
        if available - req.vram_mb < self.settings.soft_floor_mb:
            pressure = " (soft pressure)"

        log.info(json_event("acquire", req, "permit", mode, available, lease_id))
        return AcquireResponse(
            lease_id=lease_id, result="permit",
            vram_mb=req.vram_mb,
            message=f"granted{pressure}"
        )

    def release(self, req: ReleaseRequest) -> ReleaseResponse:
        lease = self._leases.pop(req.lease_id, None)
        if lease is None:
            log.warning("release unknown lease_id=%s caller_id=%s", req.lease_id, req.caller_id)
            return ReleaseResponse(released=False, message="lease not found")
        log.info("event=release lease_id=%s caller_id=%s vram_mb=%d",
                 req.lease_id, req.caller_id, lease.vram_mb)
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


def json_event(event: str, req: AcquireRequest, result: str,
               mode, available, lease_id) -> str:
    import json
    return json.dumps({
        "event": event,
        "caller_id": req.caller_id,
        "vram_mb": req.vram_mb,
        "tier": req.tier,
        "result": result,
        "mode": str(mode),
        "vram_available_mb": available,
        "lease_id": lease_id,
    })