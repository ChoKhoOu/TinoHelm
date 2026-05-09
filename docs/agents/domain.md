# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout

This repo is **single-context**. Skills should look for:

- **`CONTEXT.md`** at the repo root — the domain glossary and high-level model.
- **`docs/adr/`** — Architecture Decision Records for this repo.

Neither exists yet as of setup. Skills must **proceed silently** when they are missing — do not flag their absence, do not suggest creating them upfront. `/grill-with-docs` creates and extends them lazily as terms and decisions actually get resolved.

## Before exploring, read these

When working on a non-trivial task (refactor, new feature, debugging a subsystem), read `CONTEXT.md` and any ADRs under `docs/adr/` that touch the area you're about to work in, if they exist.

## File structure

```
/
├── CONTEXT.md                ← created lazily
├── docs/
│   ├── adr/                  ← created lazily
│   │   ├── 0001-....md
│   │   └── 0002-....md
│   └── agents/               ← this directory (skill configuration)
└── src/
```

Note: this repo already has extensive Claude-facing guidance in `CLAUDE.md` and sibling files in `docs/` (`factor.md`, `signal.md`, `nt_adapter.md`, `aligner.md`, `evaluation.md`, `pitfalls-nt-port.md`, `tui-redesign.md`, `guide/nautilustrader_complete_guide.md`). These are **not** the domain glossary — they are command references, subsystem notes, and external-API cheat sheets. `CONTEXT.md` is still the place for the project's own domain vocabulary when it gets written.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md` once it exists. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/grill-with-docs`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_
