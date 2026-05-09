# TinoHelm

TinoHelm is a single-instance quantitative trading platform built on NautilusTrader. This file is the domain glossary — when two words fight over the same concept, this file picks the winner. Keep definitions tight; push implementation detail to code.

## Language

### Data & storage

**Catalog**:
The on-disk home for all persisted market data under `{catalog_path}/`. NT ParquetDataCatalog is the underlying writer; TinoHelm overlays additional per-source grouping (`data/{category}/{source_type}/`).
_Avoid_: data store, parquet store, datalake.

**Catalog path**:
The root directory that contains a **Catalog**. Passed as a string or Path; resolved by `catalog_helpers.resolve_catalog_path(base, source_type)`.

**CatalogSession**:
A single module that owns **Catalog** CRUD — read (parquet stats, coverage), write (bars/ticks/quotes/funding), delete (per-symbol × data_type × source_type), and compact (local + remote unified). Constructed with `(catalog_path, storage=None)`; all methods are sync.
_Avoid_: CatalogService, CatalogManager, DataCatalogService.

**FundingRateTxn**:
A context manager obtained via `CatalogSession.funding_rate_transaction(symbol)`. Owns the snapshot → write Parquet → flush JSON → restore-on-failure lifecycle for funding_rate, which is the only **Catalog** datum persisted to two backends simultaneously.
_Avoid_: FundingWriter, FundingCommitter.

**Storage** (as a noun, `CatalogStorageProvider`):
The concrete backing filesystem for a **Catalog** — `LocalCatalogStorage` or `S3CatalogStorage`. Always attached to exactly one catalog root. Obtained via `get_catalog_storage(catalog_root=...)` or injected.
_Avoid_: backend, filesystem (overloaded), FS.

**Vision type**:
A Binance Vision download-side data identifier — one of 12 strings: `klines`, `markPriceKlines`, `indexPriceKlines`, `premiumIndexKlines`, `aggTrades`, `trades`, `bookTicker`, `fundingRate`, `metrics`, `liquidationHistory`, `BVOLIndex`, `EOHSummary`.
_Avoid_: stream type, Binance type.

**DB category**:
The storage-side type stored in `data_catalog.data_type`: `bar`, `trade_tick`, `quote_tick`, `funding_rate`, `metrics`, `order_book_delta`. One DB category can receive multiple **Vision types** (e.g. `klines` + `markPriceKlines` both → `bar`).
_Avoid_: write category (internal code name), storage type.

**Source type**:
The **Vision type** recorded on a catalog row, used to disambiguate multiple **Vision types** that map to the same **DB category**. Stored in `data_catalog.source_type`; `None` / empty means "legacy flat layout". Used by `resolve_catalog_path` to compute `{base}/{category}/{source_type}`.

**DataFetchJob**:
A persistent record (`data_fetch_jobs` table) representing one ingest request. States: `queued → running → completed | failed | cancelled`. On API restart, `running` is reset to `queued` and re-enqueued.

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
- A **CatalogSession** produces a **FundingRateTxn** only for `funding_rate`; all other categories write directly.
- A **Vision type** maps to exactly one **DB category** (many-to-one).
- A `data_catalog` row carries `(symbol, data_type=DB category, interval, source_type=Vision type or None)`.
- A **DataFetchJob** produces zero or more `data_catalog` rows on success; the `ingest_run_id` on each row points back to the job.
- A **Node** contains exactly 5 **Actors** plus one **LifecycleController** and one **Strategy registry**.

## Flagged ambiguities

- **"catalog" as verb vs noun**: the noun is the on-disk **Catalog**. When something mutates it, prefer "write/delete/compact the **Catalog**", not "catalog it".
- **"data type"**: ambiguous unless qualified. Use **Vision type** (request side) or **DB category** (storage side).
- **"storage"**: until 2026-05, used for both filesystem provider and catalog. Resolved: **Storage** is always the provider (`LocalCatalogStorage`/`S3CatalogStorage`); "write to catalog" uses **Catalog**.
- **"session"**: only **CatalogSession** so far. If a future DB session abstraction is introduced, give it a distinct name (e.g. `DBTxn`) — do not reuse "session".

## Example dialogue

> **Dev:** "Can I call `delete_storage` directly on the parquet files for `fundingRate`?"
> **Domain expert:** "No — for `funding_rate` the **Catalog** holds the Parquet and `~/.tino/data/funding_rates/` holds the JSON read-side. Use `CatalogSession.delete_storage(symbol, 'funding_rate', ...)`; it takes care of both. And for writes, you always need a **FundingRateTxn** because the JSON flush is gated on your DB commit."
