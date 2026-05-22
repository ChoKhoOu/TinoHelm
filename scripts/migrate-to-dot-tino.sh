#!/bin/bash
# Migrate existing tino data from project directory to ~/.tino/
# Run this AFTER stopping containers: docker compose down

set -euo pipefail

DEST="$HOME/.tino"
SRC_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Migrating tino data to $DEST"
echo "Source: $SRC_ROOT"
echo ""

# 1. Create directory structure
echo "Creating directories..."
mkdir -p "$DEST"/{config,strategies,data/catalog,data/artifacts,data/postgres,data/redis,logs}

# 2. Migrate config
if [ -d "$SRC_ROOT/config" ] && [ "$(ls -A "$SRC_ROOT/config" 2>/dev/null)" ]; then
    echo "Migrating config..."
    cp -rn "$SRC_ROOT/config/"* "$DEST/config/" 2>/dev/null || true
    echo "  config -> $DEST/config/"
fi

# 3. Migrate strategies
if [ -d "$SRC_ROOT/tino/strategies" ] && [ "$(ls -A "$SRC_ROOT/tino/strategies" 2>/dev/null)" ]; then
    echo "Migrating strategies..."
    cp -rn "$SRC_ROOT/tino/strategies/"* "$DEST/strategies/" 2>/dev/null || true
    echo "  tino/strategies -> $DEST/strategies/"
fi

# 4. Migrate catalog data (parquet files)
if [ -d "$SRC_ROOT/tino/data/catalog" ] && [ "$(ls -A "$SRC_ROOT/tino/data/catalog" 2>/dev/null)" ]; then
    echo "Migrating catalog data..."
    cp -rn "$SRC_ROOT/tino/data/catalog/"* "$DEST/data/catalog/" 2>/dev/null || true
    echo "  tino/data/catalog -> $DEST/data/catalog/"
fi

# 5. Migrate artifacts
if [ -d "$SRC_ROOT/tino/data/artifacts" ] && [ "$(ls -A "$SRC_ROOT/tino/data/artifacts" 2>/dev/null)" ]; then
    echo "Migrating artifacts..."
    cp -rn "$SRC_ROOT/tino/data/artifacts/"* "$DEST/data/artifacts/" 2>/dev/null || true
    echo "  tino/data/artifacts -> $DEST/data/artifacts/"
fi

# 6. Migrate postgres data
if [ -d "$SRC_ROOT/tino/data/postgres" ] && [ "$(ls -A "$SRC_ROOT/tino/data/postgres" 2>/dev/null)" ]; then
    echo "Migrating postgres data..."
    cp -rn "$SRC_ROOT/tino/data/postgres/"* "$DEST/data/postgres/" 2>/dev/null || true
    echo "  tino/data/postgres -> $DEST/data/postgres/"
fi

# 7. Migrate redis data
if [ -d "$SRC_ROOT/tino/data/redis" ] && [ "$(ls -A "$SRC_ROOT/tino/data/redis" 2>/dev/null)" ]; then
    echo "Migrating redis data..."
    cp -rn "$SRC_ROOT/tino/data/redis/"* "$DEST/data/redis/" 2>/dev/null || true
    echo "  tino/data/redis -> $DEST/data/redis/"
fi

# 8. Migrate logs
if [ -d "$SRC_ROOT/tino/logs" ] && [ "$(ls -A "$SRC_ROOT/tino/logs" 2>/dev/null)" ]; then
    echo "Migrating logs..."
    cp -rn "$SRC_ROOT/tino/logs/"* "$DEST/logs/" 2>/dev/null || true
    echo "  tino/logs -> $DEST/logs/"
fi

echo ""
echo "Migration complete. New structure:"
echo ""
find "$DEST" -maxdepth 2 -type d | sort | head -20
echo ""
echo "Next steps:"
echo "  1. Verify data: ls -la ~/.tino/"
echo "  2. Start containers: docker compose up -d"
echo "  3. Confirm: tino strategy list"
echo ""
echo "Once confirmed, you can remove old data:"
echo "  rm -rf $SRC_ROOT/tino/data $SRC_ROOT/tino/logs"
