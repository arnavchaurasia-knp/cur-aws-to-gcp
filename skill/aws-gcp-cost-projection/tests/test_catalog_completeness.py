"""Unit tests for catalog completeness roll-up and partial-fetch safety.
WORKORDER_refresh_observability_and_completeness.md Phase 3 & 4.
"""

import gzip
import json
import os
import shutil
import tempfile
import pytest

from scripts.catalog_manager import (
    compute_catalog_summary,
    integrity_check_file,
    _scan_source,
)


def test_compute_catalog_summary_complete():
    results = {
        "svc-compute": {"name": "Compute Engine", "status": "OK", "count": 500, "priced": 500, "issues": []},
        "svc-storage": {"name": "Cloud Storage", "status": "OK", "count": 200, "priced": 200, "issues": []},
        "svc-bigquery": {"name": "BigQuery", "status": "OK", "count": 100, "priced": 100, "issues": []},
    }
    summary = compute_catalog_summary(results, expected_count=3, threshold=100.0, prior_record_count=750)
    assert summary["source"] == "gcp_catalog"
    assert summary["expected_count"] == 3
    assert summary["fetched_count"] == 3
    assert summary["coverage_pct"] == 100.0
    assert summary["complete"] is True
    assert summary["state"] == "success"
    assert summary["record_count"] == 800
    assert summary["record_delta"] == 50
    assert summary["anomalies"] == []
    assert summary["item_outcomes"] == {
        "svc-compute": "OK",
        "svc-storage": "OK",
        "svc-bigquery": "OK",
    }


def test_compute_catalog_summary_partial_on_missing_or_degraded():
    results = {
        "svc1": {"name": "Service 1", "status": "OK", "count": 100, "priced": 100, "issues": []},
        "svc2": {"name": "Service 2", "status": "DEGRADED", "count": 5, "priced": 0, "issues": ["no SKU carries a usable unitPrice"]},
        "svc3": {"name": "Service 3", "status": "MISSING", "count": 0, "priced": 0, "issues": ["file absent"]},
    }
    summary = compute_catalog_summary(results, expected_count=3, threshold=100.0, prior_record_count=100)
    assert summary["complete"] is False
    assert summary["state"] == "partial"
    assert summary["fetched_count"] == 1
    assert summary["expected_count"] == 3
    assert summary["coverage_pct"] == round(100.0 / 3.0, 2)
    assert len(summary["anomalies"]) == 2
    assert any("Service 2" in a and "DEGRADED" in a for a in summary["anomalies"])
    assert any("Service 3" in a and "MISSING" in a for a in summary["anomalies"])
    assert summary["item_outcomes"]["svc1"] == "OK"
    assert summary["item_outcomes"]["svc2"] == "DEGRADED"
    assert summary["item_outcomes"]["svc3"] == "MISSING"


def test_fake_source_dir_scan_and_completeness_gate():
    """Simulates a fake catalog directory with valid, empty, and collapsed service dumps."""
    tmpdir = tempfile.mkdtemp()
    try:
        skus_dir = os.path.join(tmpdir, "skus")
        os.makedirs(skus_dir, exist_ok=True)

        # 1. Valid service dump
        valid_skus = [
            {
                "skuId": f"SKU-{i}",
                "description": f"Test SKU {i}",
                "serviceRegions": ["us-central1"],
                "pricingInfo": [{"pricingExpression": {"tieredRates": [{"unitPrice": {"nanos": 100000000}}]}}],
            }
            for i in range(50)
        ]
        with gzip.open(os.path.join(skus_dir, "svc_valid.json.gz"), "wt", encoding="utf-8") as f:
            json.dump(valid_skus, f)

        # 2. Empty service dump
        with gzip.open(os.path.join(skus_dir, "svc_empty.json.gz"), "wt", encoding="utf-8") as f:
            json.dump([], f)

        # 3. Collapsed service dump (only 2 SKUs when baseline is 100)
        collapsed_skus = [
            {
                "skuId": "SKU-COLLAPSED",
                "description": "Collapsed SKU",
                "serviceRegions": ["us-central1"],
                "pricingInfo": [{"pricingExpression": {"tieredRates": [{"unitPrice": {"nanos": 100000000}}]}}],
            }
        ]
        with gzip.open(os.path.join(skus_dir, "svc_collapsed.json.gz"), "wt", encoding="utf-8") as f:
            json.dump(collapsed_skus, f)

        meta = {
            "sku_counts": {
                "svc_valid": 50,
                "svc_empty": 20,
                "svc_collapsed": 100,
            }
        }
        services = [
            {"displayName": "Valid Service", "serviceId": "svc_valid"},
            {"displayName": "Empty Service", "serviceId": "svc_empty"},
            {"displayName": "Collapsed Service", "serviceId": "svc_collapsed"},
        ]
        paths = {"skus": skus_dir}

        results, healthy = _scan_source(paths, services, meta)
        assert healthy is False
        assert results["svc_valid"]["status"] == "OK"
        assert results["svc_empty"]["status"] == "EMPTY"
        assert results["svc_collapsed"]["status"] == "DEGRADED"

        summary = compute_catalog_summary(results, expected_count=len(services), threshold=100.0)
        assert summary["complete"] is False
        assert summary["state"] == "partial"
        assert summary["fetched_count"] == 1
        assert summary["expected_count"] == 3
        assert len(summary["anomalies"]) == 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_known_charged_sku_zero_rate_raises_critical_anomaly(tmp_path):
    """Pinned known-charged SKU published at $0.00 must raise a CRITICAL anomaly and force complete=false."""
    skus_path = tmp_path / "compute_test.json.gz"

    # Fixture where C1F1-02CA-F355 is published at $0.00
    skus_zero = [
        {
            "skuId": "C1F1-02CA-F355",
            "description": "Network Inter Zone Data Transfer Out",
            "serviceRegions": ["us-central1"],
            "pricingInfo": [{"pricingExpression": {"tieredRates": [{"unitPrice": {"units": "0", "nanos": 0}}]}}],
        },
        {
            "skuId": "REGULAR-SKU-1",
            "description": "Regular SKU",
            "serviceRegions": ["us-central1"],
            "pricingInfo": [{"pricingExpression": {"tieredRates": [{"unitPrice": {"units": "1", "nanos": 0}}]}}],
        },
    ]
    with gzip.open(skus_path, "wt", encoding="utf-8") as f:
        json.dump(skus_zero, f)

    res = integrity_check_file(str(skus_path))
    assert res["status"] == "DEGRADED"
    assert any("CRITICAL: known-charged SKU C1F1-02CA-F355" in issue for issue in res["issues"])

    summary = compute_catalog_summary(
        {"compute": {"name": "Compute Engine", **res}},
        expected_count=1,
        threshold=100.0
    )
    assert summary["complete"] is False
    assert any("CRITICAL: known-charged SKU C1F1-02CA-F355" in a for a in summary["anomalies"])

    # Normal fixture where C1F1-02CA-F355 has non-zero rate ($0.01)
    skus_priced = [
        {
            "skuId": "C1F1-02CA-F355",
            "description": "Network Inter Zone Data Transfer Out",
            "serviceRegions": ["us-central1"],
            "pricingInfo": [{"pricingExpression": {"tieredRates": [{"unitPrice": {"units": "0", "nanos": 10000000}}]}}],
        },
        {
            "skuId": "REGULAR-SKU-1",
            "description": "Regular SKU",
            "serviceRegions": ["us-central1"],
            "pricingInfo": [{"pricingExpression": {"tieredRates": [{"unitPrice": {"units": "1", "nanos": 0}}]}}],
        },
    ]
    with gzip.open(skus_path, "wt", encoding="utf-8") as f:
        json.dump(skus_priced, f)

    res_ok = integrity_check_file(str(skus_path))
    assert res_ok["status"] == "OK"
    summary_ok = compute_catalog_summary(
        {"compute": {"name": "Compute Engine", **res_ok}},
        expected_count=1,
        threshold=100.0
    )
    assert summary_ok["complete"] is True


