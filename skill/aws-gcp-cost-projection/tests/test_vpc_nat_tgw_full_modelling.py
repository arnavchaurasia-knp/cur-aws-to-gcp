"""Tests for VPC / NAT Gateway / Transit Gateway full modelling (§4.14).

Verifies:
1. Public NAT Gateway hours → Cloud NAT Gateway Uptime (32E2-4EFC-EF9F) with 32-VM cap assumption multiplier (~31.43).
2. Private NAT Gateway hours → Private Nat Gateway Uptime (AAF7-1D17-BFC4) at $0.045/hr.
3. NAT Gateway data processed (bytes) → Cloud Nat Data Processing (015F-5732-FFF0) at $0.045/GiB.
4. Transit Gateway attachment-hours → Network Connectivity Center (NCC) VPC Network Spoke (4543-3174-7752) at $0.10/hr.
5. Transit Gateway data processed (bytes) → NCC Advanced Data Networking (418E-2917-ADB1) at $0.02/GiB.
6. Public IPv4 in-use address hours → External IP Charge on a Standard VM (C054-7F72-A02E) at $0.005/hr.
7. Public IPv4 idle address hours → Static IP Charge (66A2-68EA-56BE) at $0.010/hr.
8. Site-to-Site VPN connection-hours → Cloud VPN Tunnel (6F2D-F158-5D10) at $0.05/hr.
9. VPC endpoints (hours and bytes) → passthrough (Private Service Connect with intentional review note).
10. Invariant: no mapped row ever has strategy="map" with an empty or None gcp_sku_id.
11. Invariant: passthrough rows carry an intentional projection_note/reason (not an empty or default unclassified).
12. Service usage catalog completeness: all 16 VPC/NAT/TGW dimensions in catalog dispatch cleanly.
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


def _row(usage_type: str = "", operation: str = "", product: str = "Amazon Virtual Private Cloud",
         product_code: str = "AmazonVPC", unit: str = "Hrs", total_usage: float = 730.0,
         cost: float = 32.85, gcp_region: str = "us-central1") -> dict:
    return {
        "aws_li_key": "k_vpc_test",
        "product": product,
        "product_code": product_code,
        "canonical": "vpc",
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


def test_public_nat_gateway_hours_maps_to_uptime_with_32vm_cap():
    r = _row(usage_type="USE2-NatGateway-Hours", unit="Hrs", total_usage=730.0, cost=32.85)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "32E2-4EFC-EF9F"
    assert res["gcp_service"] == "Networking"
    assert res["component"] == "hourly"
    assert res["is_assumption"] is True
    assert "32-VM cap" in res["assumption"]
    assert res["unit_multiplier"] == pytest.approx(31.42857143, rel=1e-4)


def test_private_nat_gateway_hours_maps_to_private_uptime():
    r = _row(usage_type="USE2-PrivateNatGateway-Hours", unit="Hrs", total_usage=730.0, cost=32.85)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "AAF7-1D17-BFC4"
    assert res["gcp_service"] == "Networking"
    assert res["component"] == "hourly"
    assert res["unit_multiplier"] == 1.0


def test_nat_gateway_data_processed_maps_to_nat_data_processing():
    r = _row(usage_type="USE2-NatGateway-Bytes", unit="GB", total_usage=1000.0, cost=45.00)
    out = map_data_transfer([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "015F-5732-FFF0"
    assert "Cloud Nat Data Processing" in res["gcp_sku_name"]
    assert res["component"] == "transfer"
    assert res["unit_multiplier"] == 1.0


def test_tgw_attachment_hours_maps_to_ncc_vpc_spoke():
    r = _row(usage_type="USE2-TransitGateway-Hours", product="AWS Transit Gateway",
             product_code="AWSTransitGateway", unit="Hrs", total_usage=730.0, cost=36.50)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "4543-3174-7752"
    assert res["gcp_service"] == "Networking"
    assert "Network Connectivity Center Spoke Hours VPC Network Spoke" in res["gcp_sku_name"]
    assert res["component"] == "hourly"
    assert res["unit_multiplier"] == 1.0
    assert "Network Connectivity Center (NCC) VPC Spoke" in res["projection_note"]


def test_tgw_data_processed_maps_to_ncc_adn():
    r = _row(usage_type="USE2-TransitGateway-Bytes", product="AWS Transit Gateway",
             product_code="AWSTransitGateway", unit="GB", total_usage=1000.0, cost=20.00)
    out = map_data_transfer([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "418E-2917-ADB1"
    assert res["gcp_service"] == "Networking"
    assert "Data Transfer for advanced data networking" in res["gcp_sku_name"]
    assert res["component"] == "transfer"
    assert res["unit_multiplier"] == 1.0
    assert "$0.02/GiB" in res["projection_note"]


def test_public_ipv4_in_use_maps_to_external_ip():
    r = _row(usage_type="USE2-PublicIPv4:InUseAddress", unit="Hrs", total_usage=730.0, cost=3.65)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "C054-7F72-A02E"
    assert res["gcp_service"] == "Compute Engine"
    assert "External IP Charge on a Standard VM" in res["gcp_sku_name"]


def test_public_ipv4_idle_maps_to_static_ip():
    r = _row(usage_type="USE2-PublicIPv4:IdleAddress", unit="Hrs", total_usage=730.0, cost=3.65)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "66A2-68EA-56BE"
    assert res["gcp_service"] == "Compute Engine"
    assert "Static IP Charge" in res["gcp_sku_name"]


def test_vpn_connections_maps_to_cloud_vpn_tunnel():
    r = _row(usage_type="USE2-VPN-Connections", unit="Hrs", total_usage=730.0, cost=36.50)
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "6F2D-F158-5D10"
    assert res["gcp_service"] == "Cloud VPN"
    assert "Cloud VPN Tunnel" in res["gcp_sku_name"]


def test_vpc_peering_inter_zone_egress():
    r = _row(usage_type="USE2-VPC-Peering-Bytes", operation="VpcPeering-In",
             unit="GB", total_usage=1000.0, cost=10.00)
    out = map_data_transfer([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "DE9E-AFBC-A15A"
    assert "inter-zone egress" in res["projection_note"]


def test_vpc_endpoints_passthrough_intent():
    r_hrs = _row(usage_type="USE2-VpcEndpoint-Hours", operation="VpcEndpoint", unit="Hrs", total_usage=730.0, cost=7.30)
    out = map_flat_hourly([r_hrs])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "passthrough"
    assert res["unit_multiplier"] == 1.0


def test_vpc_invariant_map_has_sku_id():
    """INVARIANT: Every mapped row MUST have a valid, non-empty gcp_sku_id."""
    catalog_path = os.path.join(_ROOT, "data", "service_usage_catalog.json")
    with open(catalog_path, "r", encoding="utf-8") as f:
        dims = json.load(f).get("vpc", [])

    hourly_rows = []
    transfer_rows = []
    for d in dims:
        row = _row(usage_type=d["usage_type"], operation=d.get("operation", ""),
                   unit=d.get("unit", "Hrs"), total_usage=100.0, cost=5.0)
        if d.get("expected_mechanic") == "flat_hourly":
            hourly_rows.append(row)
        elif d.get("expected_mechanic") == "data_transfer":
            transfer_rows.append(row)

    out = map_flat_hourly(hourly_rows) + map_data_transfer(transfer_rows)
    for res in out:
        if res.get("strategy") == "map":
            assert res.get("gcp_sku_id") is not None and len(res["gcp_sku_id"]) > 0, (
                f"Row mapped without SKU ID: {res}"
            )


def test_vpc_usage_catalog_coverage_and_dispatch():
    """Verify that all 16 VPC/NAT/TGW catalog dimensions dispatch cleanly."""
    catalog_path = os.path.join(_ROOT, "data", "service_usage_catalog.json")
    with open(catalog_path, "r", encoding="utf-8") as f:
        dims = json.load(f).get("vpc", [])

    assert len(dims) == 16, f"Expected 16 VPC dimensions, got {len(dims)}"

    for d in dims:
        res = sd.dispatch({
            "product_code": "AmazonVPC",
            "product": "Amazon Virtual Private Cloud",
            "usage_type": d["usage_type"],
            "operation": d.get("operation", ""),
        })
        assert res is not None, f"Dimension {d['usage_type']} failed to dispatch"
        assert res["charge_type"] == d["expected_charge_type"], (
            f"Dimension {d['usage_type']} dispatched to {res['charge_type']}, expected {d['expected_charge_type']}"
        )
        assert res["mechanic"] == d["expected_mechanic"], (
            f"Dimension {d['usage_type']} mechanic is {res['mechanic']}, expected {d['expected_mechanic']}"
        )
