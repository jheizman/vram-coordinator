# VRAM Coordinator — Design Plan

> **Canonical spec. All 18 sections. Resolved decisions inline.**
> Scope: `jheizman/vram-coordinator` only. No other project is aware of or modified by this service.

---

## §01 — Problem Statement and Context

`aibox` runs multiple GPU consumers concurrently: inference endpoints, ComfyUI workflows, and ad-hoc experiments. These processes allocate GPU VRAM independently with no mutual awareness. When two or more processes load models simultaneously they can exceed available VRAM, triggering OOM kills, CUDA context corruption, and unpredictable latency spikes with no clean recovery path.

There is no current mechanism to:
- Know how much VRAM is committed vs available at any moment
- Sequence or prioritize competing allocation requests
- Enforce a safety buffer to keep the host stable

The VRAM Coordinator is a host-level admission gatekeeper that fills this gap. Callers must acquire a VRAM grant before touching the GPU and release it when done. The coordinator is the single authority on whether there is room.

---

## §02 — Goals

1. Provide a single, authoritative source of VRAM allocation decisions on `aibox`.
2. Admit or deny acquire requests before any GPU memory is actually touched.
3. Enforce soft/hard VRAM floors to protect host and system GPU stability.
4. Support priority tiers so critical work is not starved by background tasks.
5. Operate transparently in `observe` mode (log decisions, never block) as the safe default.
6. Expose structured logs and Prometheus metrics for all decisions.
7. Be fully self-contained: no caller-specific code, no awareness of other projects required.

---

## §03 — Non-Goals

- **Not a GPU scheduler.** Does not manage CUDA contexts, streams, or execution order.
- **Not a model loader/unloader.** Does not know what models are loaded or evict them.
- **Not a UI.** Dashboard/operator UX is the responsibility of `vram-monitor`.
- **Not aware of caller internals.** Callers are opaque; the coordinator sees only `caller_id`, `vram_mb`, `tier`, and `deadline`.
- **Not a distributed system.** Single-host, single-GPU scope only (for now).
- **Not persistent.** In-memory state only for MVP; a coordinator restart clears all leases (acceptable).

---

## §04 — Scope Boundaries

| Dimension       | In scope                                      | Out of scope                        |
|-----------------|-----------------------------------------------|-------------------------------------|
| Host            | `aibox` only                                  | Any other host                      |
| GPU             | Index 0 (single GPU assumed)                  | Multi-GPU                           |
| Callers         | Any process that speaks HTTP to 127.0.0.1:8787 | Processes that bypass the coordinator |
| Integration     | Standalone service; no callers wired in Phase 1 | APIGateway, vram-monitor wiring     |
| State           | In-memory leases and queue                    | Persistent allocation history       |

---

## §05 — Architecture Overview

```
┌─────────────────────────────────────────────┐
│  aibox (host)                               │
│                                             │
│  ┌──────────────────────────────────┐       │
│  │  vram-coordinator container      │       │
│  │  user: vram-coordinator          │       │
│  │  port: 127.0.0.1:8787            │       │
│  │                                  │       │
│  │  FastAPI app                     │       │
│  │  ├── /acquire  (POST)            │       │
│  │  ├── /release  (POST)            │       │
│  │  ├── /health   (GET)             │       │
│  │  ├── /ready    (GET)             │       │
│  │  ├── /metrics  (GET)             │       │
│  │  └── /stats    (GET)             │       │
│  │                                  │       │
│  │  Coordinator core                │       │
│  │  ├── LeaseRegistry               │       │
│  │  ├── AdmissionQueue              │       │
│  │  └── GPUMonitor (pynvml)         │       │
│  └──────────────────────────────────┘       │
│                                             │
│  GPU (index 0) ──── pynvml ────────────────►│
└─────────────────────────────────────────────┘
```

**Admission flow:**
1. Caller sends `POST /acquire` with `caller_id`, `vram_mb`, `tier`, optional `deadline_seconds`.
2. Coordinator queries current available VRAM via pynvml.
3. In `observe` mode: always permit, log the decision.
4. In `enforce` mode: check floors, queue if needed, permit or deny.
5. On permit: register lease, return `lease_id`.
6. Caller sends `POST /release` with `lease_id` when done.
7. Coordinator removes lease, potentially unblocking queued requests.

---

## §06 — API Sketch

### POST /acquire
```json
Request:
{
  "caller_id": "comfyui",
  "vram_mb": 8192,
  "tier": 2,
  "deadline_seconds": 30.0
}

Response 200 (permit):
{
  "lease_id": "uuid",
  "result": "permit",
  "vram_mb": 8192,
  "message": "granted"
}

Response 200 (observe mode — always permit):
{
  "lease_id": "uuid",
  "result": "permit",
  "vram_mb": 8192,
  "message": "observe mode — would permit"
}

Response 503 (deny — enforce mode):
{
  "lease_id": null,
  "result": "deny",
  "vram_mb": 0,
  "message": "hard floor breached: 800 MB available, 1536 MB floor"
}
```

### POST /release
```json
Request:  { "lease_id": "uuid", "caller_id": "comfyui" }
Response: { "released": true, "message": "ok" }
```

### GET /health
```json
{ "status": "ok" }
```

### GET /ready
```json
{ "ready": true, "gpu_available": true, "mode": "observe" }
```

### GET /metrics
Prometheus text format. Key metrics:
- `vram_coordinator_acquire_requests_total{result="permit|deny|shed"}`
- `vram_coordinator_active_leases`
- `vram_coordinator_vram_available_mb`
- `vram_coordinator_queue_depth{tier="high|normal|low"}`

### GET /stats
```json
{
  "mode": "observe",
  "vram_total_mb": 24576,
  "vram_available_mb": 18432,
  "vram_committed_mb": 6144,
  "soft_floor_mb": 3072,
  "hard_floor_mb": 1536,
  "active_leases": 2,
  "queue_depth": 0,
  "decisions": { "permit": 42, "deny": 0, "shed": 0 }
}
```

---

## §07 — Pressure Model

VRAM availability is queried via **pynvml** (`nvidia-ml-py` package) on each acquire request and periodically in the background.

**Available VRAM** = `total_vram - used_vram - safety_overhead_mb`

**Floor semantics:**
| Level | Threshold | Behavior (enforce mode) |
|---|---|---|
| Comfortable | available > soft_floor_mb | Permit immediately |
| Soft pressure | hard_floor_mb < available ≤ soft_floor_mb | Permit but log warning; back-pressure low-tier queue |
| Hard floor | available ≤ hard_floor_mb | Deny all new requests; shed low-tier queue |

**Defaults from .env.example:**
- `SOFT_FLOOR_MB=3072`
- `HARD_FLOOR_MB=1536`
- `SAFETY_OVERHEAD_MB=768`

In `observe` mode, pressure levels are computed and logged but never acted upon.

---

## §08 — Priority Tiers and Fairness

Three tiers (lower number = higher priority):

| Tier | Value | Intended use |
|---|---|---|
| high | 1 | Critical/interactive requests |
| normal | 2 | Standard inference |
| low | 3 | Background/batch work |

**Rules:**
- Requests are admitted in tier order, then FIFO within the same tier.
- High-tier requests skip ahead of normal/low in the queue.
- Under hard-floor pressure, low-tier requests are shed first.
- No starvation guarantee for MVP (simple tier+FIFO).

---

## §09 — Back-Pressure and Load-Shedding

- Each tier has a configurable max queue depth (default: `MAX_QUEUE_DEPTH=20` total).
- Requests specify an optional `deadline_seconds` (default: 30s). If not served within deadline, the request is timed out with a `shed` result.
- Under hard-floor conditions: all low-tier queued requests are immediately shed.
- Requests beyond max queue depth are rejected immediately with `shed`.

---

## §10 — Integration Boundaries

**Phase 1 (this build):** Coordinator is standalone. No callers wired. Integration boundary is HTTP only — any process on `aibox` loopback can call it, but none are configured to do so yet.

**Future phases (not in scope now):**
- APIGateway → emits `caller_id`, `tier`, `vram_mb` on acquire; calls release when done.
- vram-monitor → reads `/stats` for dashboard visualization.

The coordinator has zero knowledge of caller internals. It does not import, reference, or depend on any other project's code.

---

## §11 — Observability

**Structured logs** (JSON to stdout, captured by Docker):
```json
{
  "ts": "2026-07-23T17:00:00Z",
  "event": "acquire",
  "caller_id": "comfyui",
  "vram_mb": 8192,
  "tier": 2,
  "result": "permit",
  "mode": "observe",
  "vram_available_mb": 18432,
  "lease_id": "uuid"
}
```

**Prometheus metrics** at `GET /metrics`:
- `vram_coordinator_acquire_requests_total{result}`
- `vram_coordinator_release_requests_total`
- `vram_coordinator_active_leases`
- `vram_coordinator_vram_available_mb`
- `vram_coordinator_vram_committed_mb`
- `vram_coordinator_queue_depth{tier}`
- `vram_coordinator_decisions_total{result}`

---

## §12 — Failure Modes and Handling

| Failure | Behavior |
|---|---|
| Coordinator unreachable | **Fail-open** — callers proceed without a lease |
| pynvml / GPU query failure | Use last known VRAM value; log `WARNING`; continue |
| Stale lease (caller crashed) | TTL-based auto-release after `LEASE_TTL_SECONDS=300` |
| Coordinator restart | All in-memory leases cleared; callers must re-acquire |
| `enforce` mode with no GPU data | Fall back to `observe` mode; log `ERROR` |

---

## §13 — Rollout Plan

| Phase | Description | Gate |
|---|---|---|
| 1 (now) | Build service, deploy in `observe` mode on `aibox` | `/health` 200, logs flowing |
| 2 | Wire first caller in shadow mode (acquire/release but fail-open) | Observe logs show real traffic |
| 3 | Enable `enforce` for low tier only | No regressions in 24h observe window |
| 4 | Full enforcement all tiers | Load test + runbook complete |

---

## §14 — Rollback Strategy

1. **Instant:** Set `COORDINATOR_MODE=observe` in `.env`, `docker compose restart vram-coordinator`. Enforcement disabled in <5s.
2. **Full removal:** `docker compose down`. Coordinator vanishes from the call path entirely (callers are fail-open anyway).
3. **Config rollback:** Git revert `.env` change, `docker compose restart`.

Runbook lives at `docs/runbook.md` (Phase 4 deliverable).

---

## §15 — Security and Safety Considerations

- **Network exposure:** Bound to `127.0.0.1:8787` on the host — no external exposure.
- **Auth:** None for MVP. Loopback-only access is the security boundary. Token auth deferred to Phase 2 if external callers are added.
- **Secrets:** `.env` owned `600` by `vram-coordinator`. Never committed to repo.
- **Container:** Runs as non-root (uid 1000 inside container). `docker` group membership on host is for Compose management only.
- **Capabilities:** No extra Linux capabilities. No `--privileged`. pynvml accesses GPU via `/dev/nvidia*` which is accessible to the docker daemon without extra caps.

---

## §16 — Configuration Surface (initial)

All config via `.env` (sourced from `.env.example`):

| Key | Type | Default | Description |
|---|---|---|---|
| `COORDINATOR_MODE` | `observe\|enforce` | `observe` | Admission mode |
| `LISTEN_HOST` | str | `0.0.0.0` | Bind address inside container |
| `LISTEN_PORT` | int | `8787` | Listen port |
| `SOFT_FLOOR_MB` | int | `3072` | Back-pressure threshold (MB) |
| `HARD_FLOOR_MB` | int | `1536` | Deny threshold (MB) |
| `SAFETY_OVERHEAD_MB` | int | `768` | Always-reserved buffer (MB) |
| `LEASE_TTL_SECONDS` | int | `300` | Auto-release stale leases after N seconds |
| `MAX_QUEUE_DEPTH` | int | `20` | Max queued requests before shedding |
| `LOG_LEVEL` | str | `INFO` | Logging verbosity |

---

## §17 — Open Decisions (RESOLVED)

| # | Decision | Resolution |
|---|---|---|
| 1 | Default launch mode | **`observe`** — coordinator logs decisions but never blocks. Operator switches to `enforce` explicitly. |
| 2 | Fail-open vs fail-closed | **Fail-open** — if coordinator is unreachable, callers proceed without a lease. |
| 3 | Phase 1 client list | **None** — coordinator is standalone. No callers wired until Phase 2. |
| 4 | API auth model | **None for MVP** — loopback-only (127.0.0.1) is the security boundary. |
| 5 | GPU query mechanism | **pynvml** (`nvidia-ml-py`) — Python-native, no subprocess overhead, well-maintained. |

---

## §18 — Acceptance Criteria for Planning Completion

- [x] All 17 content sections authored
- [x] Open decisions resolved (§17)
- [x] API contract defined with request/response schemas (§06)
- [x] Pressure model and floor semantics documented (§07)
- [x] Failure modes enumerated with explicit fail-open decision (§12)
- [x] Rollout plan phased and gated (§13)
- [ ] Service skeleton running on `aibox` with `/health` → 200
- [ ] GitHub Actions CI building and pushing `ghcr.io/jheizman/vram-coordinator:dev`
- [ ] Compose stack healthy on `aibox` in `observe` mode