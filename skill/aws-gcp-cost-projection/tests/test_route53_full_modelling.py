"""Tests for Route 53 full modelling (§4.13).

Verifies:
1. Route 53 hosted zones → Cloud DNS ManagedZone (8C22-6FC3-D478, $0.20/mo vs AWS $0.50).
2. Standard DNS queries → Cloud DNS DNS Query (port 53) (6DFF-5025-A128, $0.40/M).
3. Routing-policy queries (latency/geo/IP) → Cloud DNS DNS Query with $0.70/M surcharge disclosure note.
4. Health checks (fast/standard) → Cloud DNS Routing Policy Internet Fast Health Check (4D2F-E662-295E, $2.00/mo).
5. Health checks (premium/HTTPS/content-match) → Cloud DNS Routing Policy Internet Premium Health Check (9284-9C28-65BE, $4.00/mo).
6. Traffic Flow policy records → passthrough ($50.00/policy-record/mo) with GCP query surcharge note.
7. DNS Firewall → passthrough with DNS Armor analogue note.
8. Resolver endpoints and queries → passthrough with Cloud DNS outbound endpoint note ($182.50/mo, preview).
9. Records > 10,000 per zone fee → ignore ($0 on GCP, included in zone fee).
10. Invariant: no mapped Route 53 row ever has strategy="map" with an empty or None gcp_sku_id.
11. Service usage catalog completeness: all 15 Route 53 dimensions in catalog dispatch cleanly.
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

from apply_static_mappings import map_per_request
import spec_dispatch as sd


def _row(usage_type: str = "", operation: str = "", product: str = "Amazon Route 53",
         unit: str = "Queries", total_usage: float = 1000000.0, cost: float = 0.40,
         gcp_region: str = "global") -> dict:
    return {
        "aws_li_key": "k_route53_test",
        "product": product,
        "product_code": "AmazonRoute53",
        "canonical": "route53",
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


def test_route53_hosted_zone_maps_to_managed_zone():
    r = _row(usage_type="HostedZone", unit="Mo", total_usage=5.0, cost=2.50)
    out = map_per_request([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "8C22-6FC3-D478"
    assert res["gcp_service"] == "Cloud DNS"
    assert res["component"] == "zones"


def test_route53_standard_queries_maps_to_dns_query():
    r = _row(usage_type="USE2-DNS-Queries", unit="Queries", total_usage=1000000.0, cost=0.40)
    out = map_per_request([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "6DFF-5025-A128"
    assert res["gcp_service"] == "Cloud DNS"


def test_route53_routing_policy_queries_with_disclosure():
    r = _row(usage_type="USE2-DNS-Queries-Latency", operation="LatencyRouting",
             unit="Queries", total_usage=500000.0, cost=0.30)
    out = map_per_request([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "6DFF-5025-A128"
    assert res["component"] == "queries"
    assert "$0.70/M" in res["projection_note"]


def test_route53_fast_health_check_maps_to_fast_sku():
    r = _row(usage_type="USE2-HealthCheck-Fast", operation="", unit="Count", total_usage=2.0, cost=2.00)
    out = map_per_request([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "4D2F-E662-295E"
    assert res["gcp_service"] == "Networking"
    assert res["component"] == "healthcheck"
    assert "$2.00" in res["projection_note"]


def test_route53_premium_health_check_maps_to_premium_sku():
    r = _row(usage_type="USE2-HealthCheck-HTTPS", operation="HTTPS", unit="Count", total_usage=1.0, cost=2.00)
    out = map_per_request([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] == "9284-9C28-65BE"
    assert res["gcp_service"] == "Networking"
    assert res["component"] == "healthcheck"
    assert "$4.00" in res["projection_note"]


def test_route53_health_check_never_maps_to_dns_query_sku():
    """CRITICAL REGRESSION TEST: Health checks must NEVER map to the per-query DNS Query SKU (6DFF-5025-A128)."""
    cases = [
        _row(usage_type="HealthCheck", unit="Count"),
        _row(usage_type="USE1-HealthCheck", unit="Count"),
        _row(usage_type="USE1-HealthCheck-Fast", unit="Count"),
        _row(usage_type="USE1-HealthCheck-HTTPS", unit="Count"),
        _row(usage_type="AP-HealthCheck-StringMatch", unit="Count"),
    ]
    out = map_per_request(cases)
    for res in out:
        assert res["gcp_sku_id"] != "6DFF-5025-A128", f"Mis-mapped to per-query DNS Query SKU: {res}"
        assert res["strategy"] == "map"
        assert res["gcp_sku_id"] in ("4D2F-E662-295E", "9284-9C28-65BE")


def test_route53_traffic_flow_passthrough():
    r = _row(usage_type="USE2-TrafficFlow", operation="TrafficFlow", unit="Count", total_usage=1.0, cost=50.00)
    out = map_per_request([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "passthrough"
    assert res["component"] == "traffic_flow"
    assert res["unit_multiplier"] == 1.0
    assert "$50.00" in res["projection_note"]


def test_route53_dns_firewall_passthrough():
    r = _row(usage_type="USE2-DNS-Firewall-Queries", unit="Queries", total_usage=100000.0, cost=0.60)
    out = map_per_request([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "passthrough"
    assert res["component"] == "security"
    assert "DNS Armor" in res["projection_note"]


def test_route53_resolver_passthrough():
    r = _row(usage_type="USE2-Resolver-Endpoint-Hours", unit="Hrs", total_usage=730.0, cost=91.25)
    out = map_per_request([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "passthrough"
    assert res["component"] == "resolver"
    assert "outbound endpoint" in res["projection_note"]


def test_route53_records_over_10k_ignore():
    r = _row(usage_type="USE2-Records-Over-10k", unit="Count", total_usage=5000.0, cost=7.50)
    out = map_per_request([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "ignore"
    assert res["component"] == "records"
    assert res["unit_multiplier"] == 0.0
    assert "$0 on GCP" in res["projection_note"]


def test_route53_invariant_map_has_sku_id():
    """INVARIANT: Every mapped row MUST have a valid, non-empty gcp_sku_id."""
    catalog_path = os.path.join(_ROOT, "data", "service_usage_catalog.json")
    with open(catalog_path, "r", encoding="utf-8") as f:
        dims = json.load(f).get("route53", [])

    rows = [
        _row(usage_type=d["usage_type"], operation=d.get("operation", ""),
             unit=d.get("unit", "Queries"), total_usage=100.0, cost=1.0)
        for d in dims
    ]
    out = map_per_request(rows)
    assert len(out) == len(rows)
    for res in out:
        if res.get("strategy") == "map":
            assert res.get("gcp_sku_id") is not None and len(res["gcp_sku_id"]) > 0, (
                f"Row mapped without SKU ID: {res}"
            )


def test_route53_usage_catalog_coverage_and_dispatch():
    """Verify that all 15 Route 53 catalog dimensions dispatch without error."""
    catalog_path = os.path.join(_ROOT, "data", "service_usage_catalog.json")
    with open(catalog_path, "r", encoding="utf-8") as f:
        dims = json.load(f).get("route53", [])

    assert len(dims) == 15, f"Expected 15 Route 53 dimensions, got {len(dims)}"

    for d in dims:
        res = sd.dispatch({
            "product_code": "AmazonRoute53",
            "product": "Amazon Route 53",
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
