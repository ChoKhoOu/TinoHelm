# ADR 0001: Data-fetch scheduling is DB-driven; Redis is a coarse wake signal

- **Status:** Accepted (2026-05-11)
- **Related PRD:** Issue #162 — "make data fetch scheduling fair and high-throughput"
- **Implementing slice:** Issue #164 — "将 data fetch worker 切换为 Redis 唤醒 + DB claim"
- **Builds on:** Issue #163 — `FetchBatch` identity (`batch_id` on `DataFetchJob`)
- **Unblocks:** Issues #165 (FetchBucket fairness), #166 (soft FIFO across batches,
  legacy backlog migration)

## Context

Until #164 the data-fetch worker treated a Redis list (`tino:data:queue`) as
the scheduling source of truth. Every `DataFetchJob` insert was paired with an
`LPUSH job_id` (via `enqueue_job`), and each consumer drained work with
`BRPOP`. Order was therefore whatever Redis happened to return, not what was
persisted in the DB.

This caused two recurring pains in production:

1. **One symbol saturates the queue.** A large multi-symbol `fetch-batch`
   submission fan-outs into hundreds of jobs that all share a catalog-level
   serialization lock per `(symbol, data_type, interval)` bucket. Because
   Redis picks the most-recently-pushed job (LPUSH + BRPOP ≈ LIFO), one
   hot symbol can dominate the head of the queue while other symbols wait
   behind it — even when idle workers and untouched buckets exist.
2. **Recovery has to reason about two truths.** Startup recovery flipped
   `running → queued` in the DB *and* tried to rebuild Redis order by
   clearing the list and LPUSHing every `queued` job_id again. Any
   inconsistency (e.g. a Redis crash, a half-finished migration, a
   duplicate push from a deferred re-enqueue) required reasoning about
   whether the DB agreed with the list.

The broader fairness work in PRD #162 (bucket-level fairness, batch-level
soft FIFO) also needs a stable pick order based on durable state. Redis
list order cannot provide that without turning the worker into an ad-hoc
re-ordering engine.

## Decision

Scheduling truth for `DataFetchJob` lives in the database. Redis is retained
only as a **coarse wake signal**: a BRPOP'd message tells an idle consumer
"new work may exist, come look", nothing more.

Concretely:

1. **`enqueue_job(rds, job_id)` emits a `WAKE_TOKEN` sentinel.** The job_id
   argument is kept for call-site compatibility and logging only; Redis
   never carries it.
2. **`claim_next_queued_job(factory)` is the pick-and-claim primitive.** It
   runs one guarded `UPDATE data_fetch_jobs SET status='running' WHERE
   status='queued' AND job_id = (SELECT job_id FROM data_fetch_jobs WHERE
   status='queued' ORDER BY created_at, id LIMIT 1) RETURNING job_id`. At
   most one racing consumer lands `rowcount==1`; the loser re-tries on its
   next drain tick.
3. **`drain_once(redis_url, catalog_path)` is the consumer body.** After
   `consumer_loop` returns from `BRPOP`, the consumer calls `drain_once`,
   which repeatedly `claim_next_queued_job` + processes until the DB
   reports no more runnable rows. One wake token is therefore sufficient
   for an arbitrarily deep backlog — the old "one LPUSH per job" contract
   is gone.
4. **Lock-busy deferral is a pure back-off.** If a `(symbol, data_type,
   interval)` catalog lock is unavailable, we just sleep and let the next
   drain pass retry — the row is still `queued`, so no Redis bookkeeping
   is needed.
5. **Pre-claim cancellation keeps the row `queued`.** The worker no longer
   LPUSHes the job_id back onto Redis on cancellation; the row is already
   in the runnable pool.
6. **Startup recovery flips `running → queued` and pushes at most one
   `WAKE_TOKEN`.** The legacy Redis list is `DELETE`'d (discarding any
   stale job_id tokens from the pre-#164 scheduler), and a single wake
   token is pushed iff there is any queued work. No wake is emitted for a
   clean empty DB.
7. **Backtest-triggered single-job fetches speak the same dialect.**
   `BacktestRunner._submit_and_wait_fetch` LPUSHes `WAKE_TOKEN` (not the
   job_id) after inserting its row. With #163 that row already carries
   its own `batch_id`, so it is a single-job FetchBatch that the DB-driven
   scheduler handles identically to any other.

## Why these tradeoffs

- **LPUSH sentinel vs Pub/Sub.** LPUSH preserves the existing BRPOP
  structure, tolerates duplicate wakes (an extra BRPOP just loops), and
  gives logs a readable `"wake"` payload. Pub/Sub would lose any wake
  delivered while no consumer is subscribed — unacceptable for recovery
  paths.
- **Atomic single-statement claim vs `FOR UPDATE SKIP LOCKED`.** The
  single `UPDATE ... WHERE job_id = (SELECT ... LIMIT 1)` statement
  survives on both SQLite (tests) and Postgres (prod) without dialect
  gymnastics. Two racing consumers may both issue a `SELECT` that picks
  the same candidate; the `UPDATE ... WHERE status='queued'` guard means
  at most one will see `rowcount==1`. The loser's wasted round-trip is
  cheaper than giving up SQLite support.
- **FIFO by `(created_at, id)`.** Simple, deterministic, and enough for
  #164 alone. PRD #162 decision #8 calls for soft batch-level FIFO plus
  per-FetchBucket fairness; those rules will specialize `ORDER BY` in
  slices #165 / #166 without re-opening this decision.
- **`drain_once` does not track visited buckets.** Cross-bucket fairness
  is in scope for #165, not here; this ADR explicitly preserves
  same-bucket serial execution via the existing catalog lock and defers
  fairness policy to the next slice.

## Consequences

- **Redis list content is no longer meaningful.** Anything LPUSH'd that
  isn't `WAKE_TOKEN` is treated as a wake. Migrated / stranded job_id
  tokens left over from before #164 become harmless noise and are purged
  on startup.
- **Best-effort batch semantics survive.** `drain_once` logs and continues
  past per-job exceptions so one failing `DataFetchJob` cannot block
  siblings in the same FetchBatch (PRD #162 requirement).
- **Observability is unchanged.** Status progression (`queued → running →
  completed | failed | cancelled`), progress pubsub, and
  `tino:data:events` payloads stay on the wire exactly as before.
- **Bucket fairness and cross-batch soft FIFO are explicitly deferred.**
  The `ORDER BY created_at, id` in `claim_next_queued_job` is the single
  specialization point those future slices will extend.

## Notes for future slices

- #165 (FetchBucket fairness) can implement "least-progressed bucket first"
  by joining `data_fetch_jobs` to itself grouped by `(batch_id, symbol,
  data_type, interval)` and replacing the `ORDER BY` in
  `claim_next_queued_job`. The rest of the worker does not need to change.
- #166 (cross-batch soft FIFO + backlog migration) should preserve the
  invariant that Redis never carries a job_id. If historical backlog
  migration requires inspecting legacy Redis tokens, that should be a
  one-off migration step, not a permanent path.
