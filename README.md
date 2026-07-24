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

## Validation and safety

- Smoke test: `bash scripts/smoke_test.sh`
- Load/chaos harness: `python3 scripts/load_test.py --dry-run`
- Live load generation is **disabled by default** and requires explicit `--execute`.

## Contract and operations

- API contract: `docs/api-contract.md`
- Runbook: `docs/runbook.md`
- Planning baseline: `docs/plan.md`
- Round 4 validation notes: `docs/validation-round4.md`