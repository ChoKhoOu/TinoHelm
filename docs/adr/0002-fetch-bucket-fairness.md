# ADR 0002: FetchBucket fairness is encoded in the scheduler's ORDER BY

- **Status:** Accepted (2026-05-11)
- **Related PRD:** Issue #162 — "make data fetch scheduling fair and high-throughput"
- **Implementing slice:** Issue #165 — "在单个 FetchBatch 内实现 FetchBucket 公平调度"
- **Builds on:** ADR 0001 (#164) — DB-driven scheduler, Redis = wake signal
- **Unblocks:** Issue #166 — cross-batch soft FIFO, legacy backlog migration

## Context

After #164 the scheduler picks the next `DataFetchJob` with a single guarded
UPDATE whose target is a subquery `ORDER BY created_at, id LIMIT 1`. That
ordering gives global FIFO but produces two painful shapes under real
workload (see #162 user stories #2 and #3):

1. **Bucket monopoly inside one batch.** A multi-symbol `fetch-batch` submission
   fans into hundreds of sibling jobs; because catalog writes are serialized
   per `(symbol, data_type, interval)` bucket and the scheduler knows nothing
   about bucket activity, one hot symbol's queue of daily `aggTrades` jobs can
   hold every worker slot while other symbols' jobs sit queued.
2. **Fast retries starve siblings.** A bucket whose pipeline fails quickly
   (bad remote window, checksum mismatch) will simply get picked again next
   tick, because the ORDER BY doesn't distinguish a never-started bucket from
   one that's already chewed through several jobs.

#165 requires that inside one FetchBatch, scheduling be "least-started bucket
first", where bucket progress is `running + completed + failed + cancelled`
(PRD #162 implementation decision). Per-bucket chronological order must be
preserved (catalog consistency boundary). ADR 0001 notes for future slices
explicitly recommend implementing this by replacing the `ORDER BY` in
`claim_next_queued_job` — nothing else in the worker needs to change.

## Decision

Fairness lives in a **single SQL statement**, not in Python scheduling code.
`claim_next_queued_job` keeps its atomic `UPDATE ... WHERE status='queued'`
shape; only the subquery that picks the target job_id is specialised. The
subquery is built in `_next_queued_job_id_subquery()` and orders rows by:

1. **`batch_created_at` ASC** — per batch, derived as
   `MIN(created_at) OVER (PARTITION BY batch_id)`. Keeps cross-batch FIFO
   intact (older batches get picked first). Jobs with `batch_id IS NULL`
   (pre-#163 legacy) are coalesced to `job_id` so each becomes its own batch
   and still participates in FIFO by their own `created_at`.
2. **`bucket_started_count` ASC** — per FetchBucket inside the batch,
   `SUM(CAST(status IN ('running','completed','failed','cancelled') AS INT))`.
   This is "least-progressed bucket first" exactly as worded in the PRD.
3. **`symbol`, `data_type`, `interval` ASC** — deterministic bucket
   tiebreaker, stable across reboots and independent of insertion order.
4. **`start_date` ASC** — inside a bucket, earliest window first. This is the
   ingest-side chronological rule that existing catalog consistency code
   assumes.
5. **`created_at`, `id` ASC** — final tiebreaker for identical bucket keys,
   preserves the deterministic ordering intent from ADR 0001.

The guarded UPDATE is unchanged; concurrency semantics (at most one
`rowcount=1` winner) remain from #164.

## Why these tradeoffs

- **Single SQL vs Python picker.** ADR 0001 anticipated "specialise ORDER BY"
  as the extension point. Doing fairness in one statement keeps each claim at
  one round-trip, avoids having to read the full queued set into Python, and
  leaves `drain_once`/lock-busy/cancel paths untouched. The cost is a larger
  ORDER BY expression and a subquery-per-claim; the fairness sort is on a
  narrow predicate (`status='queued'` rows) in a small table, so the query
  plan is cheap enough — the table stays in the hundreds to low thousands of
  rows even during large backfills.
- **`SUM(CAST(... IN (...) AS INTEGER))` for started_count.** Portable across
  SQLite (tests) and Postgres (prod). No dialect-specific `FILTER` clause, no
  window functions needed. `CAST(bool AS INTEGER)` renders correctly on both
  backends (verified at compile time).
- **`COALESCE(batch_id, job_id)`.** #163 landed `batch_id` nullable so
  pre-existing rows stayed valid. Coalescing lets legacy rows act as
  single-job batches without a separate code path; this matches the PRD's
  "treat standalone fetches as single-job FetchBatch" intent.
- **Include terminal states in started_count.** Per PRD #162 implementation
  decision and #165 AC: a failing bucket must not re-jump the queue. Only
  counting `running` would make fast-failing buckets starve siblings.
- **No new scheduler metadata table.** PRD #162 explicitly rejects one; the
  fairness signal is derivable from `data_fetch_jobs` itself.

## Consequences

- **Same-bucket catalog serialization is still load-bearing.** Fair picking
  + serial catalog writes per bucket are the two halves of "parallel across
  buckets, safe inside one bucket". Neither may relax alone.
- **`drain_once` stays round-robin by accident.** After a claim flips a row
  to `running`, its bucket's `started_count` goes up by 1, so the next
  `claim_next_queued_job` naturally picks another bucket. No per-consumer
  "visited buckets" bookkeeping is needed.
- **Best-effort batch semantics hold.** A failing job increments
  `started_count` and becomes invisible to `status='queued'` filters — it
  neither blocks nor preempts siblings. `drain_once` already logs-and-continues
  per #164.
- **Cross-batch soft FIFO is unchanged.** Newer batches remain strictly
  behind older batches. #166 will relax this into soft FIFO (idle worker
  may fill from newer batches when the older batch cannot saturate all
  concurrency); the extension point is another `ORDER BY` tweak, same as
  this ADR.
- **Legacy backlog (#163 predecessors) still participates.** Rows with NULL
  `batch_id` are scheduled as isolated batches; #166's migration step will
  rebucket them without changing this ADR.

## Notes for future slices

- #166 (soft FIFO + legacy backlog migration) should plug into the existing
  `batch_created_at` ordering: "soft" means "if the oldest batch can't
  saturate worker concurrency for this tick, let a younger batch fill the
  slot." Mechanically that is an additional correlated subquery on
  `batch_created_at` — same SQL shape, one more ORDER BY term.
- If `data_fetch_jobs` ever grows into the millions of rows, the group-by
  subqueries may want a partial index on `(status, batch_id, symbol,
  data_type, interval)`. Not needed at current scale.
