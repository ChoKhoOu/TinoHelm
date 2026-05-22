#!/usr/bin/env bash
# AC-2: grep-level compliance check
# Exits 0 on success, 1 on any violation.
set -u

command -v rg >/dev/null 2>&1 || { echo "[FAIL] ripgrep (rg) not installed"; exit 1; }

# Script lives at <repo>/src/web/scripts/check-grep-fonts.sh; anchor REPO_ROOT via script location
# (src/web has a nested .git from scaffold, so `git rev-parse --show-toplevel` would return src/web).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
[ -d "$REPO_ROOT/.git" ] || { echo "[FAIL] REPO_ROOT $REPO_ROOT is not a git repo"; exit 1; }
cd "$REPO_ROOT"

[ -f "CLAUDE.md" ] || { echo "[FAIL] root CLAUDE.md not found"; exit 1; }
[ -f "src/web/CLAUDE.md" ] || { echo "[FAIL] src/web/CLAUDE.md not found"; exit 1; }

violations=0

check_no_match() {
  local desc="$1"; shift
  if "$@" > /dev/null 2>&1; then
    echo "[FAIL] $desc"
    "$@" || true
    violations=$((violations + 1))
  else
    echo "[PASS] $desc"
  fi
}

# AC-2.1 — no IBM Plex in src/web/
check_no_match "AC-2.1 No 'IBM Plex' literals in src/web/" \
  rg -q "IBM Plex" src/web/ \
     --glob '!*.html' \
     --glob '!*.bak' \
     --glob '!node_modules' \
     --glob '!.next' \
     --glob '!out' \
     --glob '!archive' \
     --glob '!CHANGELOG.md' \
     --glob '!src/web/scripts/check-grep-fonts.sh' \
     --glob '!src/web/tests/fonts/tokens.test.ts'

# AC-2.2 — no CDN direct reference in src/web/src/
check_no_match "AC-2.2 No 'fonts.googleapis.com' direct reference in src/web/src/" \
  rg -q "fonts.googleapis.com" src/web/src/

# AC-2.3 — CLAUDE.md sync (both root and web)
check_no_match "AC-2.3 No 'IBM Plex' in src/web/CLAUDE.md" rg -q "IBM Plex" src/web/CLAUDE.md
check_no_match "AC-2.3 No 'IBM Plex' in root CLAUDE.md" rg -q "IBM Plex" CLAUDE.md

if [ $violations -gt 0 ]; then
  echo "check-grep-fonts: $violations violation(s) found"
  exit 1
fi
echo "check-grep-fonts: all checks passed"
