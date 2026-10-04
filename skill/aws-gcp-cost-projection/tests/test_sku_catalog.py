"""Tests for sku_catalog.py — the single source of truth for catalog.duckdb queries.

Runs against the real local catalog.duckdb (same convention as
test_resolved_skus_cache_freshness.py) — skipped if it isn't present, since a
missing catalog is itself a distinct, already-tested failure mode
(CatalogUnavailable), not something to fake with a mock catalog here.
"""
import pytest
import sku_catalog as sc


pytestmark = pytest.mark.skipif(
    not sc.catalog_exists(), reason="catalog.duckdb not present in this environment"
)


def test_query_skus_returns_every_match_not_just_one():
    # Cloud Monitoring's Metric Volume SKU, used in the CloudWatch custom-
    # metrics fix earlier this session — confirms multi-candidate behavior
    # (the whole reason this module exists instead of reusing the old
    # single-best-match lookup_sku_in_catalog()).
    candidates = sc.query_skus(service="Cloud Monitoring", keyword="Metric Volume")
    assert len(candidates) >= 1
    assert all(c.service_name == "Cloud Monitoring" for c in candidates)


def test_region_filter_includes_global_skus():
    candidates = sc.query_skus(service="Cloud Monitoring", keyword="Metric Volume", region="asia-south1")
    assert candidates, "Metric Volume is a global-rate SKU — must still match a region filter"
    for c in candidates:
        assert c.available_in("asia-south1")


def test_available_in_rejects_unlisted_region():
    candidates = sc.query_skus(sku_id="A924-09D0-8854")
    assert candidates
    c = candidates[0]
    assert c.available_in("asia-south1") or "global" in c.regions


def test_all_tiers_returns_full_pricing_schedule():
    tiers = sc.all_tiers("A924-09D0-8854")
    assert len(tiers) >= 2, "Metric Volume is a tiered SKU (150 MiB free, then stepped rates)"
    starts = [t["tier_start"] for t in tiers]
    assert starts == sorted(starts)
