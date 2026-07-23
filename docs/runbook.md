# vram-coordinator Runbook

## Deploy/update

```bash
cd /home/vram-coordinator/vram-coordinator
git pull
docker build -t ghcr.io/jheizman/vram-coordinator:dev .
docker compose up -d
```

## Verify service

```bash
curl -sf http://127.0.0.1:8787/health
curl -sf http://127.0.0.1:8787/ready
curl -sf http://127.0.0.1:8787/stats
bash scripts/smoke_test.sh
```

## Mode switch

- Edit `.env` and set `COORDINATOR_MODE=observe` or `enforce`.
- Apply:

```bash
docker compose up -d
```

## Rollback

Fast rollback to non-blocking behavior:

```bash
sed -i 's/^COORDINATOR_MODE=.*/COORDINATOR_MODE=observe/' .env
docker compose up -d
```

Emergency stop:

```bash
docker compose down
```

## Auth and allowlist controls

Enable token auth:

```bash
# .env
REQUIRE_API_TOKEN=true
API_TOKEN=replace-with-secret
```

Enable caller allowlist:

```bash
# .env
ENFORCE_ALLOWLIST=true
ALLOWED_CALLERS=apigateway,comfyui
```

Apply with `docker compose up -d`.

## Troubleshooting

Logs:

```bash
docker logs --tail 200 vram-coordinator
```

Common signals:
- `acquire_timeout` -> deadline too low or queue too deep
- `lease_expired` -> callers not releasing leases
- `gpu_available:false` -> NVIDIA runtime/device passthrough issue

GPU visibility check:

```bash
docker exec -it vram-coordinator python - <<'PY'
import pynvml
pynvml.nvmlInit()
h = pynvml.nvmlDeviceGetHandleByIndex(0)
info = pynvml.nvmlDeviceGetMemoryInfo(h)
print(info.total, info.used)
PY
```