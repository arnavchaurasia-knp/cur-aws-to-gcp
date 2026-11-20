#!/usr/bin/env python3
"""
sku_catalog.py — thin back-compat shim over catalog_db (the catalog-read SSOT).

The catalog data-access layer was consolidated into catalog_db.py (S2,
WORKORDER_sku_catalog_consolidation.md): ONE cached read-only connection plus the
query primitives (query_skus = list-all candidates, all_tiers = tier schedule).
This module re-exports them so existing callers/tests keep working; new code
should import catalog_db directly.
"""
from __future__ import annotations

from catalog_db import (  # noqa: F401
    CATALOG_DB,
    CatalogUnavailable,
    SkuCandidate,
    all_tiers,
    catalog_exists,
    get_connection as _connect,  # legacy name
    query_skus,
)

__all__ = [
    "CATALOG_DB",
    "CatalogUnavailable",
    "SkuCandidate",
    "all_tiers",
    "catalog_exists",
    "query_skus",
]
