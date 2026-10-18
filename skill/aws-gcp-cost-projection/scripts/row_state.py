#!/usr/bin/env python3
"""scripts/row_state.py — The single accessor for data/row_state_registry.json.

Every field on the mapping table aws_li_to_gcp_li is declared in the registry.
This module is the only place DDL and domain validation logic for that table live.
"""
from __future__ import annotations
import os
import sys
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_loader import load_data_config as _cfg

_REG = _cfg("row_state_registry") or {}
_META = _REG.get("_meta", {})
TABLE_NAME = _META.get("table", "aws_li_to_gcp_li")
FIELDS = _REG.get("fields", {})
NOTE_ORDER = _REG.get("note_order", [
    "passthrough_reason",
    "rate_source",
    "rate_region_basis",
    "license_status",
    "review_flags",
    "mapper_note"
])

RATE_SOURCE = FIELDS.get("rate_source", {}).get("domain", {})
RATE_REGION_BASIS = FIELDS.get("rate_region_basis", {}).get("domain", {})
LICENSE_STATUS = FIELDS.get("license_status", {}).get("domain", {})
REVIEW_FLAGS = FIELDS.get("review_flags", {}).get("domain", {})
PASSTHROUGH_REASON = FIELDS.get("passthrough_reason", {}).get("domain", {})
STRATEGY = FIELDS.get("strategy", {}).get("domain", {})

UNRESOLVED_RATE_SOURCES = tuple(FIELDS.get("rate_source", {}).get("groups", {}).get("unresolved", [
    "no_rate", "no_catalog", "word_overlap", "unknown"
]))
FALLBACK_REGION_BASES = tuple(FIELDS.get("rate_region_basis", {}).get("groups", {}).get("fallback", [
    "dominant", "cheapest_available"
]))


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Ensure table exists and all declared fields in registry are present.
    Idempotent. The ONLY place DDL for aws_li_to_gcp_li lives."""
    # Check if table exists
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if TABLE_NAME not in tables:
        col_defs = []
        for name, spec in FIELDS.items():
            stype = spec.get("sql_type", "TEXT")
            col_defs.append(f"{name} {stype}")
        cols_sql = ",\n            ".join(col_defs)
        con.execute(f"""
            CREATE TABLE {TABLE_NAME} (
            {cols_sql}
            )
        """)
        return

    # Table exists; check columns
    existing = {c[1].lower(): c[2].upper() for c in con.execute(f"PRAGMA table_info('{TABLE_NAME}')").fetchall()}
    for name, spec in FIELDS.items():
        if name.lower() not in existing:
            stype = spec.get("sql_type", "TEXT")
            con.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {name} {stype}")


def domain_of(field: str) -> dict:
    """Return dictionary of allowed values for an enum or flagset field."""
    return FIELDS.get(field, {}).get("domain", {})


def groups_of(field: str) -> dict:
    """Return named groups of values for a field."""
    return FIELDS.get(field, {}).get("groups", {})


def sql_in_list(values) -> str:
    """SQL literal list, e.g. "'a', 'b'". Safe: values come from JSON registry."""
    safe = [v for v in sorted(values) if str(v).replace("_", "").isalnum()]
    return ", ".join("'" + v + "'" for v in safe) or "''"


def eq(field: str, value: str) -> str:
    """Return predicate 'field = value'."""
    return f"{field} = '{value}'"


def in_group(field: str, group: str) -> str:
    """Return predicate for values in a named group, e.g. 'unresolved'."""
    members = groups_of(field).get(group, [])
    if not members:
        return f"{field} IN ('')"
    return f"{field} IN ({sql_in_list(members)})"


def flag_present_clause(column: str, flag: str) -> str:
    """SQL fragment: TRUE when `flag` is present in comma-wrapped flagset."""
    return f"COALESCE({column}, ',') LIKE '%,{flag},%'"


def flag_add_expr(column: str, flag: str) -> str:
    """SQL expression returning `column` with `flag` added. Idempotent."""
    return (
        f"CASE WHEN COALESCE({column}, ',') LIKE '%,{flag},%' THEN {column} "
        f"ELSE COALESCE(NULLIF({column}, ''), ',') || '{flag},' END"
    )


def flags_of(value) -> list[str]:
    """Parse comma-wrapped flag set into list in registry order."""
    present = set((value or "").strip(",").split(",")) - {""}
    return [f for f in REVIEW_FLAGS if f in present]


def validate_value(field: str, value, tier: str = "pipeline") -> tuple[bool, str | None]:
    """Validate value and writer tier for a field.
    Returns (is_valid, error_message)."""
    if field not in FIELDS:
        return False, f"Undeclared field '{field}' in row_state_registry"
    spec = FIELDS[field]
    allowed_tiers = spec.get("writable_by", [])
    if tier not in allowed_tiers:
        return False, f"Tier '{tier}' is not permitted to write field '{field}' (allowed: {allowed_tiers})"

    if value is None:
        return True, None

    kind = spec.get("kind")
    if kind == "enum":
        domain = spec.get("domain", {})
        if value not in domain:
            return False, f"Value '{value}' not in domain of '{field}' ({list(domain.keys())})"
    elif kind == "flagset":
        domain = spec.get("domain", {})
        tokens = [t for t in str(value).strip(",").split(",") if t]
        for t in tokens:
            if t not in domain:
                return False, f"Flag token '{t}' not in domain of '{field}' ({list(domain.keys())})"
    return True, None


def set_state(con: duckdb.DuckDBPyConnection, keys: list[str] | str, by: str,
              tier: str = "pipeline", raise_on_error: bool = False, **kwargs) -> None:
    """Set state columns for rows matching keys.
    Validates domain and writer tier. Stamps decided_by if provided."""
    if isinstance(keys, str):
        keys = [keys]
    if not keys:
        return

    assignments = []
    params = []
    for field, val in kwargs.items():
        ok, err = validate_value(field, val, tier=tier)
        if not ok:
            if raise_on_error or "pytest" in sys.modules:
                raise ValueError(f"set_state error: {err}")
            print(f"WARNING: row_state.set_state: {err} — ignoring field {field}", file=sys.stderr)
            continue
        assignments.append(f"{field} = ?")
        params.append(val)

    if by:
        assignments.append("decided_by = ?")
        params.append(by)

    if not assignments:
        return

    placeholders = ",".join(["?"] * len(keys))
    assign_sql = ", ".join(assignments)
    sql = f"UPDATE {TABLE_NAME} SET {assign_sql} WHERE aws_li_key IN ({placeholders})"
    con.execute(sql, params + keys)
