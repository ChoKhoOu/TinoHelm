# ADR 0004: CLI removes generic API caller; typed subcommands are the only interface

- **Status:** Accepted (2026-05-17)
- **Related PRD:** Issue #198 — "CLI 重构：删除 tino api，全面 typed subcommand 覆盖"

## Context

The CLI was designed with an "API coverage rule": `tino api call METHOD /path`
was the primary interface, and typed subcommands (`tino backtest run`, etc.)
were documented as "convenience wrappers." This philosophy made `tino api call`
a first-class citizen and the typed commands second-class.

In practice this caused three problems:

1. **LLM agents prefer the generic caller.** Because documentation and
   CLAUDE.md told agents to "use `tino api call` first", they consistently
   constructed raw HTTP paths instead of using the discoverable, validated
   typed commands. This bypasses flag validation, produces worse error
   messages, and makes agent prompts fragile to API path changes.

2. **Capability discovery is broken.** Users (human or machine) cannot learn
   what the CLI can do from `--help` alone — some operations only exist as
   raw API paths documented nowhere in the CLI. The generic caller is an
   escape hatch that became a crutch.

3. **Three output formats are excessive.** The `-f llm` envelope
   (`{ok, data, error, meta}`) adds maintenance cost for marginal value.
   Standard Unix conventions (exit code + stderr for errors, stdout for
   data) already communicate success/failure to any caller.

## Decision

1. **Delete `tino api` entirely** — `call`, `get`, `post`, `download`, and
   `routes` subcommands are all removed. No escape hatch; if an operation
   has no typed subcommand, it is not supported from CLI.

2. **Default output is JSON.** Every command writes structured JSON to stdout
   by default. `-f text` switches to human-friendly tables/formatting.

3. **Delete `-f llm`.** Only two output formats remain: `json` (default) and
   `text`. Errors are JSON on stderr with non-zero exit code.

4. **Every API endpoint gets a typed subcommand.** Full coverage is a
   hard requirement, not a nice-to-have. Missing coverage is treated as a
   bug, not a "use `tino api call` instead" situation.

5. **Unified render module.** A single `Renderer` abstraction handles both
   formats for all commands, so adding text support is mechanical.

## Alternatives Considered

### Keep `tino api` as a hidden escape hatch

Tempting for the gap between "backend adds endpoint" and "CLI ships wrapper."
Rejected because: (a) it re-enables the exact behavior we're eliminating —
agents will find and prefer the generic caller; (b) it reduces pressure to
keep CLI coverage complete; (c) the gap is a development-process problem
(ship CLI and backend together), not a product design problem.

### Keep `-f llm` for structured error envelopes

The envelope's `ok` field duplicates exit code semantics, and `meta` (elapsed
time, request ID) can be added to JSON output directly if needed later.
The three-format matrix triples formatter maintenance for every new command.
Rejected.

### Default to text, require `-f json` for machine use

Normal for human-first CLIs. Rejected because: (a) the primary consumer of
this CLI is LLM agents, which parse JSON natively; (b) defaulting to JSON
means scripts and agents work with zero flags; (c) humans opt in to text
with one flag, which is a smaller population paying a smaller cost.

## Consequences

- All scripts and agent prompts using `tino api call ...` will break and
  must be migrated to the equivalent typed subcommand.
- CLAUDE.md and cli/README.md must be rewritten to remove the "API coverage
  rule" philosophy and document the new "typed subcommand only" convention.
- Any new backend endpoint must ship with a corresponding CLI subcommand
  before it is considered complete (definition of done includes CLI coverage).
- The internal `ApiClient` module (typed HTTP helpers) remains unchanged —
  only the public-facing generic caller is removed.
