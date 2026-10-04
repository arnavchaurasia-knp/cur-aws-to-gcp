#!/usr/bin/env python3
"""
sku_catalog.py — single source of truth for querying catalog.duckdb.

Why this exists
----------------
Two parallel SKU-lookup implementations existed before this file: resolve_sku()
/lookup_sku_in_catalog() in apply_static_mappings.py (reads data/skus/*.json.gz,
returns ONE best-guess match) and find-sku.sh (reads catalog.duckdb, returns
EVERY match as TSV, used by the LLM during manual mapping). They don't share a
data source and can silently drift apart. This module is the one place that
queries catalog.duckdb; both find-sku.sh and the CUR Line Analysis Tool's
verification scripts should go through it instead of writing their own SQL.

catalog.duckdb schema (built by build_catalog_index.py):
  skus:         sku_id, service_name, resource_family, resource_group,
                usage_type, description, usage_unit, service_regions VARCHAR[]
  tiered_rates: sku_id, tier_start, rate_usd
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import duckdb

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.environ.get("SKILL_DIR", os.path.dirname(_SCRIPTS_DIR))
_DATA_DIR = os.path.join(_SKILL_DIR, "data")
CATALOG_DB = os.path.join(_DATA_DIR, "catalog.duckdb")


class CatalogUnavailable(RuntimeError):
    """Raised when catalog.duckdb is missing — callers must not silently
    treat "no candidates" the same as "catalog not found"; the taxonomy
    (UNKNOWN_ANOMALY vs a real coverage gap) depends on telling them apart."""


@dataclass
class SkuCandidate:
    sku_id: str
    service_name: str
    resource_group: str
    usage_type: str
    regions: list[str]
    description: str
    rate_usd: float  # first non-zero tier; use all_tiers() for the full schedule
    usage_unit: str

    def available_in(self, region: str) -> bool:
        return region in self.regions or "global" in self.regions


def _connect() -> duckdb.DuckDBPyConnection:
    if not os.path.exists(CATALOG_DB):
        raise CatalogUnavailable(
            f"catalog.duckdb not found at {CATALOG_DB} — run "
            f"scripts/build-catalog-index.sh (or refresh-catalog.sh) first"
        )
    return duckdb.connect(CATALOG_DB, read_only=True)


def query_skus(
    service: str | None = None,
    region: str | None = None,
    keyword: str | None = None,
    usage_type: str | None = None,
    resource_group: str | None = None,
    sku_id: str | None = None,
) -> list[SkuCandidate]:
    """Return EVERY matching SKU (not a single best guess) — the same shape
    find-sku.sh produces, so both go through one query. All filters optional;
    omitting `region` returns candidates in every region (use available_in()
    on the caller side, or pass region to filter server-side)."""
    con = _connect()
    try:
        preds, params = ["1=1"], []
        if service:
            preds.append("s.service_name = ?")
            params.append(service)
        if region:
            preds.append("(list_contains(s.service_regions, ?) OR list_contains(s.service_regions, 'global'))")
            params.append(region)
        if keyword:
            preds.append("regexp_matches(s.description, ?, 'i')")
            params.append(keyword)
        if usage_type:
            preds.append("s.usage_type = ?")
            params.append(usage_type)
        if resource_group:
            preds.append("s.resource_group = ?")
            params.append(resource_group)
        if sku_id:
            preds.append("s.sku_id = ?")
            params.append(sku_id)

        rows = con.execute(
            f"""
            SELECT s.sku_id, s.service_name, s.resource_group, s.usage_type,
                   s.service_regions, s.description, s.usage_unit,
                   COALESCE(MIN(t.rate_usd) FILTER (WHERE t.rate_usd > 0), 0.0) AS rate_usd
            FROM skus s
            LEFT JOIN tiered_rates t ON t.sku_id = s.sku_id
            WHERE {' AND '.join(preds)}
            GROUP BY s.sku_id, s.service_name, s.resource_group, s.usage_type,
                     s.service_regions, s.description, s.usage_unit
            ORDER BY s.service_name, s.description
            """,
            params,
        ).fetchall()
    finally:
        con.close()

    return [
        SkuCandidate(
            sku_id=r[0], service_name=r[1], resource_group=r[2], usage_type=r[3],
            regions=list(r[4] or []), description=r[5], usage_unit=r[6], rate_usd=r[7],
        )
        for r in rows
    ]


def all_tiers(sku_id: str) -> list[dict]:
    """Full pricing-tier schedule for one SKU: [{"tier_start": float, "rate_usd": float}, ...]."""
    con = _connect()
    try:
        rows = con.execute(
            "SELECT tier_start, rate_usd FROM tiered_rates WHERE sku_id = ? ORDER BY tier_start",
            [sku_id],
        ).fetchall()
    finally:
        con.close()
    return [{"tier_start": r[0], "rate_usd": r[1]} for r in rows]


def catalog_exists() -> bool:
    return os.path.exists(CATALOG_DB)
