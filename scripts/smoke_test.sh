#!/usr/bin/env bash
# scripts/smoke_test.sh — end-to-end validation for vram-coordinator
# Usage: bash scripts/smoke_test.sh [base_url]
set -uo pipefail

BASE="${1:-http://127.0.0.1:8787}"
PASS=0; FAIL=0

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

jq_val() { echo "$1" | python3 -c "import sys,json; print(json.load(sys.stdin)$2)"; }

echo "=== vram-coordinator smoke test ==="
echo "  target: $BASE"
echo ""

echo "--- liveness & readiness ---"
H=$(curl -sf "$BASE/health")
check "/health status=ok" '"status":"ok"' "$H"

R=$(curl -sf "$BASE/ready")
check "/ready ready=true" '"ready":true' "$R"
check "/ready gpu_available=true" '"gpu_available":true' "$R"

echo ""
echo "--- acquire/release cycle (delta check) ---"
S_BEFORE=$(curl -sf "$BASE/stats")
LEASES_BEFORE=$(jq_val "$S_BEFORE" "['active_leases']")

ACQUIRE=$(curl -sf -X POST "$BASE/acquire" \
  -H "Content-Type: application/json" \
  -d '{"caller_id":"smoke_test","vram_mb":512,"tier":2}')
check "/acquire result=permit" '"result":"permit"' "$ACQUIRE"
LEASE=$(jq_val "$ACQUIRE" "['lease_id']")
echo "  lease_id: $LEASE"

S_MID=$(curl -sf "$BASE/stats")
LEASES_MID=$(jq_val "$S_MID" "['active_leases']")
EXPECTED_MID=$((LEASES_BEFORE + 1))
if [ "$LEASES_MID" -eq "$EXPECTED_MID" ]; then
  echo "  PASS  active_leases incremented ($LEASES_BEFORE -> $LEASES_MID)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  active_leases expected $EXPECTED_MID got $LEASES_MID"
  FAIL=$((FAIL + 1))
fi

RELEASE=$(curl -sf -X POST "$BASE/release" \
  -H "Content-Type: application/json" \
  -d "{\"lease_id\":\"$LEASE\",\"caller_id\":\"smoke_test\"}")
check "/release released=true" '"released":true' "$RELEASE"

S_AFTER=$(curl -sf "$BASE/stats")
LEASES_AFTER=$(jq_val "$S_AFTER" "['active_leases']")
if [ "$LEASES_AFTER" -eq "$LEASES_BEFORE" ]; then
  echo "  PASS  active_leases restored ($LEASES_MID -> $LEASES_AFTER)"
  PASS=$((PASS + 1))
else
  echo "  FAIL  active_leases expected $LEASES_BEFORE got $LEASES_AFTER"
  FAIL=$((FAIL + 1))
fi

echo ""
echo "--- stats sanity ---"
check "/stats has mode" '"mode"' "$S_AFTER"
check "/stats decisions present" '"decisions"' "$S_AFTER"
check "/stats vram_total_mb > 0" '"vram_total_mb":1' "$S_AFTER"

echo ""
echo "--- metrics ---"
M=$(curl -sf "$BASE/metrics")
check "/metrics vram_available" 'vram_coordinator_vram_available_mb' "$M"
check "/metrics decisions_total" 'vram_coordinator_decisions_total' "$M"

echo ""
echo "=== results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1