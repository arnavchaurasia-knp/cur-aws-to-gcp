#!/usr/bin/env python3
"""
catalog_db.py — the single data-access layer (repository) for catalog.duckdb.

Industry-standard repository pattern: ONE cached read-only connection to the GCP
SKU catalog, plus the raw query primitives. Higher layers build on this:
  * resolve_sku.py  — resolution logic (scoring, _select, unit guard) — delegates
    its connection here (get_connection) rather than opening its own.
  * sku_catalog.py  — thin back-compat shim re-exporting query_skus/all_tiers.
  * apply_rates / incremental_rerate / catalog_manager — to be migrated here
    (parity-locked; they read rate joins that feed dollars). See
    WORKORDER_sku_catalog_consolidation.md.

Only ONE module opens catalog.duckdb; everything else calls this. The connection
is read-only and cached across a run (opening it per call was a prior wart in
sku_catalog.py).

catalog.duckdb schema (built by build_catalog_index.py):
  skus:         sku_id, service_name, resource_family, resource_group,
                usage_type, description, usage_unit, service_regions VARCHAR[]
  tiered_rates: sku_id, tier_start, rate_usd
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import duckdb

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.environ.get("SKILL_DIR", os.path.dirname(_SCRIPTS_DIR))
_DATA_DIR = os.path.join(_SKILL_DIR, "data")
CATALOG_DB = os.path.join(_DATA_DIR, "catalog.duckdb")

_CONN: duckdb.DuckDBPyConnection | None = None
_CACHED_PATH: str | None = None


class CatalogUnavailable(RuntimeError):
    """Raised when catalog.duckdb is missing — callers must not silently treat
    "no candidates" the same as "catalog not found"; the taxonomy
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


def catalog_exists() -> bool:
    return os.path.exists(CATALOG_DB)


def get_connection(db_path: str | None = None) -> duckdb.DuckDBPyConnection | None:
    """Return the ONE cached read-only connection to catalog.duckdb, or None if
    the catalog file is unavailable. Liveness-checked and re-opened if the cached
    handle has gone stale or if the target path changed (e.g. in test fixtures).
    This is the single connection the whole pipeline shares for catalog reads."""
    global _CONN, _CACHED_PATH
    target = db_path or CATALOG_DB
    if _CONN is not None and _CACHED_PATH == target:
        try:
            _CONN.execute("SELECT 1")
            return _CONN
        except Exception:
            _CONN = None

    if _CONN is not None and _CACHED_PATH != target:
        try:
            _CONN.close()
        except Exception:
            pass
        _CONN = None

    if not os.path.exists(target):
        return None
    try:
        _CONN = duckdb.connect(target, read_only=True)
        _CACHED_PATH = target
        return _CONN
    except Exception:
        return None


def close_connection() -> None:
    """Close the cached connection if open (end-of-run cleanup)."""
    global _CONN, _CACHED_PATH
    if _CONN is not None:
        try:
            _CONN.close()
        except Exception:
            pass
        _CONN = None
    _CACHED_PATH = None


def _require_connection() -> duckdb.DuckDBPyConnection:
    """Connection for fail-closed callers (list-all / tiers): raises
    CatalogUnavailable rather than returning None, preserving sku_catalog's
    contract where "catalog missing" must be distinguishable from "no matches"."""
    conn = get_connection()
    if conn is None:
        raise CatalogUnavailable(
            f"catalog.duckdb not found at {CATALOG_DB} — run "
            f"scripts/build-catalog-index.sh (or refresh-catalog.sh) first"
        )
    return conn


def query_skus(
    service: str | None = None,
    region: str | None = None,
    keyword: str | None = None,
    usage_type: str | None = None,
    resource_group: str | None = None,
    sku_id: str | None = None,
) -> list[SkuCandidate]:
    """Return EVERY matching SKU (not a single best guess) — the list-all
    primitive resolve_sku intentionally lacks. All filters optional; omitting
    `region` returns candidates in every region (use SkuCandidate.available_in on
    the caller side, or pass region to filter server-side)."""
    con = _require_connection()
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

    return [
        SkuCandidate(
            sku_id=r[0], service_name=r[1], resource_group=r[2], usage_type=r[3],
            regions=list(r[4] or []), description=r[5], usage_unit=r[6], rate_usd=r[7],
        )
        for r in rows
    ]


def all_tiers(sku_id: str) -> list[dict]:
    """Full pricing-tier schedule for one SKU: [{"tier_start": float, "rate_usd": float}, ...]."""
    con = _require_connection()
    rows = con.execute(
        "SELECT tier_start, rate_usd FROM tiered_rates WHERE sku_id = ? ORDER BY tier_start",
        [sku_id],
    ).fetchall()
    return [{"tier_start": r[0], "rate_usd": r[1]} for r in rows]
