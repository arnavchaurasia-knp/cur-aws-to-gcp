"""Tests for ELB (Elastic Load Balancing) full modelling (§4.12).

Verifies:
1. ALB hours → Regional External Application Load Balancer Forwarding Rule Minimum.
2. NLB hours → Global External Passthrough Network Load Balancer Forwarding Rule (NOT Proxy NLB).
3. CLB hours → Cloud Load Balancer Forwarding Rule Minimum.
4. ALB LCU → Regional External Application Load Balancer Inbound Data Processing ($0.008/GiB).
5. NLB NLCU → External Passthrough Network Load Balancer Inbound Data Processing ($0.008/GiB)
   with explicit disclosure that AWS NLCU ($0.006) < GCP $0.008.
6. GWLB hours (per AZ) → passthrough at AWS cost with Network Security Integration (NSI) disclosure.
7. GWLB GLCU → passthrough at AWS cost with NSI disclosure.
8. GWLBE endpoints (hours and bytes) → passthrough at AWS cost with PrivateLink / PSC disclosure.
9. Invariant: no mapped ELB row ever has strategy="map" with an empty or None gcp_sku_id.
10. Service usage catalog completeness: every declared ELB dimension dispatches cleanly.
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


def _row(usage_type: str = "", operation: str = "", product: str = "Elastic Load Balancing",
         unit: str = "Hrs", total_usage: float = 100.0, cost: float = 2.25,
         gcp_region: str = "us-central1") -> dict:
    return {
        "aws_li_key": "k_elb_test",
        "product": product,
        "product_code": "AWSELB",
        "canonical": "elb",
        "usage_type": usage_type,
        "operation": operation,
        "unit": unit,
        "pricing_unit": unit,
        "total_usage": total_usage,
        "aws_amortized_cost": cost,
        "gcp_region": gcp_region,
        "line_item_type": "Usage",
        "pricing_model": "OnDemand",
    }


def test_alb_hours_maps_to_forwarding_rule():
    r = _row(usage_type="APS3-LoadBalancerUsage", operation="LoadBalancerUsage:app",
             product="Amazon Elastic Load Balancing - Application", unit="Hrs")
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] is not None and len(res["gcp_sku_id"]) > 0
    assert "Application Load Balancer Forwarding Rule Minimum" in res["gcp_sku_name"]


def test_nlb_hours_maps_to_passthrough_nlb_forwarding_rule():
    r = _row(usage_type="APS3-LoadBalancerUsage", operation="LoadBalancerUsage:net",
             product="Amazon Elastic Load Balancing - Network", unit="Hrs")
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] is not None and len(res["gcp_sku_id"]) > 0
    assert "Passthrough Network Load Balancer Forwarding Rule" in res["gcp_sku_name"]
    # Invariant: explicitly NOT the ~2.8x pricier Proxy NLB
    assert "Proxy" not in res["gcp_sku_name"]


def test_clb_hours_maps_to_classic_forwarding_rule():
    r = _row(usage_type="LoadBalancerUsage", operation="LoadBalancer-hour",
             product="Elastic Load Balancing - Classic", unit="Hrs")
    out = map_flat_hourly([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] is not None and len(res["gcp_sku_id"]) > 0
    assert "Cloud Load Balancer Forwarding Rule Minimum" in res["gcp_sku_name"]


def test_alb_lcu_data_processing():
    r = _row(usage_type="APS3-LCUUsage", operation="LCU-Hrs",
             product="Amazon Elastic Load Balancing - Application", unit="LCU-Hrs",
             total_usage=500.0)
    out = map_data_transfer([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] is not None and len(res["gcp_sku_id"]) > 0
    assert "Application Load Balancer Inbound Data Processing" in res["gcp_sku_name"]
    assert res["unit_multiplier"] == pytest.approx(1.0 / 1.073741824, rel=1e-5)


def test_nlb_nlcu_data_processing_and_overprojection_note():
    r = _row(usage_type="USE1-LCUUsage", operation="LCU-Hrs",
             product="Amazon Elastic Load Balancing - Network", unit="LCU-Hrs",
             total_usage=500.0)
    out = map_data_transfer([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] is not None and len(res["gcp_sku_id"]) > 0
    assert "Passthrough Network Load Balancer Inbound Data Processing" in res["gcp_sku_name"]
    # Disclosure that AWS NLCU ($0.006) < GCP $0.008/GiB
    assert "AWS NLCU $0.006/hr < GCP $0.008/GiB" in res["projection_note"]


def test_gwlb_hours_passthrough_with_nsi_disclosure():
    # GWLB hours must passthrough at AWS cost with NSI disclosure, never map to a forwarding rule SKU
    rows = [
        _row(usage_type="APS3-GatewayLoadBalancer-Usage", operation="", unit="Hrs", product="Elastic Load Balancing"),
        _row(usage_type="USE1-GWLB-Usage", operation="GatewayLoadBalancer", unit="Hrs", product="Elastic Load Balancing"),
        _row(usage_type="LoadBalancerUsage", operation="GatewayLoadBalancer", unit="Hrs", product="Elastic Load Balancing"),
    ]
    out = map_flat_hourly(rows)
    assert len(out) == 3
    for res in out:
        assert res["strategy"] == "passthrough"
        assert res["gcp_sku_id"] is None
        assert res["unit_multiplier"] == 1.0
        assert "Network Security Integration" in res["projection_note"] or "NSI" in res["projection_note"]
        # Invariant: never matched generic forwarding rule SKU
        assert "Forwarding Rule" not in (res.get("gcp_sku_name") or "")


def test_gwlb_glcu_passthrough():
    rows = [
        _row(usage_type="APS3-GLCUUsage", operation="GLCU-Hrs", unit="LCU-Hrs", product="Elastic Load Balancing"),
        _row(usage_type="USE1-GLCUUsage", operation="capacity unit-hour", unit="LCU-Hrs", product="Elastic Load Balancing"),
        _row(usage_type="USE1-LoadBalancer-Bytes", operation="GatewayLoadBalancer-Bytes", unit="GB", product="Elastic Load Balancing"),
    ]
    out = map_data_transfer(rows)
    assert len(out) == 3
    for res in out:
        assert res["strategy"] == "passthrough"
        assert res["gcp_sku_id"] is None
        assert res["unit_multiplier"] == 1.0
        assert "Network Security Integration" in res["projection_note"] or "NSI" in res["projection_note"]


def test_gwlbe_endpoint_passthrough():
    # Hourly endpoints
    r_hr = _row(usage_type="APS3-GatewayLoadBalancer-Endpoint-Usage", operation="", unit="Hrs", product="Elastic Load Balancing")
    out_hr = map_flat_hourly([r_hr])
    assert len(out_hr) == 1
    assert out_hr[0]["strategy"] == "passthrough"
    assert "PrivateLink" in out_hr[0]["projection_note"] or "PSC" in out_hr[0]["projection_note"]

    # Bytes endpoints
    r_bytes = _row(usage_type="APS3-GatewayLoadBalancer-Endpoint-Bytes", operation="", unit="GB", product="Elastic Load Balancing")
    out_bytes = map_data_transfer([r_bytes])
    assert len(out_bytes) == 1
    assert out_bytes[0]["strategy"] == "passthrough"
    assert "PrivateLink" in out_bytes[0]["projection_note"] or "PSC" in out_bytes[0]["projection_note"]


def test_no_mapped_elb_row_has_empty_sku_id():
    # Sweep across standard ALB, NLB, and CLB hourly and LCU rows
    test_rows = [
        _row(usage_type="APS3-LoadBalancerUsage", operation="LoadBalancerUsage:app", product="Elastic Load Balancing - Application"),
        _row(usage_type="APS3-LoadBalancerUsage", operation="LoadBalancerUsage:net", product="Elastic Load Balancing - Network"),
        _row(usage_type="LoadBalancerUsage", operation="LoadBalancer-hour", product="Elastic Load Balancing - Classic"),
        _row(usage_type="APS3-LCUUsage", operation="LCU-Hrs", product="Elastic Load Balancing - Application", unit="LCU-Hrs"),
        _row(usage_type="USE1-LCUUsage", operation="LCU-Hrs", product="Elastic Load Balancing - Network", unit="LCU-Hrs"),
        _row(usage_type="APS3-GatewayLoadBalancer-Usage", operation="", product="Elastic Load Balancing"),
        _row(usage_type="APS3-GLCUUsage", operation="GLCU-Hrs", product="Elastic Load Balancing", unit="LCU-Hrs"),
        _row(usage_type="APS3-GatewayLoadBalancer-Endpoint-Usage", operation="", product="Elastic Load Balancing"),
    ]
    out = map_flat_hourly(test_rows[:3] + [test_rows[5], test_rows[7]]) + map_data_transfer([test_rows[3], test_rows[4], test_rows[6]])
    for r in out:
        if r["strategy"] == "map":
            assert r.get("gcp_sku_id"), f"Row {r['aws_li_key']} has strategy='map' but empty gcp_sku_id!"
        elif r["strategy"] == "passthrough":
            assert r.get("unit_multiplier") == 1.0


def _run_egress_gate(mapped_rows, catalog_rows):
    """Build a minimal in-memory DB and run the real egress_strategy_invariant gate.
    mapped_rows: list of dicts from the mappers. catalog_rows: list of
    (aws_li_key, product, usage_type, operation, cost, is_workload, mechanic_group).
    Returns the gate's violation list.
    """
    import duckdb
    sys.path.insert(0, _SCRIPTS)
    import validate_fix as vf

    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE aws_li_catalog(aws_li_key VARCHAR, product VARCHAR, usage_type VARCHAR, "
        "operation VARCHAR, aws_amortized_cost DOUBLE, is_workload BOOLEAN, mechanic_group VARCHAR)"
    )
    con.execute(
        "CREATE TABLE aws_li_to_gcp_li(aws_li_key VARCHAR, strategy VARCHAR, gcp_sku_id VARCHAR, "
        "component VARCHAR, rate_source VARCHAR)"
    )
    for cr in catalog_rows:
        con.execute("INSERT INTO aws_li_catalog VALUES (?,?,?,?,?,?,?)", list(cr))
    for m in mapped_rows:
        con.execute(
            "INSERT INTO aws_li_to_gcp_li VALUES (?,?,?,?,?)",
            [m["aws_li_key"], m["strategy"], m.get("gcp_sku_id"), m.get("component"), None],
        )
    return vf.check_egress_strategy_invariant(con)


def test_gwlb_passthrough_exempt_from_egress_gate():
    """Regression for the §4.12 egress-gate trap: GWLB GLCU/bytes rows are passthrough,
    which would trip check_egress_strategy_invariant unless component='gwlb' exempts them.
    No unit test previously ran the gate on these rows, so a green suite hid a runtime fail."""
    gwlb_inputs = [
        _row(usage_type="APS3-GLCUUsage", operation="GLCU-Hrs", unit="LCU-Hrs", product="Elastic Load Balancing", cost=40.0),
        _row(usage_type="USE1-LoadBalancer-Bytes", operation="GatewayLoadBalancer-Bytes", unit="GB", product="Elastic Load Balancing", cost=12.0),
        _row(usage_type="APS3-GatewayLoadBalancer-Endpoint-Bytes", operation="", unit="GB", product="Elastic Load Balancing", cost=8.0),
    ]
    mapped = map_data_transfer(gwlb_inputs)
    # every GWLB data row must be passthrough + tagged component='gwlb'
    for m in mapped:
        assert m["strategy"] == "passthrough"
        assert m.get("component") == "gwlb", f"GWLB row must carry component='gwlb', got {m.get('component')!r}"

    catalog_rows = [
        (gwlb_inputs[0]["aws_li_key"], "Elastic Load Balancing", "APS3-GLCUUsage", "GLCU-Hrs", 40.0, True, "data_transfer"),
        (gwlb_inputs[1]["aws_li_key"], "Elastic Load Balancing", "USE1-LoadBalancer-Bytes", "GatewayLoadBalancer-Bytes", 12.0, True, "data_transfer"),
        (gwlb_inputs[2]["aws_li_key"], "Elastic Load Balancing", "APS3-GatewayLoadBalancer-Endpoint-Bytes", "", 8.0, True, "data_transfer"),
    ]
    # all three share one aws_li_key in _row(); dedupe so the DB has distinct keys
    for i, (m, cr) in enumerate(zip(mapped, catalog_rows)):
        m["aws_li_key"] = cr = f"k_gwlb_{i}"
    catalog_rows = [
        (f"k_gwlb_{i}", c[1], c[2], c[3], c[4], c[5], c[6]) for i, c in enumerate(catalog_rows)
    ]
    viol = _run_egress_gate(mapped, catalog_rows)
    assert viol == [], f"GWLB passthrough rows must be exempt from the egress gate, got violations: {viol}"


def test_non_gwlb_transfer_passthrough_still_trips_gate():
    """Guard that the 'gwlb' exemption is narrow: a genuine data-transfer row that
    passthroughs (component='transfer') must still be caught by the gate."""
    mapped = [{"aws_li_key": "k_bad", "strategy": "passthrough", "gcp_sku_id": None, "component": "transfer"}]
    catalog_rows = [("k_bad", "Data Transfer", "USE1-DataTransfer-Out-Bytes", "", 50.0, True, "data_transfer")]
    viol = _run_egress_gate(mapped, catalog_rows)
    assert len(viol) == 1, "A non-GWLB data-transfer passthrough row must still trip the egress gate"


def test_elb_catalog_and_spec_dispatch_coverage():
    cat_path = os.path.join(_ROOT, "data", "service_usage_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    elb_entries = catalog.get("elb", [])
    assert len(elb_entries) >= 12, f"Expected >= 12 ELB dimensions, got {len(elb_entries)}"

    for entry in elb_entries:
        r = _row(
            usage_type=entry["usage_type"],
            operation=entry.get("operation", ""),
            unit=entry.get("unit", "Hrs"),
            product="Elastic Load Balancing"
        )
        res = sd.dispatch(r)
        assert res is not None, f"ELB dimension {entry['usage_type']} returned None from dispatch"
        assert not res.get("source_rule", "").startswith("spec_gap"), f"ELB dimension {entry['usage_type']} fell to spec_gap"
        assert res.get("charge_type") == entry["expected_charge_type"], (
            f"ELB dimension {entry['usage_type']}: got charge_type {res.get('charge_type')}, "
            f"expected {entry['expected_charge_type']}"
        )
        assert res.get("mechanic") == entry["expected_mechanic"], (
            f"ELB dimension {entry['usage_type']}: got mechanic {res.get('mechanic')}, "
            f"expected {entry['expected_mechanic']}"
        )
