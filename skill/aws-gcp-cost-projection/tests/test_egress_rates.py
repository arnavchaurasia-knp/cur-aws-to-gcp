#!/usr/bin/env python3
"""
test_egress_rates.py — Tests for canonical egress rates and inter-zone pricing.

Phase 1 Workorder:
- Stop projecting free inter-zone transfer ($0.00).
- Inter-zone transfer between zones in the same region is $0.01/GiB worldwide.
  Reference: https://cloud.google.com/vpc/network-pricing
- Stop selecting the misleading $0 SKU C1F1-02CA-F355.
- Enforce DE9E-AFBC-A15A with rate $0.01/GiB across all regions (including non-legacy regions like asia-southeast1).
"""

import os
import sys
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from egress_rates import interzone_sku_for_region, EGRESS_SKUS
from apply_static_mappings import map_data_transfer
from projection_view import create_projection_view


def test_interzone_sku_for_region_all_regions():
    """Verify inter-zone SKU and rate across various regions (legacy and non-legacy)."""
    # Non-legacy region (previously returned $0.00 via C1F1-02CA-F355)
    sku_id, sku_name, rate = interzone_sku_for_region("asia-southeast1")
    assert sku_id == "DE9E-AFBC-A15A"
    assert sku_name == "Network Inter Zone Data Transfer Out"
    assert rate == 0.01

    # Another non-legacy region
    sku_id, sku_name, rate = interzone_sku_for_region("me-central1")
    assert sku_id == "DE9E-AFBC-A15A"
    assert rate == 0.01

    # Legacy region (us-central1)
    sku_id, sku_name, rate = interzone_sku_for_region("us-central1")
    assert sku_id == "DE9E-AFBC-A15A"
    assert rate == 0.01

    # None / unknown region
    sku_id, sku_name, rate = interzone_sku_for_region(None)
    assert sku_id == "DE9E-AFBC-A15A"
    assert rate == 0.01


def test_egress_skus_interzone_rate():
    """Verify EGRESS_SKUS interzone entry has rate 0.01."""
    assert "interzone" in EGRESS_SKUS
    _, _, rate = EGRESS_SKUS["interzone"]
    assert rate == 0.01


def test_interzone_cur_regional_bytes_mapping():
    """Verify standard AWS CUR DataTransfer-Regional-Bytes maps to inter-zone with DE9E-AFBC-A15A."""
    row_dict = {
        "aws_li_key": "k_regional_bytes",
        "product": "AWS Data Transfer",
        "usage_type": "APS1-DataTransfer-Regional-Bytes",
        "operation": "",
        "aws_region": "ap-southeast-1",
        "gcp_region": "asia-southeast1",
        "total_usage": 500.0,
        "aws_amortized_cost": 5.00,
    }
    mapped = map_data_transfer([row_dict])
    assert len(mapped) == 1
    m = mapped[0]
    assert m["strategy"] == "map"
    assert m["gcp_sku_id"] == "DE9E-AFBC-A15A"
    assert m["gcp_sku_name"] == "Network Inter Zone Data Transfer Out"


def test_interzone_mapping_and_rate_resolution():
    """End-to-end verification of inter-zone data transfer row mapping and pricing."""
    con = duckdb.connect(":memory:")

    # Create schema matching pipeline
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            aws_region VARCHAR,
            gcp_region VARCHAR,
            line_item_type VARCHAR,
            pricing_model VARCHAR,
            is_workload BOOLEAN,
            total_usage DOUBLE,
            aws_amortized_cost DOUBLE,
            billing_month VARCHAR
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            gcp_sku_name VARCHAR,
            strategy VARCHAR,
            component VARCHAR,
            unit_multiplier DOUBLE,
            mapping_confidence DOUBLE,
            projection_note VARCHAR,
            rate_source VARCHAR,
            rate_region_basis VARCHAR,
            rate_region_used VARCHAR,
            license_status VARCHAR,
            review_flags VARCHAR,
            passthrough_reason VARCHAR,
            decided_by VARCHAR
        );
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_name VARCHAR,
            resource_family VARCHAR,
            resource_group VARCHAR,
            pricing_type VARCHAR,
            region VARCHAR,
            unit VARCHAR,
            rate_usd DOUBLE,
            source VARCHAR,
            audit_url VARCHAR,
            PRIMARY KEY (gcp_sku_id, pricing_type, region)
        );
    """)

    # Populate a non-legacy region inter-zone row (e.g. Singapore / asia-southeast1)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k_interzone_sg',
            'AWS Data Transfer',
            'APS1-InterZone-Out-Bytes',
            'InterZone-Out',
            'ap-southeast-1',
            'asia-southeast1',
            'Usage',
            'OnDemand',
            true,
            1000.0,
            10.00,
            '2026-03'
        );
    """)

    # Run map_data_transfer on this catalog row
    row_dict = {
        "aws_li_key": "k_interzone_sg",
        "product": "AWS Data Transfer",
        "usage_type": "APS1-InterZone-Out-Bytes",
        "operation": "InterZone-Out",
        "aws_region": "ap-southeast-1",
        "gcp_region": "asia-southeast1",
        "total_usage": 1000.0,
        "aws_amortized_cost": 10.00,
    }
    mapped = map_data_transfer([row_dict])
    assert len(mapped) == 1
    m = mapped[0]
    assert m["strategy"] == "map"
    assert m["gcp_sku_id"] == "DE9E-AFBC-A15A"
    assert m["gcp_sku_name"] == "Network Inter Zone Data Transfer Out"

    con.execute("""
        INSERT INTO aws_li_to_gcp_li (
            aws_li_key, gcp_service, gcp_sku_id, gcp_sku_name, strategy, component, unit_multiplier, mapping_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        m["aws_li_key"], m["gcp_service"], m["gcp_sku_id"], m["gcp_sku_name"],
        m["strategy"], m["component"], m["unit_multiplier"], m["mapping_confidence"]
    ])

    # Inject canonical rate for DE9E-AFBC-A15A as done in apply_rates.py
    con.execute("""
        INSERT INTO gcp_sku_rates VALUES
        ('DE9E-AFBC-A15A', 'Compute Engine', 'Network Inter Zone Data Transfer Out',
         'Network', 'Egress', 'OnDemand', 'global', 'gibibyte',
         0.01, 'canonical-egress', 'https://cloud.google.com/vpc/network-pricing')
        ON CONFLICT (gcp_sku_id, pricing_type, region) DO UPDATE SET rate_usd = 0.01, source = 'canonical-egress'
    """)

    # Create projection view
    create_projection_view(con, dominant_region="asia-southeast1")

    # Verify projected cost in gcp_projection
    proj = con.execute("""
        SELECT strategy, gcp_sku_id, gcp_projected_cost
        FROM gcp_projection
        WHERE aws_li_key = 'k_interzone_sg'
    """).fetchone()

    assert proj is not None
    strategy, sku_id, cost = proj
    assert strategy == "map"
    assert sku_id == "DE9E-AFBC-A15A"
    # 1000 GiB * $0.01/GiB = $10.00
    assert cost == pytest.approx(10.00, abs=0.01)
    assert cost > 0.0, "Inter-zone cost must not be projected as $0.00"
