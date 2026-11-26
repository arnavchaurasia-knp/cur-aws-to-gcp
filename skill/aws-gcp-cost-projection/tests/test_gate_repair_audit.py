import json
import os
import subprocess
import sys
import duckdb
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_HERE)
_SCRIPTS_DIR = os.path.join(_SKILL_DIR, "scripts")

_NO_NULL_PROJECTED_COST_SQL = """SELECT
(SELECT count(*) FROM gcp_projection WHERE strategy IN ('map','break_down')
AND gcp_projected_cost IS NULL AND aws_amortized_cost > 1) +
(SELECT count(*) FROM gcp_projection WHERE is_workload AND strategy NOT IN ('ignore')
AND (gcp_service IS NULL OR TRIM(gcp_service) = '') AND aws_amortized_cost > 1) +
(SELECT count(*) FROM gcp_projection WHERE is_workload AND strategy NOT IN ('ignore')
AND gcp_service IS NOT NULL AND (LOWER(gcp_service) LIKE 'amazon %'
OR LOWER(gcp_service) LIKE 'aws %' OR LOWER(gcp_service) LIKE 'amazon%')
AND aws_amortized_cost > 5)"""


def test_phase4_no_null_projected_cost_repair_clears_echo_and_no_rate(tmp_path):
    """WO-3 acceptance test:
    Construct a violating DB with:
      - an echo service ('Amazon S3')
      - an unreachable rate (map with invalid gcp_sku_id -> null cost)
      - a null gcp_service on a workload row
    Assert Phase 4 gate CheckSQL fails (>0).
    Run repair (validate_fix.py --repair-only + projection_view.py).
    Assert Phase 4 gate CheckSQL passes (== 0) with no LLM intervention.
    """
    job_dir = tmp_path
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(audit_dir / "projection.duckdb")

    con = duckdb.connect(db_path)
    con.execute("""
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
            projection_note VARCHAR,
            instance_type VARCHAR,
            instance_arch VARCHAR
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR PRIMARY KEY,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            gcp_sku_name VARCHAR,
            component VARCHAR,
            strategy VARCHAR,
            unit_multiplier DOUBLE,
            projection_note VARCHAR,
            passthrough_reason VARCHAR,
            decided_by VARCHAR,
            mapping_confidence DOUBLE,
            row_state VARCHAR
        );
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id VARCHAR,
            region VARCHAR,
            pricing_type VARCHAR,
            rate_usd DOUBLE,
            gcp_service VARCHAR,
            gcp_sku_name VARCHAR
        );
    """)

    # Populate valid rate for a good SKU
    con.execute("""
        INSERT INTO gcp_sku_rates VALUES
            ('GOOD-SKU-1111', 'us-central1', 'OnDemand', 0.05, 'Compute Engine', 'Standard Storage');
    """)

    # Row 1: Echo service ('Amazon S3')
    # Row 2: Map with unresolvable SKU rate ('MISSING-SKU-9999')
    # Row 3: Null gcp_service on workload row
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('r1_echo', 'Amazon Simple Storage Service', 'TimedStorage-ByteHrs', 'StandardStorage',
             'us-east-1', 'us-central1', 'OnDemand', 'Usage', TRUE, 1000.0, 25.0, '', NULL, NULL),
            ('r2_no_rate', 'Amazon Elastic Compute Cloud', 'BoxUsage:c5.xlarge', 'RunInstances',
             'us-east-1', 'us-central1', 'OnDemand', 'Usage', TRUE, 100.0, 50.0, '', NULL, NULL),
            ('r3_null_svc', 'AWS Lambda', 'Lambda-GB-Second', 'Invoke',
             'us-east-1', 'us-central1', 'OnDemand', 'Usage', TRUE, 50000.0, 15.0, '', NULL, NULL);

        INSERT INTO aws_li_to_gcp_li VALUES
            ('r1_echo', 'Amazon S3', 'GOOD-SKU-1111', 'Standard Storage',
             'storage', 'map', 1.0, '', NULL, 'llm', 0.9, 'active'),
            ('r2_no_rate', 'Compute Engine', 'MISSING-SKU-9999', 'Missing Sku',
             'compute', 'map', 1.0, '', NULL, 'llm', 0.9, 'active'),
            ('r3_null_svc', NULL, NULL, NULL,
             'invocations', 'passthrough', 1.0, '', NULL, 'llm', 0.5, 'active');
    """)
    con.close()

    # Create projection view
    res = subprocess.run([
        sys.executable, os.path.join(_SCRIPTS_DIR, "projection_view.py"), db_path
    ], capture_output=True, text=True)
    assert res.returncode == 0, f"projection_view failed: {res.stderr}"

    con = duckdb.connect(db_path)
    violations_before = con.execute(_NO_NULL_PROJECTED_COST_SQL).fetchone()[0]
    con.close()
    assert violations_before > 0, f"Expected violations before repair, got {violations_before}"

    # Run repair: validate_fix.py --repair-only + projection_view.py
    repair_res = subprocess.run([
        sys.executable, os.path.join(_SCRIPTS_DIR, "validate_fix.py"), "--repair-only", str(job_dir)
    ], capture_output=True, text=True)
    assert repair_res.returncode == 0, f"validate_fix repair failed: {repair_res.stderr}\n{repair_res.stdout}"

    # Recreate projection view
    res2 = subprocess.run([
        sys.executable, os.path.join(_SCRIPTS_DIR, "projection_view.py"), db_path
    ], capture_output=True, text=True)
    assert res2.returncode == 0, f"projection_view failed after repair: {res2.stderr}"

    # Assert gate passes cleanly
    con = duckdb.connect(db_path)
    violations_after = con.execute(_NO_NULL_PROJECTED_COST_SQL).fetchone()[0]
    con.close()
    assert violations_after == 0, f"Expected 0 violations after repair, got {violations_after}"
