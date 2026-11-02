import json
import os
import subprocess
import sys
import duckdb
import pytest

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "apply_outlier_fixes.py")


@pytest.fixture
def test_job_dir(tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir()
    db_path = str(audit_dir / "projection.duckdb")

    conn = duckdb.connect(db_path)
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            usage_type VARCHAR,
            pricing_unit VARCHAR,
            aws_amortized_cost DOUBLE,
            gcp_region VARCHAR
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            component VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            gcp_sku_name VARCHAR,
            strategy VARCHAR,
            unit_multiplier DOUBLE,
            projection_note VARCHAR
        );
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id VARCHAR,
            region VARCHAR,
            rate_usd DOUBLE,
            pricing_type VARCHAR
        );
    """)

    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'AmazonEC2', 'BoxUsage:c5.xlarge', 'Hrs', 100.0, 'us-east-1'),
            ('k2', 'AmazonS3', 'TimedStorage-ByteHrs', 'GB-Mo', 50.0, 'us-east-1');
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k1', 'core', 'Compute Engine', '1111-2222-3333', 'N2 Core', 'map', 2.0, ''),
            ('k2', 'storage', 'Cloud Storage', '4444-5555-6666', 'Standard Storage', 'map', 1.0, '');
        INSERT INTO gcp_sku_rates VALUES
            ('1111-2222-3333', 'us-east-1', 0.03, 'OnDemand'),
            ('AAAA-BBBB-CCCC', 'us-east-1', 0.04, 'OnDemand');
    """)
    conn.close()

    return str(job_dir), db_path


def test_confirm_candidate_applied(test_job_dir):
    job_dir, db_path = test_job_dir

    candidates = {
        "k1|core": {
            "query": "G",
            "confidence": "HIGH",
            "candidate": {
                "action": "set_multiplier",
                "component": "core",
                "unit_multiplier": 4.0,
                "reason": "Instance spec says 4 cores"
            }
        }
    }
    with open(os.path.join(job_dir, "triage_candidates.json"), "w", encoding="utf-8") as f:
        json.dump(candidates, f)

    fixes = [
        {
            "aws_li_key": "k1",
            "decision": "confirm",
            "component": "core",
            "reason": "Confirming core spec multiplier"
        }
    ]
    with open(os.path.join(job_dir, "outlier_fixes.json"), "w", encoding="utf-8") as f:
        json.dump(fixes, f)

    res = subprocess.run([sys.executable, SCRIPT_PATH, job_dir], capture_output=True, text=True)
    assert res.returncode == 0
    assert "applied=1" in res.stdout

    conn = duckdb.connect(db_path, read_only=True)
    row = conn.execute("SELECT unit_multiplier FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1' AND component = 'core'").fetchone()
    conn.close()
    assert row[0] == 4.0

    notes_file = os.path.join(job_dir, "mapping-notes.md")
    assert os.path.exists(notes_file)
    with open(notes_file, encoding="utf-8") as f:
        content = f.read()
    assert "confirmed multiplier fix" in content


def test_valid_override_applied(test_job_dir):
    job_dir, db_path = test_job_dir

    candidates = {"k1|core": {"query": "A1"}}
    with open(os.path.join(job_dir, "triage_candidates.json"), "w", encoding="utf-8") as f:
        json.dump(candidates, f)

    fixes = [
        {
            "aws_li_key": "k1",
            "decision": "override",
            "component": "core",
            "gcp_sku_id": "AAAA-BBBB-CCCC",
            "gcp_sku_name": "C2 Core",
            "unit_multiplier": 4.0,
            "strategy": "map",
            "reason": "Fixing tier"
        }
    ]
    with open(os.path.join(job_dir, "outlier_fixes.json"), "w", encoding="utf-8") as f:
        json.dump(fixes, f)

    res = subprocess.run([sys.executable, SCRIPT_PATH, job_dir], capture_output=True, text=True)
    assert res.returncode == 0
    assert "applied=1" in res.stdout

    conn = duckdb.connect(db_path, read_only=True)
    row = conn.execute("SELECT gcp_sku_id, gcp_sku_name, unit_multiplier FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1' AND component = 'core'").fetchone()
    conn.close()
    assert row[0] == "AAAA-BBBB-CCCC"
    assert row[1] == "C2 Core"
    assert row[2] == 4.0


def test_unknown_key_rejected(test_job_dir):
    job_dir, db_path = test_job_dir

    fixes = [
        {
            "aws_li_key": "unknown_key_999",
            "decision": "override",
            "gcp_sku_id": "AAAA-BBBB-CCCC",
            "gcp_sku_name": "C2 Core",
            "unit_multiplier": 2.0,
            "reason": "Unknown key"
        }
    ]
    with open(os.path.join(job_dir, "outlier_fixes.json"), "w", encoding="utf-8") as f:
        json.dump(fixes, f)

    res = subprocess.run([sys.executable, SCRIPT_PATH, job_dir], capture_output=True, text=True)
    assert res.returncode == 0
    assert "applied=0" in res.stdout
    assert "errors=1" in res.stdout


def test_malformed_fix_rejected_db_unchanged(test_job_dir):
    job_dir, db_path = test_job_dir

    candidates = {"k1|core": {"query": "A1"}}
    with open(os.path.join(job_dir, "triage_candidates.json"), "w", encoding="utf-8") as f:
        json.dump(candidates, f)

    fixes = [
        {
            "aws_li_key": "k1",
            "decision": "override",
            "component": "core",
            "gcp_sku_id": "invalid-sku-format",  # Malformed SKU format
            "gcp_sku_name": "Fake SKU",
            "unit_multiplier": -1.0,             # Negative multiplier
            "reason": "Bad values"
        }
    ]
    with open(os.path.join(job_dir, "outlier_fixes.json"), "w", encoding="utf-8") as f:
        json.dump(fixes, f)

    res = subprocess.run([sys.executable, SCRIPT_PATH, job_dir], capture_output=True, text=True)
    assert res.returncode == 0
    assert "applied=0" in res.stdout
    assert "errors=1" in res.stdout

    conn = duckdb.connect(db_path, read_only=True)
    row = conn.execute("SELECT gcp_sku_id, unit_multiplier FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1' AND component = 'core'").fetchone()
    conn.close()
    assert row[0] == "1111-2222-3333"
    assert row[1] == 2.0


def test_veto_leaves_row_unchanged(test_job_dir):
    job_dir, db_path = test_job_dir

    fixes = [
        {
            "aws_li_key": "k2",
            "decision": "veto",
            "reason": "Legitimate storage price difference"
        }
    ]
    with open(os.path.join(job_dir, "outlier_fixes.json"), "w", encoding="utf-8") as f:
        json.dump(fixes, f)

    res = subprocess.run([sys.executable, SCRIPT_PATH, job_dir], capture_output=True, text=True)
    assert res.returncode == 0
    assert "vetoed=1" in res.stdout
    assert "applied=0" in res.stdout

    conn = duckdb.connect(db_path, read_only=True)
    row = conn.execute("SELECT gcp_sku_id, unit_multiplier FROM aws_li_to_gcp_li WHERE aws_li_key = 'k2'").fetchone()
    conn.close()
    assert row[0] == "4444-5555-6666"
    assert row[1] == 1.0

    notes_file = os.path.join(job_dir, "mapping-notes.md")
    with open(notes_file, encoding="utf-8") as f:
        content = f.read()
    assert "vetoed — Legitimate storage price difference" in content
