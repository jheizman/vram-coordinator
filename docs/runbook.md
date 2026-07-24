# vram-coordinator Runbook

## Deploy/update

```bash
cd /home/vram-coordinator/vram-coordinator
git pull
docker build -t ghcr.io/jheizman/vram-coordinator:dev .
docker compose up -d
bash scripts/smoke_test.sh
```

## Verify service

```bash
curl -sf http://127.0.0.1:8787/health
curl -sf http://127.0.0.1:8787/ready
curl -sf http://127.0.0.1:8787/stats
curl -sf http://127.0.0.1:8787/admin/policy
bash scripts/smoke_test.sh
```

## Runtime policy switch (no restart needed)

```bash
# GET current policy
curl -sf http://127.0.0.1:8787/admin/policy

# Switch to enforce mode (full scope)
curl -s -X POST http://127.0.0.1:8787/admin/policy \
  -H "Content-Type: application/json" \
  -d '{"mode":"enforce","enforce_scope":"all","reason":"manual-rollout"}'

# Revert to observe instantly
curl -s -X POST http://127.0.0.1:8787/admin/policy \
  -H "Content-Type: application/json" \
  -d '{"mode":"observe","reason":"rollback"}'
```

## Tiered rollout playbook

```
Phase 1: observe mode (default)
  COORDINATOR_MODE=observe

Phase 2: enforce low tier only
  POST /admin/policy {"mode":"enforce","enforce_scope":"low"}
  Monitor /stats for deny_rate and wait_ms for 10+ min
  If stable, proceed

Phase 3: expand to normal + low
  POST /admin/policy {"mode":"enforce","enforce_scope":"normal"}
  Monitor 10+ min

Phase 4: full enforcement
  POST /admin/policy {"mode":"enforce","enforce_scope":"all"}
  Monitor actively
```

Any phase: instant rollback
```bash
curl -s -X POST http://127.0.0.1:8787/admin/policy -H "Content-Type: application/json" \
  -d '{"mode":"observe","reason":"rollback"}'
```

## .env config rollback (restart required)

```bash
sed -i 's/^COORDINATOR_MODE=.*/COORDINATOR_MODE=observe/' .env
docker compose up -d
```

## Emergency stop

```bash
docker compose down
```

## Auth and allowlist

Enable admin token:
```bash
# .env
ADMIN_TOKEN=replace-with-secret
```

Enable caller token auth:
```bash
REQUIRE_API_TOKEN=true
API_TOKEN=replace-with-secret
```

Enable allowlist:
```bash
ENFORCE_ALLOWLIST=true
ALLOWED_CALLERS=apigateway,comfyui
```

Apply with `docker compose up -d`.

## Tripwire

Auto-reverts to `observe` if deny-rate exceeds `TRIPWIRE_MAX_DENY_RATE` (default 50%) over a rolling `TRIPWIRE_WINDOW_SECONDS` (default 60s) with at least `TRIPWIRE_MIN_SAMPLES` (default 10).

Disable: `TRIPWIRE_ENABLED=false` in `.env` + `docker compose up -d`.

## Troubleshooting

```bash
docker logs --tail 200 vram-coordinator
```

Signals:
- `tripwire_tripped` → deny rate too high; coordinator auto-reverted to observe
- `policy_change` → runtime policy was modified via admin endpoint
- `acquire_timeout` → deadline too low or queue too deep
- `lease_expired` → caller not releasing leases
- `gpu_available:false` → NVIDIA runtime / passthrough issue

GPU visibility check:
```bash
docker exec -it vram-coordinator python3 - <<'"'"'PY'"'"'
import pynvml; pynvml.nvmlInit()
h = pynvml.nvmlDeviceGetHandleByIndex(0)
info = pynvml.nvmlDeviceGetMemoryInfo(h)
print(f"total={info.total//1048576}MB used={info.used//1048576}MB")
PY
```