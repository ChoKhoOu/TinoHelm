#!/usr/bin/env bash
# Verify docker compose deployment for TinoHelm
# Usage: ./scripts/verify_docker.sh
set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

step() { echo -e "\n${CYAN}[STEP]${NC} $1"; }
pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

echo "=========================================="
echo "  TinoHelm Docker Compose Verification"
echo "=========================================="

# 1. Check docker compose config is valid
step "Validating docker-compose.yml..."
docker compose config --quiet 2>/dev/null || fail "docker-compose.yml is invalid"
pass "Config is valid"

# 2. Check all services are running
step "Checking service status..."
SERVICES=$(docker compose ps --format json 2>/dev/null)
echo "$SERVICES" | head -20

for SVC in postgres redis api web; do
    STATE=$(docker compose ps "$SVC" --format '{{.State}}' 2>/dev/null || echo "not found")
    if [ "$STATE" = "running" ]; then
        pass "$SVC is running"
    else
        fail "$SVC is not running (state=$STATE)"
    fi
done

# 3. Check health status
step "Checking health status..."
for SVC in postgres redis api; do
    HEALTH=$(docker compose ps "$SVC" --format '{{.Health}}' 2>/dev/null || echo "unknown")
    echo "  $SVC: $HEALTH"
    if [ "$HEALTH" = "healthy" ]; then
        pass "$SVC health check passed"
    else
        echo "  Warning: $SVC health=$HEALTH (may still be starting)"
    fi
done

# 4. Check API responds
step "Testing API health endpoint..."
for i in $(seq 1 6); do
    if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
        HEALTH=$(curl -sf http://localhost:8000/api/health)
        echo "$HEALTH" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"
        pass "API health endpoint responding"
        break
    fi
    echo "  [$((i*5))s] Waiting for API..."
    sleep 5
    [ $i -eq 6 ] && fail "API not responding after 30s"
done

# 5. Check web UI
step "Testing web UI..."
HTTP_CODE=$(curl -sf -o /dev/null -w '%{http_code}' http://localhost:3000/ 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    pass "Web UI responding (HTTP 200)"
else
    fail "Web UI not responding (HTTP $HTTP_CODE)"
fi

# 6. Check database connection
step "Testing database connection..."
docker compose exec -T postgres pg_isready -U tinohelm > /dev/null 2>&1 || fail "PostgreSQL not ready"
pass "PostgreSQL accepting connections"

# 7. Check Redis connection
step "Testing Redis connection..."
PONG=$(docker compose exec -T redis redis-cli ping 2>/dev/null || echo "")
[ "$PONG" = "PONG" ] || fail "Redis not responding"
pass "Redis responding"

# 8. Check volume mounts
step "Checking volume mounts..."
for DIR in data/postgres data/redis strategies config logs; do
    if [ -d "$DIR" ]; then
        pass "Volume mount exists: $DIR"
    else
        echo "  Warning: $DIR not found (may be created on first use)"
    fi
done

echo ""
echo "=========================================="
echo -e "  ${GREEN}DOCKER COMPOSE VERIFICATION — PASSED${NC}"
echo "=========================================="
