# TinoHelm

TinoHelm is a single-instance quantitative trading platform built on NautilusTrader. This file is the domain glossary — when two words fight over the same concept, this file picks the winner. Keep definitions tight; push implementation detail to code.

## Language

### Data & storage

**Catalog**:
The persisted market-data home rooted at one NT-native `ParquetDataCatalog`; NautilusTrader owns the physical `data/...` layout.
_Avoid_: data store, parquet store, datalake.

**Catalog path**:
The single logical root of the active **Catalog**; in production it is typically one S3-compatible URI, while development may use a local path.

**CatalogSession**:
A single module that owns **Catalog** CRUD and metadata sync for market data.
_Avoid_: CatalogService, CatalogManager, DataCatalogService.

**Funding rate update**:
The NT-native `FundingRateUpdate` record for perpetual contracts, persisted in the same **Catalog** as other built-in NT data types; its event payload may carry a funding interval, but that interval is not a stream-identity dimension.
_Avoid_: custom funding record, funding cache row.

**Auxiliary price update**:
An NT-native non-bar price stream such as `MarkPriceUpdate` or `IndexPriceUpdate`, written to and read from the **Catalog** directly rather than reconstructed from stored bars.
_Avoid_: mark/index bar, derived update stream.

**FundingRateTxn**:
A legacy transition artifact around funding-rate persistence; it should disappear once funding data is stored and read as NT-native **Funding rate update**.
_Avoid_: FundingWriter, FundingCommitter.

**Storage** (as a noun, `CatalogStorageProvider`):
The active backing store for the **Catalog** — local or S3-compatible — selected from config at runtime.
_Avoid_: backend, provider, filesystem (overloaded), FS.

**Vision type**:
A Binance download-side data identifier such as `klines`, `trades`, `fundingRate`, `markPriceKlines`, `indexPriceKlines`, or `bookTicker`; the current target model uses Binance-controlled upstreams (Vision packages plus REST tail fill where needed).
_Avoid_: stream type, Binance type.

**Canonical upstream**:
The single Binance source selected for one **NT stream type**. In the target model, each persisted NT-native stream has exactly one canonical upstream rather than multiple interchangeable feed labels.
_Avoid_: source fallback, alternate download label.

**NT-native ingest scope**:
The phase-1 persisted market-data set is exactly `bar`, `trade_tick`, `quote_tick`, `mark_price`, `index_price`, and `funding_rate`; `bookDepth`, `metrics`, `liquidationSnapshot`, and `premiumIndexKlines` are out of scope.
_Avoid_: mixed-scope catalog, partial-native mode.

**True concurrency**:
Real overlap of independent ingest work — multiple downloads and conversions progressing at the same time — while writes remain serialized only per physical NT data stream, with lock keys derived from the final NT-native stream identity rather than the download-side request label.
_Avoid_: fake parallelism, queue-only concurrency.

**In-memory ingest**:
Download, checksum verification, ZIP extraction, CSV decoding, and object conversion all happen in memory; only final NT catalog outputs may be persisted. The target model does not require staging or filesystem spill for intermediate artifacts.
_Avoid_: raw cache, staged CSV, intermediate ZIP persistence.

**NT stream type**:
The final NT-native market-data identity used by the catalog read/write path, such as `bar`, `mark_price`, `index_price`, `trade_tick`, `quote_tick`, or `funding_rate`.
_Avoid_: write category (internal code name), storage type.

**Source type**:
A legacy download-side label from the old schema/layout that should disappear from the target model.
_Avoid_: treating it as durable storage identity.

**Sampling interval**:
The upstream cadence carried by the ingest/read request; for `bar` it is part of the bar stream identity, and for `mark_price` / `index_price` it remains resolution metadata that still keeps `1m` and `5m` streams distinct.
_Avoid_: timeframe when the discussion is about non-bar update streams.

**Single remote Catalog**:
The production storage model: one S3-compatible NT-native `ParquetDataCatalog` that serves as the only persisted truth for market-data files.
_Avoid_: per-source catalog root, split catalog tree.

**NT-native query path**:
The default read path for backtest, runtime, research, and general catalog access: query through NautilusTrader catalog APIs and let the engine select Rust or PyArrow backend automatically.
_Avoid_: hand-scanning parquet as the primary read path.

**Catalog overview**:
A live, read-only summary of the current NT Catalog state returned by API queries rather than a persisted business index table.
_Avoid_: DataCatalog as a separate truth source.

**Data type list**:
A static API capability list describing which NT-native data types this release supports.
_Avoid_: deriving supported types from a persisted catalog index.

**Catalog maintenance**:
File reorganization operations such as reset-file-names, consolidate, consolidate-by-period, and delete-range are explicit maintenance actions outside the ingest hot path.
_Avoid_: automatic maintenance during every ingest.

**Catalog maintenance verbs**:
Public maintenance APIs use NT-native operation names such as `reset-file-names`, `consolidate`, `consolidate-by-period`, and `delete-range`.
_Avoid_: compact, scan, sync.

**Append-only ingest**:
The ingest hot path only appends new NT catalog data and does not perform overlap deletion or rewrite of existing time ranges.
_Avoid_: automatic overlap cleanup, transactional rewrite.

**DataFetchJob**:
A persistent record (`data_fetch_jobs` table) representing one ingest request. States: `queued → running → completed | failed | cancelled`. On API restart, `running` is reset to `queued` and re-enqueued.

**FetchBatch**:
One user-submitted fetch boundary. Every `POST /api/data/fetch-batch` call yields one FetchBatch whose fan-out of `DataFetchJob` rows shares the same `batch_id`. A backtest-triggered standalone fetch is a single-job FetchBatch. Pre-#163 rows arrive with `batch_id IS NULL`; startup recovery backfills them by grouping rows that share `created_at` (see ADR 0003). Not persisted as its own table — `batch_id` on `data_fetch_jobs` is the only durable representation.
_Avoid_: fetch request, batch job.

**FetchBucket**:
The fairness unit inside a **FetchBatch**: one `(symbol, data_type, interval)` stream. Same-bucket jobs are processed in `start_date` order under the catalog serialization lock; the scheduler picks across sibling buckets using "least-started bucket first", where started_count = `running + completed + failed + cancelled`. Not persisted — the bucket key is recomputed from `data_fetch_jobs` columns at claim time (see ADR 0002).
_Avoid_: stream, slot, channel.

**Soft FIFO**:
The cross-**FetchBatch** ordering rule. An older batch keeps priority while it still has an idle **FetchBucket** (one whose jobs haven't started). The moment every remaining queued row of the older batch sits in a bucket with an in-flight job — i.e. claiming it would just defer on the catalog lock — a newer batch's idle bucket wins the next claim, so worker capacity doesn't go to waste (see ADR 0003).
_Avoid_: strict FIFO, priority queue.

### Runtime

**Node**:
A single OS process running `TradingNode` + 5 **Actors** for either **sandbox** (Binance Demo) or **live** (Binance Live/Testnet). Not a Kubernetes node, not a blockchain node.

**Actor** (in TinoHelm usage):
An NT `Actor` subclass. The 5 canonical ones are **SnapshotActor**, **CommandActor**, **DbWriterActor**, **HealthActor**, **MetricsActor**.
_Avoid_: worker (reserved for subprocess workers), service.

**Runner**:
The process entry-point that builds a `TradingNode` and wires in the 5 **Actors** — `sandbox.py` / `live.py`. Also used for `BacktestRunner`.

**Bundle**:
A strategy-loadable unit. A **portfolio folder** (with `portfolio.yaml`) is an explicit bundle; a single `.py` file is an implicit bundle (1 strategy × 1 symbol × 0 actors).
_Avoid_: pack, package, plan.

**LifecycleController**:
The module inside the **Node** that owns the 4-level command dispatch (L1 pause/resume via msgbus, L2 flatten, L3 halt, L4 shutdown).

**Strategy registry**:
Pure-Python tracker of `StrategyBundle` discovery and strategy state machine (`available → starting → running → paused → …`). Lives in `node/strategy_registry.py`.

## Relationships

- A **CatalogSession** owns one **Storage** and one **Catalog path**.
- A **Single remote Catalog** is backed by exactly one active **Storage** in production.
- A **Vision type** maps to exactly one **NT stream type** (many-to-one only where multiple upstreams truly land on the same NT-native type).
- A `data_catalog` row carries `(symbol, data_type=NT stream type, interval where relevant)`; `source_type` is legacy-only and should leave the target schema.
- A **DataFetchJob** produces zero or more `data_catalog` rows on success; the `ingest_run_id` on each row points back to the job.
- A **FetchBatch** owns one-or-more **DataFetchJob** rows via a shared `batch_id`; jobs in the same batch are siblings from one user submission.
- A **FetchBatch** fans out into one-or-more **FetchBuckets** (one per distinct `(symbol, data_type, interval)` in the batch); buckets are the fairness unit, batches are the **Soft FIFO** unit.
- A **Node** contains exactly 5 **Actors** plus one **LifecycleController** and one **Strategy registry**.

## Flagged ambiguities

- **"catalog" as verb vs noun**: the noun is the on-disk **Catalog**. When something mutates it, prefer "write/delete/compact the **Catalog**", not "catalog it".
- **"data type"**: ambiguous unless qualified. Use **Vision type** (request side) or **DB category** (storage side).
- **"storage"**: until 2026-05, used for both filesystem provider and catalog. Resolved: **Storage** is always the provider (`LocalCatalogStorage`/`S3CatalogStorage`); "write to catalog" uses **Catalog**.
- **"session"**: only **CatalogSession** so far. If a future DB session abstraction is introduced, give it a distinct name (e.g. `DBTxn`) — do not reuse "session".

## Example dialogue

> **Dev:** "Should `markPriceKlines` live under a separate `bar/markPriceKlines` storage prefix?"
> **Domain expert:** "No — in the target model the **Catalog** is one NT-native root and NautilusTrader owns the physical layout. `source_type` stays metadata on the row, not a storage partition."
