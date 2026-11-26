#!/usr/bin/env python3
"""schema_contract.py — Central schema contract and column introspection for projection tables and views.

Provides:
  - CANONICAL_PROJECTION_COLUMNS: declared, versioned set of columns for gcp_projection
  - CANONICAL_MAPPING_COLUMNS: declared columns for aws_li_to_gcp_li
  - CANONICAL_CATALOG_COLUMNS: declared columns for aws_li_catalog
  - projection_columns(con) -> set[str]: introspects PRAGMA table_info('gcp_projection'), cached per connection
  - mapping_columns(con) -> set[str]: introspects PRAGMA table_info('aws_li_to_gcp_li'), cached per connection
  - catalog_columns(con) -> set[str]: introspects PRAGMA table_info('aws_li_catalog'), cached per connection
  - table_columns(con, table_name) -> set[str]: general introspection, cached per connection
  - require_columns(con, table_or_view, needed_columns): asserts needed_columns are present,
    raises SchemaContractViolationError with a loud, readable message naming the missing column(s) and table/view.
  - clear_schema_cache(con=None): clears cache for a connection or globally.
"""
from __future__ import annotations
from typing import Iterable, Set
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Declared canonical schema contract for gcp_projection (versioned alongside projection_view.py)
CANONICAL_PROJECTION_COLUMNS = frozenset({
    "aws_li_key",
    "product",
    "aws_region",
    "gcp_region",
    "line_item_type",
    "pricing_model",
    "is_workload",
    "total_usage",
    "aws_amortized_cost",
    "strategy",
    "gcp_service",
    "gcp_sku_id",
    "component",
    "unit_multiplier",
    "passthrough_reason",
    "decided_by",
    "rate_region_basis",
    "rate_region_used",
    "license_status",
    "review_flags",
    "commitment_basis",
    "aws_cost_od_est",
    "aws_od_cost",
    "od_basis",
    "projection_note",
    "gcp_projected_cost",
    "gcp_cost_1yr_cud",
    "gcp_cost_3yr_cud",
})

# Declared canonical schema contract for aws_li_catalog (from ingest.py)
CANONICAL_CATALOG_COLUMNS = frozenset({
    "aws_li_key",
    "product",
    "usage_type",
    "operation",
    "aws_region",
    "gcp_region",
    "from_region",
    "to_region",
    "from_region_raw",
    "to_region_raw",
    "pricing_model",
    "line_item_type",
    "is_workload",
    "total_usage",
    "aws_amortized_cost",
    "projection_note",
    "instance_type",
    "instance_vcpus",
    "instance_ram_gb",
    "instance_arch",
    "instance_count",
    "billing_days",
    "aws_effective_unit_rate",
    "license_model",
    "operating_system",
    "database_engine",
    "deployment_option",
    "volume_type",
    "pricing_unit",
    "workload_class",
    "billing_format",
    "product_code",
    "billing_month",
    "aws_amortized_cost_native",
    "currency",
    "public_on_demand_cost",
    "aws_od_cost",
    "sp_ri_discount_pct",
    "od_basis",
    "source_line_items",
    "commitment_arn",
    "public_on_demand_rate",
    "savings_plan_rate",
})

# Canonical mapping columns imported dynamically from row_state if available
try:
    from row_state import FIELDS as _MAPPING_FIELDS
    CANONICAL_MAPPING_COLUMNS = frozenset(c.lower() for c in _MAPPING_FIELDS.keys())
except Exception:
    CANONICAL_MAPPING_COLUMNS = frozenset({
        "aws_li_key",
        "strategy",
        "gcp_service",
        "gcp_sku_id",
        "gcp_sku_name",
        "component",
        "unit_multiplier",
        "passthrough_reason",
        "decided_by",
        "projection_note",
        "confidence",
        "rate_source",
        "rate_region_basis",
        "rate_region_used",
        "license_status",
        "review_flags",
        "row_state",
    })

# Cache structure: dict[id(con), dict[table_name_lower, set[column_names_lower]]]
_SCHEMA_CACHE: dict[int, dict[str, set[str]]] = {}


class SchemaContractViolationError(Exception):
    """Raised when a table or view fails the required schema contract."""
    pass


def clear_schema_cache(con=None) -> None:
    """Clear cached table info for a given connection or globally."""
    if con is None:
        _SCHEMA_CACHE.clear()
    else:
        _SCHEMA_CACHE.pop(id(con), None)


def table_columns(con, table_name: str) -> set[str]:
    """Return lowercase column names for table_name, cached per connection.
    Returns empty set if table does not exist or PRAGMA fails.
    """
    cid = id(con)
    t_key = table_name.lower().strip()
    conn_cache = _SCHEMA_CACHE.setdefault(cid, {})
    if t_key in conn_cache:
        return conn_cache[t_key]

    try:
        rows = con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        cols = {r[1].lower() for r in rows}
    except Exception:
        cols = set()

    conn_cache[t_key] = cols
    return cols


def projection_columns(con) -> set[str]:
    """Return lowercase column names present in gcp_projection view."""
    return table_columns(con, "gcp_projection")


def mapping_columns(con) -> set[str]:
    """Return lowercase column names present in aws_li_to_gcp_li table."""
    return table_columns(con, "aws_li_to_gcp_li")


def catalog_columns(con) -> set[str]:
    """Return lowercase column names present in aws_li_catalog table."""
    return table_columns(con, "aws_li_catalog")


def require_columns(con, table_name: str, needed: Iterable[str]) -> set[str]:
    """Assert that table_name has all needed columns.
    Raises SchemaContractViolationError with a loud, informative error message if any are missing.
    Returns the set of present columns on success.
    """
    present = table_columns(con, table_name)
    needed_set = {c.lower() for c in needed}
    missing = needed_set - present
    if missing:
        missing_sorted = sorted(missing)
        present_sorted = sorted(present)
        raise SchemaContractViolationError(
            f"Schema contract violation: '{table_name}' is missing required column(s): "
            f"{', '.join(missing_sorted)}. "
            f"Present columns ({len(present_sorted)}): {', '.join(present_sorted) or '(none / table missing)'}"
        )
    return present
