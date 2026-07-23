import logging
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from .config import Settings
from .coordinator import Coordinator
from .models import (
    AcquireRequest, AcquireResponse,
    ReleaseRequest, ReleaseResponse,
    StatsResponse,
)

settings = Settings()
coordinator = Coordinator(settings)

logging.basicConfig(
    stream=sys.stdout,
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    coordinator.start()
    yield
    coordinator.stop()


app = FastAPI(title="vram-coordinator", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    from .gpu import query_vram
    info = query_vram()
    return {
        "ready": True,
        "gpu_available": info is not None,
        "mode": settings.coordinator_mode,
    }


@app.post("/acquire", response_model=AcquireResponse)
def acquire(req: AcquireRequest):
    return coordinator.acquire(req)


@app.post("/release", response_model=ReleaseResponse)
def release(req: ReleaseRequest):
    return coordinator.release(req)


@app.get("/stats", response_model=StatsResponse)
def stats():
    return coordinator.stats()


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    s = coordinator.stats()
    lines = [
        "# HELP vram_coordinator_vram_available_mb Available VRAM in MB",
        "# TYPE vram_coordinator_vram_available_mb gauge",
        f"vram_coordinator_vram_available_mb {s['vram_available_mb']}",
        "# HELP vram_coordinator_vram_committed_mb Committed VRAM in MB",
        "# TYPE vram_coordinator_vram_committed_mb gauge",
        f"vram_coordinator_vram_committed_mb {s['vram_committed_mb']}",
        "# HELP vram_coordinator_active_leases Number of active leases",
        "# TYPE vram_coordinator_active_leases gauge",
        f"vram_coordinator_active_leases {s['active_leases']}",
        "# HELP vram_coordinator_queue_depth Current queue depth",
        "# TYPE vram_coordinator_queue_depth gauge",
        f"vram_coordinator_queue_depth {s['queue_depth']}",
    ]
    for result, count in s["decisions"].items():
        lines += [
            f"# HELP vram_coordinator_decisions_total Admission decisions",
            f"# TYPE vram_coordinator_decisions_total counter",
            f'vram_coordinator_decisions_total{{result="{result}"}} {count}',
        ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    uvicorn.run(
        "vram_coordinator.main:app",
        host=settings.listen_host,
        port=settings.listen_port,
        log_config=None,
    )