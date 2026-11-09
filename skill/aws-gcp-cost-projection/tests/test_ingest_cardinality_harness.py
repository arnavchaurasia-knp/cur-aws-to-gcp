"""
Unit and integration tests for Phase 0 Cardinality & Loss Reconciliation Harness (WORKORDER_region_fidelity_and_parts_dispatch).
Tests the ingest_stats table creation, cardinality conservation invariant, per-service breakdown,
and mismatch warning / data_gaps.json sync.
"""
import os
import sys
import json
import duckdb
import pytest

_SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SKILL, "scripts"))

from ingest import _compute_and_record_ingest_stats, _sync_cardinality_data_gaps


def test_ingest_stats_table_schema_and_conservation(tmp_path):
    """Verify ingest_stats records accurate counts, preserves conservation invariant, and records breakdown."""
    db_path = str(tmp_path / "projection.duckdb")
    conn = duckdb.connect(db_path)

    # Mock aws_raw
    conn.execute("CREATE TABLE aws_raw (id INT, cost DOUBLE)")
    conn.execute("INSERT INTO aws_raw VALUES (1, 10.0), (2, 20.0), (3, 30.0)")

    # Mock aws_li_catalog
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            product_code VARCHAR,
            aws_amortized_cost DOUBLE
        )
    """)
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'Amazon EC2', 'AmazonEC2', 30.0),
            ('k2', 'Amazon S3', 'AmazonS3', 30.0)
    """)

    # Mock _raw_flat view representing dedup input
    conn.execute("""
        CREATE TEMP VIEW _raw_flat AS
        SELECT
            'Amazon EC2' as product, 'AmazonEC2' as product_code,
            'box' as usage_type, 'run' as operation, 'us-east-1' as aws_region,
            'OnDemand' as pricing_model, 'Usage' as line_item_type, true as is_workload,
            '' as license_model, '' as operating_system, '' as database_engine,
            '' as deployment_option, '' as volume_type, 'Hrs' as pricing_unit,
            '2026-11' as billing_month, 10.0 as row_cost
        UNION ALL
        SELECT
            'Amazon EC2', 'AmazonEC2', 'box', 'run', 'us-east-1',
            'OnDemand', 'Usage', true, '', '', '', '', '', 'Hrs', '2026-11', 20.0
        UNION ALL
        SELECT
            'Amazon S3', 'AmazonS3', 'storage', 'put', 'us-east-1',
            'OnDemand', 'Usage', true, '', '', '', '', '', 'GB-Mo', '2026-11', 30.0
    """)

    raw_cost_sum = _compute_and_record_ingest_stats(conn, job_dir=str(tmp_path))

    # Assert ingest_stats exists and contains expected record
    stats = conn.execute("SELECT * FROM ingest_stats").fetchone()
    assert stats is not None
    cols = [c[0] for c in conn.execute("DESCRIBE ingest_stats").fetchall()]
    row = dict(zip(cols, stats))

    assert row["raw_row_count"] == 3
    assert row["distinct_dedup_tuples_in"] == 2
    assert row["catalog_row_count"] == 2
    assert row["invariant_holds"] is True
    assert abs(row["raw_cost_sum"] - 60.0) < 1e-4
    assert abs(row["catalog_cost_sum"] - 60.0) < 1e-4

    # Verify JSON breakdown
    breakdown = json.loads(row["service_breakdown"])
    assert "AmazonEC2" in breakdown
    assert breakdown["AmazonEC2"]["in"]["rows"] == 2
    assert breakdown["AmazonEC2"]["in"]["distinct_tuples"] == 1
    assert breakdown["AmazonEC2"]["out"]["distinct_tuples"] == 1

    assert "AmazonS3" in breakdown
    assert breakdown["AmazonS3"]["in"]["rows"] == 1
    assert breakdown["AmazonS3"]["in"]["distinct_tuples"] == 1
    assert breakdown["AmazonS3"]["out"]["distinct_tuples"] == 1

    conn.close()


def test_cardinality_conservation_violation_syncs_data_gaps(tmp_path):
    """When distinct_in != cat_rows, data_gaps.json must record the violation."""
    gaps_file = tmp_path / "data_gaps.json"

    _sync_cardinality_data_gaps(str(tmp_path), distinct_in=5, cat_rows=4, raw_cost=100.0, cat_cost=80.0)

    assert gaps_file.exists()
    with open(gaps_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("count") >= 1
    items = data.get("items", [])
    violation = next((it for it in items if it.get("type") == "cardinality_conservation_violation"), None)
    assert violation is not None
    assert violation["distinct_dedup_tuples_in"] == 5
    assert violation["catalog_row_count"] == 4
    assert violation["delta"] == -1
    assert violation["cost"] == 20.0
