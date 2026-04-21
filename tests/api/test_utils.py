"""Tests for tinohelm.api._utils — shared helpers for API routes."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from tinohelm.api._utils import (
    HEX_PREFIX_RE,
    MIN_PREFIX_LEN,
    UUID_RE,
    decode_redis_str,
    fetch_redis_progress,
    fetch_redis_progress_batch,
    is_full_uuid,
    load_redis_json,
    resolve_artifact_path,
    validate_uuid_or_400,
)


_VALID_UUID = "ac5ef009-1234-5678-abcd-ef0123456789"


# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------


class TestUuidRegex:
    def test_matches_canonical_lowercase(self):
        assert UUID_RE.match(_VALID_UUID)

    def test_rejects_uppercase(self):
        assert UUID_RE.match(_VALID_UUID.upper()) is None

    def test_rejects_missing_hyphens(self):
        assert UUID_RE.match(_VALID_UUID.replace("-", "")) is None

    def test_rejects_wrong_section_lengths(self):
        assert UUID_RE.match("ac5ef009-1234-5678-abcd-ef01234567890") is None  # 13 in last

    def test_rejects_non_hex(self):
        assert UUID_RE.match("zzzzzzzz-1234-5678-abcd-ef0123456789") is None

    def test_rejects_leading_trailing_whitespace(self):
        assert UUID_RE.match(f" {_VALID_UUID}") is None
        assert UUID_RE.match(f"{_VALID_UUID} ") is None


class TestHexPrefixRegex:
    def test_matches_pure_hex(self):
        assert HEX_PREFIX_RE.match("ac5ef009")

    def test_rejects_empty_string(self):
        assert HEX_PREFIX_RE.match("") is None

    def test_rejects_non_hex(self):
        assert HEX_PREFIX_RE.match("xyz") is None
        assert HEX_PREFIX_RE.match("ac5-ef") is None

    def test_min_prefix_len_constant(self):
        assert MIN_PREFIX_LEN == 4


# ---------------------------------------------------------------------------
# UUID validation helpers
# ---------------------------------------------------------------------------


class TestIsFullUuid:
    def test_accepts_valid_uuid(self):
        assert is_full_uuid(_VALID_UUID) is True

    def test_rejects_uppercase(self):
        assert is_full_uuid(_VALID_UUID.upper()) is False

    def test_rejects_prefix(self):
        assert is_full_uuid("ac5ef009") is False


class TestValidateUuidOr400:
    def test_returns_lowercased_on_valid(self):
        assert validate_uuid_or_400(_VALID_UUID) == _VALID_UUID

    def test_trims_and_lowercases(self):
        assert validate_uuid_or_400(f"  {_VALID_UUID.upper()}  ") == _VALID_UUID

    def test_raises_400_on_invalid(self):
        with pytest.raises(HTTPException) as exc:
            validate_uuid_or_400("not-a-uuid")
        assert exc.value.status_code == 400
        assert "Invalid run_id format" in exc.value.detail

    def test_raises_400_on_empty(self):
        with pytest.raises(HTTPException) as exc:
            validate_uuid_or_400("")
        assert exc.value.status_code == 400

    def test_raises_400_on_prefix(self):
        with pytest.raises(HTTPException):
            validate_uuid_or_400("ac5ef009")


# ---------------------------------------------------------------------------
# Artifact path resolution
# ---------------------------------------------------------------------------


class TestResolveArtifactPath:
    def test_resolves_run_dir_inside_root(self, tmp_path: Path):
        result = resolve_artifact_path(tmp_path, _VALID_UUID)
        assert result == (tmp_path / _VALID_UUID).resolve()

    def test_resolves_with_child_segments(self, tmp_path: Path):
        result = resolve_artifact_path(tmp_path, _VALID_UUID, "results.json")
        assert result == (tmp_path / _VALID_UUID / "results.json").resolve()

    def test_resolves_multiple_segments(self, tmp_path: Path):
        result = resolve_artifact_path(tmp_path, _VALID_UUID, "sub", "file.csv")
        assert result == (tmp_path / _VALID_UUID / "sub" / "file.csv").resolve()

    def test_rejects_non_uuid(self, tmp_path: Path):
        with pytest.raises(HTTPException) as exc:
            resolve_artifact_path(tmp_path, "not-a-uuid", "x.json")
        assert exc.value.status_code == 400
        assert "Invalid run_id format" in exc.value.detail

    def test_rejects_empty_run_id(self, tmp_path: Path):
        with pytest.raises(HTTPException) as exc:
            resolve_artifact_path(tmp_path, "", "x.json")
        assert exc.value.status_code == 400

    def test_rejects_dotdot_in_segment(self, tmp_path: Path):
        """Path traversal via ``..`` segments must raise 400 Invalid path."""
        with pytest.raises(HTTPException) as exc:
            resolve_artifact_path(tmp_path, _VALID_UUID, "..", "..", "etc", "passwd")
        assert exc.value.status_code == 400
        assert "Invalid path" in exc.value.detail

    def test_rejects_absolute_path_segment(self, tmp_path: Path):
        """``/absolute/path`` inside a segment must land outside the root."""
        with pytest.raises(HTTPException) as exc:
            resolve_artifact_path(tmp_path, _VALID_UUID, "/etc/passwd")
        assert exc.value.status_code == 400
        assert "Invalid path" in exc.value.detail

    def test_accepts_str_and_path_for_root(self, tmp_path: Path):
        a = resolve_artifact_path(str(tmp_path), _VALID_UUID)
        b = resolve_artifact_path(tmp_path, _VALID_UUID)
        assert a == b

    def test_symlink_escape_is_resolved(self, tmp_path: Path):
        """A symlink escaping the artifacts root must trigger the boundary check."""
        # Create artifact root + symlink pointing outside
        root = tmp_path / "artifacts"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        run_dir = root / _VALID_UUID
        run_dir.symlink_to(outside, target_is_directory=True)

        with pytest.raises(HTTPException) as exc:
            resolve_artifact_path(root, _VALID_UUID, "file.json")
        assert exc.value.status_code == 400

    def test_upper_case_uuid_normalised(self, tmp_path: Path):
        result = resolve_artifact_path(tmp_path, _VALID_UUID.upper())
        assert result.name == _VALID_UUID  # lowercased


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------


class TestDecodeRedisStr:
    def test_none_returns_none(self):
        assert decode_redis_str(None) is None

    def test_bytes_decoded_to_str(self):
        assert decode_redis_str(b"hello") == "hello"

    def test_str_passthrough(self):
        assert decode_redis_str("hello") == "hello"

    def test_invalid_utf8_returns_none(self):
        assert decode_redis_str(b"\xff\xfe\xfd") is None

    def test_int_coerced_to_str(self):
        assert decode_redis_str(42) == "42"


class TestLoadRedisJson:
    @pytest.mark.asyncio
    async def test_returns_decoded_json(self):
        rds = AsyncMock()
        rds.get.return_value = '{"a": 1, "b": [2, 3]}'
        result = await load_redis_json(rds, "key", default=None)
        assert result == {"a": 1, "b": [2, 3]}
        rds.get.assert_called_once_with("key")

    @pytest.mark.asyncio
    async def test_returns_default_on_missing(self):
        rds = AsyncMock()
        rds.get.return_value = None
        default = {"status": "offline"}
        result = await load_redis_json(rds, "key", default=default)
        assert result is default

    @pytest.mark.asyncio
    async def test_returns_default_on_invalid_json(self):
        rds = AsyncMock()
        rds.get.return_value = "not json at all"
        default = {"x": 1}
        result = await load_redis_json(rds, "key", default=default)
        assert result is default

    @pytest.mark.asyncio
    async def test_accepts_bytes_from_redis(self):
        rds = AsyncMock()
        rds.get.return_value = b'{"k": "v"}'
        assert await load_redis_json(rds, "key") == {"k": "v"}

    @pytest.mark.asyncio
    async def test_default_is_none_by_default(self):
        rds = AsyncMock()
        rds.get.return_value = None
        assert await load_redis_json(rds, "key") is None

    @pytest.mark.asyncio
    async def test_invalid_utf8_returns_default(self):
        rds = AsyncMock()
        rds.get.return_value = b"\xff\xfe"
        default = {"d": 1}
        assert await load_redis_json(rds, "key", default=default) is default

    @pytest.mark.asyncio
    async def test_parses_lists_and_nulls(self):
        rds = AsyncMock()
        rds.get.return_value = '[1, null, "x"]'
        assert await load_redis_json(rds, "key") == [1, None, "x"]


class TestFetchRedisProgress:
    @pytest.mark.asyncio
    async def test_returns_int(self):
        rds = AsyncMock()
        rds.get.return_value = "42"
        assert await fetch_redis_progress(rds, "key") == 42

    @pytest.mark.asyncio
    async def test_bytes_coerced(self):
        rds = AsyncMock()
        rds.get.return_value = b"75"
        assert await fetch_redis_progress(rds, "key") == 75

    @pytest.mark.asyncio
    async def test_none_on_missing(self):
        rds = AsyncMock()
        rds.get.return_value = None
        assert await fetch_redis_progress(rds, "key") is None

    @pytest.mark.asyncio
    async def test_none_on_non_numeric(self):
        rds = AsyncMock()
        rds.get.return_value = "not-a-number"
        assert await fetch_redis_progress(rds, "key") is None

    @pytest.mark.asyncio
    async def test_negative_int_passes_through(self):
        rds = AsyncMock()
        rds.get.return_value = "-5"
        assert await fetch_redis_progress(rds, "key") == -5

    @pytest.mark.asyncio
    async def test_float_string_rejected(self):
        """int() raises on float strings; caller gets None, not a coerced int."""
        rds = AsyncMock()
        rds.get.return_value = "42.5"
        assert await fetch_redis_progress(rds, "key") is None


class TestFetchRedisProgressBatch:
    @pytest.mark.asyncio
    async def test_empty_keys_returns_empty_list_without_pipeline(self):
        rds = AsyncMock()
        result = await fetch_redis_progress_batch(rds, [])
        assert result == []
        rds.pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_ints_in_order(self):
        rds = MagicMock()
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=["10", "20", "30"])
        rds.pipeline.return_value = pipe

        result = await fetch_redis_progress_batch(rds, ["a", "b", "c"])
        assert result == [10, 20, 30]
        # Each key was queued once
        assert pipe.get.call_count == 3
        pipe.get.assert_any_call("a")
        pipe.get.assert_any_call("b")
        pipe.get.assert_any_call("c")

    @pytest.mark.asyncio
    async def test_missing_slots_become_none(self):
        rds = MagicMock()
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[None, "42", b"99"])
        rds.pipeline.return_value = pipe

        result = await fetch_redis_progress_batch(rds, ["k1", "k2", "k3"])
        assert result == [None, 42, 99]

    @pytest.mark.asyncio
    async def test_non_numeric_becomes_none(self):
        rds = MagicMock()
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=["abc", "42"])
        rds.pipeline.return_value = pipe

        result = await fetch_redis_progress_batch(rds, ["k1", "k2"])
        assert result == [None, 42]

    @pytest.mark.asyncio
    async def test_single_key_uses_pipeline(self):
        rds = MagicMock()
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=["5"])
        rds.pipeline.return_value = pipe

        result = await fetch_redis_progress_batch(rds, ["only"])
        assert result == [5]
        rds.pipeline.assert_called_once()
