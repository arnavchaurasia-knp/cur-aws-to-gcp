#!/usr/bin/env python3
"""tests/test_accuracy_report.py — Test accuracy scorecard, diff, and threshold checks."""

import json
import os
import subprocess
import sys
import tempfile
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from accuracy_report import (
    compute_scorecard,
    compute_portfolio_scorecard,
    compute_diff,
    check_thresholds,
)


def _create_sample_db(db_path: str, is_baseline: bool = True):
    """Create a realistic duckdb for testing scorecard calculations."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            pricing_unit VARCHAR,
            aws_amortized_cost DOUBLE,
            aws_od_cost DOUBLE,
            od_basis VARCHAR,
            mechanic_group VARCHAR,
            mechanic_rule VARCHAR,
            is_workload BOOLEAN
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            strategy VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            decided_by VARCHAR
        );
        CREATE TABLE gcp_projection (
            aws_li_key VARCHAR,
            product VARCHAR,
            aws_amortized_cost DOUBLE,
            aws_od_cost DOUBLE,
            od_basis VARCHAR,
            gcp_projected_cost DOUBLE,
            strategy VARCHAR,
            gcp_service VARCHAR,
            decided_by VARCHAR,
            is_workload BOOLEAN
        );
    """)

    # Baseline items
    ec2_gcp = 95.0 if is_baseline else 90.0
    rds_gcp = 48.0 if is_baseline else 45.0
    s3_gcp = 21.0 if is_baseline else 18.0

    con.execute(f"""
        INSERT INTO aws_li_catalog VALUES
        ('k1', 'AmazonEC2', 'BoxUsage:t3.medium', 'RunInstances', 'Hrs', 100.0, 100.0, 'public_od', 'compute_breakdown', 'spec:ec2.instance_hour', TRUE),
        ('k2', 'AmazonRDS', 'Aurora:ServerlessV2Usage', '', 'ACU-Hr', 50.0, 50.0, 'reconstructed_default', 'managed_db', 'spec:rds.aurora_serverless_v2', TRUE),
        ('k3', 'AmazonS3', 'TimedStorage-ByteHrs', '', 'GB-Mo', 20.0, 20.0, 'public_od', 'object_storage', 'spec:s3.storage', TRUE),
        ('k4', 'AmazonVPC', 'NatGateway-Hours', '', 'Hrs', 30.0, 30.0, 'public_od', 'misc', 'spec_gap:vpc', TRUE),
        ('k5', 'AmazonRoute53', 'Zone-Queries', '', 'Queries', 10.0, 10.0, 'amortized_fallback', 'misc', 'misc', TRUE);

        INSERT INTO aws_li_to_gcp_li VALUES
        ('k1', 'map', 'Compute Engine', 'sku_ec2', 'family_mapper.py'),
        ('k2', 'break_down', 'Cloud SQL', 'sku_rds', 'apply_static_mappings.py'),
        ('k3', 'map', 'Cloud Storage', 'sku_s3', 'apply_static_mappings.py'),
        ('k4', 'passthrough', 'Cloud NAT', NULL, 'auto_review.py'),
        ('k5', 'passthrough', 'Cloud DNS', NULL, 'llm');

        INSERT INTO gcp_projection VALUES
        ('k1', 'AmazonEC2', 100.0, 100.0, 'public_od', {ec2_gcp}, 'map', 'Compute Engine', 'family_mapper.py', TRUE),
        ('k2', 'AmazonRDS', 50.0, 50.0, 'reconstructed_default', {rds_gcp}, 'break_down', 'Cloud SQL', 'apply_static_mappings.py', TRUE),
        ('k3', 'AmazonS3', 20.0, 20.0, 'public_od', {s3_gcp}, 'map', 'Cloud Storage', 'apply_static_mappings.py', TRUE),
        ('k4', 'AmazonVPC', 30.0, 30.0, 'public_od', 30.0, 'passthrough', 'Cloud NAT', 'auto_review.py', TRUE),
        ('k5', 'AmazonRoute53', 10.0, 10.0, 'amortized_fallback', 10.0, 'passthrough', 'Cloud DNS', 'llm', TRUE);
    """)
    con.close()


def test_scorecard_calculation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "projection-audit", "projection.duckdb")
        _create_sample_db(db_path, is_baseline=True)

        sc = compute_scorecard(db_path)
        assert sc["status"] == "ok"
        h = sc["headline"]
        assert h["workload_rows"] == 5
        assert h["aws_amortized_cost"] == 210.0
        assert h["aws_od_cost"] == 210.0
        assert h["gcp_projected_cost"] == 204.0  # 95 + 48 + 21 + 30 + 10

        # Coverage mix
        # k1 (100) + k2 (50) + k3 (20) = 170 deterministic
        # k4 (30) + k5 (10) = 40 passthrough
        mix = sc["coverage_mix"]
        assert mix["deterministic"]["aws_od"] == 170.0
        assert mix["deterministic"]["rows"] == 3
        assert pytest.approx(mix["deterministic"]["pct_of_spend"], 0.1) == (170.0 / 210.0) * 100.0
        assert mix["passthrough"]["aws_od"] == 40.0
        assert mix["passthrough"]["rows"] == 2

        # Determinism holes
        dh = sc["determinism_holes"]
        assert dh["spec_gap"]["total_usd"] == 30.0  # k4 VPC
        assert len(dh["spec_gap"]["by_service"]) == 1
        assert dh["spec_gap"]["by_service"][0]["service"] == "vpc"
        assert dh["misc"]["total_usd"] == 40.0      # k4 + k5 in misc group

        # OD-basis mix
        ob = sc["od_basis_mix"]
        assert ob["public_od"]["usd"] == 150.0  # k1 (100) + k3 (20) + k4 (30)
        assert ob["reconstructed_default"]["usd"] == 50.0  # k2
        assert ob["amortized_fallback"]["usd"] == 10.0     # k5


def test_diff_mode():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_a = os.path.join(tmp_dir, "run_a", "projection-audit", "projection.duckdb")
        db_b = os.path.join(tmp_dir, "run_b", "projection-audit", "projection.duckdb")
        _create_sample_db(db_a, is_baseline=True)
        _create_sample_db(db_b, is_baseline=False)

        sc_a = compute_scorecard(db_a)
        sc_b = compute_scorecard(db_b)
        diff = compute_diff(sc_a, sc_b)

        assert diff["headline_delta"]["baseline_gcp_projected"] == 204.0
        assert diff["headline_delta"]["new_gcp_projected"] == 193.0  # 90+45+18+30+10
        assert diff["headline_delta"]["gcp_projected_delta"] == -11.0

        # Moved services
        moved = diff["moved_services"]
        assert len(moved) == 3
        services_moved = {m["service"] for m in moved}
        assert "AmazonEC2" in services_moved
        assert "AmazonRDS" in services_moved
        assert "AmazonS3" in services_moved


def test_threshold_checks():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "projection.duckdb")
        _create_sample_db(db_path, is_baseline=True)
        sc = compute_scorecard(db_path)

        # Baseline: 170/210 = 80.95% deterministic coverage, spec gap 30/210 = 14.28%
        # Threshold: min_deterministic_cov=80.0 should pass, max_spec_gap_pct=15.0 should pass
        passed, checks = check_thresholds(sc, min_deterministic_cov=80.0, max_spec_gap_pct=15.0)
        assert passed is True
        for c in checks:
            assert c["passed"] is True

        # Stricter threshold: min_deterministic_cov=90.0 should fail
        passed, checks = check_thresholds(sc, min_deterministic_cov=90.0, max_spec_gap_pct=15.0)
        assert passed is False
        cov_check = next(c for c in checks if c["name"] == "min_deterministic_coverage")
        assert cov_check["passed"] is False


def test_cli_json_and_exit_codes():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "projection.duckdb")
        _create_sample_db(db_path, is_baseline=True)

        scripts_dir = os.path.join(os.path.dirname(__file__), "..", "scripts")
        script_path = os.path.join(scripts_dir, "accuracy_report.py")

        # Run with --json
        res = subprocess.run(
            [sys.executable, script_path, db_path, "--json"],
            capture_output=True, text=True
        )
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data["status"] == "ok"
        assert data["headline"]["workload_rows"] == 5

        # Run with --check (should pass with relaxed threshold)
        res_pass = subprocess.run(
            [sys.executable, script_path, db_path, "--check", "--min-deterministic-cov", "80.0", "--max-spec-gap-pct", "15.0"],
            capture_output=True, text=True
        )
        assert res_pass.returncode == 0
        assert "ALL ACCURACY THRESHOLDS PASSED" in res_pass.stdout

        # Run with --check (should fail with strict threshold)
        res_fail = subprocess.run(
            [sys.executable, script_path, db_path, "--check", "--min-deterministic-cov", "95.0"],
            capture_output=True, text=True
        )
        assert res_fail.returncode == 1
        assert "BREACH" in res_fail.stdout


def test_graceful_degradation_on_missing_columns():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "legacy.duckdb")
        con = duckdb.connect(db_path)
        # Minimal legacy catalog missing od_basis, mechanic_rule, aws_od_cost
        con.execute("""
            CREATE TABLE aws_li_catalog (
                aws_li_key VARCHAR PRIMARY KEY,
                product VARCHAR,
                aws_amortized_cost DOUBLE
            );
            INSERT INTO aws_li_catalog VALUES
            ('l1', 'AmazonEC2', 100.0);
        """)
        con.close()

        sc = compute_scorecard(db_path)
        assert sc["status"] == "ok"
        assert sc["headline"]["workload_rows"] == 1
        assert sc["headline"]["aws_amortized_cost"] == 100.0
        # od_basis gracefully fell back to amortized_fallback
        assert sc["od_basis_mix"]["amortized_fallback"]["usd"] == 100.0

