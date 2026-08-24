# VRAM Coordinator Operations Handoff

## Role and boundary

The VRAM Coordinator is the admission-control service for AIBox GPU workloads. It owns VRAM lease arbitration and coordinator-side policy/telemetry. Applications are responsible for calling `acquire` before GPU work, retaining the returned lease through queued and processing states, calling `renew` for long work, and calling `release` on every terminal or failure path.

This repository and its deployed checkout are the only coordinator scope. Changes to EasyAI, FindRAI, Image Gallery Browser, Immich, ComfyUI, Ollama, detector workers, or other services are out of scope and must be coordinated separately. Host-level sudo, Docker, and service mutations require explicit user approval through the canonical cross-session coordinator.

## Architecture

- FastAPI service in `src/vram_coordinator/`.
- `gpu.py` queries NVML from the NVIDIA-enabled container.
- `coordinator.py` maintains leases, tiered queues, deadlines, VRAM floors, tripwire protection, and decision telemetry.
- `main.py` exposes health, admission, policy, metrics, and request-ID propagation.
- `models.py` defines strict request/response contracts.
- Runtime image: `ghcr.io/jheizman/vram-coordinator:dev`.
- AIBox checkout: `/home/vram-coordinator/vram-coordinator`.
- SSH alias: `aibox-vram-coordinator` (user `vram-coordinator`).
- Service bind: `127.0.0.1:8787` on AIBox; the container also joins the external `ai-shared` Docker network for peer-container access.

## Admission policy

- `tier=1`: high/interactive priority.
- `tier=2`: normal/batch priority.
- `tier=3`: low/background priority.
- `HARD_FLOOR_MB=1536`, `SOFT_FLOOR_MB=3072`, and `SAFETY_OVERHEAD_MB=768` are the current defaults.
- Observe mode is the safe default; enforcement can be staged by `ENFORCE_SCOPE=low|normal|all`.
- Queue depth and deadlines are bounded per tier. The tripwire automatically returns enforcement to observe when the configured deny-rate threshold is exceeded.
- Explicit denies/sheds are not fail-open. Client applications own timeout handling, circuit breakers, and any bounded interactive fail-open behavior.
- Leases expire after the configured TTL unless renewed. Release is idempotent.

## API surface

- `POST /acquire`: request `{caller_id, tier, vram_mb, deadline_seconds?}` and receive `permit`, `deny`, or `shed` plus `lease_id`.
- `POST /renew`: request `{lease_id, caller_id}` to reset the lease TTL for long-running work.
- `POST /release`: request `{lease_id, caller_id}`; safe to repeat.
- `GET /health`, `GET /ready`, `GET /stats`, `GET /metrics`.
- `GET/POST /admin/policy`: admin-token-protected runtime policy inspection/change when `ADMIN_TOKEN` is configured.
- Caller authentication uses bearer `API_TOKEN`; allowlisting is enabled in the live environment for `easyai,smoke_test`.

## Current integration status

- EasyAI integration was planned around dynamic per-request VRAM sizing, request-bound leases, terminal release, video renewals, and a client-side circuit breaker. No other caller is currently integrated with the coordinator.
- The coordinator currently reports no active leases from external callers; loaded services not calling `/acquire` are invisible to lease telemetry.
- `sam31-worker` is a high-demand shared service with `maxConcurrency:1`; observed consumers include `findrai-app-1` and `image-browser`. A future service-slot/mutex extension is proposed but not implemented.
- SigLIP2 Docker stop/start/status control remains review-only. It would require Docker SDK and `/var/run/docker.sock`; the socket grants powerful host control and needs an explicit security decision before implementation.

## AIBox service mappings (observed 2026-08-20 to 2026-08-24)

| Service/container | Port or network address | Notes |
|---|---|---|
| vram-coordinator | `127.0.0.1:8787` | Coordinator API; `ai-shared` network member |
| sam31-worker | `127.0.0.1:8103->8103`; `ai-shared:8103` | SAM 3.1 checkpoint `sam3.1_multiplex.pt`; max concurrency 1 |
| easyai-easyai-1 | `127.0.0.1:4500->4200` | EasyAI API |
| findrai-app-1 | `0.0.0.0:4000->4000` | Observed SAM consumer |
| image-browser | `127.0.0.1:8000->8000` | Observed SAM consumer |
| apigateway-app-1 | `0.0.0.0:4300->4000` | API gateway |
| siglip2-embed | `ai-shared` only | Persistent embedding worker |
| reranker | `127.0.0.1:11435->8000` | GPU service |
| grounding-dino-detector | `ai-shared` only | Auto-unloading detector |
| owlv2-detector | `ai-shared` only | GPU detector |
| local-sam2-ram-worker | `ai-shared` only | GPU worker |
| florence2-large-vision | `127.0.0.1:8094->8094` | GPU vision service |
| immich_server | `127.0.0.1:2283->2283` | Immich API |
| immich_machine_learning | internal only | Immich ML; buffalo_l measurement showed ~931 MiB cold full-pipeline delta and ~300s natural eviction |
| comfyui-app-1 | internal/host-specific | Primary image/video GPU workload |

## Validation and recovery

- Run `bash scripts/smoke_test.sh` from the deployed checkout; current suite has 26 checks.
- Check `curl -sf http://127.0.0.1:8787/health`, `/ready`, `/stats`, and `/metrics`.
- `docker compose up -d --build` rebuilds from the AIBox checkout and preserves the NVIDIA runtime.
- Runtime policy can be reverted to observe without restart using `/admin/policy` with the admin token.
- The live `.env` contains generated auth secrets and is gitignored; never copy those values into docs or commits.
