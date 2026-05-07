"""BinanceVision unified downloader.

Downloads historical data files from https://data.binance.vision/ with:
- Monthly-first planning (monthly packages + daily tail coverage)
- Incremental skip (already-downloaded files are not re-fetched)
- SHA-256 checksum verification
- Exponential backoff retry (429/5xx) — shared policy from
  :mod:`tinohelm.data.providers._rest`
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import httpx

from tinohelm.core.utils import is_within_dir
from tinohelm.data.instruments import strip_to_binance_api_symbol
from tinohelm.data.providers._rest import (
    DEFAULT_MAX_RETRIES,
    MAX_BACKOFF_SECONDS,
    REQUEST_ERROR_SLEEP_SECONDS,
    SERVER_ERROR_SLEEP_SECONDS,
    backoff_seconds,
    classify_http_status,
    request_with_retry,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data type availability matrix
# (data_type) -> (supports_daily, supports_monthly)
# ---------------------------------------------------------------------------
DATA_TYPE_AVAILABILITY: dict[str, tuple[bool, bool]] = {
    "aggTrades":            (True,  True),
    "trades":               (True,  True),
    "bookTicker":           (True,  True),
    "klines":               (True,  True),
    "indexPriceKlines":     (True,  True),
    "markPriceKlines":      (True,  True),
    "premiumIndexKlines":   (True,  True),
    "fundingRate":          (False, True),   # monthly only
    "bookDepth":            (True,  False),  # daily only
    # "liquidationSnapshot" — not available on data.binance.vision (verified 2026-04)
    "metrics":              (True,  False),  # daily only
}

# klines-family types have an extra {interval} path segment in the URL
_KLINES_TYPES = frozenset({
    "klines",
    "indexPriceKlines",
    "markPriceKlines",
    "premiumIndexKlines",
})

_VISION_BASE = "https://data.binance.vision"
_MAX_RETRIES = DEFAULT_MAX_RETRIES
_STREAM_CHUNK_SIZE = 1024 * 1024
_SPOOL_MAX_BYTES = 8 * 1024 * 1024


class ChecksumError(Exception):
    """Raised when SHA-256 checksum verification fails."""


@dataclass
class DownloadTask:
    url: str
    checksum_url: str
    dest_path: Path   # final CSV path after extraction
    zip_path: Path    # ZIP file path
    granularity: str  # "daily" or "monthly"


class _BorrowedBinaryReader:
    """File-like proxy whose close() does not close the owned spool."""

    def __init__(self, raw: BinaryIO) -> None:
        self._raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def close(self) -> None:
        return None

    @property
    def closed(self) -> bool:
        return bool(getattr(self._raw, "closed", False))

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def __iter__(self):
        return iter(self._raw)

    def __getattr__(self, name: str):
        return getattr(self._raw, name)


@dataclass
class VisionCsvPayload:
    """Bounded-memory CSV source extracted from a Binance Vision ZIP archive."""

    name: str
    file: BinaryIO

    @property
    def path(self) -> Path:
        """Logical CSV path used only for logging/backward-compatible helpers."""
        return Path(self.name)

    @property
    def stem(self) -> str:
        return Path(self.name).stem

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix

    @property
    def closed(self) -> bool:
        return bool(getattr(self.file, "closed", False))

    def open(self):
        """Return a readable handle reset to the start without transferring ownership."""
        if self.closed:
            raise ValueError(f"CSV source {self.name!r} is closed")
        self.file.seek(0)
        return _BorrowedBinaryReader(self.file)

    def close(self) -> None:
        self.file.close()

    def __repr__(self) -> str:
        if self.closed:
            return f"VisionCsvPayload(name={self.name!r}, closed=True)"
        pos = self.file.tell()
        try:
            self.file.seek(0, 2)
            size = self.file.tell()
        finally:
            self.file.seek(pos)
        return f"VisionCsvPayload(name={self.name!r}, bytes={size}, closed=False)"


class VisionDownloader:
    """Download raw data packages from data.binance.vision.

    Parameters
    ----------
    raw_dir:
        Root directory for raw downloaded files.
        Defaults to ``~/.tino/data/raw``.
    concurrency:
        Maximum number of parallel downloads.
    """

    def __init__(
        self,
        raw_dir: str | Path = "~/.tino/data/raw",
        concurrency: int = 5,
    ) -> None:
        self.raw_dir = Path(raw_dir).expanduser()
        self.concurrency = concurrency

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------

    def _build_url(
        self,
        data_type: str,
        symbol: str,
        asset_class: str,
        granularity: str,
        date_or_month: str,
        interval: str | None = None,
    ) -> str:
        """Build a BinanceVision download URL.

        Parameters
        ----------
        data_type:
            E.g. ``"klines"``, ``"aggTrades"``, ``"fundingRate"``.
        symbol:
            Raw API symbol without exchange suffix, e.g. ``"BTCUSDT"``.
        asset_class:
            ``"um"`` (USDT-margined perpetuals) or ``"cm"`` (coin-margined).
        granularity:
            ``"daily"`` or ``"monthly"``.
        date_or_month:
            ``"YYYY-MM-DD"`` for daily, ``"YYYY-MM"`` for monthly.
        interval:
            Required for klines-family types, e.g. ``"1m"``, ``"5m"``.
        """
        # futures/um or futures/cm path prefix
        prefix = f"data/futures/{asset_class}/{granularity}/{data_type}/{symbol}"

        if data_type in _KLINES_TYPES:
            if interval is None:
                raise ValueError(f"interval is required for data_type={data_type!r}")
            # klines: extra interval directory; filename is {symbol}-{interval}-{date}.zip
            fname = f"{symbol}-{interval}-{date_or_month}.zip"
            path = f"{prefix}/{interval}/{fname}"
        else:
            fname = f"{symbol}-{data_type}-{date_or_month}.zip"
            path = f"{prefix}/{fname}"

        return f"{_VISION_BASE}/{path}"

    # ------------------------------------------------------------------
    # Download planning
    # ------------------------------------------------------------------

    def plan_downloads(
        self,
        data_type: str,
        symbol: str,
        asset_class: str,
        start: date,
        end: date,
        interval: str | None = None,
    ) -> list[DownloadTask]:
        """Plan download tasks using monthly-first strategy.

        Monthly packages are preferred for full calendar months.
        Daily packages fill the partial-month head and tail.

        Parameters
        ----------
        data_type:
            BinanceVision data type key.
        symbol:
            NautilusTrader-style symbol (e.g. ``"BTCUSDT-PERP"``).
            Stripped to API form (``"BTCUSDT"``) internally.
        asset_class:
            ``"um"`` or ``"cm"``.
        start / end:
            Inclusive date range.
        interval:
            Required for klines-family types.

        Returns
        -------
        list[DownloadTask]
            Ordered list of tasks (head dailies → monthlies → tail dailies).
        """
        has_daily, has_monthly = DATA_TYPE_AVAILABILITY.get(data_type, (True, True))
        api_symbol = strip_to_binance_api_symbol(symbol)
        tasks: list[DownloadTask] = []

        if not has_daily and not has_monthly:
            logger.warning("data_type=%r has no known availability; skipping", data_type)
            return tasks

        if not has_monthly or not has_daily:
            # Only one granularity available — enumerate all days or all months
            if has_daily:
                for d in _iter_dates(start, end):
                    tasks.append(self._make_task(
                        data_type, api_symbol, asset_class, "daily",
                        d.strftime("%Y-%m-%d"), interval,
                    ))
            else:
                for year, month in _iter_months(start, end):
                    tasks.append(self._make_task(
                        data_type, api_symbol, asset_class, "monthly",
                        f"{year}-{month:02d}", interval,
                    ))
            return tasks

        # Both granularities available → monthly-first with daily head/tail
        # Determine which months are fully covered by [start, end]
        first_full_month_start, last_full_month_end = _full_month_bounds(start, end)

        # HEAD: daily tasks from start up to (but not including) first full month
        if first_full_month_start is not None and start < first_full_month_start:
            head_end = first_full_month_start - timedelta(days=1)
            for d in _iter_dates(start, head_end):
                tasks.append(self._make_task(
                    data_type, api_symbol, asset_class, "daily",
                    d.strftime("%Y-%m-%d"), interval,
                ))

        # MONTHLY: full calendar months
        if first_full_month_start is not None:
            for year, month in _iter_months(first_full_month_start, last_full_month_end):  # type: ignore[arg-type]
                tasks.append(self._make_task(
                    data_type, api_symbol, asset_class, "monthly",
                    f"{year}-{month:02d}", interval,
                ))

        # TAIL: daily tasks from day after last full month to end
        if last_full_month_end is not None and end > last_full_month_end:
            tail_start = last_full_month_end + timedelta(days=1)
            for d in _iter_dates(tail_start, end):
                tasks.append(self._make_task(
                    data_type, api_symbol, asset_class, "daily",
                    d.strftime("%Y-%m-%d"), interval,
                ))

        # No full month found → all daily
        if first_full_month_start is None:
            for d in _iter_dates(start, end):
                tasks.append(self._make_task(
                    data_type, api_symbol, asset_class, "daily",
                    d.strftime("%Y-%m-%d"), interval,
                ))

        return tasks

    def _make_task(
        self,
        data_type: str,
        api_symbol: str,
        asset_class: str,
        granularity: str,
        date_str: str,
        interval: str | None,
    ) -> DownloadTask:
        url = self._build_url(data_type, api_symbol, asset_class, granularity, date_str, interval)
        checksum_url = url + ".CHECKSUM"

        # Storage: raw_dir/{data_type}/{symbol}/{filename}
        dest_dir = self.raw_dir / data_type / api_symbol

        if data_type in _KLINES_TYPES and interval:
            stem = f"{api_symbol}-{interval}-{date_str}"
        else:
            stem = f"{api_symbol}-{data_type}-{date_str}"

        zip_path = dest_dir / f"{stem}.zip"
        csv_path = dest_dir / f"{stem}.csv"

        return DownloadTask(
            url=url,
            checksum_url=checksum_url,
            dest_path=csv_path,
            zip_path=zip_path,
            granularity=granularity,
        )

    # ------------------------------------------------------------------
    # Download, verify, extract
    # ------------------------------------------------------------------

    async def download_file(self, url: str, dest_path: Path) -> Path:
        """Download a single file with retry and incremental skip.

        Skips download if ``dest_path`` already exists and is non-empty.
        Retries on 429/5xx with exponential backoff; raises immediately on 404.

        Parameters
        ----------
        url:
            Full URL to download.
        dest_path:
            Local file path to write.

        Returns
        -------
        Path
            The ``dest_path`` after successful download.
        """
        if dest_path.exists() and dest_path.stat().st_size > 0:
            logger.debug("Skip (exists): %s", dest_path.name)
            return dest_path

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await request_with_retry(
                client,
                url,
                max_retries=_MAX_RETRIES,
                raise_on_404=True,
                follow_redirects=True,
            )
        assert resp is not None  # raise_on_404=True — None path is impossible
        dest_path.write_bytes(resp.content)
        logger.debug("Downloaded: %s (%d bytes)", dest_path.name, len(resp.content))
        return dest_path

    async def download_bytes(self, url: str) -> bytes:
        """Download a single file into memory with the shared retry policy."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await request_with_retry(
                client,
                url,
                max_retries=_MAX_RETRIES,
                raise_on_404=True,
                follow_redirects=True,
            )
        assert resp is not None  # raise_on_404=True — None path is impossible
        payload = resp.content
        logger.debug("Downloaded in memory: %s (%d bytes)", url.rsplit("/", 1)[-1], len(payload))
        return payload

    async def download_stream(self, url: str) -> BinaryIO:
        """Download a file by streaming response chunks into a bounded spool."""
        attempt = 0
        while True:
            spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES, mode="w+b")
            try:
                total = 0
                async with httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream("GET", url, follow_redirects=True) as resp:
                        resp.raise_for_status()
                        async for chunk in resp.aiter_bytes():
                            if not chunk:
                                continue
                            spool.write(chunk)
                            total += len(chunk)
                spool.seek(0)
                logger.debug("Downloaded stream: %s (%d bytes)", url.rsplit("/", 1)[-1], total)
                return spool
            except httpx.HTTPStatusError as exc:
                spool.close()
                status = exc.response.status_code
                kind = classify_http_status(status)
                if kind == "not_found":
                    raise
                if kind == "rate_limit":
                    attempt += 1
                    if attempt > _MAX_RETRIES:
                        raise
                    wait = backoff_seconds(attempt, max_seconds=MAX_BACKOFF_SECONDS)
                    logger.warning(
                        "Rate limited (HTTP %d) for %s, retry %d/%d in %ds",
                        status, url, attempt, _MAX_RETRIES, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                if kind == "server_error":
                    attempt += 1
                    if attempt > _MAX_RETRIES:
                        raise
                    logger.warning(
                        "Server error (HTTP %d) for %s, retry %d/%d in %.1fs",
                        status, url, attempt, _MAX_RETRIES, SERVER_ERROR_SLEEP_SECONDS,
                    )
                    await asyncio.sleep(SERVER_ERROR_SLEEP_SECONDS)
                    continue
                raise
            except httpx.RequestError as exc:
                spool.close()
                attempt += 1
                if attempt > _MAX_RETRIES:
                    raise
                logger.warning(
                    "Request error for %s: %s, retry %d/%d",
                    url, exc, attempt, _MAX_RETRIES,
                )
                await asyncio.sleep(REQUEST_ERROR_SLEEP_SECONDS)
                continue
            except Exception:
                spool.close()
                raise

    async def verify_checksum_bytes(
        self,
        zip_payload: bytes,
        zip_name: str,
        checksum_url: str,
    ) -> None:
        """Verify a ZIP payload against Binance Vision's SHA-256 checksum."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(checksum_url, follow_redirects=True)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.warning("Checksum file not found (404): %s — skipping", checksum_url)
                    return
                raise

        checksum_text = resp.text.strip()
        parts = checksum_text.split(None, 1)
        if not parts:
            logger.warning("Empty checksum file at %s — skipping", checksum_url)
            return
        expected_hash = parts[0].lower()

        digest = hashlib.sha256()
        view = memoryview(zip_payload)
        chunk_size = 1024 * 1024
        for offset in range(0, len(view), chunk_size):
            digest.update(view[offset:offset + chunk_size])
        actual_hash = digest.hexdigest().lower()

        if actual_hash != expected_hash:
            raise ChecksumError(
                f"Checksum mismatch for {zip_name}: "
                f"expected {expected_hash}, got {actual_hash}"
            )

        logger.debug("Checksum OK: %s", zip_name)

    async def verify_checksum_stream(
        self,
        zip_file: BinaryIO,
        zip_name: str,
        checksum_url: str,
    ) -> None:
        """Verify a seekable ZIP stream against Binance Vision's SHA-256 checksum."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(checksum_url, follow_redirects=True)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.warning("Checksum file not found (404): %s — skipping", checksum_url)
                    zip_file.seek(0)
                    return
                raise

        checksum_text = resp.text.strip()
        parts = checksum_text.split(None, 1)
        if not parts:
            logger.warning("Empty checksum file at %s — skipping", checksum_url)
            zip_file.seek(0)
            return
        expected_hash = parts[0].lower()

        digest = hashlib.sha256()
        zip_file.seek(0)
        for chunk in iter(lambda: zip_file.read(_STREAM_CHUNK_SIZE), b""):
            digest.update(chunk)
        zip_file.seek(0)
        actual_hash = digest.hexdigest().lower()

        if actual_hash != expected_hash:
            raise ChecksumError(
                f"Checksum mismatch for {zip_name}: "
                f"expected {expected_hash}, got {actual_hash}"
            )

        logger.debug("Checksum OK: %s", zip_name)

    async def verify_checksum(self, zip_path: Path, checksum_url: str) -> None:
        """Verify the SHA-256 checksum of a downloaded ZIP.

        Downloads the ``.CHECKSUM`` file, parses the expected hash, computes
        the actual hash of ``zip_path``, and raises ``ChecksumError`` on
        mismatch (deleting the corrupt ZIP first).

        A 404 for the checksum file is treated as a non-fatal warning.

        Parameters
        ----------
        zip_path:
            Local path to the downloaded ZIP file.
        checksum_url:
            URL of the ``.CHECKSUM`` file.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(checksum_url, follow_redirects=True)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.warning("Checksum file not found (404): %s — skipping", checksum_url)
                    return
                raise

        # Format: "{sha256hash}  {filename}\n"
        checksum_text = resp.text.strip()
        parts = checksum_text.split(None, 1)
        if not parts:
            logger.warning("Empty checksum file at %s — skipping", checksum_url)
            return
        expected_hash = parts[0].lower()

        with zip_path.open("rb") as fh:
            if hasattr(hashlib, "file_digest"):
                actual_hash = hashlib.file_digest(fh, "sha256").hexdigest().lower()
            else:
                digest = hashlib.sha256()
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
                actual_hash = digest.hexdigest().lower()

        if actual_hash != expected_hash:
            zip_path.unlink(missing_ok=True)
            raise ChecksumError(
                f"Checksum mismatch for {zip_path.name}: "
                f"expected {expected_hash}, got {actual_hash}"
            )

        logger.debug("Checksum OK: %s", zip_path.name)

    def extract_zip(self, zip_path: Path) -> Path:
        """Extract a ZIP archive and return the path of the first CSV file.

        Parameters
        ----------
        zip_path:
            Path to the ZIP file to extract.

        Returns
        -------
        Path
            Path to the first ``.csv`` file found inside the archive.

        Raises
        ------
        FileNotFoundError
            If the ZIP contains no ``.csv`` files.
        """
        dest_dir = zip_path.parent
        with zipfile.ZipFile(zip_path, "r") as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_names:
                raise FileNotFoundError(f"No CSV file found inside {zip_path.name}")
            member = csv_names[0]
            target = (dest_dir / member).resolve()
            if not is_within_dir(target, dest_dir):
                raise ValueError(
                    f"Zip entry {member!r} would escape extraction directory"
                )
            zf.extract(member, dest_dir)

        csv_path = dest_dir / member
        logger.debug("Extracted: %s", csv_path.name)
        return csv_path

    def extract_zip_bytes(self, zip_payload: bytes, zip_name: str) -> VisionCsvPayload:
        """Extract the first CSV from a ZIP payload into a bounded CSV spool."""
        zip_file = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES, mode="w+b")
        try:
            zip_file.write(zip_payload)
            zip_file.seek(0)
            return self.extract_zip_stream(zip_file, zip_name)
        finally:
            zip_file.close()

    def extract_zip_stream(self, zip_file: BinaryIO, zip_name: str) -> VisionCsvPayload:
        """Extract the first CSV from a seekable ZIP stream into a bounded spool."""
        csv_file = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES, mode="w+b")
        try:
            zip_file.seek(0)
            with zipfile.ZipFile(zip_file, "r") as zf:
                csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if not csv_names:
                    raise FileNotFoundError(f"No CSV file found inside {zip_name}")
                member = csv_names[0]
                logical = PurePosixPath(member.replace("\\", "/"))
                if logical.is_absolute() or ".." in logical.parts:
                    raise ValueError(
                        f"Zip entry {member!r} would escape extraction directory"
                    )
                with zf.open(member, "r") as fh:
                    while True:
                        chunk = fh.read(_STREAM_CHUNK_SIZE)
                        if not chunk:
                            break
                        csv_file.write(chunk)

            csv_file.seek(0)
            name = logical.name or member
            logger.debug("Extracted to bounded CSV source: %s", name)
            return VisionCsvPayload(name=name, file=csv_file)
        except Exception:
            csv_file.close()
            raise

    async def execute_task(self, task: DownloadTask) -> Path | VisionCsvPayload:
        """Execute one download task without staging fresh raw ZIP/CSV files."""
        # Backward-compatible incremental skip: leave existing raw CSVs path-
        # backed so chunked converters can stream them instead of reading the
        # whole file into memory up front.
        if task.dest_path.exists() and task.dest_path.stat().st_size > 0:
            logger.debug("Skip (csv exists): %s", task.dest_path.name)
            return task.dest_path

        logger.info("Downloading %s [%s]", task.zip_path.name, task.granularity)
        zip_file = await self.download_stream(task.url)
        try:
            await self.verify_checksum_stream(
                zip_file,
                task.zip_path.name,
                task.checksum_url,
            )
            # Run sync extraction in thread pool to avoid blocking the event loop.
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                self.extract_zip_stream,
                zip_file,
                task.zip_path.name,
            )
        finally:
            zip_file.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iter_dates(start: date, end: date):
    """Yield each date from start to end (inclusive)."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _iter_months(start: date, end: date):
    """Yield (year, month) tuples for each calendar month in [start, end]."""
    year, month = start.year, start.month
    end_year, end_month = end.year, end.month
    while (year, month) <= (end_year, end_month):
        yield year, month
        month += 1
        if month > 12:
            month = 1
            year += 1


def _month_end(year: int, month: int) -> date:
    """Return the last day of the given month."""
    if month == 12:
        return date(year + 1, 1, 1) - timedelta(days=1)
    return date(year, month + 1, 1) - timedelta(days=1)


def _full_month_bounds(
    start: date,
    end: date,
) -> tuple[date | None, date | None]:
    """Return the first and last day of the contiguous block of full calendar
    months that fit entirely within [start, end].

    Returns ``(None, None)`` if no full month fits.

    Examples
    --------
    >>> _full_month_bounds(date(2025, 1, 15), date(2025, 4, 10))
    (date(2025, 2, 1), date(2025, 3, 31))

    >>> _full_month_bounds(date(2025, 2, 1), date(2025, 3, 31))
    (date(2025, 2, 1), date(2025, 3, 31))

    >>> _full_month_bounds(date(2025, 2, 15), date(2025, 2, 20))
    (None, None)
    """
    # First candidate: month that starts on or after start
    if start.day == 1:
        fm_year, fm_month = start.year, start.month
    else:
        # Advance to the first of the next month
        if start.month == 12:
            fm_year, fm_month = start.year + 1, 1
        else:
            fm_year, fm_month = start.year, start.month + 1

    first_full_start = date(fm_year, fm_month, 1)

    # Last candidate: month that ends on or before end
    lm_end = _month_end(end.year, end.month)
    if lm_end <= end:
        lm_year, lm_month = end.year, end.month
    else:
        # Step back one month
        if end.month == 1:
            lm_year, lm_month = end.year - 1, 12
        else:
            lm_year, lm_month = end.year, end.month - 1
        lm_end = _month_end(lm_year, lm_month)

    last_full_end = lm_end

    # Validate the range makes sense
    if first_full_start > last_full_end:
        return None, None
    if first_full_start > end:
        return None, None

    return first_full_start, last_full_end
