<!--
Reviewer's quick orientation
- TinoHelm wraps NautilusTrader; never re-implements its primitives.
- Each strategy = one Compose pod; pods communicate via Redis Streams.
- Discord notifier = one pod, watches Redis, fires slash commands back.
-->

## Summary
<!-- 1-3 bullet points: what changed and why -->

## Design intent
<!-- What NT primitive does this layer over? Why is the new code necessary
     vs. just calling NT directly? Link to source files or RELEASES.md sections
     when the answer is "NT v1.226 doesn't yet support X" — that justification
     should outlive this PR. -->

## Test plan
- [ ] `uv run pytest -q` — all tests green
- [ ] `uv run ruff check tinohelm tests`
- [ ] If touching the strategy pod: `make sandbox STRATEGY=example` boots cleanly
- [ ] If touching the notifier: a Discord channel receives at least one
      embed when running against a live (or sandboxed) strategy pod
- [ ] Updated relevant docstrings / README

## Risk & rollback
<!-- One sentence on blast radius, one sentence on how to revert. -->

## Out of scope
<!-- Anything reviewers might expect but isn't here, with rationale. -->
