"""
tests/test_emr_serverless_full_modelling.py — Complete test suite for EMR Serverless (§4.19).

Validates:
  1. Standard x86 vCPU-Hours maps to Dataproc Serverless DCU (mult=600.0, component='compute', strategy='map').
  2. Standard Memory-GB-Hours maps to Dataproc Serverless DCU (mult=100.0, is_assumption=True, strategy='map').
  3. Short signature GB-Hours maps to Dataproc Serverless DCU (mult=100.0).
  4. Graviton ARM vCPU-Hours & Memory-GB-Hours map to Dataproc Serverless DCU (architecture-agnostic DCU).
  5. Flex execution vCPU-Hours & Memory-GB-Hours map to Standard DCU (no spot-DCU on GCP; honest cost).
  6. Empirical rate check in us-central1: 1 vCPU-h * rate ≈ $0.036/h (0.6 * $0.06), 1 GB-h * rate ≈ $0.006/h (0.1 * $0.06).
  7. Serverless ephemeral storage (>20 GB free tier) passes through at cost parity with unit_model_mismatch reason.
  8. Cluster-mode EMR per-instance surcharge (BoxUsage / node-hours) continues to map to Dataproc Premium licensing fee ($0.01/vCPU-hr).
  9. Cluster-mode spot/backup rows continue to pass through with intentional reason.
 10. Cluster-mode unknown vCPU fallback passes through with intentional reason.
 11. Core Invariants:
     - No mapped row has empty or unresolved gcp_sku_id.
     - Every passthrough row carries an intentional passthrough_reason.
     - Multiplier is never 1.0 on a mapped serverless row.
 12. Service usage catalog completeness & spec dispatch parity for all 14 EMR dimensions.
"""
from __future__ import annotations

import json
import os
import duckdb
import pytest

from scripts.apply_static_mappings import (
    map_emr,
    resolve_sku,
    GCP_DATAPROC,
    GCP_COMPUTE_ENGINE,
)
import scripts.spec_dispatch as sd


# ---------------------------------------------------------------------------
# Test fixtures & helpers
# ---------------------------------------------------------------------------

CATALOG_DB = os.path.join(
    os.path.dirname(__file__), "..", "data", "catalog.duckdb"
)


def _row(usage_type: str, operation: str = "StartJobRun", product: str = "Amazon EMR Serverless",
         unit: str = "Hrs", gcp_region: str = "us-central1", key: str = "k_emr_test",
         cost: float = 10.0, instance_vcpus: int | None = None) -> dict:
    return {
        "aws_li_key": key,
        "product": product,
        "usage_type": usage_type,
        "operation": operation,
        "pricing_unit": unit,
        "gcp_region": gcp_region,
        "aws_amortized_cost": cost,
        "total_usage": 1.0,
        "instance_vcpus": instance_vcpus,
    }


def _get_sku_rate(sku_id: str) -> float:
    con = duckdb.connect(CATALOG_DB, read_only=True)
    res = con.execute("SELECT rate_usd FROM tiered_rates WHERE sku_id = ?", [sku_id]).fetchone()
    con.close()
    assert res is not None, f"SKU {sku_id} not found in tiered_rates"
    return float(res[0])


# ---------------------------------------------------------------------------
# 1. Standard x86 Serverless Compute & Memory
# ---------------------------------------------------------------------------

def test_emr_serverless_standard_vcpu():
    """Standard x86 vCPU-Hours maps to Dataproc Serverless DCU (mult=600.0)."""
    r = _row("USE1-VCPU-Hours", operation="StartJobRun", unit="Hrs")
    res = map_emr([r])
    assert len(res) == 1
    m = res[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == GCP_DATAPROC
    assert m["gcp_sku_name"] == "Data Compute Unit (standard) — Dataproc Serverless"
    assert m["gcp_sku_id"] is not None
    assert m["unit_multiplier"] == 600.0
    assert m["component"] == "compute"
    assert m["mapping_confidence"] == 0.75
    assert "0.6 DCU/vCPU" in m["projection_note"]


def test_emr_serverless_standard_memory():
    """Standard Memory-GB-Hours maps to Dataproc Serverless DCU (mult=100.0, assumption)."""
    r = _row("USE1-Memory-GB-Hours", operation="StartJobRun", unit="GB-Hours")
    res = map_emr([r])
    assert len(res) == 1
    m = res[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == GCP_DATAPROC
    assert m["gcp_sku_name"] == "Data Compute Unit (standard) — Dataproc Serverless"
    assert m["gcp_sku_id"] is not None
    assert m["unit_multiplier"] == 100.0
    assert m["component"] == "compute"
    assert m["is_assumption"] is True
    assert m["assumption"] == "dataproc_serverless_mem_dcu_ratio=0.1"
    assert "0.1 DCU/GiB" in m["projection_note"]


def test_emr_serverless_short_gb_hours_signature():
    """Short signature GB-Hours maps to Dataproc Serverless DCU (mult=100.0)."""
    r = _row("USE1-GB-Hours", operation="StartJobRun", unit="GB-Hours")
    res = map_emr([r])
    assert len(res) == 1
    m = res[0]
    assert m["strategy"] == "map"
    assert m["unit_multiplier"] == 100.0
    assert m["component"] == "compute"


# ---------------------------------------------------------------------------
# 2. Graviton ARM & Flex Variants
# ---------------------------------------------------------------------------

def test_emr_serverless_graviton_variants():
    """Graviton ARM vCPU & Memory map to Dataproc Serverless DCU (DCU is architecture-agnostic)."""
    r_vcpu = _row("USE1-Graviton-VCPU-Hours", operation="StartJobRun", unit="Hrs")
    r_mem = _row("USE1-Graviton-Memory-GB-Hours", operation="StartJobRun", unit="GB-Hours")

    res = map_emr([r_vcpu, r_mem])
    assert len(res) == 2

    assert res[0]["strategy"] == "map"
    assert res[0]["unit_multiplier"] == 600.0
    assert res[0]["gcp_sku_id"] is not None

    assert res[1]["strategy"] == "map"
    assert res[1]["unit_multiplier"] == 100.0
    assert res[1]["gcp_sku_id"] is not None


def test_emr_serverless_flex_variants():
    """Flex execution maps to Standard DCU (honest GCP target, no spot-DCU available)."""
    r_vcpu = _row("USE1-Flex-VCPU-Hours", operation="StartJobRun", unit="Hrs")
    r_mem = _row("USE1-Flex-Memory-GB-Hours", operation="StartJobRun", unit="GB-Hours")

    res = map_emr([r_vcpu, r_mem])
    assert len(res) == 2

    assert res[0]["strategy"] == "map"
    assert res[0]["unit_multiplier"] == 600.0

    assert res[1]["strategy"] == "map"
    assert res[1]["unit_multiplier"] == 100.0


# ---------------------------------------------------------------------------
# 3. Empirical Rate & Cost Sanity Checks
# ---------------------------------------------------------------------------

def test_emr_serverless_empirical_rates():
    """Verify calculated hourly rates against official GCP Dataproc Serverless pricing ($0.06/DCU-hr)."""
    r_vcpu = _row("USE1-VCPU-Hours", gcp_region="us-central1")
    r_mem = _row("USE1-Memory-GB-Hours", gcp_region="us-central1")

    res_vcpu = map_emr([r_vcpu])[0]
    res_mem = map_emr([r_mem])[0]

    assert res_vcpu["strategy"] == "map"
    assert res_mem["strategy"] == "map"

    # Both resolve to the standard batch DCU SKU
    sku_vcpu = res_vcpu["gcp_sku_id"]
    sku_mem = res_mem["gcp_sku_id"]
    assert sku_vcpu == sku_mem == "EC7A-EF05-537E"

    base_rate = _get_sku_rate(sku_vcpu)
    assert base_rate == pytest.approx(6.0e-05, rel=1e-3)

    # 1 vCPU-h cost: 600.0 * 6.0e-05 = $0.036/h (matches 0.6 DCU * $0.06/DCU-h)
    vcpu_hr_cost = res_vcpu["unit_multiplier"] * base_rate
    assert vcpu_hr_cost == pytest.approx(0.036, rel=1e-3)

    # 1 GB-h cost: 100.0 * 6.0e-05 = $0.006/h (matches 0.1 DCU * $0.06/DCU-h)
    mem_hr_cost = res_mem["unit_multiplier"] * base_rate
    assert mem_hr_cost == pytest.approx(0.006, rel=1e-3)

    # 12 vCPU + 48 GiB default Spark cluster:
    # AWS: 12 * $0.052624 + 48 * $0.0057785 = $0.90885/h
    # GCP: 12 * $0.036 + 48 * $0.006 = $0.720/h (~20.8% savings)
    total_gcp_h = 12 * vcpu_hr_cost + 48 * mem_hr_cost
    assert total_gcp_h == pytest.approx(0.72, rel=1e-3)


# ---------------------------------------------------------------------------
# 4. Storage & Passthrough Handling
# ---------------------------------------------------------------------------

def test_emr_serverless_storage_passthrough():
    """Serverless ephemeral storage passes through with unit_model_mismatch reason."""
    r = _row("USE1-Storage-GB-Hours", operation="StartJobRun", unit="GB-Hours")
    res = map_emr([r])
    assert len(res) == 1
    m = res[0]
    assert m["strategy"] == "passthrough"
    assert m["gcp_service"] == GCP_DATAPROC
    assert m["gcp_sku_id"] is None
    assert m["unit_multiplier"] == 1.0
    assert m["component"] == "storage"
    assert m["passthrough_reason"] == "unit_model_mismatch"
    assert "GiB-month vs AWS GiB-hour" in m["projection_note"]


# ---------------------------------------------------------------------------
# 5. Cluster-Mode Management Fee (No Regression)
# ---------------------------------------------------------------------------

def test_emr_cluster_mode_mgmt_fee_regression():
    """Cluster-mode EMR per-instance surcharge still maps to Dataproc Premium licensing fee."""
    r = _row(
        "USE1-BoxUsage:m5.xlarge",
        operation="RunInstances",
        product="Amazon Elastic MapReduce",
        unit="Hrs",
        instance_vcpus=4,
    )
    res = map_emr([r])
    assert len(res) == 1
    m = res[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == GCP_COMPUTE_ENGINE
    assert m["gcp_sku_name"] == "Licensing Fee for Google Cloud Dataproc (CPU cost)"
    assert m["gcp_sku_id"] is not None
    assert m["unit_multiplier"] == 4.0
    assert m["component"] == "management"
    assert "4 vCPU × $0.01/hr" in m["projection_note"]


def test_emr_cluster_mode_spot_passthrough():
    """Cluster-mode spot surcharge passes through with intentional reason."""
    r = _row(
        "USE1-SpotUsage:m5.xlarge",
        operation="RunInstances",
        product="Amazon Elastic MapReduce",
        unit="Hrs",
    )
    res = map_emr([r])
    assert len(res) == 1
    m = res[0]
    assert m["strategy"] == "passthrough"
    assert m["passthrough_reason"] == "no_gcp_equivalent"


def test_emr_cluster_mode_unknown_vcpu_passthrough():
    """Cluster-mode unknown instance vCPU count passes through with intentional reason."""
    r = _row(
        "USE1-CustomUsage:unknown.instance",
        operation="RunInstances",
        product="Amazon Elastic MapReduce",
        unit="Hrs",
    )
    res = map_emr([r])
    assert len(res) == 1
    m = res[0]
    assert m["strategy"] == "passthrough"
    assert m["passthrough_reason"] == "unresolved_sku"


# ---------------------------------------------------------------------------
# 6. Core Invariants
# ---------------------------------------------------------------------------

def test_emr_invariants_across_variants():
    """Ensure no mapped row has empty gcp_sku_id and all passthrough rows carry a valid reason."""
    test_rows = [
        _row("USE1-VCPU-Hours"),
        _row("USE1-Memory-GB-Hours"),
        _row("USE1-GB-Hours"),
        _row("USE1-Graviton-VCPU-Hours"),
        _row("USE1-Graviton-Memory-GB-Hours"),
        _row("USE1-Flex-VCPU-Hours"),
        _row("USE1-Flex-Memory-GB-Hours"),
        _row("USE1-Storage-GB-Hours"),
        _row("USE1-BoxUsage:m5.xlarge", instance_vcpus=4),
        _row("USE1-SpotUsage:m5.xlarge"),
        _row("USE1-UnknownUsage"),
    ]

    results = map_emr(test_rows)
    for m in results:
        if m["strategy"] == "map":
            assert m["gcp_sku_id"] is not None, f"Mapped row has None gcp_sku_id: {m}"
            assert len(str(m["gcp_sku_id"])) > 0
            assert m["unit_multiplier"] > 1.0, f"Multiplier expected > 1.0 for mapped row: {m}"
        elif m["strategy"] == "passthrough":
            assert m.get("passthrough_reason") is not None, f"Passthrough row missing reason: {m}"
            assert m["passthrough_reason"] in (
                "unit_model_mismatch",
                "no_gcp_equivalent",
                "unresolved_sku",
                "no_rate_in_region",
            )
            assert m["unit_multiplier"] == 1.0, f"Passthrough multiplier should be 1.0: {m}"


# ---------------------------------------------------------------------------
# 7. Service Usage Catalog & Spec Dispatch Parity
# ---------------------------------------------------------------------------

def test_emr_catalog_completeness_and_dispatch():
    """Verify all 14 EMR dimensions in service_usage_catalog.json dispatch to mechanic 'emr'."""
    cat_path = os.path.join(os.path.dirname(__file__), "..", "data", "service_usage_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    assert "emr" in catalog, "emr missing from service_usage_catalog.json"
    emr_dims = catalog["emr"]
    assert len(emr_dims) == 14, f"Expected 14 EMR dimensions, found {len(emr_dims)}"

    for d in emr_dims:
        row = {
            "canonical": "emr",
            "product": "Amazon EMR Serverless" if "serverless" in d["expected_charge_type"] else "Amazon Elastic MapReduce",
            "product_code": "AmazonEMRServerless" if "serverless" in d["expected_charge_type"] else "AmazonEMR",
            "usage_type": d["usage_type"],
            "operation": d["operation"],
            "unit": d["unit"],
            "pricing_unit": d["unit"],
        }
        disp = sd.dispatch(row)
        assert disp is not None, f"Failed to dispatch EMR dimension: {d}"
        assert disp["mechanic"] == d["expected_mechanic"], (
            f"Mechanic mismatch for {d['usage_type']}: got {disp['mechanic']}, expected {d['expected_mechanic']}"
        )
        assert disp["charge_type"] == d["expected_charge_type"], (
            f"Charge type mismatch for {d['usage_type']}: got {disp['charge_type']}, expected {d['expected_charge_type']}"
        )

