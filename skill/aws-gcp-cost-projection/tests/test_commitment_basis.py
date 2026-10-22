#!/usr/bin/env python3
"""tests/test_commitment_basis.py — Tests for commitment basis classification,
estimated on-demand pricing, and anti-flattery rules.
"""
import duckdb
import pytest
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from projection_view import classify_commitment_basis, create_projection_view
import row_state


@pytest.fixture
def base_db():
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT PRIMARY KEY,
            product TEXT,
            aws_region TEXT,
            gcp_region TEXT,
            line_item_type TEXT,
            pricing_model TEXT,
            usage_type TEXT,
            operation TEXT,
            is_workload BOOLEAN,
            total_usage DOUBLE,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key TEXT,
            strategy TEXT,
            gcp_service TEXT,
            gcp_sku_id TEXT,
            component TEXT,
            unit_multiplier DOUBLE,
            projection_note TEXT
        );
    """)
    row_state.ensure_schema(conn)
    conn.execute("""
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id TEXT,
            gcp_service TEXT,
            gcp_sku_name TEXT,
            resource_family TEXT,
            resource_group TEXT,
            pricing_type TEXT,
            region TEXT,
            unit TEXT,
            rate_usd DOUBLE,
            source TEXT,
            audit_url TEXT
        );
    """)
    return conn


def test_commitment_basis_classification():
    """T5.1: Spot, 1-year RI, 3-year RI, and plain on-demand rows each get the right basis."""
    assert classify_commitment_basis("Spot") == "spot"
    assert classify_commitment_basis("OnDemand", "USE1-SpotUsage:c4.large") == "spot"
    assert classify_commitment_basis("OnDemand", operation="RunInstances:SV001:Spot") == "spot"

    # 3-year commitments
    assert classify_commitment_basis("Committed", operation="RunInstances:3yrNoUpfront") == "committed_3yr"
    assert classify_commitment_basis("Reserved", usage_type="HeavyUsage:3-year") == "committed_3yr"
    assert classify_commitment_basis("Committed", usage_type="APS3-SavingsPlan:36mo") == "committed_3yr"

    # 1-year commitments
    assert classify_commitment_basis("Committed", operation="RunInstances:1yrNoUpfront") == "committed_1yr"
    assert classify_commitment_basis("Reserved", usage_type="HeavyUsage:1-year") == "committed_1yr"

    # Plain on-demand
    assert classify_commitment_basis("OnDemand") == "on_demand"
    assert classify_commitment_basis("OnDemand", "BoxUsage:t3.medium", "RunInstances") == "on_demand"
    assert classify_commitment_basis(None) == "on_demand"


def test_unknown_term_uses_smaller_discount_and_says_so(base_db):
    """T5.2: An unreadable term yields committed_1yr plus an explicit note stating term not stated on the bill.
    This is the anti-flattery guard: smaller discount produces higher/fairer AWS list price, never flattering GCP.
    """
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_unknown_ri', 'Amazon RDS', 'us-east-1', 'us-east1', 'DiscountedUsage', 'Reserved', 'RDS:db.m6g.xlarge', 'CreateDBInstance', true, 100, 100.0);
        INSERT INTO aws_li_to_gcp_li (aws_li_key, strategy, gcp_service, gcp_sku_id, component, unit_multiplier, projection_note) VALUES
            ('k_unknown_ri', 'passthrough', 'Cloud SQL', NULL, NULL, 1.0, NULL);
    """)
    create_projection_view(conn)
    row = conn.execute("""
        SELECT commitment_basis, aws_cost_od_est, projection_note
        FROM gcp_projection
        WHERE aws_li_key = 'k_unknown_ri'
    """).fetchone()

    assert row[0] == "committed_1yr"
    # With default 0.38 1-yr RI discount, divisor is 0.62 -> 100 / 0.62 approx 161.29
    assert row[1] == pytest.approx(100.0 / (1.0 - 0.38), rel=1e-3)
    assert "term not stated on the bill" in row[2]
    assert "38% 1-year RI discount" in row[2]


def test_od_estimate_math(base_db):
    """T5.3: Billed 60.00 at a 0.70 Spot discount gives 200.00; changing the assumption changes the result."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_spot_1', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'Spot', 'USE1-SpotUsage:c4.large', 'RunInstances', true, 100, 60.0);
        INSERT INTO aws_li_to_gcp_li (aws_li_key, strategy, gcp_service, gcp_sku_id, component, unit_multiplier, projection_note) VALUES
            ('k_spot_1', 'passthrough', 'Compute Engine', NULL, NULL, 1.0, NULL);
    """)
    # Default spot discount is 0.70 -> 60 / (1 - 0.70) = 200.00
    create_projection_view(conn)
    row = conn.execute("SELECT aws_cost_od_est FROM gcp_projection WHERE aws_li_key = 'k_spot_1'").fetchone()
    assert row[0] == pytest.approx(200.0)

    # Change assumption dynamically to 0.60 -> 60 / (1 - 0.60) = 150.00
    create_projection_view(conn, assumptions={"aws_spot_discount_pct": 0.60})
    row2 = conn.execute("SELECT aws_cost_od_est FROM gcp_projection WHERE aws_li_key = 'k_spot_1'").fetchone()
    assert row2[0] == pytest.approx(150.0)


def test_billed_cost_preserved(base_db):
    """T5.4: aws_amortized_cost is untouched and the bill still reconciles to the invoice total."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_spot', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'Spot', 'USE1-SpotUsage:c4.large', 'RunInstances', true, 100, 60.0),
            ('k_od', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'OnDemand', 'BoxUsage:t3.medium', 'RunInstances', true, 100, 40.0);
        INSERT INTO aws_li_to_gcp_li (aws_li_key, strategy, gcp_service, gcp_sku_id, component, unit_multiplier, projection_note) VALUES
            ('k_spot', 'passthrough', 'Compute Engine', NULL, NULL, 1.0, NULL),
            ('k_od', 'passthrough', 'Compute Engine', NULL, NULL, 1.0, NULL);
    """)
    create_projection_view(conn)

    # In catalog, costs remain 60.0 and 40.0
    cat_sum = conn.execute("SELECT SUM(aws_amortized_cost) FROM aws_li_catalog").fetchone()[0]
    assert cat_sum == pytest.approx(100.0)

    # In projection, aws_amortized_cost column is intact and untouched
    proj_billed_sum = conn.execute("SELECT SUM(aws_amortized_cost) FROM gcp_projection").fetchone()[0]
    assert proj_billed_sum == pytest.approx(100.0)

    # While aws_cost_od_est shows the restated list estimate (200 + 40 = 240)
    proj_est_sum = conn.execute("SELECT SUM(aws_cost_od_est) FROM gcp_projection").fetchone()[0]
    assert proj_est_sum == pytest.approx(240.0)


def test_on_demand_rows_unchanged(base_db):
    """T5.5: For on_demand rows the estimate equals the billed cost exactly, so the headline does not shift for them."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_od1', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'OnDemand', 'BoxUsage:c5.xlarge', 'RunInstances', true, 100, 125.50),
            ('k_od2', 'Amazon S3', 'us-east-1', 'us-east1', 'Usage', 'OnDemand', 'TimedStorage-ByteHrs', 'StandardStorage', true, 1000, 45.25);
        INSERT INTO aws_li_to_gcp_li (aws_li_key, strategy, gcp_service, gcp_sku_id, component, unit_multiplier, projection_note) VALUES
            ('k_od1', 'passthrough', 'Compute Engine', NULL, NULL, 1.0, NULL),
            ('k_od2', 'passthrough', 'Cloud Storage', NULL, NULL, 1.0, NULL);
    """)
    create_projection_view(conn)
    rows = conn.execute("""
        SELECT commitment_basis, aws_amortized_cost, aws_cost_od_est, projection_note
        FROM gcp_projection
        ORDER BY aws_li_key
    """).fetchall()

    for basis, billed, est, note in rows:
        assert basis == "on_demand"
        assert billed == pytest.approx(est)
        assert note is None or "AWS list price estimated" not in note


def test_estimated_rows_carry_disclosure(base_db):
    """T5.6: Every row with an estimate has a note naming the assumed discount."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_spot', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'Spot', 'USE1-SpotUsage:c4.large', 'RunInstances', true, 100, 30.0),
            ('k_ri1', 'Amazon RDS', 'us-east-1', 'us-east1', 'DiscountedUsage', 'Reserved', 'RDS:db.t3.medium', 'RunInstances', true, 100, 50.0),
            ('k_ri3', 'Amazon EC2', 'us-east-1', 'us-east1', 'DiscountedUsage', 'Committed', 'HeavyUsage:c5.xlarge:3yr', 'RunInstances', true, 100, 40.0);
        INSERT INTO aws_li_to_gcp_li (aws_li_key, strategy, gcp_service, gcp_sku_id, component, unit_multiplier, projection_note) VALUES
            ('k_spot', 'passthrough', 'Compute Engine', NULL, NULL, 1.0, 'Spot instance note'),
            ('k_ri1', 'passthrough', 'Cloud SQL', NULL, NULL, 1.0, NULL),
            ('k_ri3', 'passthrough', 'Compute Engine', NULL, NULL, 1.0, 'Existing note');
    """)
    create_projection_view(conn)
    rows = conn.execute("""
        SELECT aws_li_key, commitment_basis, projection_note
        FROM gcp_projection
        ORDER BY aws_li_key
    """).fetchall()

    row_map = {r[0]: (r[1], r[2]) for r in rows}

    # Spot
    basis, note = row_map['k_spot']
    assert basis == 'spot'
    assert '70% Spot discount' in note
    assert 'Spot instance note' in note

    # 1yr RI (term not stated on the bill)
    basis, note = row_map['k_ri1']
    assert basis == 'committed_1yr'
    assert '38% 1-year RI discount' in note
    assert 'term not stated on the bill' in note

    # 3yr RI
    basis, note = row_map['k_ri3']
    assert basis == 'committed_3yr'
    assert '52% 3-year RI discount' in note
    assert 'Existing note' in note


def test_commitment_fee_not_grossed_up(base_db):
    """C3.1: A row whose operation reads '3yr No Upfront : CSP : $5.0 Hourly commitment' has aws_cost_od_est == aws_amortized_cost."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_sp_fee', 'Savings Plans for Compute usage', 'us-east-1', 'us-east1', 'Usage', 'OnDemand', 'Compute Savings Plans', '3yr No Upfront : CSP : $5.0 Hourly commitment', false, 744, 3720.0);
        INSERT INTO aws_li_to_gcp_li (aws_li_key, strategy, gcp_service, gcp_sku_id, component, unit_multiplier, projection_note) VALUES
            ('k_sp_fee', 'ignore', 'Compute Engine', NULL, NULL, 1.0, NULL);
    """)
    create_projection_view(conn)
    row = conn.execute("""
        SELECT commitment_basis, aws_amortized_cost, aws_cost_od_est, projection_note
        FROM gcp_projection
        WHERE aws_li_key = 'k_sp_fee'
    """).fetchone()

    assert row[0] == "on_demand"
    assert row[2] == pytest.approx(row[1])
    assert row[2] == pytest.approx(3720.0)
    assert row[3] is None or "AWS list price estimated" not in row[3]


def test_commitment_fee_not_workload():
    """C3.2: Commitment charges/fees are not workload in ingest classification."""
    from ingest import _is_workload_py
    assert _is_workload_py(
        "Usage",
        "Savings Plans for Compute usage",
        "Compute Savings Plans",
        "3yr No Upfront : CSP : $5.0 Hourly commitment"
    ) is False
    assert _is_workload_py(
        "Usage",
        "Amazon EC2",
        "HeavyUsage",
        "Reserved instance fee for m5.xlarge"
    ) is False


def test_discounted_usage_still_grossed_up(base_db):
    """C3.3: A usage row covered by a Savings Plan or RI keeps its divisor (guard against over-matching)."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_sp_usage', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'Committed', 'USE1-BoxUsage:c5.xlarge', 'RunInstances covered by Compute Savings Plans', true, 100, 70.0);
        INSERT INTO aws_li_to_gcp_li (aws_li_key, strategy, gcp_service, gcp_sku_id, component, unit_multiplier, projection_note) VALUES
            ('k_sp_usage', 'passthrough', 'Compute Engine', NULL, NULL, 1.0, NULL);
    """)
    create_projection_view(conn)
    row = conn.execute("""
        SELECT commitment_basis, aws_amortized_cost, aws_cost_od_est, projection_note
        FROM gcp_projection
        WHERE aws_li_key = 'k_sp_usage'
    """).fetchone()

    # Still classified as committed (defaults to 1yr since no 3yr term in usage text)
    assert row[0] == "committed_1yr"
    # Grossed up with 1yr RI default (0.38)
    assert row[2] == pytest.approx(70.0 / (1.0 - 0.38))
    assert row[2] > 70.0
    assert "38% 1-year RI discount" in row[3]


def test_no_basis_bucket_has_zero_gcp(base_db):
    """C3.4: No commitment_basis bucket holds material AWS cost against $0.00 of GCP."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_ri_valid', 'Amazon EC2', 'us-east-1', 'us-east1', 'DiscountedUsage', 'Committed', 'USE1-BoxUsage:c5.xlarge:3yr', 'RunInstances', true, 100, 100.0),
            ('k_spot_valid', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'Spot', 'USE1-SpotUsage:c4.large', 'RunInstances', true, 100, 30.0);
        INSERT INTO aws_li_to_gcp_li (aws_li_key, strategy, gcp_service, gcp_sku_id, component, unit_multiplier, projection_note) VALUES
            ('k_ri_valid', 'map', 'Compute Engine', 'SKU-VM', 'core', 1.0, NULL),
            ('k_spot_valid', 'map', 'Compute Engine', 'SKU-VM', 'core', 1.0, NULL);
        INSERT INTO gcp_sku_rates VALUES
            ('SKU-VM', 'Compute Engine', 'VM', 'Compute', 'CPU', 'OnDemand', 'us-east1', 'h', 0.50, 'cat', 'url');
    """)
    create_projection_view(conn)
    buckets = conn.execute("""
        SELECT commitment_basis, SUM(aws_cost_od_est), SUM(gcp_projected_cost)
        FROM gcp_projection
        GROUP BY commitment_basis
    """).fetchall()

    for basis, aws_tot, gcp_tot in buckets:
        if (aws_tot or 0.0) > 50.0:
            assert (gcp_tot or 0.0) > 0.0, f"Bucket {basis} has material AWS cost (${aws_tot:,.2f}) against $0.00 GCP"

