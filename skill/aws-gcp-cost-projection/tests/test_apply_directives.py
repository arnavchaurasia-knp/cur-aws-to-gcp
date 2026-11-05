#!/usr/bin/env python3
"""Tests for scripts/apply_directives.py."""
import json
import os
import tempfile
import duckdb
import pytest

from scripts.directives import Directive, save_ledger
from scripts.apply_directives import apply_directives_to_db


@pytest.fixture
def temp_job_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_dir = os.path.join(tmpdir, "projection-audit")
        os.makedirs(audit_dir, exist_ok=True)
        db_path = os.path.join(audit_dir, "projection.duckdb")

        con = duckdb.connect(db_path)
        con.execute("""
            CREATE TABLE aws_li_catalog (
                aws_li_key TEXT PRIMARY KEY,
                product TEXT,
                product_code TEXT,
                canonical_service TEXT,
                usage_type TEXT,
                operation TEXT,
                region TEXT,
                account_id TEXT,
                tags TEXT,
                mechanic_group TEXT
            );
            CREATE TABLE aws_li_to_gcp_li (
                aws_li_key TEXT PRIMARY KEY,
                gcp_service TEXT,
                strategy TEXT,
                is_workload BOOLEAN,
                projection_note TEXT
            );
        """)

        con.execute("""
            INSERT INTO aws_li_catalog VALUES
            ('k1', 'Amazon Elastic Compute Cloud', 'AmazonEC2', 'ec2', 'BoxUsage:t3.medium', 'RunInstances', 'us-east-1', '123', '{}', 'compute_breakdown'),
            ('k2', 'Amazon Simple Storage Service', 'AmazonS3', 's3', 'TimedStorage-ByteHrs', 'StandardStorage', 'us-east-1', '123', '{}', 'object_storage'),
            ('k3', 'AWS Cost & Usage Report', 'AWSBill', 'tax', 'Tax', '', '', '123', '{}', 'commitment_discount');

            INSERT INTO aws_li_to_gcp_li VALUES
            ('k1', 'Compute Engine', 'map', TRUE, 'baseline note'),
            ('k2', 'Cloud Storage', 'map', TRUE, 's3 note'),
            ('k3', 'Tax', 'passthrough', TRUE, '');
        """)
        con.close()

        yield tmpdir, db_path


def test_apply_directives_success(temp_job_env):
    tmpdir, db_path = temp_job_env

    d1 = Directive(
        id="d_s3_pass",
        author="user",
        selector={"service": "s3"},
        action={"type": "set_strategy", "value": "passthrough"},
    )
    d2 = Directive(
        id="d_note_k1",
        author="user",
        selector={"aws_li_keys": ["k1"]},
        action={"type": "annotate", "value": "Customer confirmed reserved capacity"},
    )
    save_ledger(tmpdir, [d1, d2])

    updated = apply_directives_to_db(db_path, check_only=False, job_dir=tmpdir)
    assert updated >= 2

    con = duckdb.connect(db_path, read_only=True)
    res = con.execute("SELECT aws_li_key, strategy, projection_note, applied_directives FROM aws_li_to_gcp_li ORDER BY aws_li_key").fetchall()
    con.close()

    rows = {r[0]: {"strategy": r[1], "note": r[2], "applied": json.loads(r[3] or "[]")} for r in res}

    # k1 got annotated
    assert "d_note_k1" in rows["k1"]["applied"]
    assert "Customer confirmed reserved capacity" in rows["k1"]["note"]
    assert rows["k1"]["strategy"] == "map"

    # k2 strategy changed to passthrough
    assert "d_s3_pass" in rows["k2"]["applied"]
    assert rows["k2"]["strategy"] == "passthrough"


def test_apply_directives_check_only(temp_job_env):
    tmpdir, db_path = temp_job_env

    d = Directive(
        id="d_test",
        author="user",
        selector={"service": "ec2"},
        action={"type": "set_strategy", "value": "ignore"},
    )
    save_ledger(tmpdir, [d])

    # Check-only run
    updated = apply_directives_to_db(db_path, check_only=True, job_dir=tmpdir)
    assert updated >= 1

    # Database state must be unchanged
    con = duckdb.connect(db_path, read_only=True)
    k1_strat = con.execute("SELECT strategy FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()[0]
    con.close()
    assert k1_strat == "map"  # Not ignore!

