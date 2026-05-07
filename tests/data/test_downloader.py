"""Tests for tinohelm.data.downloader.VisionDownloader.

All HTTP calls are mocked — no network access required.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tinohelm.data.downloader import (
    ChecksumError,
    DATA_TYPE_AVAILABILITY,
    VisionCsvPayload,
    VisionDownloader,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dl(tmp_path: Path) -> VisionDownloader:
    return VisionDownloader(raw_dir=str(tmp_path))


def _zip_bytes(member_name: str, csv_content: str) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member_name, csv_content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. plan_downloads — date-split logic
# ---------------------------------------------------------------------------

class TestPlanDownloads:
    def test_monthly_first_with_daily_head_and_tail(self, tmp_path):
        """Jan 15 → Apr 10: head dailies + Feb monthly + Mar monthly + tail dailies."""
        dl = _make_dl(tmp_path)
        tasks = dl.plan_downloads(
            "klines", "BTCUSDT-PERP", "um",
            date(2025, 1, 15), date(2025, 4, 10), interval="1m",
        )
        granularities = [t.granularity for t in tasks]
        assert "monthly" in granularities
        assert "daily" in granularities
        monthly_count = sum(1 for g in granularities if g == "monthly")
        assert monthly_count == 2  # Feb, Mar

        # Head: Jan 15–31 = 17 daily tasks
        head = [t for t in tasks if t.granularity == "daily" and "2025-01" in t.zip_path.name]
        assert len(head) == 17

        # Tail: Apr 1–10 = 10 daily tasks
        tail = [t for t in tasks if t.granularity == "daily" and "2025-04" in t.zip_path.name]
        assert len(tail) == 10

    def test_full_months_only_monthly(self, tmp_path):
        """Feb 1 → Mar 31: exactly 2 monthly packages, no daily."""
        dl = _make_dl(tmp_path)
        tasks = dl.plan_downloads(
            "klines", "BTCUSDT-PERP", "um",
            date(2025, 2, 1), date(2025, 3, 31), interval="1m",
        )
        granularities = [t.granularity for t in tasks]
        assert all(g == "monthly" for g in granularities)
        assert len(tasks) == 2

    def test_same_month_all_daily(self, tmp_path):
        """Feb 15 → Feb 20 (same month, partial): all daily, 6 tasks."""
        dl = _make_dl(tmp_path)
        tasks = dl.plan_downloads(
            "klines", "BTCUSDT-PERP", "um",
            date(2025, 2, 15), date(2025, 2, 20), interval="1m",
        )
        assert all(t.granularity == "daily" for t in tasks)
        assert len(tasks) == 6  # 15,16,17,18,19,20

    def test_monthly_only_type(self, tmp_path):
        """fundingRate is monthly-only → only monthly tasks regardless of range."""
        dl = _make_dl(tmp_path)
        tasks = dl.plan_downloads(
            "fundingRate", "BTCUSDT-PERP", "um",
            date(2025, 1, 1), date(2025, 3, 31),
        )
        assert all(t.granularity == "monthly" for t in tasks)
        assert len(tasks) == 3  # Jan, Feb, Mar

    def test_daily_only_type(self, tmp_path):
        """bookDepth is daily-only → only daily tasks."""
        dl = _make_dl(tmp_path)
        tasks = dl.plan_downloads(
            "bookDepth", "BTCUSDT-PERP", "um",
            date(2025, 1, 1), date(2025, 1, 5),
        )
        assert all(t.granularity == "daily" for t in tasks)
        assert len(tasks) == 5

    def test_single_day_range(self, tmp_path):
        """Single day → exactly 1 daily task."""
        dl = _make_dl(tmp_path)
        tasks = dl.plan_downloads(
            "aggTrades", "BTCUSDT-PERP", "um",
            date(2025, 3, 15), date(2025, 3, 15),
        )
        assert len(tasks) == 1
        assert tasks[0].granularity == "daily"

    def test_symbol_stripped_to_api_form(self, tmp_path):
        """NT-style symbol BTCUSDT-PERP is stripped to BTCUSDT in the URL."""
        dl = _make_dl(tmp_path)
        tasks = dl.plan_downloads(
            "klines", "BTCUSDT-PERP", "um",
            date(2025, 2, 1), date(2025, 2, 28), interval="5m",
        )
        assert len(tasks) == 1
        assert "BTCUSDT" in tasks[0].url
        assert "BTCUSDT-PERP" not in tasks[0].url

    def test_tasks_ordered_head_monthly_tail(self, tmp_path):
        """Task order: daily head → monthly → daily tail."""
        dl = _make_dl(tmp_path)
        tasks = dl.plan_downloads(
            "klines", "BTCUSDT-PERP", "um",
            date(2025, 1, 20), date(2025, 3, 5), interval="1m",
        )
        daily_indices = [i for i, t in enumerate(tasks) if t.granularity == "daily"]
        monthly_indices = [i for i, t in enumerate(tasks) if t.granularity == "monthly"]
        # All head dailies come before monthlies
        assert all(i < monthly_indices[0] for i in daily_indices if i < monthly_indices[0])
        # All tail dailies come after monthlies
        assert all(i > monthly_indices[-1] for i in daily_indices if i > monthly_indices[-1])


# ---------------------------------------------------------------------------
# 2. _build_url — URL format
# ---------------------------------------------------------------------------

class TestBuildUrl:
    def setup_method(self):
        # raw_dir is not used for URL building
        self.dl = VisionDownloader(raw_dir="/tmp/test_raw")

    def test_klines_daily_url_structure(self):
        url = self.dl._build_url("klines", "BTCUSDT", "um", "daily", "2025-01-15", interval="1m")
        assert url.startswith("https://data.binance.vision/")
        assert "futures/um/daily/klines/BTCUSDT/1m/" in url
        assert url.endswith("BTCUSDT-1m-2025-01-15.zip")

    def test_klines_monthly_url_structure(self):
        url = self.dl._build_url("klines", "BTCUSDT", "um", "monthly", "2025-01", interval="5m")
        assert "futures/um/monthly/klines/BTCUSDT/5m/" in url
        assert url.endswith("BTCUSDT-5m-2025-01.zip")

    def test_non_klines_url_no_interval_segment(self):
        url = self.dl._build_url("aggTrades", "BTCUSDT", "um", "daily", "2025-01-15")
        assert "futures/um/daily/aggTrades/BTCUSDT/" in url
        assert url.endswith("BTCUSDT-aggTrades-2025-01-15.zip")
        # No interval directory
        assert "/1m/" not in url
        assert "/5m/" not in url

    def test_non_klines_monthly_url(self):
        url = self.dl._build_url("fundingRate", "BTCUSDT", "um", "monthly", "2025-03")
        assert "futures/um/monthly/fundingRate/BTCUSDT/" in url
        assert url.endswith("BTCUSDT-fundingRate-2025-03.zip")

    def test_klines_requires_interval(self):
        with pytest.raises(ValueError, match="interval is required"):
            self.dl._build_url("klines", "BTCUSDT", "um", "daily", "2025-01-15")

    def test_mark_price_klines_requires_interval(self):
        with pytest.raises(ValueError, match="interval is required"):
            self.dl._build_url("markPriceKlines", "BTCUSDT", "um", "daily", "2025-01-15")

    def test_cm_asset_class(self):
        url = self.dl._build_url("klines", "BTCUSD", "cm", "daily", "2025-01-15", interval="1h")
        assert "futures/cm/daily/klines/BTCUSD/1h/" in url

    def test_checksum_url_suffix(self, tmp_path):
        """_make_task appends .CHECKSUM to the checksum_url."""
        dl = _make_dl(tmp_path)
        task = dl._make_task("aggTrades", "BTCUSDT", "um", "daily", "2025-01-15", None)
        assert task.checksum_url == task.url + ".CHECKSUM"


# ---------------------------------------------------------------------------
# 3. download_file — incremental skip
# ---------------------------------------------------------------------------

class TestDownloadFile:
    @pytest.mark.asyncio
    async def test_skip_existing_non_empty_file(self, tmp_path):
        """Existing non-empty file → skip without making any HTTP request."""
        dest = tmp_path / "existing.zip"
        dest.write_bytes(b"some content")

        dl = _make_dl(tmp_path)
        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await dl.download_file("https://example.com/file.zip", dest)

        mock_client_cls.assert_not_called()
        assert result == dest

    @pytest.mark.asyncio
    async def test_redownload_zero_byte_file(self, tmp_path):
        """Zero-byte file → re-download."""
        dest = tmp_path / "empty.zip"
        dest.write_bytes(b"")  # zero bytes

        fake_content = b"real zip content"
        mock_response = MagicMock()
        mock_response.content = fake_content
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        dl = _make_dl(tmp_path)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await dl.download_file("https://example.com/file.zip", dest)

        assert result == dest
        assert dest.read_bytes() == fake_content

    @pytest.mark.asyncio
    async def test_download_new_file(self, tmp_path):
        """Non-existent file → download and write content."""
        dest = tmp_path / "new_file.zip"
        fake_content = b"downloaded bytes"

        mock_response = MagicMock()
        mock_response.content = fake_content
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        dl = _make_dl(tmp_path)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await dl.download_file("https://example.com/file.zip", dest)

        assert result == dest
        assert dest.read_bytes() == fake_content

    @pytest.mark.asyncio
    async def test_404_raises_immediately(self, tmp_path):
        """HTTP 404 → raise HTTPStatusError immediately (no retry)."""
        dest = tmp_path / "notfound.zip"

        # Build a realistic HTTPStatusError for 404
        mock_response = MagicMock()
        mock_response.status_code = 404
        exc_404 = httpx.HTTPStatusError("404", request=MagicMock(), response=mock_response)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=exc_404)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        dl = _make_dl(tmp_path)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                await dl.download_file("https://example.com/missing.zip", dest)

        # Should only be called once — no retries on 404
        assert mock_client.get.call_count == 1


class TestInMemoryExecuteTask:
    @pytest.mark.asyncio
    async def test_execute_task_existing_csv_returns_path_for_chunked_streaming(self, tmp_path, monkeypatch):
        """Legacy raw CSV cache skips network without loading the full CSV into memory."""
        dl = _make_dl(tmp_path)
        task = dl._make_task("aggTrades", "BTCUSDT", "um", "daily", "2025-01-15", None)
        task.dest_path.parent.mkdir(parents=True)
        task.dest_path.write_text("a,b\n1,2\n", encoding="utf-8")

        def fail_read_bytes(self):
            raise AssertionError("existing raw CSV must remain path-backed for chunked streaming")

        monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

        with patch("httpx.AsyncClient") as mock_client_cls:
            result = await dl.execute_task(task)

        mock_client_cls.assert_not_called()
        assert result == task.dest_path

    @pytest.mark.asyncio
    async def test_execute_task_returns_csv_payload_without_raw_zip_or_csv_files(self, tmp_path):
        """The end-to-end Vision path keeps downloaded ZIP and extracted CSV in memory."""
        dl = _make_dl(tmp_path)
        task = dl._make_task("aggTrades", "BTCUSDT", "um", "daily", "2025-01-15", None)
        zip_payload = _zip_bytes("BTCUSDT-aggTrades-2025-01-15.csv", "a,b\n1,2\n")
        checksum_text = f"{hashlib.sha256(zip_payload).hexdigest()}  {task.zip_path.name}\n"

        zip_response = MagicMock()
        zip_response.content = zip_payload
        zip_response.raise_for_status = MagicMock()
        checksum_response = MagicMock()
        checksum_response.text = checksum_text
        checksum_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[zip_response, checksum_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await dl.execute_task(task)

        assert isinstance(result, VisionCsvPayload)
        assert result.name == "BTCUSDT-aggTrades-2025-01-15.csv"
        assert result.content == b"a,b\n1,2\n"
        assert not task.zip_path.exists()
        assert not task.dest_path.exists()

    @pytest.mark.asyncio
    async def test_execute_task_does_not_use_path_writes_for_fresh_download(self, tmp_path, monkeypatch):
        """A fresh download must not stage the raw ZIP through Path.write_bytes()."""
        dl = _make_dl(tmp_path)
        task = dl._make_task("aggTrades", "BTCUSDT", "um", "daily", "2025-01-15", None)
        zip_payload = _zip_bytes("data.csv", "a,b\n1,2\n")
        checksum_text = f"{hashlib.sha256(zip_payload).hexdigest()}  {task.zip_path.name}\n"

        zip_response = MagicMock()
        zip_response.content = zip_payload
        zip_response.raise_for_status = MagicMock()
        checksum_response = MagicMock()
        checksum_response.text = checksum_text
        checksum_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[zip_response, checksum_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        def fail_write_bytes(self, data):
            raise AssertionError("execute_task must not write raw Vision ZIP/CSV files")

        monkeypatch.setattr(Path, "write_bytes", fail_write_bytes)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await dl.execute_task(task)

        assert result.content == b"a,b\n1,2\n"

    def test_extract_zip_bytes_rejects_entries_that_escape_logical_root(self, tmp_path):
        dl = _make_dl(tmp_path)
        zip_payload = _zip_bytes("../evil.csv", "a,b\n1,2\n")

        with pytest.raises(ValueError, match="would escape"):
            dl.extract_zip_bytes(zip_payload, "evil.zip")


# ---------------------------------------------------------------------------
# 4. verify_checksum
# ---------------------------------------------------------------------------

class TestVerifyChecksum:
    @pytest.mark.asyncio
    async def test_matching_checksum_no_exception(self, tmp_path):
        """Correct checksum → no exception raised."""
        zip_path = tmp_path / "data.zip"
        content = b"zip file contents"
        zip_path.write_bytes(content)
        correct_hash = hashlib.sha256(content).hexdigest()
        checksum_text = f"{correct_hash}  data.zip\n"

        mock_response = MagicMock()
        mock_response.text = checksum_text
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        dl = _make_dl(tmp_path)
        with patch("httpx.AsyncClient", return_value=mock_client):
            # Should not raise
            await dl.verify_checksum(zip_path, "https://example.com/data.zip.CHECKSUM")

        assert zip_path.exists()

    @pytest.mark.asyncio
    async def test_checksum_hashing_does_not_read_whole_file(self, tmp_path, monkeypatch):
        """Checksum verification streams bytes instead of Path.read_bytes()."""
        zip_path = tmp_path / "data.zip"
        content = b"zip file contents"
        zip_path.write_bytes(content)
        correct_hash = hashlib.sha256(content).hexdigest()

        mock_response = MagicMock()
        mock_response.text = f"{correct_hash}  data.zip\n"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        def fail_read_bytes(self):
            raise AssertionError("read_bytes should not be used for checksum hashing")

        monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

        dl = _make_dl(tmp_path)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await dl.verify_checksum(zip_path, "https://example.com/data.zip.CHECKSUM")

    @pytest.mark.asyncio
    async def test_mismatched_checksum_raises_and_deletes_zip(self, tmp_path):
        """Wrong checksum → ChecksumError raised + ZIP deleted."""
        zip_path = tmp_path / "data.zip"
        zip_path.write_bytes(b"corrupt content")
        wrong_hash = "a" * 64  # obviously wrong sha256

        mock_response = MagicMock()
        mock_response.text = f"{wrong_hash}  data.zip\n"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        dl = _make_dl(tmp_path)
        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ChecksumError):
                await dl.verify_checksum(zip_path, "https://example.com/data.zip.CHECKSUM")

        # ZIP must be deleted after mismatch
        assert not zip_path.exists()

    @pytest.mark.asyncio
    async def test_404_checksum_warns_and_continues(self, tmp_path):
        """404 on checksum file → log warning, no exception."""
        zip_path = tmp_path / "data.zip"
        zip_path.write_bytes(b"any content")

        mock_response = MagicMock()
        mock_response.status_code = 404
        exc_404 = httpx.HTTPStatusError("404", request=MagicMock(), response=mock_response)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=exc_404)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        dl = _make_dl(tmp_path)
        with patch("httpx.AsyncClient", return_value=mock_client):
            # Should NOT raise
            await dl.verify_checksum(zip_path, "https://example.com/data.zip.CHECKSUM")

        # ZIP is preserved
        assert zip_path.exists()


# ---------------------------------------------------------------------------
# 5. extract_zip
# ---------------------------------------------------------------------------

class TestExtractZip:
    def test_extract_csv_returns_path(self, tmp_path):
        csv_content = "1,2,3\n4,5,6\n"
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("data.csv", csv_content)

        dl = _make_dl(tmp_path)
        result = dl.extract_zip(zip_path)

        assert result.name == "data.csv"
        assert result.read_text() == csv_content

    def test_extract_first_csv_when_multiple(self, tmp_path):
        zip_path = tmp_path / "multi.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("alpha.csv", "a,b\n")
            zf.writestr("beta.csv", "c,d\n")

        dl = _make_dl(tmp_path)
        result = dl.extract_zip(zip_path)
        # Should return the first CSV found
        assert result.suffix == ".csv"
        assert result.exists()

    def test_no_csv_raises_file_not_found(self, tmp_path):
        zip_path = tmp_path / "no_csv.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("data.txt", "hello")
            zf.writestr("readme.md", "# readme")

        dl = _make_dl(tmp_path)
        with pytest.raises(FileNotFoundError, match="No CSV file"):
            dl.extract_zip(zip_path)

    def test_csv_case_insensitive(self, tmp_path):
        zip_path = tmp_path / "upper.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("DATA.CSV", "x,y\n")

        dl = _make_dl(tmp_path)
        result = dl.extract_zip(zip_path)
        assert result.exists()


# ---------------------------------------------------------------------------
# 6. DATA_TYPE_AVAILABILITY sanity checks
# ---------------------------------------------------------------------------

class TestDataTypeAvailability:
    def test_known_types_present(self):
        expected = {
            "klines", "aggTrades", "trades", "bookTicker",
            "fundingRate", "bookDepth", "metrics",
            "indexPriceKlines", "markPriceKlines", "premiumIndexKlines",
        }
        assert expected.issubset(set(DATA_TYPE_AVAILABILITY.keys()))

    def test_funding_rate_monthly_only(self):
        has_daily, has_monthly = DATA_TYPE_AVAILABILITY["fundingRate"]
        assert not has_daily
        assert has_monthly

    def test_book_depth_daily_only(self):
        has_daily, has_monthly = DATA_TYPE_AVAILABILITY["bookDepth"]
        assert has_daily
        assert not has_monthly

    def test_klines_both_granularities(self):
        has_daily, has_monthly = DATA_TYPE_AVAILABILITY["klines"]
        assert has_daily
        assert has_monthly
