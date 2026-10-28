"""tests/test_export_report.py — Tests for export_report.py multi-format, level, and settings filtering."""
import csv
import json
import os
import subprocess
import sys
import tempfile
import duckdb
import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS_DIR)


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
