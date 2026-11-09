import json
import os
import sys
import duckdb
import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from passthrough_enforcer import enforce_assumption_passthroughs


def _build_test_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            product_code VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            pricing_unit VARCHAR,
            instance_type VARCHAR,
            mechanic_group VARCHAR,
            mechanic_rule VARCHAR,
            is_workload BOOLEAN,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR PRIMARY KEY,
            gcp_service VARCHAR,
            component VARCHAR,
            strategy VARCHAR,
            unit_multiplier DOUBLE,
            projection_note VARCHAR,
            passthrough_reason VARCHAR
        );
    """)
    # Add a MemoryDB row and an S3 row
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('mdb1', 'Amazon MemoryDB', 'AmazonMemoryDB', 'APS1-NodeUsage:db.r6g.large', 'CreateDBNode', 'Hrs', 'db.r6g.large', 'flat_hourly', 'memorydb_node_hours', TRUE, 150.0),
            ('s3_1', 'Amazon Simple Storage Service', 'AmazonS3', 'TimedStorage-ByteHrs', 'StandardStorage', 'GB-Mo', '', 'object_storage', 's3_standard', TRUE, 45.0);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('mdb1', 'Cloud Memorystore for Redis', 'cache', 'map', 1.0, 'Memorystore Redis tier', NULL),
            ('s3_1', 'Cloud Storage', 'storage', 'map', 1.0, 'Standard storage', NULL);
    """)
    return con


def test_passthrough_enforcer_when_disabled(tmp_path):
    con = _build_test_db()
    # No snapshot written -> no passthroughs
    res = enforce_assumption_passthroughs(job_dir_or_db=str(tmp_path), conn=con)
    assert res == {}

    # Check rows untouched
    strat = con.execute("SELECT strategy FROM aws_li_to_gcp_li WHERE aws_li_key = 'mdb1'").fetchone()[0]
    assert strat == "map"


def test_passthrough_enforcer_when_enabled(tmp_path):
    con = _build_test_db()
    snap = {
        "passthrough": {
            "memorydb_node_gib": True
        }
    }
    with open(tmp_path / "assumptions.json", "w", encoding="utf-8") as f:
        json.dump(snap, f)

    res = enforce_assumption_passthroughs(job_dir_or_db=str(tmp_path), conn=con)
    assert "memorydb_node_gib" in res
    assert res["memorydb_node_gib"]["rows"] == 1
    assert res["memorydb_node_gib"]["usd"] == 150.0

    # Verify MemoryDB row flipped
    row = con.execute("""
        SELECT strategy, unit_multiplier, passthrough_reason, projection_note
        FROM aws_li_to_gcp_li WHERE aws_li_key = 'mdb1'
    """).fetchone()
    assert row[0] == "passthrough"
    assert row[1] == 1.0
    assert row[2] == "operator_assumption"
    assert "[passthrough: forced via assumption memorydb_node_gib]" in row[3]

    # Verify S3 row untouched
    s3_row = con.execute("""
        SELECT strategy, passthrough_reason
        FROM aws_li_to_gcp_li WHERE aws_li_key = 's3_1'
    """).fetchone()
    assert s3_row[0] == "map"
    assert s3_row[1] is None


def test_passthrough_enforcer_idempotent(tmp_path):
    con = _build_test_db()
    snap = {
        "passthrough": {
            "memorydb_node_gib": True
        }
    }
    with open(tmp_path / "assumptions.json", "w", encoding="utf-8") as f:
        json.dump(snap, f)

    # First run
    enforce_assumption_passthroughs(job_dir_or_db=str(tmp_path), conn=con)
    note1 = con.execute("SELECT projection_note FROM aws_li_to_gcp_li WHERE aws_li_key = 'mdb1'").fetchone()[0]

    # Second run
    enforce_assumption_passthroughs(job_dir_or_db=str(tmp_path), conn=con)
    note2 = con.execute("SELECT projection_note FROM aws_li_to_gcp_li WHERE aws_li_key = 'mdb1'").fetchone()[0]

    assert note1 == note2
    assert note2.count("[passthrough: forced via assumption memorydb_node_gib]") == 1


def test_passthrough_enforcer_collapses_breakdown_components(tmp_path):
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR,
            product VARCHAR,
            product_code VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            pricing_unit VARCHAR,
            instance_type VARCHAR,
            mechanic_group VARCHAR,
            mechanic_rule VARCHAR,
            is_workload BOOLEAN,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            gcp_service VARCHAR,
            component VARCHAR,
            strategy VARCHAR,
            unit_multiplier DOUBLE,
            projection_note VARCHAR,
            passthrough_reason VARCHAR
        );
    """)
    # Add a Glue row that was broken down into 2 components (core + ram)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('glue1', 'AWS Glue', 'AWSGlue', 'DPU-Hour', 'RunJob', 'Hrs', '', 'compute_breakdown', 'glue_dpu', TRUE, 80.0);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('glue1', 'Cloud Dataflow', 'core', 'break_down', 4.0, '4 vCPUs per DPU', NULL),
            ('glue1', 'Cloud Dataflow', 'ram', 'break_down', 16.0, '16 GiB per DPU', NULL);
    """)
    snap = {
        "passthrough": {
            "glue_dpu_vcpu": True
        }
    }
    with open(tmp_path / "assumptions.json", "w", encoding="utf-8") as f:
        json.dump(snap, f)

    res = enforce_assumption_passthroughs(job_dir_or_db=str(tmp_path), conn=con)
    assert "glue_dpu_vcpu" in res
    assert res["glue_dpu_vcpu"]["rows"] == 1
    assert res["glue_dpu_vcpu"]["usd"] == 80.0

    # Verify that aws_li_to_gcp_li was collapsed to EXACTLY ONE row
    rows = con.execute("SELECT strategy, unit_multiplier, component, passthrough_reason FROM aws_li_to_gcp_li WHERE aws_li_key = 'glue1'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "passthrough"
    assert rows[0][1] == 1.0
    assert rows[0][2] is None
    assert rows[0][3] == "operator_assumption"
