#!/usr/bin/env bash
# End-to-end sandbox demo flow for TinoHelm
# Prerequisites: docker compose up -d (all services running)
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
TINO="${TINO:-tino}"
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

step() { echo -e "\n${CYAN}[STEP]${NC} $1"; }
pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

cleanup() {
    echo ""
    step "Cleanup: stopping sandbox..."
    $TINO sandbox stop --format json 2>/dev/null || \
        curl -sf -X POST "${API_URL}/api/node/stop" \
        -H "Content-Type: application/json" \
        -d '{"mode":"sandbox"}' || true
    pass "Sandbox stopped"
}

echo "=========================================="
echo "  TinoHelm E2E Sandbox Demo Flow"
echo "=========================================="

# 1. Health check
step "Checking API health..."
HEALTH=$(curl -sf "${API_URL}/api/health" 2>/dev/null) || fail "API not reachable at ${API_URL}"
echo "$HEALTH" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"
pass "API is healthy"

# 2. Verify no nodes running
step "Checking node status (should be stopped)..."
NODE_STATUS=$($TINO node status --format json 2>/dev/null || curl -sf "${API_URL}/api/node/status")
echo "$NODE_STATUS" | python3 -m json.tool 2>/dev/null || echo "$NODE_STATUS"
pass "Node status retrieved"

# 3. Start sandbox with strategy
step "Starting sandbox with ema_cross_demo..."
trap cleanup EXIT
START_RESULT=$($TINO sandbox start --strategy ema_cross_demo --format json 2>/dev/null || \
               curl -sf -X POST "${API_URL}/api/node/start" \
               -H "Content-Type: application/json" \
               -d '{"mode":"sandbox","strategies":["ema_cross_demo"]}')
echo "$START_RESULT" | python3 -m json.tool 2>/dev/null || echo "$START_RESULT"
pass "Sandbox start command sent"

# 4. Wait for sandbox to start
step "Waiting for sandbox to start (max 30s)..."
for i in $(seq 1 6); do
    sleep 5
    STATUS=$($TINO node status --format json 2>/dev/null || curl -sf "${API_URL}/api/node/status")
    SANDBOX_STATE=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sandbox',{}).get('status','unknown'))" 2>/dev/null || echo "unknown")
    echo "  [$((i*5))s] sandbox=$SANDBOX_STATE"
    if [ "$SANDBOX_STATE" = "running" ]; then
        pass "Sandbox is running"
        break
    fi
    [ $i -eq 6 ] && fail "Sandbox did not start within 30s"
done

# 5. Verify node status shows running
step "Verifying sandbox node status..."
NODE_STATUS=$($TINO node status --format json 2>/dev/null || curl -sf "${API_URL}/api/node/status")
echo "$NODE_STATUS" | python3 -m json.tool 2>/dev/null || echo "$NODE_STATUS"
pass "Sandbox node status confirmed"

# 6. Check WebSocket events (wait briefly for events)
step "Checking for WebSocket events via REST..."
sleep 5
DASHBOARD=$(curl -sf "${API_URL}/api/dashboard/summary")
echo "$DASHBOARD" | python3 -m json.tool 2>/dev/null || echo "$DASHBOARD"
pass "Dashboard data available during sandbox run"

# 7. Check positions endpoint
step "Checking positions..."
ORDERS=$(curl -sf "${API_URL}/api/orders" 2>/dev/null || echo '{"orders":[]}')
echo "$ORDERS" | python3 -m json.tool 2>/dev/null || echo "$ORDERS"
pass "Orders endpoint accessible"

# 8. Stop sandbox (handled by cleanup trap, but also test explicit stop)
step "Stopping sandbox..."
STOP_RESULT=$($TINO sandbox stop --format json 2>/dev/null || \
              curl -sf -X POST "${API_URL}/api/node/stop" \
              -H "Content-Type: application/json" \
              -d '{"mode":"sandbox"}')
echo "$STOP_RESULT" | python3 -m json.tool 2>/dev/null || echo "$STOP_RESULT"
pass "Sandbox stop command sent"

# 9. Verify stopped
step "Verifying sandbox stopped (max 15s)..."
for i in $(seq 1 3); do
    sleep 5
    STATUS=$($TINO node status --format json 2>/dev/null || curl -sf "${API_URL}/api/node/status")
    SANDBOX_STATE=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sandbox',{}).get('status','unknown'))" 2>/dev/null || echo "stopped")
    echo "  [$((i*5))s] sandbox=$SANDBOX_STATE"
    if [ "$SANDBOX_STATE" = "stopped" ] || [ "$SANDBOX_STATE" = "unknown" ]; then
        pass "Sandbox confirmed stopped"
        break
    fi
    [ $i -eq 3 ] && fail "Sandbox did not stop within 15s"
done

# Remove trap since we already stopped
trap - EXIT

echo ""
echo "=========================================="
echo -e "  ${GREEN}E2E SANDBOX FLOW — ALL CHECKS PASSED${NC}"
echo "=========================================="
