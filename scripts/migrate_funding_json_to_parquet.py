#!/usr/bin/env python3
"""Migrate funding-rate JSON files to Parquet format.

Scans ``~/.tino/data/funding_rates/*.json`` and writes a corresponding
Parquet file at ``{catalog_root}/data/funding_rate/{symbol}.parquet`` for
each JSON found.

Usage
-----
    uv run python scripts/migrate_funding_json_to_parquet.py
    uv run python scripts/migrate_funding_json_to_parquet.py --dry-run
    uv run python scripts/migrate_funding_json_to_parquet.py --delete-json

Arguments
---------
--dry-run       Preview files that would be converted; do not write Parquet.
--delete-json   Delete the source JSON file after a successful Parquet write
                (default: keep JSON for backward compatibility).
--catalog-root  Override the catalog root path (default: ~/.tino/data/catalog).
--json-dir      Override the JSON source directory (default: ~/.tino/data/funding_rates).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate funding-rate JSON files to Parquet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only — do not write any files.",
    )
    parser.add_argument(
        "--delete-json",
        action="store_true",
        help="Delete source JSON after successful Parquet write (default: keep).",
    )
    parser.add_argument(
        "--catalog-root",
        default=None,
        help="Parquet catalog root (default: ~/.tino/data/catalog).",
    )
    parser.add_argument(
        "--json-dir",
        default=None,
        help="Source JSON directory (default: ~/.tino/data/funding_rates).",
    )
    return parser.parse_args()


def _load_json(path: Path) -> list[dict]:
    """Load and validate a funding-rate JSON file. Returns list of records."""
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array, got {type(data).__name__}")
    return data


def _symbol_from_stem(stem: str) -> str:
    """Derive the symbol string from a JSON filename stem.

    Example: ``btcusdt-perp`` → ``btcusdt-perp`` (lowercase preserved).
    The stem is used as-is because write_funding_rate_parquet also lower-cases.
    """
    return stem


def migrate(
    json_dir: Path,
    catalog_root: Path,
    dry_run: bool,
    delete_json: bool,
) -> int:
    """Run the migration.

    Returns the number of files successfully converted.
    """
    from tinohelm.data.catalog import write_funding_rate_parquet, funding_rate_parquet_path

    json_files = sorted(json_dir.glob("*.json"))
    if not json_files:
        print(f"No JSON files found in {json_dir}")
        return 0

    print(f"Found {len(json_files)} JSON file(s) in {json_dir}")
    if dry_run:
        print("[dry-run] No files will be written.\n")

    converted = 0
    for json_path in json_files:
        symbol = _symbol_from_stem(json_path.stem)
        parquet_dest = funding_rate_parquet_path(symbol, catalog_root)

        try:
            records = _load_json(json_path)
        except Exception as exc:
            print(f"  SKIP  {json_path.name}: cannot read JSON — {exc}")
            continue

        print(
            f"  {'PREVIEW' if dry_run else 'CONVERT'}  {json_path.name}"
            f" ({len(records)} records) → {parquet_dest}"
        )

        if dry_run:
            converted += 1
            continue

        # Convert JSON records to the format expected by write_funding_rate_parquet
        # JSON format: {"funding_time_ms": int, "funding_rate": float}
        # write_funding_rate_parquet accepts plain dicts with these keys directly.
        try:
            write_funding_rate_parquet(
                records=records,
                symbol=symbol,
                catalog_root=catalog_root,
            )
        except Exception as exc:
            print(f"  ERROR  {json_path.name}: Parquet write failed — {exc}")
            continue

        converted += 1
        print(f"  OK     {parquet_dest}")

        if delete_json:
            try:
                json_path.unlink()
                print(f"  DEL    {json_path}")
            except OSError as exc:
                print(f"  WARN   Could not delete {json_path}: {exc}")

    print(f"\nDone: {converted}/{len(json_files)} file(s) {'previewed' if dry_run else 'converted'}.")
    return converted


def main() -> None:
    args = _parse_args()

    json_dir = Path(args.json_dir) if args.json_dir else Path.home() / ".tino" / "data" / "funding_rates"
    catalog_root = Path(args.catalog_root) if args.catalog_root else Path.home() / ".tino" / "data" / "catalog"

    if not json_dir.exists():
        print(f"JSON directory does not exist: {json_dir}")
        sys.exit(0)

    n = migrate(
        json_dir=json_dir,
        catalog_root=catalog_root,
        dry_run=args.dry_run,
        delete_json=args.delete_json,
    )
    sys.exit(0 if n >= 0 else 1)


if __name__ == "__main__":
    main()
