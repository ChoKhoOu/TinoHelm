# ADR 0003: Soft FIFO across FetchBatches + one-shot legacy backlog migration

- **Status:** Accepted (2026-05-11)
- **Related PRD:** Issue #162 — "make data fetch scheduling fair and high-throughput"
- **Implementing slice:** Issue #166 — "实现跨 FetchBatch 软 FIFO 并接管旧 backlog"
- **Builds on:** ADR 0001 (#164) DB-driven scheduler, ADR 0002 (#165) FetchBucket
  fairness via `ORDER BY`
- **Unblocks:** none — this is the terminal slice of PRD #162.

## Context

ADR 0002 landed per-FetchBucket fairness inside one batch. Cross-batch
ordering was still **strict FIFO**: an older batch with any queued row
blocked every newer batch. Two production shapes make strict FIFO
operationally bad:

1. **Older batch saturated by catalog lock.** The remaining queued rows of
   the older batch all sit in buckets whose running sibling still holds
   the catalog lock. Under strict FIFO the worker claims one of those
   queued rows, discovers the lock is busy, and `_defer_locked_queued_job`
   just sleeps — a newer batch's completely untouched bucket would have
   made real progress in the same tick.
2. **One-off backtest fetch waits for a long backfill.** A year-scale
   `fetch-batch` submission still has thousands of queued rows in buckets
   that are all active; meanwhile a backtest-triggered single-job
   FetchBatch sits right behind them. User-visible latency becomes
   unbounded on a machine with idle worker capacity.

The PRD requires **soft FIFO**: older batches keep priority *when they can
still occupy a worker slot*. When they cannot, a newer batch's idle bucket
should fill the slot.

Additionally, pre-#163 rows carry `batch_id IS NULL`. Under ADR 0002 they
participate via `COALESCE(batch_id, job_id)` — but that collapses every
legacy row into its own single-job batch, so a multi-symbol submission
made before #163 no longer scheduled like one FetchBatch. The PRD
(decisions #23–#24) resolves this by rule-based backfill at rollout.

## Decision

Two changes land together; neither is useful without the other.

### 1. Soft FIFO — one extra `ORDER BY` term

`_next_queued_job_id_subquery` keeps its shape (ADR 0002) and prepends a
**single new ordering key** before `batch_created_at`:

```
ORDER BY
    bucket_running_count > 0 ASC,   -- NEW: idle buckets globally first
    batch_created_at        ASC,    -- existing: cross-batch FIFO
    bucket_started_count    ASC,    -- existing: #165 fairness
    symbol, data_type, interval, start_date, created_at, id
```

`bucket_running_count` is computed in the same `bucket_started_subq` as
`started_count`, via `SUM(CAST(status = 'running' AS INTEGER))`. The
boolean expression `bucket_running_count > 0` renders portably on SQLite
(tests) and Postgres (prod) — same portability contract as ADR 0002.

The semantics of the new term:

- `0` — bucket is idle (no in-flight work); claiming it will make real
  progress this tick.
- `1` — bucket already has a running job; claiming its queued row will
  just defer on the catalog lock.

Sorting `ASC` on that boolean makes **any idle bucket anywhere in the
queue beat any non-idle bucket from the oldest batch**. When both classes
have candidates, `batch_created_at` resolves within the class — so strict
FIFO still holds among idle buckets (older wins) and among non-idle
buckets (older wins). That's the soft part of soft FIFO: the relaxation
kicks in *only* when older can't progress.

### 2. One-shot legacy backlog migration — `backfill_legacy_batch_ids`

`recover_interrupted_jobs` calls a new coroutine
`backfill_legacy_batch_ids(factory)` after `_flip_running_to_queued` but
**before** clearing the legacy Redis list. The coroutine:

1. Selects every `DataFetchJob` with `batch_id IS NULL`, ordered by
   `(created_at, id)` for determinism.
2. Groups rows that share `created_at` exactly. Because
   `DataFetchJob.created_at` comes from `server_default=func.now()` and
   every fetch-batch submission commits under one transaction's `now()`,
   shared `created_at` is the tightest rule-based proxy for "same
   submission" that does not require reading historical API logs.
3. Allocates one fresh `uuid4()` per group and UPDATEs every row in that
   group. Rows whose `created_at` is unique still get a batch_id —
   they become single-job FetchBatches, mirroring how the new scheduler
   already treats backtest-triggered standalone fetches.
4. Returns the number of rows touched so recovery can log coverage.

Running it a second time finds nothing to touch (the filter is `batch_id
IS NULL`), so rollout is idempotent.

Ordering the call *before* `rds.delete(QUEUE_KEY)` means a crash mid-
backfill leaves the old wake token on the Redis list — the next startup
retries backfill and re-asserts scheduler ownership instead of silently
losing both sides.

## Why these tradeoffs

- **Boolean prefix vs reshuffling `batch_created_at`.** The alternative —
  making `batch_created_at` itself depend on "can this batch saturate" —
  couples two unrelated notions and is hard to explain. A leading boolean
  is one extra SQL expression, self-documenting, and composes cleanly
  with every term below it.
- **Per-claim "saturate" detection vs worker-concurrency awareness.** The
  PRD says "soft priority". One honest reading is global: *if the older
  batch cannot occupy all `N` worker slots at once, let newer fill the
  rest*. That requires worker-count awareness in SQL — brittle in tests
  and irrelevant to the actual pathology (catalog-lock deferral). The
  pathology is **per-bucket**: a queued row is useless right now if its
  bucket is running. So we check per-bucket, per-claim — cheap, local,
  and exactly what unblocks the worker.
- **`created_at` exact match vs time window.** A window (e.g. "rows
  within 2 seconds group together") would absorb clock skew, but the
  source of `created_at` is Postgres `func.now()` fixed per transaction,
  so within one submission every row is identical by construction. A
  window would also silently merge unrelated concurrent submissions that
  happen to arrive 1.9s apart. Exact match is both tighter and more
  predictable — we accept that two truly distinct submissions arriving
  in the same database tick will collapse into one migrated FetchBatch
  (rare, harmless — their per-bucket fairness is unchanged).
- **Backfill in `recover_interrupted_jobs` vs standalone migration
  script.** Rollout semantics are "scheduling truth now lives in the DB";
  the DB-driven scheduler is started by the API lifespan right after
  `recover_interrupted_jobs` returns. Bundling the backfill with recovery
  guarantees the first scheduler tick sees a fully migrated table. A
  separate script would require operator discipline — not worth the cost
  when the operation is idempotent and cheap.
- **No new migration version.** #163 already added the nullable
  `batch_id` column. #166 is a data-only fill, not a schema change, so
  it lives in application code where retries and logging are trivial.

## Consequences

- **Cross-batch FIFO is now soft, per-tick, per-bucket.** An operator
  inspecting the queue may see a newer batch's row get claimed while
  older queued rows remain — this is by design when the older batch's
  remaining work all sits on busy catalog locks. Progress in the older
  batch still advances as soon as those locks clear, at which point the
  soft-FIFO boolean flips their buckets back to idle and they reclaim
  priority.
- **Bucket fairness inside a batch is unchanged.** The new leading term
  only changes behavior when the bucket-is-idle class differs across
  batches; within one batch every row's `bucket_running_count > 0` is
  computed from the same set of siblings, so `bucket_started_count` from
  ADR 0002 still decides order.
- **Legacy backlog is scheduled like native backlog after one startup.**
  On the first post-#166 boot, `backfill_legacy_batch_ids` populates
  `batch_id` for every NULL row; subsequent boots are no-ops.
- **`drain_once` stays unchanged.** Both the idle-bucket preference and
  the per-bucket fairness are resolved inside `claim_next_queued_job`;
  the consumer loop does not need to know about it.
- **Best-effort failure semantics still hold.** A failed job goes to
  `status='failed'` — that doesn't make its bucket `running` again, so
  the new soft-FIFO term doesn't re-prefer it; `started_count` (ADR
  0002) still keeps it out of the way.
- **Worker-count assumption is encoded only implicitly.** The new order
  only ever breaks FIFO for buckets that are *actually* running now.
  If worker concurrency is lowered to 1 in config, strict FIFO is
  functionally restored (because at most one bucket can be running at a
  time, older will always have idle capacity unless it's the one running
  — in which case relaxing FIFO is still the right call).

## Notes for future slices

- PRD #162 is complete with this slice. Any follow-up for operator knobs
  (priority overrides, pause-by-batch) would live in its own PRD and
  will naturally slot in as another leading `ORDER BY` term, matching
  the extension pattern established by ADR 0002 and this ADR.
- If `data_fetch_jobs` grows to millions of rows, the `bucket_started_subq`
  aggregate becomes the hot spot (same concern as ADR 0002). The partial
  index recommended there `(status, batch_id, symbol, data_type, interval)`
  covers this ADR too; no additional index is required.
