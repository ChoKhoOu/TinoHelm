#!/usr/bin/env bash
# verify-ds-compliance.sh — Design System Compliance Scanner
# Implements R1-R14 scans + --selftest + --preflight-before-css-delete
#
# Usage:
#   verify-ds-compliance.sh                          # Run R1-R14 full scan
#   verify-ds-compliance.sh --selftest               # Run positive/negative assertions
#   verify-ds-compliance.sh --preflight-before-css-delete  # s10 pre-delete check (R1-R14 must be 0)
#   verify-ds-compliance.sh --fix-hint               # Attach migration hints to violations
#   verify-ds-compliance.sh --mode both-themes       # Token/theme-discipline subset
#   verify-ds-compliance.sh --help                   # Print usage
#
# Exit codes:
#   0 — compliant (or selftest passed)
#   1 — violations found
#   2 — script error (missing deps, selftest failed, bad args)

set -euo pipefail

# ─── Dependency check ──────────────────────────────────────────────────────────
command -v rg >/dev/null 2>&1 || { echo "[ERROR] ripgrep (rg) not installed"; exit 2; }
rg --pcre2 --version >/dev/null 2>&1 || { echo "[ERROR] ripgrep PCRE2 support required (rg --pcre2)"; exit 2; }

# ─── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"   # src/web/
SRC="$ROOT/src"

# ─── Arguments ─────────────────────────────────────────────────────────────────
MODE="default"
FIX_HINT=0

for arg in "$@"; do
  case "$arg" in
    --selftest)               MODE="selftest" ;;
    --preflight-before-css-delete) MODE="preflight" ;;
    --mode)                   ;;  # consumed by next pass
    --fix-hint)               FIX_HINT=1 ;;
    --help|-h)
      cat <<'EOF'
verify-ds-compliance.sh — Design System Compliance Scanner (R1-R14)

USAGE:
  verify-ds-compliance.sh                           Run R1-R14 full scan
  verify-ds-compliance.sh --selftest                Run all rule assertions (positive + negative)
  verify-ds-compliance.sh --preflight-before-css-delete  Check R1-R14 all-zero (before s10 css delete)
  verify-ds-compliance.sh --fix-hint                Attach migration hints to each violation
  verify-ds-compliance.sh --mode both-themes        Theme-discipline subset (R1-R5, R10, R13)
  verify-ds-compliance.sh --help                    Print this help

EXIT CODES:
  0  compliant / selftest passed
  1  violations found
  2  script error (missing dep / selftest failed / bad args)

RULES:
  R1   fontFamily inline var(--font-u/d) in tsx/ts/js
  R2   legacy bt-* className usage in tsx/jsx
  R3   legacy dc-* className usage + dc-type-* dict values
  R4   legacy single-letter cg/ca/cr/ci/dim/mono as standalone tokens (PCRE2 lookaround)
  R5   hardcoded hex colors in tsx/ts
  R6   Tooltip without CHART_TOOLTIP_PROPS spread
  R7   CartesianGrid without CHART_GRID_STYLE spread
  R8   Legend wrapperStyle without CHART_LEGEND_STYLE ref (PCRE2)
  R9   ReferenceLine label with inline fontSize/fill without CHART_LABEL_STYLE spread
  R10  arbitrary var() token usage in tsx (excluding components/ui/)
  R11  legacy CSS class definitions in globals.css
  R12  inline fontSize style prop in tsx/jsx (excluding Recharts transparent contexts)
  R13  undefined --accent-* CSS variable usage (11 variants)
  R14  factor-research primitive classes in tsx/jsx (PCRE2 lookaround)
EOF
      exit 0
      ;;
    both-themes)
      # previous arg was --mode
      MODE="both-themes"
      ;;
  esac
done

# Re-process for "--mode both-themes" as two separate tokens
args=("$@")
n=${#args[@]}
for ((i=0; i<n; i++)); do
  if [[ "${args[$i]}" == "--mode" ]] && [[ $((i+1)) -lt $n ]] && [[ "${args[$((i+1))]}" == "both-themes" ]]; then
    MODE="both-themes"
  fi
done

# ─── Counters ──────────────────────────────────────────────────────────────────
TOTAL_VIOLATIONS=0
VIOLATED_FILES=()

# ─── Helper: run_rule ───────────────────────────────────────────────────────────
# Usage: run_rule RULE_ID "description" fix_hint_text <rg command and args>
# Prints each matching line in format: [RULE_ID] FILE:LINE:COL excerpt
# Returns number of violations found (also increments TOTAL_VIOLATIONS)

run_rule() {
  local rule_id="$1"; shift
  local description="$1"; shift
  local fix_hint_text="$1"; shift
  # remaining args: rg invocation

  local output
  output=$("$@" 2>/dev/null || true)

  if [[ -z "$output" ]]; then
    return 0
  fi

  local count=0
  local file_seen=()
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    echo "[$rule_id] $line"
    if [[ $FIX_HINT -eq 1 ]] && [[ -n "$fix_hint_text" ]]; then
      echo "  → Migrate: $fix_hint_text"
    fi
    count=$((count + 1))
    # track unique files
    local fpath
    fpath=$(echo "$line" | cut -d: -f1)
    local already=0
    for f in "${VIOLATED_FILES[@]+"${VIOLATED_FILES[@]}"}"; do
      [[ "$f" == "$fpath" ]] && already=1 && break
    done
    [[ $already -eq 0 ]] && VIOLATED_FILES+=("$fpath")
  done <<< "$output"

  TOTAL_VIOLATIONS=$((TOTAL_VIOLATIONS + count))
  return 0
}

# ─── SELFTEST ──────────────────────────────────────────────────────────────────
if [[ "$MODE" == "selftest" ]]; then
  echo "=== verify-ds-compliance.sh --selftest ==="
  echo "Running positive/negative assertions for R4/R6/R7/R8/R9/R10/R12/R13/R14..."
  echo ""

  FAIL=0
  PASS=0

  # assert_match RULE CONTENT — must produce at least one match
  assert_match() {
    local rule="$1"
    local content="$2"
    local tmpfile
    tmpfile=$(mktemp /tmp/ds-selftest-XXXXXX.tsx)
    printf '%s\n' "$content" > "$tmpfile"
    # run the appropriate rule pattern inline
    local matched=0
    case "$rule" in
      R4)
        rg --pcre2 -q 'className\s*=\s*["\x27][^"\x27]*(?<![-a-zA-Z0-9_])(cg|ca|cr|ci|dim|mono)(?![-a-zA-Z0-9_])[^"\x27]*["\x27]' "$tmpfile" 2>/dev/null && matched=1 || true
        ;;
      R6)
        rg -U --multiline-dotall -q '<(Recharts)?Tooltip\b[^/]*?/>' "$tmpfile" 2>/dev/null && matched=1 || true
        if [[ $matched -eq 1 ]]; then
          # must NOT contain ...CHART_TOOLTIP_PROPS
          if rg -q '\.\.\.CHART_TOOLTIP_PROPS' "$tmpfile" 2>/dev/null; then
            matched=0
          fi
        fi
        ;;
      R6_MULTILINE)
        rg -U --multiline-dotall -q '<(Recharts)?Tooltip\b[^/]*?/>' "$tmpfile" 2>/dev/null && matched=1 || true
        if [[ $matched -eq 1 ]]; then
          if rg -q '\.\.\.CHART_TOOLTIP_PROPS' "$tmpfile" 2>/dev/null; then
            matched=0
          fi
        fi
        ;;
      R7)
        rg -U --multiline-dotall -q '<CartesianGrid\b[^/]*?/>' "$tmpfile" 2>/dev/null && matched=1 || true
        if [[ $matched -eq 1 ]]; then
          if rg -q '\.\.\.CHART_GRID_STYLE' "$tmpfile" 2>/dev/null; then
            matched=0
          fi
        fi
        ;;
      R8)
        rg -U --multiline-dotall -q '<Legend\b[^/]*?/>' "$tmpfile" 2>/dev/null && {
          local block8
          block8=$(rg -U --multiline-dotall -o '<Legend\b[^/]*?/>' "$tmpfile" 2>/dev/null || true)
          if echo "$block8" | grep -q 'wrapperStyle\s*=\s*{{'; then
            if ! echo "$block8" | grep -q 'CHART_LEGEND_STYLE'; then
              matched=1
            fi
          fi
        } || true
        ;;
      R8_MULTILINE)
        rg -U --multiline-dotall -q '<Legend\b[^/]*?/>' "$tmpfile" 2>/dev/null && {
          local block8m
          block8m=$(rg -U --multiline-dotall -o '<Legend\b[^/]*?/>' "$tmpfile" 2>/dev/null || true)
          if echo "$block8m" | grep -q 'wrapperStyle\s*=\s*{{'; then
            if ! echo "$block8m" | grep -q 'CHART_LEGEND_STYLE'; then
              matched=1
            fi
          fi
        } || true
        ;;
      R9)
        # single-line
        rg -U --multiline-dotall -q '<ReferenceLine\b[^/]*?/>' "$tmpfile" 2>/dev/null && {
          # has label with fontSize/fill/fontFamily but no ...CHART_LABEL_STYLE
          local block
          block=$(rg -U --multiline-dotall -o '<ReferenceLine\b[^/]*?/>' "$tmpfile" 2>/dev/null || true)
          if echo "$block" | rg -q 'label\s*=\s*\{\{'; then
            if echo "$block" | rg -q 'fontSize|fill|fontFamily'; then
              if ! echo "$block" | rg -q '\.\.\.CHART_LABEL_STYLE'; then
                matched=1
              fi
            fi
          fi
        } || true
        ;;
      R9_MULTILINE)
        rg -U --multiline-dotall -q '<ReferenceLine\b[^/]*?/>' "$tmpfile" 2>/dev/null && {
          local block
          block=$(rg -U --multiline-dotall -o '<ReferenceLine\b[^/]*?/>' "$tmpfile" 2>/dev/null || true)
          if echo "$block" | rg -q 'label\s*=\s*\{\{'; then
            if echo "$block" | rg -q 'fontSize|fill|fontFamily'; then
              if ! echo "$block" | rg -q '\.\.\.CHART_LABEL_STYLE'; then
                matched=1
              fi
            fi
          fi
        } || true
        ;;
      R10)
        rg -q '(bg|text|border)-\[var\(--' "$tmpfile" 2>/dev/null && matched=1 || true
        ;;
      R12)
        rg -q 'style\s*=\s*\{\{[^}]*fontSize\s*:' "$tmpfile" 2>/dev/null && matched=1 || true
        ;;
      R13)
        rg -q 'var\(--accent-(green|orange|red|amber|blue|purple)(-?(10|20))?\)' "$tmpfile" 2>/dev/null && matched=1 || true
        ;;
      R14)
        rg --pcre2 -q 'className\s*=\s*\{?["`'"'"'][^"`'"'"']*(?<![-a-zA-Z0-9_])(sc|cd|sl|fl|fi|fsel|ctbl|dtab|cd-h|cd-b|sc-l|sc-v|sc-sub|turn-row|turn-item|turn-label|turn-val|verdict|verdict-pass|verdict-warn|verdict-fail|factor-dot|factor-limit|data-avail|action-row|frow|fg|hbar|hbar-label|hbar-wrap|hbar-fill|hbar-val|explorer|config-panel|result-panel|acc-group|acc-head|acc-body|acc-item|param-section|param-row|param-label|param-val|param-input|param-unit|param-select|param-divider|cfg-section|cfg-title|hm-grid|hm-label|hm-cell|hm-tick|wf-row|wf-label|wf-bar-wrap|wf-bar|wf-val|rpt-head|rpt-back|rpt-title|rpt-sub|rpt-meta|rpt-meta-item|report-content|tab-bar|hist-clickable|hist-pager|empty-icon|empty-title|empty-desc|spinner)(?![-a-zA-Z0-9_])[^"`'"'"']*["`'"'"']' "$tmpfile" 2>/dev/null && matched=1 || true
        ;;
    esac
    rm -f "$tmpfile"
    if [[ $matched -eq 1 ]]; then
      echo "[PASS] assert_match $rule: $(printf '%q' "$content")"
      PASS=$((PASS + 1))
    else
      echo "[FAIL] assert_match $rule: SHOULD MATCH but did NOT: $(printf '%q' "$content")"
      FAIL=$((FAIL + 1))
    fi
  }

  # assert_no_match RULE CONTENT — must produce zero matches
  assert_no_match() {
    local rule="$1"
    local content="$2"
    local tmpfile
    tmpfile=$(mktemp /tmp/ds-selftest-XXXXXX.tsx)
    printf '%s\n' "$content" > "$tmpfile"
    local matched=0
    case "$rule" in
      R4)
        rg --pcre2 -q 'className\s*=\s*["\x27][^"\x27]*(?<![-a-zA-Z0-9_])(cg|ca|cr|ci|dim|mono)(?![-a-zA-Z0-9_])[^"\x27]*["\x27]' "$tmpfile" 2>/dev/null && matched=1 || true
        ;;
      R6)
        rg -U --multiline-dotall -q '<(Recharts)?Tooltip\b[^/]*?/>' "$tmpfile" 2>/dev/null && {
          if ! rg -q '\.\.\.CHART_TOOLTIP_PROPS' "$tmpfile" 2>/dev/null; then
            matched=1
          fi
        } || true
        ;;
      R7)
        rg -U --multiline-dotall -q '<CartesianGrid\b[^/]*?/>' "$tmpfile" 2>/dev/null && {
          if ! rg -q '\.\.\.CHART_GRID_STYLE' "$tmpfile" 2>/dev/null; then
            matched=1
          fi
        } || true
        ;;
      R8)
        rg -U --multiline-dotall -q '<Legend\b[^/]*?/>' "$tmpfile" 2>/dev/null && {
          local block8n
          block8n=$(rg -U --multiline-dotall -o '<Legend\b[^/]*?/>' "$tmpfile" 2>/dev/null || true)
          if echo "$block8n" | grep -q 'wrapperStyle\s*=\s*{{'; then
            if ! echo "$block8n" | grep -q 'CHART_LEGEND_STYLE'; then
              matched=1
            fi
          fi
        } || true
        ;;
      R9)
        rg -U --multiline-dotall -q '<ReferenceLine\b[^/]*?/>' "$tmpfile" 2>/dev/null && {
          local block
          block=$(rg -U --multiline-dotall -o '<ReferenceLine\b[^/]*?/>' "$tmpfile" 2>/dev/null || true)
          if echo "$block" | rg -q 'label\s*=\s*\{\{'; then
            if echo "$block" | rg -q 'fontSize|fill|fontFamily'; then
              if ! echo "$block" | rg -q '\.\.\.CHART_LABEL_STYLE'; then
                matched=1
              fi
            fi
          fi
        } || true
        ;;
      R9_MULTILINE)
        rg -U --multiline-dotall -q '<ReferenceLine\b[^/]*?/>' "$tmpfile" 2>/dev/null && {
          local block
          block=$(rg -U --multiline-dotall -o '<ReferenceLine\b[^/]*?/>' "$tmpfile" 2>/dev/null || true)
          if echo "$block" | rg -q 'label\s*=\s*\{\{'; then
            if echo "$block" | rg -q 'fontSize|fill|fontFamily'; then
              if ! echo "$block" | rg -q '\.\.\.CHART_LABEL_STYLE'; then
                matched=1
              fi
            fi
          fi
        } || true
        ;;
      R10)
        rg -q '(bg|text|border)-\[var\(--' "$tmpfile" 2>/dev/null && matched=1 || true
        ;;
      R12)
        rg -q 'style\s*=\s*\{\{[^}]*fontSize\s*:' "$tmpfile" 2>/dev/null && matched=1 || true
        ;;
      R13)
        rg -q 'var\(--accent-(green|orange|red|amber|blue|purple)(-?(10|20))?\)' "$tmpfile" 2>/dev/null && matched=1 || true
        ;;
      R14)
        rg --pcre2 -q 'className\s*=\s*\{?["`'"'"'][^"`'"'"']*(?<![-a-zA-Z0-9_])(sc|cd|sl|fl|fi|fsel|ctbl|dtab|cd-h|cd-b|sc-l|sc-v|sc-sub|turn-row|turn-item|turn-label|turn-val|verdict|verdict-pass|verdict-warn|verdict-fail|factor-dot|factor-limit|data-avail|action-row|frow|fg|hbar|hbar-label|hbar-wrap|hbar-fill|hbar-val|explorer|config-panel|result-panel|acc-group|acc-head|acc-body|acc-item|param-section|param-row|param-label|param-val|param-input|param-unit|param-select|param-divider|cfg-section|cfg-title|hm-grid|hm-label|hm-cell|hm-tick|wf-row|wf-label|wf-bar-wrap|wf-bar|wf-val|rpt-head|rpt-back|rpt-title|rpt-sub|rpt-meta|rpt-meta-item|report-content|tab-bar|hist-clickable|hist-pager|empty-icon|empty-title|empty-desc|spinner)(?![-a-zA-Z0-9_])[^"`'"'"']*["`'"'"']' "$tmpfile" 2>/dev/null && matched=1 || true
        ;;
    esac
    rm -f "$tmpfile"
    if [[ $matched -eq 0 ]]; then
      echo "[PASS] assert_no_match $rule: $(printf '%q' "$content")"
      PASS=$((PASS + 1))
    else
      echo "[FAIL] assert_no_match $rule: SHOULD NOT MATCH but DID: $(printf '%q' "$content")"
      FAIL=$((FAIL + 1))
    fi
  }

  # Special: assert_no_match for R10 in components/ui/ path context
  assert_no_match_r10_ui() {
    # R10 excludes src/components/ui/**; verify real glob exclusion
    # Write a temp tsx in a temp components/ui subfolder and confirm rg won't report it
    local tmpdir
    tmpdir=$(mktemp -d /tmp/ds-selftest-ui-XXXXXX)
    mkdir -p "$tmpdir/components/ui"
    printf 'className="bg-[var(--acc-d)]"\n' > "$tmpdir/components/ui/button.tsx"
    local count=0
    count=$(rg -c '(bg|text|border)-\[var\(--' "$tmpdir" \
              --glob '!**/components/ui/**' 2>/dev/null || echo "0")
    rm -rf "$tmpdir"
    if [[ "$count" == "0" ]] || [[ -z "$count" ]]; then
      echo "[PASS] assert_no_match R10: components/ui/** glob exclusion works"
      PASS=$((PASS + 1))
    else
      echo "[FAIL] assert_no_match R10: components/ui/** glob exclusion FAILED (found $count matches)"
      FAIL=$((FAIL + 1))
    fi
  }

  echo "--- R4 (single-letter tokens) ---"
  assert_match    R4 'className="cg"'
  assert_match    R4 'className="cr mono"'
  assert_match    R4 'className="dim"'
  assert_no_match R4 'className="font-mono"'
  assert_no_match R4 'className="bg-qds-success-dim"'
  assert_no_match R4 'className="text-qds-info-dim"'
  assert_no_match R4 'className="animate-qds-pulse"'
  assert_no_match R4 'className="dark:bg-transparent"'
  assert_no_match R4 'className="bg-qds-accent-dim text-primary"'

  echo ""
  echo "--- R6 (Tooltip spread) ---"
  assert_match    R6 '<Tooltip contentStyle={TOOLTIP_STYLE} />'
  assert_no_match R6 '<Tooltip {...CHART_TOOLTIP_PROPS} />'

  echo ""
  echo "--- R6 multiline ---"
  assert_match    R6_MULTILINE "$(printf '<RechartsTooltip\n  contentStyle={{ background: "var(--popover)" }}\n/>')"
  assert_no_match R6           '<RechartsTooltip {...CHART_TOOLTIP_PROPS} contentStyle={{background:"var(--popover)"}} />'

  echo ""
  echo "--- R7 (CartesianGrid spread) ---"
  assert_match    R7 '<CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" />'
  assert_no_match R7 '<CartesianGrid {...CHART_GRID_STYLE} />'
  assert_no_match R7 '<CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />'
  assert_no_match R7 '<CartesianGrid strokeDasharray="3 3" {...CHART_GRID_STYLE} />'
  assert_no_match R7 '<CartesianGrid {...CHART_GRID_STYLE} vertical={false} />'

  echo ""
  echo "--- R8 (Legend wrapperStyle, single-line) ---"
  assert_match    R8 '<Legend wrapperStyle={{ fontSize: ".62rem", fontFamily: "var(--font-d)" }} />'
  assert_match    R8 '<Legend wrapperStyle={{ fontSize: 10, color: "var(--t2)" }} />'
  assert_no_match R8 '<Legend wrapperStyle={CHART_LEGEND_STYLE} />'
  assert_no_match R8 '<Legend wrapperStyle={{ ...CHART_LEGEND_STYLE }} />'
  assert_no_match R8 '<Legend wrapperStyle={{ ...CHART_LEGEND_STYLE, fontSize: 10 }} />'
  assert_no_match R8 '<Legend iconSize={8} wrapperStyle={{ ...CHART_LEGEND_STYLE }} />'

  echo ""
  echo "--- R8 multiline ---"
  assert_match    R8_MULTILINE "$(printf '<Legend\n  iconType="circle"\n  iconSize={8}\n  wrapperStyle={{ fontSize: 10, fontFamily: "var(--font-mono)" }}\n/>')"
  assert_match    R8_MULTILINE "$(printf '<Legend\n  wrapperStyle={{ fontSize: 10, color: "var(--t2)" }}\n  formatter={(v) => v}\n/>')"
  assert_no_match R8_MULTILINE "$(printf '<Legend\n  iconSize={8}\n  wrapperStyle={CHART_LEGEND_STYLE}\n/>')"
  assert_no_match R8_MULTILINE "$(printf '<Legend\n  iconSize={8}\n  wrapperStyle={{ ...CHART_LEGEND_STYLE }}\n/>')"

  echo ""
  echo "--- R9 (ReferenceLine label, single-line) ---"
  assert_match    R9 '<ReferenceLine label={{ value: "x", fill: "var(--warn)", fontSize: 9 }} />'
  assert_no_match R9 '<ReferenceLine label={{ ...CHART_LABEL_STYLE, value: "x" }} />'

  echo ""
  echo "--- R9 multiline ---"
  assert_match    R9_MULTILINE "$(printf '<ReferenceLine\n  x={10}\n  stroke="var(--warn)"\n  label={{ value: "x", fontSize: 10 }}\n/>')"
  assert_no_match R9_MULTILINE "$(printf '<ReferenceLine\n  x={10}\n  label={{ ...CHART_LABEL_STYLE, value: "x" }}\n/>')"

  echo ""
  echo "--- R10 (arbitrary var tokens, ui/ exclusion) ---"
  assert_no_match_r10_ui

  echo ""
  echo "--- R12 (fontSize inline, Recharts transparent context) ---"
  assert_no_match R12 'wrapperStyle={{ fontSize: ".62rem" }}'
  assert_no_match R12 '<Tooltip labelStyle={{ fontSize: 11 }} />'

  echo ""
  echo "--- R13 (undefined --accent-* vars, 11 variants) ---"
  assert_match    R13 'text-[var(--accent-green)]'
  assert_match    R13 'text-[var(--accent-orange)]'
  assert_match    R13 'text-[var(--accent-red)]'
  assert_match    R13 'text-[var(--accent-amber)]'
  assert_match    R13 'bg-[var(--accent-blue)]'
  assert_match    R13 'border-[var(--accent-purple)]'
  assert_match    R13 'bg-[var(--accent-red-20)]'
  assert_match    R13 'bg-[var(--accent-green-10)]'
  assert_match    R13 'bg-[var(--accent-amber-20)]'
  assert_match    R13 'bg-[var(--accent-blue-20)]'
  assert_match    R13 'bg-[var(--accent-purple-20)]'
  assert_no_match R13 'text-[var(--accent-foreground)]'
  assert_no_match R13 'text-[var(--accent)]'

  echo ""
  echo "--- R14 (factor-research primitives, PCRE2 lookaround) ---"
  assert_match    R14 'className="sc"'
  assert_match    R14 'className="sc-l"'
  assert_match    R14 'className="cd"'
  assert_match    R14 'className="ctbl"'
  assert_match    R14 'className="fsel"'
  assert_match    R14 'className="verdict-pass"'
  assert_match    R14 'className="turn-val cr"'
  assert_match    R14 'className="rpt-title"'
  assert_match    R14 'className="sc-l inline-flex items-center"'
  assert_match    R14 'className="hm-grid"'
  assert_match    R14 'className="hm-label"'
  assert_match    R14 'className="hm-cell"'
  # Template string — write to file with backtick intact
  tmpf_tpl=$(mktemp /tmp/ds-selftest-XXXXXX.tsx)
  printf 'className={`sc-v ${stale.cls}`}\n' > "$tmpf_tpl"
  if rg --pcre2 -q 'className\s*=\s*\{?["`'"'"'][^"`'"'"']*(?<![-a-zA-Z0-9_])(sc|cd|sl|fl|fi|fsel|ctbl|dtab|cd-h|cd-b|sc-l|sc-v|sc-sub|turn-row|turn-item|turn-label|turn-val|verdict|verdict-pass|verdict-warn|verdict-fail|factor-dot|factor-limit|data-avail|action-row|frow|fg|hbar|hbar-label|hbar-wrap|hbar-fill|hbar-val|explorer|config-panel|result-panel|acc-group|acc-head|acc-body|acc-item|param-section|param-row|param-label|param-val|param-input|param-unit|param-select|param-divider|cfg-section|cfg-title|hm-grid|hm-label|hm-cell|hm-tick|wf-row|wf-label|wf-bar-wrap|wf-bar|wf-val|rpt-head|rpt-back|rpt-title|rpt-sub|rpt-meta|rpt-meta-item|report-content|tab-bar|hist-clickable|hist-pager|empty-icon|empty-title|empty-desc|spinner)(?![-a-zA-Z0-9_])[^"`'"'"']*["`'"'"']' "$tmpf_tpl" 2>/dev/null; then
    echo "[PASS] assert_match R14: template string backtick form"
    PASS=$((PASS + 1))
  else
    echo "[FAIL] assert_match R14: template string backtick SHOULD MATCH but did NOT"
    FAIL=$((FAIL + 1))
  fi
  rm -f "$tmpf_tpl"
  # Negative cases
  assert_no_match R14 'className="bg-card"'
  assert_no_match R14 'className="font-sans"'
  assert_no_match R14 'className="sc-column"'
  assert_no_match R14 'className="fg-primary"'
  assert_no_match R14 'className="fi-rocket"'
  assert_no_match R14 'className="cd-hover"'
  assert_no_match R14 'className="sl-indicator"'
  assert_no_match R14 'className="scroll"'
  assert_no_match R14 'className="cards"'

  echo ""
  echo "=== Selftest Summary ==="
  echo "Passed: $PASS  Failed: $FAIL"
  if [[ $FAIL -gt 0 ]]; then
    echo "SELFTEST FAILED"
    exit 2
  fi
  echo "SELFTEST PASSED"
  exit 0
fi

# ─── RULE IMPLEMENTATIONS ──────────────────────────────────────────────────────

# R1: fontFamily inline var(--font-u/d) in tsx/ts/js
scan_r1() {
  rg -n \
    --glob '**/*.{ts,tsx,jsx,js}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    --glob '!**/out/**' \
    'fontFamily:\s*["\x27]var\(--font-[ud]\)["\x27]' \
    "$SRC" 2>/dev/null | grep -v 'chartTheme\.ts' || true
}

# R2: legacy bt-* className usage in tsx/jsx
scan_r2() {
  rg --pcre2 -n \
    --glob '**/*.{tsx,jsx}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    'className\s*=\s*["\x27{][^"\x27}]*\bbt-[a-z0-9-]+\b' \
    "$SRC" 2>/dev/null || true
}

# R3: legacy dc-* className + dc-type-* dict values
scan_r3() {
  {
    rg --pcre2 -n \
      --glob '**/*.{tsx,jsx,ts}' \
      --glob '!**/node_modules/**' \
      --glob '!**/.next/**' \
      'className\s*=\s*["\x27{][^"\x27}]*\bdc-[a-z0-9-]+\b' \
      "$SRC" 2>/dev/null || true
    # Also catch dc-type-* string dict values outside className context (types.ts)
    rg -n \
      --glob '**/*.{tsx,jsx,ts}' \
      --glob '!**/node_modules/**' \
      --glob '!**/.next/**' \
      '["\x27]dc-type-[a-z]+["\x27]' \
      "$SRC" 2>/dev/null || true
  } | sort -u
}

# R4: single-letter legacy tokens (PCRE2 lookaround)
scan_r4() {
  rg --pcre2 -n \
    --glob '**/*.{tsx,jsx}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    'className\s*=\s*["\x27][^"\x27]*(?<![-a-zA-Z0-9_])(cg|ca|cr|ci|dim|mono)(?![-a-zA-Z0-9_])[^"\x27]*["\x27]' \
    "$SRC" 2>/dev/null || true
}

# R5: hardcoded hex colors
scan_r5() {
  rg -n \
    --glob '**/*.{tsx,jsx,ts}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    --glob '!**/globals.css' \
    '(bg|text|border)-\[#[0-9a-fA-F]{3,8}\]|color:\s*["\x27]#[0-9a-fA-F]{3,8}' \
    "$SRC" 2>/dev/null || true
}

# R6: Tooltip without CHART_TOOLTIP_PROPS (two-phase)
scan_r6() {
  local tmpfile
  tmpfile=$(mktemp /tmp/ds-r6-XXXXXX.txt)
  rg -U --multiline-dotall -n \
    --glob '**/*.{tsx,jsx}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    '<(Recharts)?Tooltip\b[^/]*?/>' \
    "$SRC" 2>/dev/null > "$tmpfile" || true
  # Filter: keep lines (blocks) that contain contentStyle but NOT ...CHART_TOOLTIP_PROPS
  grep 'contentStyle' "$tmpfile" | grep -v '\.\.\.CHART_TOOLTIP_PROPS' || true
  rm -f "$tmpfile"
}

# R7: CartesianGrid without CHART_GRID_STYLE (two-phase)
scan_r7() {
  local tmpfile
  tmpfile=$(mktemp /tmp/ds-r7-XXXXXX.txt)
  rg -U --multiline-dotall -n \
    --glob '**/*.{tsx,jsx}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    '<CartesianGrid\b[^/]*?/>' \
    "$SRC" 2>/dev/null > "$tmpfile" || true
  # Keep any CartesianGrid that does NOT include CHART_GRID_STYLE spread
  grep -v '\.\.\.CHART_GRID_STYLE' "$tmpfile" | grep -E '(stroke|strokeDasharray)\s*=' || true
  rm -f "$tmpfile"
}

# R8: Legend wrapperStyle without CHART_LEGEND_STYLE (two-phase multiline)
scan_r8() {
  local tmpfile
  tmpfile=$(mktemp /tmp/ds-r8-XXXXXX.txt)
  rg -U --multiline-dotall -n \
    --glob '**/*.{tsx,jsx}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    '<Legend\b[^/]*?/>' \
    "$SRC" 2>/dev/null > "$tmpfile" || true
  # Keep Legend blocks that have wrapperStyle={{ (inline object) but NOT CHART_LEGEND_STYLE
  grep 'wrapperStyle\s*=\s*{{' "$tmpfile" | grep -v 'CHART_LEGEND_STYLE' || true
  rm -f "$tmpfile"
}

# R9 using awk for reliable multiline block detection
scan_r9_v2() {
  local tmpfile
  tmpfile=$(mktemp /tmp/ds-r9v2-XXXXXX.txt)
  # Collect all <ReferenceLine.../> blocks with file:line prefix
  rg -U --multiline-dotall -n \
    --glob '**/*.{tsx,jsx}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    '<ReferenceLine\b[^/]*?/>' \
    "$SRC" 2>/dev/null > "$tmpfile" || true
  # Now use awk to identify lines that contain label={{ with fontSize/fill/fontFamily but no CHART_LABEL_STYLE
  awk '
    /label\s*=\s*\{\{/ && /fontSize|fill|fontFamily/ && !/CHART_LABEL_STYLE/ {
      print $0
    }
  ' "$tmpfile" || true
  rm -f "$tmpfile"
}

# R10: arbitrary var() tokens (excluding components/ui/)
scan_r10() {
  rg -n \
    --glob '**/*.{tsx,jsx}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    --glob '!**/components/ui/**' \
    '(bg|text|border)-\[var\(--' \
    "$SRC" 2>/dev/null || true
}

# R11: legacy CSS class definitions in globals.css
scan_r11() {
  rg --pcre2 -n \
    '\.bt-[a-z-]+\s*\{|\.dc-[a-z-]+\s*\{|(^|[;\}])\s*\.(cg|ca|cr|ci|dim|mono)\s*\{|\.(sc|cd|sl|fl|fi|fsel|ctbl|dtab|acc-[a-z]+|param-[a-z]+|cfg-[a-z]+|hm-[a-z]+|wf-[a-z]+|hbar(-[a-z]+)?|rpt-[a-z]+|turn-[a-z]+|verdict(-[a-z]+)?|factor-dot|factor-limit|data-avail|action-row|spinner|tip|badge|frow|fg)\s*\{' \
    "$SRC/app/globals.css" 2>/dev/null || true
}

# R12: inline fontSize style prop (excluding Recharts transparent contexts)
scan_r12() {
  rg -n \
    --glob '**/*.{tsx,jsx}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    'style\s*=\s*\{\{[^}]*fontSize\s*:' \
    "$SRC" 2>/dev/null | \
  grep -v 'chartTheme\.ts\|wrapperStyle\|contentStyle\|labelStyle\|CHART_LEGEND_STYLE\|CHART_LABEL_STYLE\|CHART_TOOLTIP_STYLE\|CHART_TOOLTIP_PROPS\|tick=' || true
}

# R13: undefined --accent-* CSS variable usage (11 variants)
scan_r13() {
  rg -n \
    --glob '**/*.{tsx,jsx,ts,js,css}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    'var\(--accent-(green|orange|red|amber|blue|purple)(-?(10|20))?\)' \
    "$SRC" 2>/dev/null | grep -v 'globals\.css' || true
}

# R14: factor-research primitive classes (PCRE2 lookaround)
# Pattern covers single/double quotes and backtick template strings
R14_TOKENS='sc|cd|sl|fl|fi|fsel|ctbl|dtab|cd-h|cd-b|sc-l|sc-v|sc-sub|turn-row|turn-item|turn-label|turn-val|verdict|verdict-pass|verdict-warn|verdict-fail|factor-dot|factor-limit|data-avail|action-row|frow|fg|hbar|hbar-label|hbar-wrap|hbar-fill|hbar-val|explorer|config-panel|result-panel|acc-group|acc-head|acc-body|acc-item|param-section|param-row|param-label|param-val|param-input|param-unit|param-select|param-divider|cfg-section|cfg-title|hm-grid|hm-label|hm-cell|hm-tick|wf-row|wf-label|wf-bar-wrap|wf-bar|wf-val|rpt-head|rpt-back|rpt-title|rpt-sub|rpt-meta|rpt-meta-item|report-content|tab-bar|hist-clickable|hist-pager|empty-icon|empty-title|empty-desc|spinner'

scan_r14() {
  rg --pcre2 -n \
    --glob '**/*.{tsx,jsx}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    "className\s*=\s*\{?[\"'\`][^\"'\`]*(?<![-a-zA-Z0-9_])(${R14_TOKENS})(?![-a-zA-Z0-9_])[^\"'\`]*[\"'\`]" \
    "$SRC" 2>/dev/null || true
}

# ─── FIX HINTS ─────────────────────────────────────────────────────────────────
HINT_R1="Remove style={{ fontFamily: 'var(--font-d)' }} → add font-mono className; var(--font-u) → font-sans"
HINT_R2="bt-row→Tailwind grid; bt-cd/bt-cd-header/bt-cd-body→<Card>/<CardHeader>/<CardContent>; bt-status→<StatusBadge>; bt-kpi-*→<StatCard>"
HINT_R3="dc-filter-*→Tailwind flex + data-[active]; dc-dtbl→<Table>; dc-type-*→<Badge> bg-qds-{color}-dim; dc-cov-*→Tailwind h-1"
HINT_R4="cg→text-qds-success; cr→text-destructive; ca→text-primary; ci→text-qds-info; dim→text-muted-foreground; mono→font-mono"
HINT_R5="Replace hardcoded hex with QDS token class: #xxxx → use text-foreground/text-primary/text-qds-success etc."
HINT_R6="<Tooltip {...CHART_TOOLTIP_PROPS} /> — spread CHART_TOOLTIP_PROPS, remove local contentStyle"
HINT_R7="<CartesianGrid {...CHART_GRID_STYLE} /> — spread CHART_GRID_STYLE (extra props allowed)"
HINT_R8="<Legend wrapperStyle={CHART_LEGEND_STYLE} /> or wrapperStyle={{ ...CHART_LEGEND_STYLE }}"
HINT_R9="<ReferenceLine label={{ ...CHART_LABEL_STYLE, value: '...', fill: 'var(--warn)' }} />"
HINT_R10="Replace bg-[var(--xxx)] with Tailwind semantic class: bg-card, bg-background, text-primary, etc."
HINT_R11="Remove legacy CSS class definition from globals.css (run after all call sites migrated)"
HINT_R12="Remove inline fontSize style → use Tailwind text-[0.7rem] or text-sm etc."
HINT_R13="Replace var(--accent-green)→text-qds-success; --accent-red→text-destructive; --accent-amber→text-qds-warning; --accent-blue→text-qds-info; --accent-orange→text-primary; --accent-purple→text-primary"
HINT_R14=".sc/.sc-l/.sc-v→<StatCard>/<SectionLabel>; .cd/.cd-h/.cd-b→<Card>/<CardHeader>/<CardContent>; .sl→<SectionLabel>; .fsel→<Select>; .ctbl→<Table>; .acc-*→<Accordion>; .param-*→<ParamRow>; .hm-*→Tailwind grid+CSS; .spinner→Lucide spinner"

# ─── BOTH-THEMES MODE ──────────────────────────────────────────────────────────
if [[ "$MODE" == "both-themes" ]]; then
  echo "=== DS Compliance — --mode both-themes ==="
  echo "Scanning: R1, R5, R10, R13 (theme-discipline subset, excluding components/ui/ + components/qds/)"
  echo ""

  BOTH_THEMES_SRC="$SRC"

  run_rule "R1-font-inline" "fontFamily inline var(--font-u/d)" "$HINT_R1" \
    rg -n \
      --glob '**/*.{ts,tsx,jsx,js}' \
      --glob '!**/node_modules/**' \
      --glob '!**/.next/**' \
      --glob '!src/lib/chartTheme.ts' \
      --glob '!**/components/ui/**' \
      --glob '!**/components/qds/**' \
      'fontFamily:\s*["\x27]var\(--font-[ud]\)["\x27]' \
      "$BOTH_THEMES_SRC"

  run_rule "R5-hardcoded-hex" "Hardcoded hex color" "$HINT_R5" \
    rg -n \
      --glob '**/*.{tsx,jsx,ts}' \
      --glob '!**/node_modules/**' \
      --glob '!**/.next/**' \
      --glob '!**/globals.css' \
      --glob '!**/components/ui/**' \
      --glob '!**/components/qds/**' \
      '(bg|text|border)-\[#[0-9a-fA-F]{3,8}\]|color:\s*["\x27]#[0-9a-fA-F]{3,8}' \
      "$BOTH_THEMES_SRC"

  run_rule "R10-arbitrary-token" "Arbitrary var() token (excluding ui/)" "$HINT_R10" \
    rg -n \
      --glob '**/*.{tsx,jsx}' \
      --glob '!**/node_modules/**' \
      --glob '!**/.next/**' \
      --glob '!**/components/ui/**' \
      --glob '!**/components/qds/**' \
      '(bg|text|border)-\[var\(--' \
      "$BOTH_THEMES_SRC"

  run_rule "R13-undefined-var" "Undefined --accent-* CSS variable" "$HINT_R13" \
    rg -n \
      --glob '**/*.{tsx,jsx,ts,js,css}' \
      --glob '!**/node_modules/**' \
      --glob '!**/.next/**' \
      --glob '!src/app/globals.css' \
      --glob '!**/components/ui/**' \
      --glob '!**/components/qds/**' \
      'var\(--accent-(green|orange|red|amber|blue|purple)(-?(10|20))?\)' \
      "$BOTH_THEMES_SRC"

  # Also check dark: prefix in business code (excluding ui/ and qds/)
  dark_violations=$(rg -n \
    --glob '**/*.{tsx,jsx}' \
    --glob '!**/node_modules/**' \
    --glob '!**/.next/**' \
    --glob '!**/components/ui/**' \
    --glob '!**/components/qds/**' \
    '\bdark:[a-z]' \
    "$BOTH_THEMES_SRC" 2>/dev/null || true)
  if [[ -n "$dark_violations" ]]; then
    while IFS= read -r line; do
      echo "[R-DARK-PREFIX] $line"
      TOTAL_VIOLATIONS=$((TOTAL_VIOLATIONS + 1))
    done <<< "$dark_violations"
  fi

  NFILES=${#VIOLATED_FILES[@]}
  echo ""
  echo "Total violations: $TOTAL_VIOLATIONS across $NFILES files"
  [[ $TOTAL_VIOLATIONS -eq 0 ]] && exit 0 || exit 1
fi

# ─── PREFLIGHT MODE ────────────────────────────────────────────────────────────
if [[ "$MODE" == "preflight" ]]; then
  echo "=== DS Compliance — --preflight-before-css-delete ==="
  echo "Running R1-R14 (excluding R11). ALL must be zero before s10 can delete globals.css legacy definitions."
  echo ""

  # Run each rule and collect violations
  _run_preflight_rule() {
    local rule_id="$1"
    local hint="$2"
    shift 2
    local out
    out=$("$@" 2>/dev/null || true)
    local count=0
    if [[ -n "$out" ]]; then
      while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        echo "[$rule_id] $line"
        count=$((count + 1))
      done <<< "$out"
    fi
    echo "  → $rule_id: $count violation(s)"
    TOTAL_VIOLATIONS=$((TOTAL_VIOLATIONS + count))
  }

  _run_preflight_rule "R1"  "$HINT_R1"  scan_r1
  _run_preflight_rule "R2"  "$HINT_R2"  scan_r2
  _run_preflight_rule "R3"  "$HINT_R3"  scan_r3
  _run_preflight_rule "R4"  "$HINT_R4"  scan_r4
  _run_preflight_rule "R5"  "$HINT_R5"  scan_r5
  _run_preflight_rule "R6"  "$HINT_R6"  scan_r6
  _run_preflight_rule "R7"  "$HINT_R7"  scan_r7
  _run_preflight_rule "R8"  "$HINT_R8"  scan_r8
  _run_preflight_rule "R9"  "$HINT_R9"  scan_r9_v2
  _run_preflight_rule "R10" "$HINT_R10" scan_r10
  # R11 intentionally skipped (scans globals.css itself — not a call-site check)
  _run_preflight_rule "R12" "$HINT_R12" scan_r12
  _run_preflight_rule "R13" "$HINT_R13" scan_r13
  _run_preflight_rule "R14" "$HINT_R14" scan_r14

  echo ""
  if [[ $TOTAL_VIOLATIONS -eq 0 ]]; then
    echo "PREFLIGHT PASSED — All R1-R14 call sites are clean. Safe to proceed with s10 globals.css cleanup."
    exit 0
  else
    echo "PREFLIGHT FAILED — $TOTAL_VIOLATIONS violation(s) remain. DO NOT delete globals.css legacy definitions yet."
    echo ""
    echo "Preflight failure → rollback target mapping:"
    echo "  R2 violations → s4 (backtest) or s5 (data-catalog) not complete"
    echo "  R3 violations → s5 (data-catalog) not complete"
    echo "  R4 violations → s4/s5/s6/s7/s8/s9 single-letter token cleanup pending"
    echo "  R14 violations → s4 (backtest tabs) or s5/s6 (factor-research) not complete"
    echo "  R1/R12 violations → fontFamily/fontSize inline still present in tsx files"
    echo "  R6/R7/R8/R9 violations → Recharts constant spread not complete"
    echo "  R10 violations → arbitrary var() tokens remain in business code"
    echo "  R13 violations → undefined --accent-* vars not migrated (s7/s9)"
    exit 1
  fi
fi

# ─── DEFAULT MODE: R1-R14 FULL SCAN ───────────────────────────────────────────
echo "=== DS Compliance Scan — R1-R14 ==="
echo "Source root: $SRC"
echo ""

# Each run_rule call: RULE_ID description hint <scan_function>
# We call scan functions and pipe output through run_rule

_exec_rule() {
  local rule_id="$1"
  local description="$2"
  local hint="$3"
  local scan_fn="$4"

  local output
  output=$($scan_fn 2>/dev/null || true)

  if [[ -z "$output" ]]; then
    echo "[$rule_id] ✓ no violations"
    return 0
  fi

  local count=0
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    echo "[$rule_id] $line"
    if [[ $FIX_HINT -eq 1 ]] && [[ -n "$hint" ]]; then
      echo "  → Migrate: $hint"
    fi
    count=$((count + 1))
    local fpath
    fpath=$(echo "$line" | cut -d: -f1)
    local already=0
    for f in "${VIOLATED_FILES[@]+"${VIOLATED_FILES[@]}"}"; do
      [[ "$f" == "$fpath" ]] && already=1 && break
    done
    [[ $already -eq 0 ]] && VIOLATED_FILES+=("$fpath")
  done <<< "$output"
  TOTAL_VIOLATIONS=$((TOTAL_VIOLATIONS + count))
}

_exec_rule "R1-font-inline"              "fontFamily inline var(--font-u/d)"         "$HINT_R1"  scan_r1
_exec_rule "R2-legacy-class-bt"          "legacy bt-* className"                     "$HINT_R2"  scan_r2
_exec_rule "R3-legacy-class-dc"          "legacy dc-* className + dc-type-* dict"    "$HINT_R3"  scan_r3
_exec_rule "R4-legacy-class-single"      "standalone cg/ca/cr/ci/dim/mono tokens"    "$HINT_R4"  scan_r4
_exec_rule "R5-hardcoded-hex"            "hardcoded hex colors"                       "$HINT_R5"  scan_r5
_exec_rule "R6-tooltip-spread"           "Tooltip without CHART_TOOLTIP_PROPS"        "$HINT_R6"  scan_r6
_exec_rule "R7-grid-spread"              "CartesianGrid without CHART_GRID_STYLE"     "$HINT_R7"  scan_r7
_exec_rule "R8-legend-spread"            "Legend wrapperStyle without CHART_LEGEND_STYLE" "$HINT_R8" scan_r8
_exec_rule "R9-reference-line-label"     "ReferenceLine inline label without CHART_LABEL_STYLE" "$HINT_R9" scan_r9_v2
_exec_rule "R10-arbitrary-token"         "arbitrary var() token (excluding ui/)"     "$HINT_R10" scan_r10
_exec_rule "R11-globals-legacy"          "legacy CSS class in globals.css"            "$HINT_R11" scan_r11
_exec_rule "R12-fontsize-inline"         "inline fontSize style prop"                 "$HINT_R12" scan_r12
_exec_rule "R13-undefined-var"           "undefined --accent-* CSS variables"         "$HINT_R13" scan_r13
_exec_rule "R14-factor-research-primitive" "factor-research primitive classes"        "$HINT_R14" scan_r14

echo ""
NFILES=${#VIOLATED_FILES[@]}
echo "Total violations: $TOTAL_VIOLATIONS across $NFILES files"

if [[ $TOTAL_VIOLATIONS -eq 0 ]]; then
  echo "All rules passed."
  exit 0
fi
exit 1
