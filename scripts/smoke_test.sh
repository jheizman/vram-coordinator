#!/usr/bin/env bash
# scripts/smoke_test.sh
set -uo pipefail

BASE="${1:-http://127.0.0.1:8787}"
PASS=0
FAIL=0

check() {
  local label="$1" expected="$2" actual="$3"
  if echo "$actual" | grep -q "$expected"; then
    echo "  PASS  $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL  $label"
    echo "        expected: $expected"
    echo "        got:      $actual"
    FAIL=$((FAIL + 1))
  fi
}

jq_field() { echo "$1" | python3 -c "import sys,json; print(json.load(sys.stdin)$2)"; }

echo "=== vram-coordinator smoke test ==="
echo "  target: $BASE"

echo "--- health/ready ---"
H=$(curl -sf "$BASE/health")
check "/health ok" '"status":"ok"' "$H"
check "/health request_id" '"request_id"' "$H"

R=$(curl -sf "$BASE/ready")
check "/ready ready=true" '"ready":true' "$R"
check "/ready gpu_available=true" '"gpu_available":true' "$R"

echo "--- acquire/release ---"
S_BEFORE=$(curl -sf "$BASE/stats")
LEASES_BEFORE=$(jq_field "$S_BEFORE" "['active_leases']")

ACQ=$(curl -sf -X POST "$BASE/acquire" -H "Content-Type: application/json" -d '{"caller_id":"smoke_test","vram_mb":512,"tier":2}')
check "/acquire permit" '"result":"permit"' "$ACQ"
check "/acquire request_id" '"request_id"' "$ACQ"
LEASE=$(jq_field "$ACQ" "['lease_id']")

S_MID=$(curl -sf "$BASE/stats")
LEASES_MID=$(jq_field "$S_MID" "['active_leases']")
if [ "$LEASES_MID" -eq $((LEASES_BEFORE + 1)) ]; then
  echo "  PASS  active leases incremented"
  PASS=$((PASS + 1))
else
  echo "  FAIL  active leases did not increment"
  FAIL=$((FAIL + 1))
fi

REL1=$(curl -sf -X POST "$BASE/release" -H "Content-Type: application/json" -d "{\"lease_id\":\"$LEASE\",\"caller_id\":\"smoke_test\"}")
check "/release first ok" '"released":true' "$REL1"

REL2=$(curl -sf -X POST "$BASE/release" -H "Content-Type: application/json" -d "{\"lease_id\":\"$LEASE\",\"caller_id\":\"smoke_test\"}")
check "/release idempotent" '"already released"' "$REL2"

S_AFTER=$(curl -sf "$BASE/stats")
LEASES_AFTER=$(jq_field "$S_AFTER" "['active_leases']")
if [ "$LEASES_AFTER" -eq "$LEASES_BEFORE" ]; then
  echo "  PASS  active leases restored"
  PASS=$((PASS + 1))
else
  echo "  FAIL  active leases mismatch after release"
  FAIL=$((FAIL + 1))
fi
check "/stats decision_reasons present" '"decision_reasons"' "$S_AFTER"

echo "--- metrics ---"
M=$(curl -sf "$BASE/metrics")
check "/metrics queue_depth_by_tier" 'vram_coordinator_queue_depth_by_tier' "$M"
check "/metrics decisions_total" 'vram_coordinator_decisions_total' "$M"
check "/metrics decision_reasons_total" 'vram_coordinator_decision_reasons_total' "$M"
check "/metrics wait_ms_total" 'vram_coordinator_wait_ms_total' "$M"

echo "=== results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]