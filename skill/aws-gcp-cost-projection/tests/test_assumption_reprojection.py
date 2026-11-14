#!/usr/bin/env python3
"""test_assumption_reprojection.py — Test that edited assumptions reach projection math (WORKORDER_round3_audit.md A-1, B-1, A-2)."""

import json
import os
import sys
import duckdb
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_TESTS_DIR)
_SCRIPTS_DIR = os.path.join(_SKILL_DIR, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from config_loader import load_assumptions, get_assumption_rejections
from projection_view import create_projection_view


@pytest.fixture
def mock_pipeline_db():
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            aws_region VARCHAR,
            gcp_region VARCHAR,
            line_item_type VARCHAR,
            pricing_model VARCHAR,
            operation VARCHAR,
            usage_type VARCHAR,
            is_workload BOOLEAN,
            total_usage DOUBLE,
            aws_amortized_cost DOUBLE,
            aws_od_cost DOUBLE,
            sp_ri_discount_pct DOUBLE,
            od_basis VARCHAR,
            instance_type VARCHAR,
            source_line_items BIGINT
        )
    """)
    conn.execute("""
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            strategy VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            gcp_machine_type VARCHAR,
            component VARCHAR,
            unit_multiplier DOUBLE,
            passthrough_reason VARCHAR,
            decided_by VARCHAR,
            rate_region_basis VARCHAR,
            rate_region_used VARCHAR,
            license_status VARCHAR,
            review_flags VARCHAR,
            mapping_confidence DOUBLE,
            projection_note VARCHAR,
            applied_directives VARCHAR,
            is_assumption BOOLEAN
        )
    """)
    conn.execute("""
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id VARCHAR,
            region VARCHAR,
            pricing_type VARCHAR,
            rate_usd DOUBLE,
            source VARCHAR
        )
    """)

    # SP row: amortized cost is $700.00
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k_sp_1',
            'Amazon Elastic Compute Cloud',
            'us-east-1',
            'us-central1',
            'SavingsPlanCoveredUsage',
            'OnDemand',
            'RunInstances',
            'USE1-BoxUsage:c5.large',
            TRUE,
            730.0,
            700.0,
            1000.0,
            0.30,
            'reconstructed_default',
            'c5.large',
            10
        )
    """)
    conn.execute("""
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_sp_1',
            'map',
            'Compute Engine',
            'SKU-TEST-1',
            'c2-standard-4',
            'core',
            1.0,
            NULL,
            'spec_dispatch',
            'exact',
            'us-central1',
            'unlicensed',
            NULL,
            1.0,
            'Mapped to Compute Engine',
            NULL,
            FALSE
        )
    """)
    conn.execute("""
        INSERT INTO gcp_sku_rates VALUES
        ('SKU-TEST-1', 'us-central1', 'OnDemand', 0.95, 'catalog'),
        ('SKU-TEST-1', 'us-central1', 'Commit1Yr', 0.65, 'catalog'),
        ('SKU-TEST-1', 'us-central1', 'Commit3Yr', 0.45, 'catalog')
    """)

    yield conn
    conn.close()


def test_edited_assumption_changes_reprojection_math(mock_pipeline_db):
    """A-1: When an assumption is edited (e.g. SP discount 30% -> 50%), rebuilding the projection view updates aws_od_cost."""
    conn = mock_pipeline_db

    # 1. Baseline: 30% SP discount -> $700 / (1 - 0.30) = $1,000.00
    create_projection_view(conn, dominant_region="us-central1", assumptions={"aws_sp_discount_pct": 0.30})
    row1 = conn.execute("SELECT aws_od_cost, aws_cost_od_est FROM gcp_projection WHERE aws_li_key = 'k_sp_1'").fetchone()
    assert row1 is not None
    assert abs(row1[0] - 1000.0) < 1e-2
    assert abs(row1[1] - 1000.0) < 1e-2

    # 2. Re-project with edited assumption: 50% SP discount -> $700 / (1 - 0.50) = $1,400.00
    create_projection_view(conn, dominant_region="us-central1", assumptions={"aws_sp_discount_pct": 0.50})
    row2 = conn.execute("SELECT aws_od_cost, aws_cost_od_est FROM gcp_projection WHERE aws_li_key = 'k_sp_1'").fetchone()
    assert row2 is not None
    assert abs(row2[0] - 1400.0) < 1e-2
    assert abs(row2[1] - 1400.0) < 1e-2

    # Total changes!
    assert row2[0] != row1[0]


def test_load_assumptions_picks_up_cwd_snapshot(tmp_path, monkeypatch):
    """A-1: load_assumptions() called with no arguments inspects cwd for assumptions.json."""
    monkeypatch.chdir(tmp_path)

    snapshot = {
        "values": {
            "aws_sp_discount_pct": 0.45,
            "lambda_mb_per_vcpu": 3000.0
        }
    }
    with open(tmp_path / "assumptions.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f)

    loaded = load_assumptions()
    assert loaded["aws_sp_discount_pct"] == 0.45
    assert loaded["lambda_mb_per_vcpu"] == 3000.0


def test_ratio_rejection_surfaces_warning(tmp_path):
    """A-2: An out-of-band ACU ratio is rejected, reverted, and surfaced in get_assumption_rejections()."""
    snapshot = {
        "values": {
            "aurora_acu_ram_gib": 7.0,
            "aurora_acu_vcpu": 2.0
        }
    }
    with open(tmp_path / "assumptions.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f)

    loaded = load_assumptions(str(tmp_path))
    # Must revert to valid default (2.0 / 0.25)
    assert loaded["aurora_acu_ram_gib"] == 2.0
    assert loaded["aurora_acu_vcpu"] == 0.25

    # Warning must be surfaced
    rejections = get_assumption_rejections()
    assert "aurora_acu" in rejections
    assert "out of band" in rejections["aurora_acu"]


def test_summary_and_export_diff_uses_od_cost(mock_pipeline_db):
    """B-1: Export queries and summary totals read aws_od_cost for OD-vs-OD Diff calculation."""
    conn = mock_pipeline_db
    import export_report

    create_projection_view(conn, dominant_region="us-central1", assumptions={"aws_sp_discount_pct": 0.30})

    q_summary = export_report._build_query(fx_rate=1.0, curr_code="USD", aws_fx_rate=1.0, conn=conn)
    rows = conn.execute(q_summary).fetchall()
    assert len(rows) == 1

    # On-Demand rate is 0.95 * 730 = 693.50. AWS OD is 1000.00.
    # Diff = 1000.00 - 693.50 = 306.50
    # Prior bug compared AWS Amortized ($700) - GCP OD ($693.50) = $6.50
    diff_val = rows[0][11]  # "Diff ($)"
    assert abs(diff_val - 306.50) < 1.0
