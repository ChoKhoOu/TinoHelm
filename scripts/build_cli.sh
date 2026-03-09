#!/usr/bin/env bash
# Build the tino CLI into a standalone binary using PyInstaller.
#
# Usage:
#   ./scripts/build_cli.sh              # Build + package as tar.gz (default)
#   ./scripts/build_cli.sh --onefile    # Build as single binary (slow startup)
#
# Output:
#   dist/tino/tino                      # Executable (onedir)
#   dist/tino-<os>-<arch>.tar.gz        # Distributable archive
#
# Install (end user):
#   tar xzf tino-darwin-arm64.tar.gz -C ~/.local
#   # Add to PATH: export PATH="$HOME/.local/tino:$PATH"
#
# The CLI is a thin HTTP client — it does NOT bundle NautilusTrader or
# the server-side code, so the resulting binary is lightweight (~15-30 MB).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# ── Parse args ────────────────────────────────────────────────────────────
MODE="--onedir"
for arg in "$@"; do
    case "$arg" in
        --onedir)  MODE="--onedir" ;;
        --onefile) MODE="--onefile" ;;
        *)         echo "Unknown arg: $arg"; exit 1 ;;
    esac
done

# ── Ensure venv + PyInstaller ─────────────────────────────────────────────
VENV_DIR="$PROJECT_ROOT/.venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating venv..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# Install the project (editable) so tinohelm.cli is importable
pip install -e "." --quiet 2>/dev/null

# Install PyInstaller if missing
if ! python -m PyInstaller --version &>/dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller --quiet
fi

# ── Build ─────────────────────────────────────────────────────────────────
echo "Building tino CLI binary ($MODE)..."

python -m PyInstaller \
    $MODE \
    --name tino \
    --clean \
    --noconfirm \
    --strip \
    --collect-submodules tinohelm.cli \
    --hidden-import tinohelm \
    --hidden-import tinohelm.cli \
    --hidden-import tinohelm.cli.main \
    --hidden-import tinohelm.cli.backtest \
    --hidden-import tinohelm.cli.strategy \
    --hidden-import tinohelm.cli.data \
    --hidden-import tinohelm.cli.node \
    --hidden-import tinohelm.cli._http \
    --hidden-import tinohelm.cli._style \
    --exclude-module nautilus_trader \
    --exclude-module fastapi \
    --exclude-module uvicorn \
    --exclude-module sqlalchemy \
    --exclude-module asyncpg \
    --exclude-module alembic \
    --exclude-module redis \
    --exclude-module pydantic_settings \
    --exclude-module plotly \
    --exclude-module optuna \
    --exclude-module numpy \
    --exclude-module pandas \
    --exclude-module scipy \
    --exclude-module matplotlib \
    --exclude-module PIL \
    --exclude-module cv2 \
    --exclude-module torch \
    --exclude-module tensorflow \
    --exclude-module tkinter \
    --exclude-module test \
    --exclude-module unittest \
    "$PROJECT_ROOT/src/tinohelm/cli/main.py"

# ── Verify ────────────────────────────────────────────────────────────────
if [ "$MODE" = "--onefile" ]; then
    BINARY="$PROJECT_ROOT/dist/tino"
else
    BINARY="$PROJECT_ROOT/dist/tino/tino"
fi

if [ -f "$BINARY" ]; then
    SIZE=$(du -sh "$BINARY" | cut -f1)
    echo ""
    echo "Build successful!"
    echo "  Binary: $BINARY"
    echo "  Size:   $SIZE"

    # Package as tar.gz for distribution (onedir only)
    if [ "$MODE" = "--onedir" ]; then
        OS=$(uname -s | tr '[:upper:]' '[:lower:]')
        ARCH=$(uname -m)
        ARCHIVE="$PROJECT_ROOT/dist/tino-${OS}-${ARCH}.tar.gz"
        tar czf "$ARCHIVE" -C "$PROJECT_ROOT/dist" tino/
        ASIZE=$(du -sh "$ARCHIVE" | cut -f1)
        echo "  Archive: $ARCHIVE ($ASIZE)"
        echo ""
        echo "Install:"
        echo "  tar xzf tino-${OS}-${ARCH}.tar.gz -C ~/.local"
        echo "  export PATH=\"\$HOME/.local/tino:\$PATH\""
    fi

    echo ""
    echo "Test it:"
    echo "  $BINARY version"
    echo "  $BINARY --help"
else
    echo "ERROR: Binary not found at $BINARY"
    exit 1
fi
