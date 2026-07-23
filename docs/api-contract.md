# API Contract v0.2.0

Base URL: `http://127.0.0.1:8787`

All responses include `x-request-id` header. You can provide your own `x-request-id` request header.

## Auth controls (disabled by default)

- `REQUIRE_API_TOKEN=false` by default.
- When enabled, callers must send `Authorization: Bearer <token>`.
- Optional caller guard: `ENFORCE_ALLOWLIST=true` with `ALLOWED_CALLERS=caller-a,caller-b`.

## POST /acquire

Request:
```json
{
  "caller_id": "example-caller",
  "vram_mb": 1024,
  "tier": 2,
  "deadline_seconds": 30
}
```

Response `200`:
```json
{
  "lease_id": "uuid-or-null",
  "result": "permit|deny|shed",
  "vram_mb": 1024,
  "message": "granted",
  "request_id": "uuid"
}
```

Semantics:
- `permit`: lease granted.
- `deny`: rejected due to hard floor.
- `shed`: dropped due to queue pressure or deadline expiry.

## POST /release

Request:
```json
{
  "lease_id": "uuid",
  "caller_id": "example-caller"
}
```

Response `200`:
```json
{
  "released": true,
  "message": "ok|already released",
  "request_id": "uuid"
}
```

`/release` is idempotent. Releasing an already released or unknown lease returns success with `message: "already released"`.

## GET /health

Response `200`:
```json
{"status":"ok","request_id":"uuid"}
```

## GET /ready

Response `200`:
```json
{"ready":true,"gpu_available":true,"mode":"observe|enforce","request_id":"uuid"}
```

## GET /stats

Response `200`:
```json
{
  "mode": "observe",
  "vram_total_mb": 16303,
  "vram_available_mb": 13796,
  "vram_committed_mb": 2048,
  "soft_floor_mb": 3072,
  "hard_floor_mb": 1536,
  "active_leases": 2,
  "queue_depth": 1,
  "queue_depth_by_tier": {"high":0,"normal":1,"low":0},
  "decisions": {"permit":12,"deny":2,"shed":1}
}
```

## GET /metrics

Prometheus text exposition including:
- `vram_coordinator_vram_available_mb`
- `vram_coordinator_vram_committed_mb`
- `vram_coordinator_active_leases`
- `vram_coordinator_queue_depth`
- `vram_coordinator_queue_depth_by_tier{tier=...}`
- `vram_coordinator_decisions_total{result=...}`

## Error model

Any 4xx/5xx follows:
```json
{
  "code": "http_401|http_403|internal_error",
  "message": "human-readable detail",
  "request_id": "uuid"
}
```