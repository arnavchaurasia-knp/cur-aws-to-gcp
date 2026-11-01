#!/usr/bin/env python3
"""tests/test_real_discount_resolution.py — Tests for WORKORDER_real_discount_resolution.

Verifies the 5-tier precedence hierarchy:
  1. publicOnDemandCost on the row (public_od).
  2. Effective rate from the row itself (reconstructed_resolved).
  3. Per-commitment effective rate from covered-usage vs public-OD totals (reconstructed_resolved).
  4. Portfolio default assumptions (reconstructed_default).
  5. Amortized fallback when no rate can be resolved (amortized_fallback).

Also verifies:
  - Per-commitment granularity: Compute SP (~40%) and EC2 RI (~55%) never share a blended rate.
  - Commitment fee rows are not grossed up.
  - Accuracy report reflects reconstructed_resolved.
"""

import os
import sys
import duckdb
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from ingest import apply_od_basis
from accuracy_report import compute_scorecard


@pytest.fixture
def base_db():
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            aws_region VARCHAR,
            gcp_region VARCHAR,
            pricing_model VARCHAR,
            line_item_type VARCHAR,
            is_workload BOOLEAN,
            total_usage DOUBLE,
            aws_amortized_cost DOUBLE,
            public_on_demand_cost DOUBLE,
            aws_od_cost DOUBLE,
            sp_ri_discount_pct DOUBLE,
            od_basis VARCHAR,
            commitment_arn VARCHAR,
            public_on_demand_rate DOUBLE,
            savings_plan_rate DOUBLE,
            aws_effective_unit_rate DOUBLE
        );
    """)
    return conn


def test_public_od_precedence(base_db):
    """Tier 1: publicOnDemandCost on the row wins over commitment or default rates."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog (
            aws_li_key, product, usage_type, operation, pricing_model, line_item_type,
            is_workload, total_usage, aws_amortized_cost, public_on_demand_cost, commitment_arn
        ) VALUES (
            'k1', 'AmazonEC2', 'BoxUsage:c5.xlarge', 'RunInstances', 'Committed',
            'SavingsPlanCoveredUsage', true, 100.0, 60.0, 100.0, 'arn:aws:savingsplans::123:sp-01'
        );
    """)

    apply_od_basis(conn)

    row = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k1'").fetchone()
    assert row[0] == 100.0
    assert row[1] == pytest.approx(0.40)
    assert row[2] == "public_od"


def test_ondemand_usage_is_public_od(base_db):
    """Tier 1: Genuine on-demand usage is public_od with 0% discount."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog (
            aws_li_key, product, usage_type, operation, pricing_model, line_item_type,
            is_workload, total_usage, aws_amortized_cost
        ) VALUES (
            'k_od', 'AmazonEC2', 'BoxUsage:t3.medium', 'RunInstances', 'OnDemand', 'Usage',
            true, 50.0, 50.0
        );
    """)

    apply_od_basis(conn)

    row = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k_od'").fetchone()
    assert row[0] == 50.0
    assert row[1] == 0.0
    assert row[2] == "public_od"


def test_row_rate_resolution_from_usage_and_rate(base_db):
    """Tier 2: Row carrying public_on_demand_rate and total_usage resolves effective OD."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog (
            aws_li_key, product, usage_type, operation, pricing_model, line_item_type,
            is_workload, total_usage, aws_amortized_cost, public_on_demand_rate
        ) VALUES (
            'k_rate', 'AmazonEC2', 'BoxUsage:m5.large', 'RunInstances', 'Committed',
            'SavingsPlanCoveredUsage', true, 100.0, 65.0, 1.00
        );
    """)

    apply_od_basis(conn)

    row = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k_rate'").fetchone()
    assert row[0] == 100.0  # 100 * 1.00
    assert row[1] == pytest.approx((100.0 - 65.0) / 100.0)  # 0.35
    assert row[2] == "reconstructed_resolved"


def test_row_rate_resolution_from_rates_ratio(base_db):
    """Tier 2: Row carrying public_on_demand_rate and savings_plan_rate resolves ratio."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog (
            aws_li_key, product, usage_type, operation, pricing_model, line_item_type,
            is_workload, total_usage, aws_amortized_cost, public_on_demand_rate, savings_plan_rate
        ) VALUES (
            'k_rates_ratio', 'AmazonEC2', 'BoxUsage:c5.2xlarge', 'RunInstances', 'Committed',
            'SavingsPlanCoveredUsage', true, 0.0, 70.0, 1.00, 0.70
        );
    """)

    apply_od_basis(conn)

    row = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k_rates_ratio'").fetchone()
    assert row[1] == pytest.approx(0.30)
    assert row[0] == pytest.approx(70.0 / (1.0 - 0.30))  # 100.0
    assert row[2] == "reconstructed_resolved"


def test_per_commitment_effective_rate_resolution(base_db):
    """Tier 3: Commitment ARN discount is derived from covered-usage vs public-OD totals
    and applied to member rows lacking public OD."""
    conn = base_db
    sp_arn = "arn:aws:savingsplans::123456789012:savingsplan/sp-compute-001"
    conn.execute("""
        INSERT INTO aws_li_catalog (
            aws_li_key, product, usage_type, operation, pricing_model, line_item_type,
            is_workload, total_usage, aws_amortized_cost, public_on_demand_cost, commitment_arn
        ) VALUES
            -- Row 1 has public_on_demand_cost -> reveals the commitment discount: 60 amortized / 100 OD = 40% discount
            ('k_sp_anchor', 'AmazonEC2', 'BoxUsage:c5.xlarge', 'RunInstances', 'Committed',
             'SavingsPlanCoveredUsage', true, 100.0, 60.0, 100.0, ?),
            -- Row 2 is covered by the SAME commitment ARN but public_on_demand_cost is NULL/0
            ('k_sp_target', 'AmazonEC2', 'BoxUsage:c5.2xlarge', 'RunInstances', 'Committed',
             'SavingsPlanCoveredUsage', true, 50.0, 30.0, NULL, ?);
    """, [sp_arn, sp_arn])

    apply_od_basis(conn)

    # Row 1 is public_od
    r1 = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k_sp_anchor'").fetchone()
    assert r1[0] == 100.0
    assert r1[1] == pytest.approx(0.40)
    assert r1[2] == "public_od"

    # Row 2 resolved the 40% discount from the commitment ARN
    r2 = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k_sp_target'").fetchone()
    assert r2[1] == pytest.approx(0.40)
    assert r2[0] == pytest.approx(30.0 / (1.0 - 0.40))  # 50.0
    assert r2[2] == "reconstructed_resolved"


def test_distinct_commitments_never_share_blended_rate(base_db):
    """Invariant: A Compute SP (~40%) and an EC2 Standard RI (~55%) must not share one blended rate."""
    conn = base_db
    sp_arn = "arn:aws:savingsplans::123456789012:savingsplan/sp-compute-001"
    ri_arn = "arn:aws:ec2:us-east-1:123456789012:reservation/res-ec2-002"

    conn.execute("""
        INSERT INTO aws_li_catalog (
            aws_li_key, product, usage_type, operation, pricing_model, line_item_type,
            is_workload, total_usage, aws_amortized_cost, public_on_demand_cost, commitment_arn
        ) VALUES
            -- SP commitment: 60/100 -> 40% discount
            ('k_sp_1', 'AmazonEC2', 'BoxUsage:c5.xlarge', 'RunInstances', 'Committed',
             'SavingsPlanCoveredUsage', true, 100.0, 60.0, 100.0, ?),
            ('k_sp_target', 'AmazonEC2', 'BoxUsage:c5.large', 'RunInstances', 'Committed',
             'SavingsPlanCoveredUsage', true, 50.0, 30.0, NULL, ?),

            -- RI commitment: 45/100 -> 55% discount
            ('k_ri_1', 'AmazonEC2', 'BoxUsage:m5.xlarge', 'RunInstances', 'Reserved',
             'DiscountedUsage', true, 100.0, 45.0, 100.0, ?),
            ('k_ri_target', 'AmazonEC2', 'BoxUsage:m5.large', 'RunInstances', 'Reserved',
             'DiscountedUsage', true, 50.0, 22.5, NULL, ?);
    """, [sp_arn, sp_arn, ri_arn, ri_arn])

    apply_od_basis(conn)

    sp_target = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k_sp_target'").fetchone()
    ri_target = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k_ri_target'").fetchone()

    # Verify SP resolved 40%
    assert sp_target[1] == pytest.approx(0.40)
    assert sp_target[0] == pytest.approx(50.0)
    assert sp_target[2] == "reconstructed_resolved"

    # Verify RI resolved 55% (distinct from SP, not blended)
    assert ri_target[1] == pytest.approx(0.55)
    assert ri_target[0] == pytest.approx(50.0)
    assert ri_target[2] == "reconstructed_resolved"


def test_commitment_fee_rows_not_grossed_up(base_db):
    """Anti-flattery guard: Recurring commitment fees must not be grossed up."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog (
            aws_li_key, product, usage_type, operation, pricing_model, line_item_type,
            is_workload, total_usage, aws_amortized_cost
        ) VALUES
            ('k_sp_fee', 'Savings Plans for Compute usage', 'Compute Savings Plans',
             '3yr No Upfront : CSP : $5.0 Hourly commitment', 'OnDemand', 'SavingsPlanRecurringFee',
             false, 744.0, 3720.0),
            ('k_ri_fee', 'AmazonEC2', 'HeavyUsage', 'Reserved instance fee for m5.xlarge',
             'OnDemand', 'RIFee', false, 1.0, 500.0);
    """)

    apply_od_basis(conn)

    rows = conn.execute("SELECT aws_li_key, aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog").fetchall()
    row_map = {r[0]: (r[1], r[2], r[3]) for r in rows}

    assert row_map['k_sp_fee'] == (3720.0, 0.0, 'amortized_fallback')
    assert row_map['k_ri_fee'] == (500.0, 0.0, 'amortized_fallback')


def test_portfolio_default_fallback(base_db):
    """Tier 4: Commitment row with no resolvable rates falls back to portfolio default."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog (
            aws_li_key, product, usage_type, operation, pricing_model, line_item_type,
            is_workload, total_usage, aws_amortized_cost
        ) VALUES
            ('k_unresolved_1yr', 'AmazonEC2', 'BoxUsage:r5.xlarge', 'RunInstances', 'Committed',
             'DiscountedUsage', true, 100.0, 62.0),
            ('k_unresolved_3yr', 'AmazonEC2', 'BoxUsage:r5.xlarge:3yr', 'RunInstances', 'Committed',
             'DiscountedUsage', true, 100.0, 48.0);
    """)

    apply_od_basis(conn, assumptions={"aws_ri_1yr_discount_pct": 0.38, "aws_ri_3yr_discount_pct": 0.52})

    r1 = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k_unresolved_1yr'").fetchone()
    r3 = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k_unresolved_3yr'").fetchone()

    # 1yr fallback: 0.38 default discount -> 62 / (1 - 0.38) = 100.0
    assert r1[1] == pytest.approx(0.38)
    assert r1[0] == pytest.approx(100.0)
    assert r1[2] == "reconstructed_default"

    # 3yr fallback: 0.52 default discount -> 48 / (1 - 0.52) = 100.0
    assert r3[1] == pytest.approx(0.52)
    assert r3[0] == pytest.approx(100.0)
    assert r3[2] == "reconstructed_default"


def test_amortized_fallback_no_discount(base_db):
    """Tier 5: Row with no discount or rates falls to amortized_fallback."""
    conn = base_db
    conn.execute("""
        INSERT INTO aws_li_catalog (
            aws_li_key, product, usage_type, operation, pricing_model, line_item_type,
            is_workload, total_usage, aws_amortized_cost
        ) VALUES (
            'k_misc', 'AWS Support', 'Support-Usage', 'SupportCharge', 'Other', 'Other',
            false, 1.0, 250.0
        );
    """)

    apply_od_basis(conn)

    r = conn.execute("SELECT aws_od_cost, sp_ri_discount_pct, od_basis FROM aws_li_catalog WHERE aws_li_key = 'k_misc'").fetchone()
    assert r[0] == 250.0
    assert r[1] == 0.0
    assert r[2] == "amortized_fallback"


def test_scorecard_tracks_reconstructed_resolved(tmp_path):
    """Accuracy report scorecard correctly tracks reconstructed_resolved alongside other bases."""
    from tests.test_accuracy_report import _create_sample_db
    db_path = str(tmp_path / "projection-audit" / "projection.duckdb")
    _create_sample_db(db_path)

    # Insert a reconstructed_resolved row into both tables
    conn = duckdb.connect(db_path)
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
        ('k_res', 'AmazonEC2', 'BoxUsage:c5.xlarge', 'RunInstances', 'Hrs', 30.0, 50.0, 'reconstructed_resolved', 'compute_breakdown', 'spec:ec2.instance_hour', TRUE);
        INSERT INTO aws_li_to_gcp_li VALUES
        ('k_res', 'map', 'Compute Engine', 'sku_ec2', 'spec:ec2');
        INSERT INTO gcp_projection VALUES
        ('k_res', 'AmazonEC2', 30.0, 50.0, 'reconstructed_resolved', 48.0, 'map', 'Compute Engine', 'spec:ec2', TRUE);
    """)
    conn.close()

    sc = compute_scorecard(db_path)
    assert sc["status"] == "ok"
    ob = sc["od_basis_mix"]

    assert "reconstructed_resolved" in ob
    assert ob["reconstructed_resolved"]["usd"] == 50.0
    assert ob["reconstructed_resolved"]["rows"] == 1
    assert ob["public_od"]["usd"] == 150.0
    assert ob["reconstructed_default"]["usd"] == 50.0
    assert ob["amortized_fallback"]["usd"] == 10.0
