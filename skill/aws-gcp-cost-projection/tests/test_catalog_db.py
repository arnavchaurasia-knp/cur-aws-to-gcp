"""Tests for catalog_db.py — the single data-access repository for catalog.duckdb (Subsystem S2)."""
import os
from unittest.mock import patch
import pytest

import catalog_db


def test_catalog_exists():
    assert catalog_db.catalog_exists() == os.path.exists(catalog_db.CATALOG_DB)


@pytest.mark.skipif(not catalog_db.catalog_exists(), reason="catalog.duckdb not present")
def test_get_connection_singleton():
    c1 = catalog_db.get_connection()
    c2 = catalog_db.get_connection()
    assert c1 is not None
    assert c1 is c2, "get_connection() must return the same cached connection singleton"

    # Verify connection is live
    res = c1.execute("SELECT 1").fetchone()
    assert res == (1,)


@pytest.mark.skipif(not catalog_db.catalog_exists(), reason="catalog.duckdb not present")
def test_close_and_reopen_connection():
    c1 = catalog_db.get_connection()
    assert c1 is not None
    catalog_db.close_connection()
    assert catalog_db._CONN is None

    c2 = catalog_db.get_connection()
    assert c2 is not None
    assert c2.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.skipif(not catalog_db.catalog_exists(), reason="catalog.duckdb not present")
def test_query_skus_and_tiers():
    candidates = catalog_db.query_skus(service="Cloud Monitoring", keyword="Metric Volume")
    assert len(candidates) >= 1
    c = candidates[0]
    assert c.service_name == "Cloud Monitoring"
    assert isinstance(c, catalog_db.SkuCandidate)
    assert c.available_in("asia-south1") or "global" in c.regions

    tiers = catalog_db.all_tiers(c.sku_id)
    assert len(tiers) >= 1
    assert "rate_usd" in tiers[0]
    assert "tier_start" in tiers[0]


def test_catalog_unavailable():
    with patch.object(catalog_db, "CATALOG_DB", "/nonexistent/catalog.duckdb"):
        with patch.object(catalog_db, "_CONN", None):
            assert catalog_db.catalog_exists() is False
            assert catalog_db.get_connection() is None
            with pytest.raises(catalog_db.CatalogUnavailable):
                catalog_db._require_connection()
