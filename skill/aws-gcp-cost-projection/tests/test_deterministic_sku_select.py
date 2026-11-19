#!/usr/bin/env python3
"""
tests/test_deterministic_sku_select.py — Verification of deterministic _select,
Spot filtering (OD-vs-OD), and ambiguous-resolution diagnostics.

Covers:
- Part A: "spot" in _NOISE_QUALIFIERS prevents Spot SKUs from winning generic patterns
- Part B: stable tiebreaking (specificity -> lowest rate -> sku_id) regardless of input order
- Part C: ambiguous_sku_resolution diagnostic emitted when >=2 distinct rates survive
"""
import pytest
from unittest.mock import patch

from scripts import apply_static_mappings as sm


def test_select_determinism_order_independence():
    """Candidates presented in different order must yield the exact same winner."""
    # Synthetic candidates with identical region and pattern match
    c1 = {
        "skuId": "SKU-B",
        "description": "Custom Service Premium Tier in Iowa",
        "serviceRegions": ["us-central1"],
        "geoTaxonomy": {"type": "REGIONAL", "regions": ["us-central1"]},
        "category": {"resourceGroup": "Compute"},
        "pricingInfo": [{"pricingExpression": {"usageUnit": "h", "tieredRates": [{"unitPrice": {"units": 0, "nanos": 100000000}}]}}], # $0.10
    }
    c2 = {
        "skuId": "SKU-A",
        "description": "Custom Service Standard Tier in Iowa",
        "serviceRegions": ["us-central1"],
        "geoTaxonomy": {"type": "REGIONAL", "regions": ["us-central1"]},
        "category": {"resourceGroup": "Compute"},
        "pricingInfo": [{"pricingExpression": {"usageUnit": "h", "tieredRates": [{"unitPrice": {"units": 0, "nanos": 40000000}}]}}], # $0.04
    }
    c3 = {
        "skuId": "SKU-C",
        "description": "Custom Service Standard Tier in Iowa with Extra Feature",
        "serviceRegions": ["us-central1"],
        "geoTaxonomy": {"type": "REGIONAL", "regions": ["us-central1"]},
        "category": {"resourceGroup": "Compute"},
        "pricingInfo": [{"pricingExpression": {"usageUnit": "h", "tieredRates": [{"unitPrice": {"units": 0, "nanos": 40000000}}]}}], # $0.04 but less specific
    }

    # Simulate catalog loading with synthetic candidates
    with patch("os.path.exists", return_value=True), \
         patch.object(sm, "_load_services", return_value={"CustomService": "MOCK-SVC"}), \
         patch.object(sm, "_load_sku_file") as mock_load:
        
        # Order 1: [c1, c2, c3]
        mock_load.return_value = [c1, c2, c3]
        res1 = sm.lookup_sku_in_catalog("CustomService", "Custom Service", "us-central1")

        # Order 2: [c3, c1, c2]
        mock_load.return_value = [c3, c1, c2]
        res2 = sm.lookup_sku_in_catalog("CustomService", "Custom Service", "us-central1")

        # Order 3: [c2, c3, c1]
        mock_load.return_value = [c2, c3, c1]
        res3 = sm.lookup_sku_in_catalog("CustomService", "Custom Service", "us-central1")

        # In all orders, SKU-A must win:
        # 1. c1 and c2 have extra tokens: 2 ("tier", "iowa"). c3 has extra tokens: 5 ("tier", "iowa", "with", "extra", "feature").
        #    c3 is eliminated by specificity.
        # 2. c1 ($0.10) vs c2 ($0.04): c2 wins by lowest rate.
        assert res1[0] == "SKU-A"
        assert res2[0] == "SKU-A"
        assert res3[0] == "SKU-A"


def test_part_a_spot_filtered_by_noise_qualifiers():
    """Pattern matching both Spot and On-Demand SKUs must resolve to On-Demand (OD-vs-OD headline policy)."""
    # In real catalog for Kubernetes Engine, 'Autopilot Arm.*Pod CPU' matches:
    # - 0143-E959-575B: "Autopilot Arm Spot Pod CPU (us-central1)" ($0.01217)
    # - 4E00-09E3-D27C: "Autopilot Arm Pod CPU (us-central1)" ($0.03216)
    sku_id, unit, rg = sm.lookup_sku_in_catalog("Kubernetes Engine", r"Autopilot Arm.*Pod CPU", "us-central1")
    assert sku_id == "4E00-09E3-D27C"  # On-demand SKU
    assert sku_id != "0143-E959-575B"  # NOT Spot SKU


def test_part_b_standard_vs_premium_deterministic_choice():
    """When a generic pattern matches Standard + Premium, resolution is deterministic (lowest rate)."""
    # In real catalog for Dataproc:
    # - A9D4-0464-05DA: "Dataproc Serverless Shuffle Standard" ($0.04)
    # - AB51-B7BF-0C21: "Dataproc Serverless Shuffle Premium" ($0.10)
    sku_id, unit, rg = sm.lookup_sku_in_catalog("Dataproc", "Dataproc Serverless Shuffle", "us-central1")
    assert sku_id == "A9D4-0464-05DA"  # Standard tier ($0.04) wins over Premium ($0.10)


def test_part_c_ambiguous_resolution_diagnostic_emitted():
    """When a pass has >1 surviving candidate with >=2 distinct rates, ambiguous_sku_resolution is emitted."""
    diag_calls = []

    def mock_diag(level, code, source, message, **kwargs):
        diag_calls.append((level, code, source, message, kwargs))

    with patch.object(sm, "diag", side_effect=mock_diag):
        sku_id, _, _ = sm.lookup_sku_in_catalog("Dataproc", "Dataproc Serverless Shuffle", "us-central1")
        assert sku_id == "A9D4-0464-05DA"

    ambig_calls = [c for c in diag_calls if c[1] == "ambiguous_sku_resolution"]
    assert len(ambig_calls) == 1
    level, code, source, message, kwargs = ambig_calls[0]
    assert level == "warn"
    assert source == "apply_static_mappings.py"
    assert "Dataproc / 'Dataproc Serverless Shuffle' @ us-central1: 2 priced variants" in message
    assert "picked A9D4-0464-05DA ($0.04)" in message
    assert "AB51-B7BF-0C21 ($0.1)" in message
    assert kwargs.get("detail", {}).get("candidate_count") == 2
    assert kwargs.get("detail", {}).get("chosen_sku_id") == "A9D4-0464-05DA"


def test_part_c_unambiguous_resolution_emits_no_diagnostic():
    """Unambiguous resolution (only 1 candidate or all candidates have identical rate) emits no diagnostic."""
    diag_calls = []

    def mock_diag(level, code, source, message, **kwargs):
        diag_calls.append((level, code, source, message, kwargs))

    # A pinned/specific exact pattern in Cloud KMS (e.g. Active software symmetric key versions)
    with patch.object(sm, "diag", side_effect=mock_diag):
        sku_id, _, _ = sm.lookup_sku_in_catalog(
            "Cloud Key Management Service (KMS)",
            "^Active software symmetric key versions$",
            "global",
        )
        assert sku_id is not None

    ambig_calls = [c for c in diag_calls if c[1] == "ambiguous_sku_resolution"]
    assert len(ambig_calls) == 0
