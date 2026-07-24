import logging
import sys
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import Settings
from .coordinator import Coordinator
from .models import (
    AcquireRequest, AcquireResponse,
    ErrorResponse,
    PolicyUpdateRequest, PolicyResponse,
    ReleaseRequest, ReleaseResponse,
    StatsResponse,
)

settings = Settings()
settings.validate_policy()
coordinator = Coordinator(settings)

logging.basicConfig(
    stream=sys.stdout,
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await coordinator.start()
    yield
    await coordinator.stop()


app = FastAPI(title="vram-coordinator", version="0.4.0", lifespan=lifespan)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _validate_auth(request: Request) -> None:
    if settings.require_api_token:
        auth = request.headers.get("authorization", "")
        prefix = "Bearer "
        if not auth.startswith(prefix):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = auth[len(prefix):].strip()
        if token != settings.api_token:
            raise HTTPException(status_code=403, detail="invalid token")


def _validate_admin(request: Request) -> None:
    if settings.admin_token:
        auth = request.headers.get("authorization", "")
        prefix = "Bearer "
        if not auth.startswith(prefix):
            raise HTTPException(status_code=401, detail="admin auth required")
        token = auth[len(prefix):].strip()
        if token != settings.admin_token:
            raise HTTPException(status_code=403, detail="invalid admin token")


def _validate_caller(caller_id: str) -> None:
    if settings.enforce_allowlist:
        allowed = settings.allowed_callers_set
        if not allowed:
            raise HTTPException(status_code=500, detail="allowlist enabled but empty")
        if caller_id not in allowed:
            raise HTTPException(status_code=403, detail=f"caller '{caller_id}' is not allowlisted")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    rid = _request_id(request)
    return JSONResponse(status_code=exc.status_code,
                        content=ErrorResponse(code=f"http_{exc.status_code}",
                                              message=str(exc.detail), request_id=rid).model_dump())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    rid = _request_id(request)
    return JSONResponse(status_code=500,
                        content=ErrorResponse(code="internal_error", message=str(exc),
                                              request_id=rid).model_dump())


@app.get("/health")
async def health(request: Request):
    return {"status": "ok", "request_id": _request_id(request)}


@app.get("/ready")
async def ready(request: Request):
    from .gpu import query_vram
    info = query_vram()
    return {
        "ready": True,
        "gpu_available": info is not None,
        "mode": settings.coordinator_mode.value,
        "enforce_scope": settings.enforce_scope.value,
        "request_id": _request_id(request),
    }


@app.post("/acquire", response_model=AcquireResponse,
          responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}})
async def acquire(req: AcquireRequest, request: Request):
    _validate_auth(request)
    _validate_caller(req.caller_id)
    return await coordinator.acquire(req, _request_id(request))


@app.post("/release", response_model=ReleaseResponse,
          responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}})
async def release(req: ReleaseRequest, request: Request):
    _validate_auth(request)
    _validate_caller(req.caller_id)
    return await coordinator.release(req, _request_id(request))


@app.get("/stats", response_model=StatsResponse)
async def stats():
    return await coordinator.stats()


@app.post("/admin/policy", response_model=PolicyResponse,
          responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}})
async def update_policy(req: PolicyUpdateRequest, request: Request):
    _validate_admin(request)
    return await coordinator.update_policy(req, _request_id(request))


@app.get("/admin/policy", response_model=PolicyResponse)
async def get_policy(request: Request):
    _validate_admin(request)
    return await coordinator.update_policy(PolicyUpdateRequest(), _request_id(request))


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    s = await coordinator.stats()
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
        "# HELP vram_coordinator_queue_depth_by_tier Queue depth by tier",
        "# TYPE vram_coordinator_queue_depth_by_tier gauge",
        f"vram_coordinator_queue_depth_by_tier{{tier=\"high\"}} {s['queue_depth_by_tier']['high']}",
        f"vram_coordinator_queue_depth_by_tier{{tier=\"normal\"}} {s['queue_depth_by_tier']['normal']}",
        f"vram_coordinator_queue_depth_by_tier{{tier=\"low\"}} {s['queue_depth_by_tier']['low']}",
        "# HELP vram_coordinator_wait_ms_total Total wait ms for granted acquires",
        "# TYPE vram_coordinator_wait_ms_total counter",
        f"vram_coordinator_wait_ms_total {s['wait_ms_total']}",
        "# HELP vram_coordinator_wait_ms_count Granted acquires counted in wait total",
        "# TYPE vram_coordinator_wait_ms_count counter",
        f"vram_coordinator_wait_ms_count {s['wait_ms_count']}",
        "# HELP vram_coordinator_tripwire_deny_rate Rolling deny rate",
        "# TYPE vram_coordinator_tripwire_deny_rate gauge",
        f"vram_coordinator_tripwire_deny_rate {s['tripwire_deny_rate']}",
        "# HELP vram_coordinator_policy_changes_total Total runtime policy changes",
        "# TYPE vram_coordinator_policy_changes_total counter",
        f"vram_coordinator_policy_changes_total {s['policy_changes']}",
    ]
    for result, count in s["decisions"].items():
        lines += [
            "# HELP vram_coordinator_decisions_total Admission decisions",
            "# TYPE vram_coordinator_decisions_total counter",
            f'vram_coordinator_decisions_total{{result="{result}"}} {count}',
        ]
    for reason, count in s["decision_reasons"].items():
        lines += [
            "# HELP vram_coordinator_decision_reasons_total Decision reasons",
            "# TYPE vram_coordinator_decision_reasons_total counter",
            f'vram_coordinator_decision_reasons_total{{reason="{reason}"}} {count}',
        ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    uvicorn.run(
        "vram_coordinator.main:app",
        host=settings.listen_host,
        port=settings.listen_port,
        log_config=None,
    )