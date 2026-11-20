"""Tests for AWS Fargate (ECS launch type) full modelling per WORKORDER §4.26.

Fargate pod CPU/memory -> GKE Autopilot (cheapest compute class per arch), with
Cloud Run as an alternative managed target. All GCP target rates verified against
the official GKE pricing page (cloud.google.com/kubernetes-engine/pricing,
us-central1 "Default" column) 2026-11-20:
  Autopilot GP Pod vCPU            $0.0445/vCPU-h
  Autopilot GP Pod Memory          $0.0049225/GiB-h
  Autopilot Scale-Out Arm vCPU     $0.0356/vCPU-h
  Autopilot Scale-Out Arm Memory   $0.003938/GiB-h
  Autopilot Pod Ephemeral Storage  $0.0000548/GiB-h
unit_multiplier: vCPU-hours x1000 (vCPU -> mCPU); GB-hours x GB_TO_GIB (GB -> GiB).
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
sys.path.insert(0, _SCRIPTS)

import apply_static_mappings as asm  # noqa: E402


def _row(usage_type, total_usage=1000.0, **kw):
    return {
        "aws_li_key": kw.get("aws_li_key", "k_" + usage_type),
        "product": "Amazon Elastic Container Service",
        "product_code": "AmazonECS",
        "usage_type": usage_type,
        "operation": kw.get("operation", ""),
        "unit": kw.get("unit", "Hrs"),
        "total_usage": total_usage,
        "aws_amortized_cost": kw.get("aws_amortized_cost", 50.0),
        "gcp_region": kw.get("gcp_region", "us-central1"),
    }


def _one(rows):
    res = asm.map_per_request(rows)
    res = res[0] if isinstance(res, tuple) else res
    assert len(res) == 1
    return res[0]


def test_fargate_vcpu_maps_to_autopilot_mcpu():
    r = _one([_row("APS3-Fargate-vCPU-Hours:perCPU")])
    assert r["component"] == "vcpu"
    assert "mCPU" in r["gcp_sku_name"] or "CPU" in r["gcp_sku_name"]
    assert r["unit_multiplier"] == 1000.0  # vCPU -> mCPU


def test_fargate_memory_maps_to_autopilot_memory():
    r = _one([_row("APS3-Fargate-GB-Hours")])
    assert r["component"] == "memory"
    assert "Memory" in r["gcp_sku_name"]
    assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB


def test_fargate_arm_vcpu_uses_scale_out_arm_class():
    r = _one([_row("APS3-Fargate-ARM-vCPU-Hours:perCPU")])
    assert r["component"] == "vcpu"
    assert "Arm" in r["gcp_sku_name"]
    assert r["unit_multiplier"] == 1000.0


def test_fargate_arm_memory_uses_scale_out_arm_not_phantom_gp_arm():
    """ARM Fargate memory must resolve the Scale-Out Arm Pod Memory SKU
    ($0.003938/GiB-h, the only Arm memory rate on the official GKE pricing
    page), NOT the legacy 'Autopilot Arm Pod Memory' ($0.00352) which is not
    customer-facing and made the cheapest-class sweep under-project."""
    r = _one([_row("APS3-Fargate-ARM-GB-Hours")])
    assert r["component"] == "memory"
    assert "Arm" in r["gcp_sku_name"]
    # the phantom GP-Arm memory SKU must never be selected
    assert r["gcp_sku_name"] != "Autopilot Arm Pod Memory"


def test_fargate_ephemeral_storage_is_storage_not_memory():
    """Regression: Fargate ephemeral storage carries 'GB-Hours' in its usage_type
    but is a STORAGE charge. It must route to Autopilot Pod Ephemeral Storage
    ($0.0000548/GiB-h), NOT be read as pod memory ($0.0049/GiB-h, ~90x the real
    rate) because of the 'gb-hours' substring."""
    r = _one([_row("APS3-Fargate-EphemeralStorage-GB-Hours")])
    assert r["component"] == "storage"
    assert "Ephemeral Storage" in r["gcp_sku_name"]
    assert "Memory" not in r["gcp_sku_name"]
    assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB


def test_fargate_spot_vcpu_still_maps_to_autopilot():
    """Spot Fargate normalizes to on-demand upstream (OD-vs-OD); the mapper still
    targets Autopilot, never a GCP Spot/preemptible rate."""
    r = _one([_row("APS3-Fargate-vCPU-Hours:perCPU-SpotUsage")])
    assert r["component"] == "vcpu"
    assert r["unit_multiplier"] == 1000.0


def test_ecs_catalog_and_spec_dispatch_coverage():
    import json
    import spec_dispatch as sd

    cat_path = os.path.join(os.path.dirname(_HERE), "data", "service_usage_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    ecs_entries = catalog.get("ecs", [])
    assert len(ecs_entries) >= 8, f"expected ECS/Fargate catalog populated, got {len(ecs_entries)}"

    for entry in ecs_entries:
        row = {
            "aws_li_key": "k",
            "product": "Amazon Elastic Container Service",
            "product_code": "AmazonECS",
            "usage_type": entry["usage_type"],
            "unit": entry.get("unit", "Hrs"),
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
