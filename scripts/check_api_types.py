#!/usr/bin/env python3
"""Validate Rust serde structs against FastAPI OpenAPI schema.

Usage:
    # With a running API server:
    python scripts/check_api_types.py --url http://localhost:8000

    # With a saved openapi.json file:
    python scripts/check_api_types.py --file openapi.json

Checks that every Rust response struct in cli/src/types.rs has a matching
OpenAPI schema definition with compatible fields.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Rust struct name -> OpenAPI schema name mapping
RUST_TO_OPENAPI: dict[str, str] = {
    "BacktestRunItem": "BacktestRunItem",
    "BacktestRunList": "BacktestRunList",
    "BacktestRunStatus": "BacktestRunStatus",
    "BacktestRunResponse": "BacktestRunResponse",
    "BacktestCancelResponse": "BacktestCancelResponse",
    "RescanResult": "RescanResponse",
    "ValidateResult": "ValidateResponse",
}

# Rust type -> JSON Schema type mapping
RUST_TYPE_COMPAT: dict[str, set[str]] = {
    "String": {"string"},
    "u8": {"integer"},
    "u32": {"integer"},
    "u64": {"integer"},
    "i32": {"integer"},
    "i64": {"integer"},
    "f64": {"number"},
    "bool": {"boolean"},
    "serde_json::Value": {"object", "array", "string", "number", "integer", "boolean"},
}


def parse_rust_structs(types_rs: str) -> dict[str, dict[str, str]]:
    """Parse Rust struct definitions from types.rs.

    Returns: {StructName: {field_name: rust_type, ...}, ...}
    """
    structs: dict[str, dict[str, str]] = {}
    current_struct: str | None = None
    fields: dict[str, str] = {}

    for line in types_rs.splitlines():
        line = line.strip()

        # Match struct declaration
        m = re.match(r"pub struct (\w+)\s*\{", line)
        if m:
            current_struct = m.group(1)
            fields = {}
            continue

        if current_struct and line == "}":
            structs[current_struct] = fields
            current_struct = None
            continue

        if current_struct:
            # Match field: pub field_name: Type,
            fm = re.match(r"pub (\w+):\s*(.+?),?\s*$", line)
            if fm:
                field_name = fm.group(1)
                rust_type = fm.group(2).strip().rstrip(",")
                fields[field_name] = rust_type

    return structs


def unwrap_option(rust_type: str) -> tuple[str, bool]:
    """Unwrap Option<T> -> (T, True) or (T, False) if not optional."""
    m = re.match(r"Option<(.+)>", rust_type)
    if m:
        return m.group(1), True
    return rust_type, False


def check_struct(
    struct_name: str,
    rust_fields: dict[str, str],
    schema: dict,
    all_schemas: dict,
) -> list[str]:
    """Check a Rust struct against an OpenAPI schema definition."""
    issues: list[str] = []

    schema_props = schema.get("properties", {})
    required = set(schema.get("required", []))

    for field_name, rust_type in rust_fields.items():
        # Handle serde rename
        api_name = field_name
        if field_name == "strategy_type":
            api_name = "type"

        if api_name not in schema_props:
            issues.append(f"  {struct_name}.{field_name}: not in OpenAPI schema")
            continue

        inner_type, is_optional = unwrap_option(rust_type)

        # Check required vs optional
        if api_name in required and is_optional:
            issues.append(
                f"  {struct_name}.{field_name}: Rust has Option but OpenAPI requires it"
            )

    # Check for OpenAPI fields missing from Rust
    for api_field in schema_props:
        rust_name = api_field
        if api_field == "type":
            rust_name = "strategy_type"
        if rust_name not in rust_fields:
            if api_field in required:
                issues.append(
                    f"  {struct_name}: missing required field '{api_field}' from OpenAPI"
                )

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Rust types against OpenAPI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="API base URL to fetch /openapi.json")
    group.add_argument("--file", help="Path to openapi.json file")
    parser.add_argument(
        "--types-rs",
        default="cli/src/types.rs",
        help="Path to Rust types.rs file",
    )
    args = parser.parse_args()

    # Load OpenAPI spec
    if args.url:
        import urllib.request

        url = f"{args.url.rstrip('/')}/openapi.json"
        with urllib.request.urlopen(url) as resp:
            spec = json.loads(resp.read())
    else:
        spec = json.loads(Path(args.file).read_text())

    # Load Rust types
    types_path = Path(args.types_rs)
    if not types_path.exists():
        print(f"ERROR: {types_path} not found")
        return 1

    rust_structs = parse_rust_structs(types_path.read_text())

    schemas = spec.get("components", {}).get("schemas", {})

    all_issues: list[str] = []
    checked = 0

    for rust_name, openapi_name in RUST_TO_OPENAPI.items():
        if rust_name not in rust_structs:
            all_issues.append(f"{rust_name}: not found in types.rs")
            continue
        if openapi_name not in schemas:
            all_issues.append(f"{rust_name}: OpenAPI schema '{openapi_name}' not found")
            continue

        checked += 1
        issues = check_struct(
            rust_name, rust_structs[rust_name], schemas[openapi_name], schemas
        )
        all_issues.extend(issues)

    print(f"Checked {checked}/{len(RUST_TO_OPENAPI)} struct pairs")

    if all_issues:
        print(f"\n{len(all_issues)} issue(s) found:")
        for issue in all_issues:
            print(f"  - {issue}")
        return 1

    print("All checks passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
