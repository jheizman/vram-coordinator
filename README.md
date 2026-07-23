# vram-coordinator
Host-level GPU VRAM admission/coordinator service.

## Runtime

- Compose stack lives in this repository (`docker-compose.yml`).
- Copy `.env.example` to `.env` and tune policy before first run.
- Default mode is `observe`.

## Endpoints

- `POST /acquire`
- `POST /release`
- `GET /health`
- `GET /ready`
- `GET /stats`
- `GET /metrics`

## Contract and operations

- API contract: `docs/api-contract.md`
- Runbook: `docs/runbook.md`
- Planning baseline: `docs/plan.md`