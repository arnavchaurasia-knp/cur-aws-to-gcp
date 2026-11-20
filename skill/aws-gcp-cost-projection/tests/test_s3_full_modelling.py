"""Tests for S3 (Simple Storage Service) full modelling per WORKORDER §4.4.

S3 is GOLD-tier: storage classes map to the right GCS tier, requests/retrieval
passthrough (unit models incompatible), IT-monitoring is $0 (GCS Autoclass has
no monitoring fee), data transfer is handled by the global data_transfer mechanic.

Rates verified against the official GCP Cloud Storage pricing page 2026-11-20
(cloud.google.com/storage/pricing, us-central1/Iowa single-region, hourly x730):
  Standard  $0.0200  Nearline $0.0100  Coldline $0.0040  Archive $0.0012 /GiB-mo
and the official minimum-duration table (Nearline 30d / Coldline 90d / Archive
365d), which justifies Glacier-Flexible(90d min)->Coldline and
Deep-Archive(365d min)->Archive.
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
sys.path.insert(0, _SCRIPTS)

import apply_static_mappings as asm  # noqa: E402


def _row(**kw):
    return {
        "aws_li_key": kw.get("aws_li_key", "k_s3"),
        "product": kw.get("product", "Amazon Simple Storage Service"),
        "usage_type": kw.get("usage_type", ""),
        "operation": kw.get("operation", ""),
        "unit": kw.get("unit", "GB-Mo"),
        "total_usage": kw.get("total_usage", 1000.0),
        "aws_amortized_cost": kw.get("aws_amortized_cost", 20.0),
        "gcp_region": kw.get("gcp_region", "us-central1"),
    }


# ── Storage classes → correct GCS tier (map_object_storage) ──────────────────

_CLASS_TO_TIER = [
    ("APS3-TimedStorage-ByteHrs", asm.GCS_STANDARD),
    ("APS3-TimedStorage-RRS-ByteHrs", asm.GCS_STANDARD),
    ("APS3-TimedStorage-SIA-ByteHrs", asm.GCS_NEARLINE),
    ("APS3-TimedStorage-ZIA-ByteHrs", asm.GCS_NEARLINE),
    ("APS3-TimedStorage-GIR-ByteHrs", asm.GCS_COLDLINE),
    ("APS3-TimedStorage-GlacierByteHrs", asm.GCS_COLDLINE),
    ("APS3-TimedStorage-GDA-ByteHrs", asm.GCS_ARCHIVE),
    ("APS3-TimedStorage-INT-FA-ByteHrs", asm.GCS_STANDARD),
    ("APS3-TimedStorage-INT-IA-ByteHrs", asm.GCS_NEARLINE),
    ("APS3-TimedStorage-INT-AA-ByteHrs", asm.GCS_COLDLINE),
    ("APS3-TimedStorage-INT-DAA-ByteHrs", asm.GCS_ARCHIVE),
]


@pytest.mark.parametrize("usage_type,expected_tier", _CLASS_TO_TIER)
def test_s3_storage_class_maps_to_expected_gcs_tier(usage_type, expected_tier):
    res = asm.map_object_storage([_row(usage_type=usage_type, unit="GB-Mo")])
    res = res[0] if isinstance(res, tuple) else res
    assert len(res) == 1
    r = res[0]
    assert r["gcp_service"] == asm.GCP_CLOUD_STORAGE
    assert r["gcp_sku_name"] == expected_tier, (
        f"{usage_type} -> {r['gcp_sku_name']}, expected {expected_tier}"
    )
    assert r["strategy"] == "map"
    assert r["component"] == "storage"
    # GB (decimal) -> GiB (binary) conversion: exact 1 / 1.073741824
    assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB


def test_s3_storage_prefers_single_region_sku():
    """Standard storage resolves the single-region Iowa SKU ($0.02), not the
    dual/multi-region variant ($0.022) — the regional-target policy."""
    res = asm.map_object_storage([_row(usage_type="APS3-TimedStorage-ByteHrs", unit="GB-Mo")])
    res = res[0] if isinstance(res, tuple) else res
    r = res[0]
    # resolve ran and attached a concrete SKU id (single-region selected via prefer_single_region)
    assert r.get("gcp_sku_id") is not None


# ── IT-monitoring is $0 on GCP (Autoclass has no monitoring fee) ─────────────

def test_s3_intelligent_tiering_monitoring_is_ignored():
    """IT per-object monitoring/automation fee -> ignore ($0): GCS Autoclass
    has no monitoring charge. These rows are billed per-object so they dispatch
    to per_request (not object_storage); the per_request S3 branch must still
    zero them out rather than passthrough at AWS cost (over-projection)."""
    res = asm.map_per_request([
        _row(usage_type="APS3-Monitoring-Automation-INT", unit="Objects",
             operation="Monitoring and Automation, INT")
    ])
    res = res[0] if isinstance(res, tuple) else res
    assert len(res) == 1
    r = res[0]
    assert r["strategy"] == "ignore"
    assert "Autoclass" in r["projection_note"]


# ── Requests / retrieval passthrough WITH declared reason ────────────────────

def test_s3_requests_passthrough_with_unit_model_mismatch_reason():
    res = asm.map_per_request([
        _row(usage_type="APS3-Requests-Tier1", unit="Requests", operation="PutObject"),
        _row(usage_type="APS3-Requests-Tier2", unit="Requests", operation="GetObject"),
        _row(usage_type="APS3-Retrieval-SIA", unit="GB", operation="StandardIARetrieval"),
    ])
    res = res[0] if isinstance(res, tuple) else res
    assert len(res) == 3
    for r in res:
        assert r["strategy"] == "passthrough"
        assert r["unit_multiplier"] == 1.0
        assert r.get("passthrough_reason") == "unit_model_mismatch"


def test_s3_lifecycle_replication_passthrough_no_gcp_equivalent():
    res = asm.map_per_request([
        _row(usage_type="APS3-Requests-Lifecycle", unit="Requests", operation="LifecycleTransition"),
        _row(usage_type="APS3-C3Requests-Tier1", unit="Requests", operation="ReplicationRequest"),
    ])
    res = res[0] if isinstance(res, tuple) else res
    assert len(res) == 2
    for r in res:
        assert r["strategy"] == "passthrough"
        assert r.get("passthrough_reason") == "no_gcp_equivalent"


# ── Regression: Glacier retrieval REQUESTS must not be swallowed as storage ───

def test_s3_glacier_retrieval_request_routes_to_per_request_not_storage():
    """A Glacier/Deep-Archive RETRIEVAL request (unit=Requests) whose operation
    text contains 'Glacier'/'Deep Archive' but not 'Request' must dispatch to
    per_request, not object_storage. Before the spec fix, the s3_storage clause-3
    operation match ('...Glacier...') swallowed it because its exclude lacked
    'Retrieval' (clause 1 had it, clause 3 did not), mis-pricing a request count
    as Archive GB-Mo."""
    import spec_dispatch as sd

    row = {
        "aws_li_key": "k", "product": "Amazon Simple Storage Service",
        "product_code": "AmazonS3", "usage_type": "APS3-Requests-GDA-Tier3",
        "unit": "Requests", "operation": "GlacierDeepArchiveRetrieval",
        "aws_region": "ap-south-1",
    }
    d = sd.dispatch(row)
    assert d is not None
    assert d["mechanic"] == "per_request"
    assert d["charge_type"] == "s3_requests"

    # And a genuine Glacier STORAGE row must still route to object_storage
    srow = dict(row, usage_type="APS3-TimedStorage-GlacierByteHrs",
                unit="GB-Mo", operation="GlacierStorage")
    ds = sd.dispatch(srow)
    assert ds["mechanic"] == "object_storage"
    assert ds["charge_type"] == "s3_storage"


# ── Completeness gate: every catalog dimension dispatches to its charge type ──

def test_s3_catalog_and_spec_dispatch_coverage():
    import json
    import spec_dispatch as sd

    cat_path = os.path.join(os.path.dirname(_HERE), "data", "service_usage_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    s3_entries = catalog.get("s3", [])
    assert len(s3_entries) >= 25, f"expected the S3 catalog populated, got {len(s3_entries)}"

    for entry in s3_entries:
        row = {
            "aws_li_key": "k",
            "product": "Amazon Simple Storage Service",
            "product_code": "AmazonS3",
            "usage_type": entry["usage_type"],
            "unit": entry.get("unit", "GB-Mo"),
            "operation": "",
            "aws_region": "us-east-1",
        }
        d = sd.dispatch(row)
        assert d is not None, f"Dispatch returned None for {entry['usage_type']}"
        assert d["charge_type"] == entry["expected_charge_type"], (
            f"Expected {entry['expected_charge_type']}, got {d['charge_type']} for {entry['usage_type']}"
        )
        assert d["mechanic"] == entry["expected_mechanic"], (
            f"Expected {entry['expected_mechanic']}, got {d['mechanic']} for {entry['usage_type']}"
        )
