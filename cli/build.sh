#!/usr/bin/env bash
# Build the Rust tino CLI binary.
#
# Usage:
#   ./cli/build.sh              # Release build
#   ./cli/build.sh --debug      # Debug build (faster, larger)
#
# Output:
#   cli/target/release/tino     # Release binary (~2 MB)
#   cli/target/debug/tino       # Debug binary (if --debug)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PROFILE="release"
for arg in "$@"; do
    case "$arg" in
        --debug) PROFILE="debug" ;;
        *)       echo "Unknown arg: $arg"; exit 1 ;;
    esac
done

if [ "$PROFILE" = "release" ]; then
    echo "Building tino (release)..."
    cargo build --release
    BINARY="$SCRIPT_DIR/target/release/tino"
else
    echo "Building tino (debug)..."
    cargo build
    BINARY="$SCRIPT_DIR/target/debug/tino"
fi

if [ -f "$BINARY" ]; then
    SIZE=$(du -sh "$BINARY" | cut -f1)
    echo ""
    echo "Build successful!"
    echo "  Binary: $BINARY"
    echo "  Size:   $SIZE"
    echo ""
    echo "Test it:"
    echo "  $BINARY version"
    echo "  $BINARY --help"
else
    echo "ERROR: Binary not found at $BINARY"
    exit 1
fi
