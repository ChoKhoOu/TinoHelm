#!/usr/bin/env bash
# End-to-end backtest demo flow for TinoHelm
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

echo "=========================================="
echo "  TinoHelm E2E Backtest Demo Flow"
echo "=========================================="

# 1. Health check
step "Checking API health..."
HEALTH=$(curl -sf "${API_URL}/api/health" 2>/dev/null) || fail "API not reachable at ${API_URL}"
echo "$HEALTH" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"
pass "API is healthy"

# 2. Verify strategies are discovered
step "Listing strategies..."
STRATEGY="${E2E_STRATEGY:-btc_multi_factor}"
INTERVAL="${E2E_INTERVAL:-5m}"
STRATEGIES=$($TINO strategy list --format json 2>/dev/null || curl -sf "${API_URL}/api/strategies")
echo "$STRATEGIES" | python3 -m json.tool 2>/dev/null || echo "$STRATEGIES"
echo "$STRATEGIES" | grep -q "$STRATEGY" || fail "$STRATEGY not found in strategies"
pass "$STRATEGY strategy discovered"

# 3. Validate strategy
step "Validating $STRATEGY..."
VALID=$($TINO strategy validate "$STRATEGY" --format json 2>/dev/null || \
        curl -sf -X POST "${API_URL}/api/strategies/${STRATEGY}/validate")
echo "$VALID" | python3 -m json.tool 2>/dev/null || echo "$VALID"
pass "Strategy validation complete"

# 4. Fetch sample data
step "Fetching sample data (BTCUSDT-PERP ${INTERVAL}, last 30 days)..."
END_DATE=$(date +%Y-%m-%d)
START_DATE=$(date -v-30d +%Y-%m-%d 2>/dev/null || date -d "30 days ago" +%Y-%m-%d)
FETCH=$($TINO data fetch BTCUSDT-PERP "$INTERVAL" "$START_DATE" "$END_DATE" --format json 2>/dev/null || \
        curl -sf -X POST "${API_URL}/api/data/fetch-batch" \
        -H "Content-Type: application/json" \
        -d "{\"symbols\":[\"BTCUSDT-PERP\"],\"intervals\":[\"${INTERVAL}\"],\"start\":\"${START_DATE}\",\"end\":\"${END_DATE}\"}")
echo "$FETCH" | python3 -m json.tool 2>/dev/null || echo "$FETCH"
pass "Data fetch initiated"

# 5. Submit backtest
step "Submitting backtest run..."
RUN_RESULT=$($TINO backtest run "$STRATEGY" --symbol BTCUSDT-PERP --interval "$INTERVAL" --start "$START_DATE" --end "$END_DATE" --format json 2>/dev/null || \
             curl -sf -X POST "${API_URL}/api/backtest/run" \
             -H "Content-Type: application/json" \
             -d "{\"strategy\":\"${STRATEGY}\",\"symbols\":[\"BTCUSDT-PERP\"],\"interval\":\"${INTERVAL}\",\"start_date\":\"${START_DATE}\",\"end_date\":\"${END_DATE}\"}")
echo "$RUN_RESULT" | python3 -m json.tool 2>/dev/null || echo "$RUN_RESULT"
RUN_ID=$(echo "$RUN_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null || echo "")
[ -n "$RUN_ID" ] || fail "No run_id returned"
pass "Backtest submitted: run_id=$RUN_ID"

# 6. Poll for completion
step "Waiting for backtest to complete (max 120s)..."
for i in $(seq 1 24); do
    sleep 5
    STATUS=$($TINO backtest status "$RUN_ID" --format json 2>/dev/null || \
             curl -sf "${API_URL}/api/backtest/${RUN_ID}/status")
    CURRENT=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "unknown")
    echo "  [$((i*5))s] status=$CURRENT"
    if [ "$CURRENT" = "completed" ]; then
        pass "Backtest completed"
        break
    elif [ "$CURRENT" = "failed" ]; then
        fail "Backtest failed"
    fi
    [ $i -eq 24 ] && fail "Backtest timed out after 120s"
done

# 7. Get result
step "Fetching backtest result..."
RESULT=$($TINO backtest result "$RUN_ID" --format json 2>/dev/null || \
         curl -sf "${API_URL}/api/backtest/${RUN_ID}/result")
echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"
pass "Backtest result retrieved"

# 8. Verify dashboard shows data
step "Checking dashboard summary..."
DASHBOARD=$(curl -sf "${API_URL}/api/dashboard/summary")
echo "$DASHBOARD" | python3 -m json.tool 2>/dev/null || echo "$DASHBOARD"
pass "Dashboard summary available"

# 9. Verify backtest list
step "Listing backtest runs..."
RUNS=$($TINO backtest list --format json 2>/dev/null || curl -sf "${API_URL}/api/backtest/runs")
echo "$RUNS" | python3 -m json.tool 2>/dev/null || echo "$RUNS"
pass "Backtest runs listed"

# 10. Check web UI
step "Checking web UI is accessible..."
curl -sf http://localhost:3000/ > /dev/null || fail "Web UI not reachable at http://localhost:3000"
pass "Web UI accessible"

echo ""
echo "=========================================="
echo -e "  ${GREEN}E2E BACKTEST FLOW — ALL CHECKS PASSED${NC}"
echo "=========================================="
