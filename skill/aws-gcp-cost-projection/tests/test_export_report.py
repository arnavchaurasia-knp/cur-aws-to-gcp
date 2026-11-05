"""tests/test_export_report.py — Tests for export_report.py multi-format, level, and settings filtering."""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import duckdb
import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
if SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)


@pytest.fixture
def mock_duckdb_job(tmp_path):
    job_dir = tmp_path / "test_job"
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_path = audit_dir / "projection.duckdb"

    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT PRIMARY KEY,
            product TEXT,
            instance_type TEXT,
            is_workload BOOLEAN,
            usage_type TEXT,
            line_item_description TEXT,
            aws_amortized_cost DOUBLE,
            pricing_unit TEXT,
            line_item_type TEXT,
            mechanic_group TEXT,
            gcp_region TEXT
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key TEXT,
            component TEXT,
            strategy TEXT,
            gcp_service TEXT,
            gcp_machine_type TEXT,
            gcp_sku_id TEXT,
            gcp_sku_name TEXT,
            mapping_confidence DOUBLE
        );
        CREATE TABLE gcp_projection (
            aws_li_key TEXT,
            component TEXT,
            gcp_service TEXT,
            gcp_region TEXT,
            gcp_projected_cost DOUBLE,
            gcp_cost_1yr_cud DOUBLE,
            gcp_cost_3yr_cud DOUBLE
        );
        CREATE TABLE run_results (
            run_id TEXT,
            prospect TEXT,
            ts_utc TIMESTAMP
        );
    """)

    conn.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k1', 'AmazonEC2', 'm5.large', true, 'BoxUsage:m5.large', 'm5.large in us-east-1', 100.0, 'Hrs', 'Usage', 'compute', 'us-east-1'
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k1', 'compute', 'map', 'Compute Engine', 'n2-standard-2', 'sku-1', 'N2 Instance', 0.95
        );
        INSERT INTO gcp_projection VALUES (
            'k1', 'compute', 'Compute Engine', 'us-central1', 80.0, 50.0, 40.0
        );
        INSERT INTO run_results VALUES ('run-1', 'AcmeCorp', '2026-10-28 12:00:00');
    """)
    conn.close()
    return str(job_dir), str(db_path)


def test_export_report_csv_summary(mock_duckdb_job, tmp_path):
    job_dir, db_path = mock_duckdb_job
    out_csv = tmp_path / "export_summary.csv"

    script = os.path.join(SCRIPTS_DIR, "export_report.py")
    res = subprocess.run([
        sys.executable, script, "--format", "csv", "--db", db_path, "--out", str(out_csv)
    ], cwd=job_dir, capture_output=True, text=True)

    assert res.returncode == 0, res.stderr
    assert out_csv.exists()

    with open(out_csv, newline="", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert len(reader) >= 2
        headers = reader[0]
        assert "AWS Service" in headers
        assert "GCP OD ($)" in headers


def test_export_report_csv_line_item(mock_duckdb_job, tmp_path):
    job_dir, db_path = mock_duckdb_job
    out_csv = tmp_path / "export_line_items.csv"

    script = os.path.join(SCRIPTS_DIR, "export_report.py")
    res = subprocess.run([
        sys.executable, script, "--format", "csv", "--level", "line-item", "--db", db_path, "--out", str(out_csv)
    ], cwd=job_dir, capture_output=True, text=True)

    assert res.returncode == 0, res.stderr
    assert out_csv.exists()

    with open(out_csv, newline="", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        headers = reader[0]
        assert "AWS Key" in headers
        assert "Usage Type" in headers
        assert "GCP SKU" in headers
        assert reader[1][headers.index("AWS Key")] == "k1"


def test_export_report_json(mock_duckdb_job, tmp_path):
    job_dir, db_path = mock_duckdb_job
    out_json = tmp_path / "export.json"

    script = os.path.join(SCRIPTS_DIR, "export_report.py")
    res = subprocess.run([
        sys.executable, script, "--format", "json", "--level", "summary", "--db", db_path, "--out", str(out_json)
    ], cwd=job_dir, capture_output=True, text=True)

    assert res.returncode == 0, res.stderr
    assert out_json.exists()

    with open(out_json, encoding="utf-8") as f:
        data = json.load(f)
        assert data["level"] == "summary"
        assert data["currency"] == "USD"
        assert data["rows_count"] >= 1
        assert len(data["data"]) == data["rows_count"]
        row = data["data"][0]
        assert "AWS Service" in row
        assert "GCP OD ($)" in row


def test_export_report_column_toggles(mock_duckdb_job, tmp_path):
    job_dir, db_path = mock_duckdb_job
    # Write report-settings.json to toggle off 1yr CUD and 3yr CUD
    settings_file = os.path.join(job_dir, "report-settings.json")
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump({
            "values": {
                "col_gcp_cud_1yr": "false",
                "col_gcp_cud_3yr": "false"
            }
        }, f)

    out_json = tmp_path / "export_toggled.json"
    script = os.path.join(SCRIPTS_DIR, "export_report.py")
    res = subprocess.run([
        sys.executable, script, "--format", "json", "--db", db_path, "--out", str(out_json)
    ], cwd=job_dir, capture_output=True, text=True)

    assert res.returncode == 0, res.stderr
    with open(out_json, encoding="utf-8") as f:
        data = json.load(f)
        row = data["data"][0]
        # Verify 1yr and 3yr CUD columns are removed
        for k in row.keys():
            assert "1yr CUD" not in k
            assert "3yr CUD" not in k
        # But AWS and GCP OD still exist
        assert "AWS Cost ($)" in row
        assert "GCP OD ($)" in row


def test_export_report_directive_provenance(mock_duckdb_job, tmp_path):
    """Line item export discloses applied directives provenance."""
    job_dir, db_path = mock_duckdb_job

    conn = duckdb.connect(db_path)
    conn.execute("ALTER TABLE aws_li_to_gcp_li ADD COLUMN applied_directives TEXT")
    conn.execute("UPDATE aws_li_to_gcp_li SET applied_directives = '[\"d_rev_1\"]' WHERE aws_li_key = 'k1'")
    conn.close()

    with open(os.path.join(job_dir, "directives.json"), "w", encoding="utf-8") as f:
        json.dump([{
            "id": "d_rev_1",
            "author": "user",
            "source_utterance": "map to Compute Engine custom",
            "selector": {"aws_li_keys": ["k1"]},
            "action": {"type": "set_strategy", "value": "map"},
            "status": "applied"
        }], f)

    out_csv = tmp_path / "export_prov.csv"
    script = os.path.join(SCRIPTS_DIR, "export_report.py")
    res = subprocess.run([
        sys.executable, script, "--format", "csv", "--level", "line-item", "--db", db_path, "--out", str(out_csv)
    ], cwd=job_dir, capture_output=True, text=True)

    assert res.returncode == 0, res.stderr
    with open(out_csv, newline="", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        headers = reader[0]
        assert "Refinement" in headers
        ref_idx = headers.index("Refinement")
        row = reader[1]
        assert "Changed by your request: map to Compute Engine custom [id: d_rev_1]" in row[ref_idx]


def test_revoke_restores_baseline(tmp_path):
    """Revoking a directive (superseding it) and reapplying restores baseline state."""
    job_dir = tmp_path / "revoke_job"
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_path = audit_dir / "projection.duckdb"
    db_baseline = audit_dir / "projection.baseline.duckdb"

    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            canonical_service VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            gcp_service VARCHAR,
            strategy VARCHAR,
            is_workload BOOLEAN,
            projection_note VARCHAR
        );
    """)
    conn.execute("INSERT INTO aws_li_catalog VALUES ('k1', 'AmazonEC2', 'ec2', 'box', 'run', 100.0)")
    conn.execute("INSERT INTO aws_li_to_gcp_li VALUES ('k1', 'Compute Engine', 'map', TRUE, 'baseline note')")
    conn.close()

    # Save baseline copy (event-sourced pattern)
    shutil.copyfile(str(db_path), str(db_baseline))

    # Apply directive d1: set strategy = ignore
    from scripts.directives import Directive, save_ledger
    from scripts.apply_directives import apply_directives_to_db

    d1 = Directive("d1", "user", {"aws_li_keys": ["k1"]}, {"type": "set_strategy", "value": "ignore"}, status="applied")
    save_ledger(str(job_dir), [d1])

    apply_directives_to_db(str(db_path), job_dir=str(job_dir))

    conn = duckdb.connect(str(db_path), read_only=True)
    strat = conn.execute("SELECT strategy FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()[0]
    conn.close()
    assert strat == "ignore"

    # Revoke d1 (mark superseded)
    from scripts.refinement_thread import revoke_directive
    rev_ok, _, _ = revoke_directive(str(job_dir), "d1")
    assert rev_ok

    # Replay from baseline (event-sourced delete-and-replay)
    shutil.copyfile(str(db_baseline), str(db_path))
    apply_directives_to_db(str(db_path), job_dir=str(job_dir))

    conn = duckdb.connect(str(db_path), read_only=True)
    strat_restored = conn.execute("SELECT strategy FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()[0]
    conn.close()
    assert strat_restored == "map"


def test_export_report_preference_verbs_provenance_and_revert(tmp_path):
    """Phase 3 preference verbs (family, region, pricing_basis) disclose in export and restore on revoke."""
    job_dir = tmp_path / "pref_job"
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_path = audit_dir / "projection.duckdb"
    db_baseline = audit_dir / "projection.baseline.duckdb"

    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            canonical_service VARCHAR,
            instance_type VARCHAR,
            is_workload BOOLEAN,
            usage_type VARCHAR,
            operation VARCHAR,
            line_item_description VARCHAR,
            aws_amortized_cost DOUBLE,
            pricing_unit VARCHAR,
            line_item_type VARCHAR,
            mechanic_group VARCHAR,
            gcp_region VARCHAR
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            component VARCHAR,
            strategy VARCHAR,
            gcp_service VARCHAR,
            gcp_family VARCHAR,
            gcp_machine_type VARCHAR,
            gcp_sku_id VARCHAR,
            gcp_sku_name VARCHAR,
            mapping_confidence DOUBLE,
            is_workload BOOLEAN,
            projection_note VARCHAR,
            gcp_region VARCHAR,
            rate_region_basis VARCHAR,
            rate_region_used VARCHAR,
            review_flags VARCHAR,
            applied_directives VARCHAR
        );
        CREATE TABLE gcp_projection (
            aws_li_key VARCHAR,
            component VARCHAR,
            gcp_service VARCHAR,
            gcp_region VARCHAR,
            gcp_projected_cost DOUBLE,
            gcp_cost_1yr_cud DOUBLE,
            gcp_cost_3yr_cud DOUBLE
        );
        CREATE TABLE run_results (
            run_id VARCHAR,
            prospect VARCHAR,
            ts_utc TIMESTAMP
        );
    """)

    conn.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k1', 'AmazonEC2', 'ec2', 'm5.large', true, 'BoxUsage:m5.large', 'RunInstances', 'm5.large in us-east-1', 100.0, 'Hrs', 'Usage', 'compute', 'us-east-1'
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k1', 'core', 'map', 'Compute Engine', 'N2', 'n2-standard-2', 'sku-1', 'N2 Instance', 0.95, true, 'baseline note', 'us-central1', 'dominant', 'us-central1', NULL, NULL
        );
        INSERT INTO gcp_projection VALUES (
            'k1', 'core', 'Compute Engine', 'us-central1', 80.0, 50.0, 40.0
        );
        INSERT INTO run_results VALUES ('run-1', 'AcmeCorp', '2026-10-28 12:00:00');
    """)
    conn.close()

    # Save baseline copy for event-sourced delete-and-replay
    shutil.copyfile(str(db_path), str(db_baseline))

    # Apply 3 Phase 3 preference directives:
    # 1. set_target_family -> N2D
    # 2. set_region -> europe-west1
    # 3. set_pricing_basis -> cud_3yr
    from scripts.directives import Directive, save_ledger
    from scripts.apply_directives import apply_directives_to_db

    d1 = Directive("d_fam", "user", {"aws_li_keys": ["k1"]}, {"type": "set_target_family", "value": "N2D"}, source_utterance="use N2D for compute", status="applied")
    d2 = Directive("d_reg", "user", {"aws_li_keys": ["k1"]}, {"type": "set_region", "value": "europe-west1", "policy": "exact"}, source_utterance="run in europe-west1", status="applied")
    d3 = Directive("d_cud", "user", {"aws_li_keys": ["k1"]}, {"type": "set_pricing_basis", "value": "cud_3yr"}, source_utterance="use 3yr CUD", status="applied")

    save_ledger(str(job_dir), [d1, d2, d3])
    apply_directives_to_db(str(db_path), job_dir=str(job_dir))

    # Verify changes applied in DuckDB (before -> after delta)
    conn = duckdb.connect(str(db_path), read_only=True)
    row = conn.execute("SELECT gcp_family, gcp_region, review_flags, projection_note, applied_directives FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()
    fam, reg, rflags, note, app_dirs = row
    proj_cost = conn.execute("SELECT gcp_projected_cost FROM gcp_projection WHERE aws_li_key = 'k1'").fetchone()[0]
    conn.close()

    assert fam == "N2D"
    assert reg == "europe-west1"
    assert "cud_3yr" in rflags
    assert "[Directive: target family pinned to N2D]" in note
    assert "[Directive: region set to europe-west1" in note
    assert proj_cost < 80.0  # Discounted by 3yr CUD multiplier

    # Test export CSV disclosing provenance in Refinement column
    out_csv = tmp_path / "export_pref.csv"
    script = os.path.join(SCRIPTS_DIR, "export_report.py")
    res_csv = subprocess.run([
        sys.executable, script, "--format", "csv", "--level", "line-item", "--db", str(db_path), "--out", str(out_csv)
    ], cwd=str(job_dir), capture_output=True, text=True)
    assert res_csv.returncode == 0, res_csv.stderr
    assert out_csv.exists()

    with open(out_csv, newline="", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        headers = reader[0]
        assert "Refinement" in headers
        ref_idx = headers.index("Refinement")
        row_data = reader[1]
        ref_text = row_data[ref_idx]
        assert "Changed by your request: use N2D for compute [id: d_fam]" in ref_text
        assert "Changed by your request: run in europe-west1 [id: d_reg]" in ref_text
        assert "Changed by your request: use 3yr CUD [id: d_cud]" in ref_text

    # Test export JSON disclosing provenance
    out_json = tmp_path / "export_pref.json"
    res_json = subprocess.run([
        sys.executable, script, "--format", "json", "--level", "line-item", "--db", str(db_path), "--out", str(out_json)
    ], cwd=str(job_dir), capture_output=True, text=True)
    assert res_json.returncode == 0, res_json.stderr
    assert out_json.exists()

    with open(out_json, encoding="utf-8") as f:
        data = json.load(f)
        ref_text_json = data["data"][0]["Refinement"]
        assert "use N2D for compute" in ref_text_json
        assert "run in europe-west1" in ref_text_json
        assert "use 3yr CUD" in ref_text_json

    # Test Revoke: mark directives superseded
    from scripts.refinement_thread import revoke_directive
    for did in ["d_fam", "d_reg", "d_cud"]:
        rev_ok, _, _ = revoke_directive(str(job_dir), did)
        assert rev_ok

    # Replay from baseline (event-sourced delete-and-replay)
    shutil.copyfile(str(db_baseline), str(db_path))
    apply_directives_to_db(str(db_path), job_dir=str(job_dir))

    # Verify baseline state restored
    conn = duckdb.connect(str(db_path), read_only=True)
    row_restored = conn.execute("SELECT gcp_family, gcp_region, review_flags FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()
    proj_cost_restored = conn.execute("SELECT gcp_projected_cost FROM gcp_projection WHERE aws_li_key = 'k1'").fetchone()[0]
    conn.close()

    assert row_restored[0] == "N2"
    assert row_restored[1] == "us-central1"
    assert row_restored[2] is None
    assert proj_cost_restored == 80.0


