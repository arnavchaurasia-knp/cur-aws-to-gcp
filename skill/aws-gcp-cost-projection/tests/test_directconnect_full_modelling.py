"""Tests for Direct Connect full modelling (§4.16).

Verifies:
1. Dedicated 400G port hours → Cloud Interconnect 400Gbps Dedicated circuit ($85/hr).
2. Dedicated 100G port hours → Cloud Interconnect 100Gbps Dedicated circuit ($23.28/hr).
3. Dedicated 10G port hours → Cloud Interconnect 10Gbps Dedicated circuit ($2.328/hr).
4. Dedicated 1G port hours → Cloud Interconnect 1Gbps Partner VLAN attachment ($0.2778/hr) + disclosure (no 1G dedicated on GCP).
5. Hosted connection tiers (50M, 100M, 500M, 1G, 10G) → Cloud Interconnect <tier> VLAN attachment via Google partner.
6. Unknown speed port hours → 10G Dedicated circuit default ($2.328/hr; assumption flag, conf 0.55, architecture review).
7. Data Transfer Out (US) → Cloud Interconnect Local Data Transfer in UnitedStates ($0.02/GiB).
8. Data Transfer Out (Europe) → Cloud Interconnect Local Data Transfer in Europe ($0.02/GiB).
9. Data Transfer In → ignore ($0.00 / free on both clouds).
10. Invariant: no mapped Direct Connect row ever has strategy="map" with an empty or None gcp_sku_id.
11. Invariant: all passthrough rows declare an intentional passthrough_reason.
12. Service usage catalog completeness: all 14 Direct Connect dimensions in catalog dispatch cleanly.
"""
from __future__ import annotations

import json
import os
import sys
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SCRIPTS = os.path.join(_ROOT, "scripts")
sys.path.insert(0, _SCRIPTS)

from apply_static_mappings import map_flat_hourly, map_data_transfer
import spec_dispatch as sd


def _row(usage_type: str = "USE1-Port-Usage:10G",
         operation: str = "",
         product: str = "AWS Direct Connect",
         product_code: str = "AWSDirectConnect",
         unit: str = "Hrs",
         total_usage: float = 730.0,
         cost: float = 1642.50,
         gcp_region: str = "us-central1") -> dict:
    return {
        "aws_li_key": "k_dc_test",
        "product": product,
        "product_code": product_code,
        "canonical": "directconnect",
        "usage_type": usage_type,
        "operation": operation,
        "unit": unit,
        "pricing_unit": unit,
        "total_usage": total_usage,
        "usage_amount": total_usage,
        "aws_amortized_cost": cost,
        "gcp_region": gcp_region,
        "line_item_type": "Usage",
        "pricing_model": "OnDemand",
    }


def test_directconnect_dedicated_400g():
    r = _row(usage_type="USE1-Port-Usage:400G", cost=62050.0)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Compute Engine"
    assert "400Gbps Dedicated circuit" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None
    assert res["unit_multiplier"] == 1.0
    assert res["mapping_confidence"] == 0.90


def test_directconnect_dedicated_100g():
    r = _row(usage_type="USE1-Port-Usage:100G", cost=16425.0)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Compute Engine"
    assert "100Gbps Dedicated circuit" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None
    assert res["unit_multiplier"] == 1.0
    assert res["mapping_confidence"] == 0.90


def test_directconnect_dedicated_10g():
    r = _row(usage_type="USE1-Port-Usage:10G", cost=1642.50)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Compute Engine"
    assert "10Gbps Dedicated circuit" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None
    assert res["unit_multiplier"] == 1.0
    assert res["mapping_confidence"] == 0.90


def test_directconnect_dedicated_1g():
    r = _row(usage_type="USE1-Port-Usage:1G", cost=219.0)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Compute Engine"
    assert "1Gbps VLAN attachment via Google partner" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None
    assert res["unit_multiplier"] == 1.0
    assert "no 1Gbps Dedicated circuit" in res["projection_note"]


def test_directconnect_hosted_50m():
    r = _row(usage_type="USE1-HostedConnection-50M", cost=21.90)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert "50Mbps VLAN attachment via Google partner" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None


def test_directconnect_hosted_100m():
    r = _row(usage_type="USE1-HostedConnection-100M", cost=43.80)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert "100Mbps VLAN attachment via Google partner" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None


def test_directconnect_hosted_500m():
    r = _row(usage_type="USE1-HostedConnection-500M", cost=146.00)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert "500Mbps VLAN attachment via Google partner" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None


def test_directconnect_hosted_1g():
    r = _row(usage_type="USE1-HostedConnection-1G", cost=240.90)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert "1Gbps VLAN attachment via Google partner" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None


def test_directconnect_hosted_10g():
    r = _row(usage_type="USE1-HostedConnection-10G", cost=1810.40)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert "10Gbps VLAN attachment via Google partner" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None


def test_directconnect_unparsed_speed_assumption():
    r = _row(usage_type="APS3-DirectConnect:Port", cost=1642.50)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert "10Gbps Dedicated circuit" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None
    assert res["is_assumption"] is True
    assert "DirectConnect port speed unknown" in res["assumption"]
    assert res["mapping_confidence"] == 0.55
    assert "architecture review recommended" in res["projection_note"]


def test_directconnect_data_transfer_out_local_us():
    r = _row(usage_type="USE1-DataXfer-Out", unit="GB", total_usage=5000.0, cost=100.0, gcp_region="us-central1")
    out = map_data_transfer([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Compute Engine"
    assert "Local Data Transfer in UnitedStates" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None
    assert res["unit_multiplier"] == 1.0


def test_directconnect_data_transfer_out_local_eu():
    r = _row(usage_type="EU-DataXfer-Out", unit="GB", total_usage=5000.0, cost=100.0, gcp_region="europe-west1")
    out = map_data_transfer([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Compute Engine"
    assert "Local Data Transfer in Europe" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None


def test_directconnect_data_transfer_in_free():
    r = _row(usage_type="USE1-DataXfer-In", unit="GB", total_usage=5000.0, cost=0.0)
    out = map_data_transfer([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "ignore"
    assert res["gcp_sku_id"] is None
    assert "free on GCP" in res["projection_note"]


def test_directconnect_invariants():
    test_cases = [
        _row(usage_type="USE1-Port-Usage:400G"),
        _row(usage_type="USE1-Port-Usage:100G"),
        _row(usage_type="USE1-Port-Usage:10G"),
        _row(usage_type="USE1-Port-Usage:1G"),
        _row(usage_type="USE1-HostedConnection-50M"),
        _row(usage_type="USE1-HostedConnection-100M"),
        _row(usage_type="USE1-HostedConnection-500M"),
        _row(usage_type="USE1-HostedConnection-1G"),
        _row(usage_type="USE1-HostedConnection-10G"),
        _row(usage_type="APS3-DirectConnect:Port"),
    ]
    hourly_res = map_flat_hourly(test_cases)
    for res in hourly_res:
        strat = res.get("strategy")
        sku_id = res.get("gcp_sku_id")
        if strat == "map":
            assert sku_id is not None and len(str(sku_id).strip()) > 0, f"Map row has empty sku_id: {res}"
        elif strat == "passthrough":
            assert sku_id is None, f"Passthrough row has non-None sku_id: {res}"
            assert res.get("passthrough_reason"), f"Passthrough row missing passthrough_reason: {res}"

    transfer_cases = [
        _row(usage_type="USE1-DataXfer-Out", unit="GB"),
        _row(usage_type="EU-DataXfer-Out", unit="GB", gcp_region="europe-west1"),
        _row(usage_type="USE1-DataXfer-In", unit="GB"),
    ]
    transfer_res = map_data_transfer(transfer_cases)
    for res in transfer_res:
        strat = res.get("strategy")
        sku_id = res.get("gcp_sku_id")
        if strat == "map":
            assert sku_id is not None and len(str(sku_id).strip()) > 0, f"Map row has empty sku_id: {res}"
        elif strat == "passthrough":
            assert sku_id is None, f"Passthrough row has non-None sku_id: {res}"
            assert res.get("passthrough_reason"), f"Passthrough row missing passthrough_reason: {res}"
        elif strat == "ignore":
            assert sku_id is None


def test_service_usage_catalog_directconnect_completeness_and_dispatch():
    cat_path = os.path.join(_ROOT, "data", "service_usage_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    assert "directconnect" in catalog, "directconnect missing from service_usage_catalog.json"
    entries = catalog["directconnect"]
    assert len(entries) >= 14, f"Expected >= 14 directconnect catalog entries, got {len(entries)}"

    for entry in entries:
        row = {
            "canonical": "directconnect",
            "product": "AWS Direct Connect",
            "product_code": "AWSDirectConnect",
            "usage_type": entry["usage_type"],
            "operation": entry.get("operation", ""),
            "pricing_unit": entry.get("unit", "Hrs"),
            "line_item_type": "Usage",
            "pricing_model": "OnDemand",
        }
        res = sd.dispatch(row)
        expected_ct = entry.get("expected_charge_type")
        expected_mech = entry.get("expected_mechanic")
        if expected_ct:
            assert res.get("charge_type") == expected_ct, (
                f"Mismatch for {entry['usage_type']}: expected {expected_ct}, got {res.get('charge_type')}"
            )
        if expected_mech:
            assert res.get("mechanic") == expected_mech, (
                f"Mismatch mechanic for {entry['usage_type']}: expected {expected_mech}, got {res.get('mechanic')}"
            )
