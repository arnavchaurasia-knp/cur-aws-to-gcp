"""tests/test_sku_mapping_audit_remediation.py — Tests for WORKORDER_sku_mapping_audit_2026-11.

Covers:
- A-1: license_fraction upper clamp (0.95 rejected -> None, 0.3 allowed)
- A-2: .metal shapes borrow sibling license_fraction for Windows/Linux symmetry
- A-3: accelerator shapes (GPU/TPU/Inferentia/Trainium) skip separate OS-license components
- A-4: Oracle License-Included vs BYOL branching on license_model
- C-3a: Assumptions disclosure in XLSX export sheet and CSV --by-assumptions
"""
import csv
import io
import json
import os
import subprocess
import sys
import duckdb
import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from license_split import get_aws_license_split, max_license_fraction, load_ec2_specs
from apply_static_mappings import _license_entries, _is_accelerator_instance, map_gce_row, parse_instance
from export_report import _build_assumptions_table


# ---------------------------------------------------------------------------
# A-1: license_fraction upper clamp
# ---------------------------------------------------------------------------
def test_license_fraction_clamp(monkeypatch, capsys):
    # Mock ec2 specs with an out-of-bounds fraction (0.95) and normal (0.35)
    mock_specs = {
        "test.absurd": {
            "vcpus": 4, "ram_gb": 16.0,
            "license_fraction": {"windows": 0.95, "rhel": 0.99}
        },
        "test.normal": {
            "vcpus": 4, "ram_gb": 16.0,
            "license_fraction": {"windows": 0.35, "rhel": 0.20}
        }
    }
    monkeypatch.setattr("license_split.load_ec2_specs", lambda: mock_specs)

    # 0.95 fraction must be clamped/rejected -> returns None (graceful fallback)
    res_absurd = get_aws_license_split("test.absurd", "Windows", 100.0)
    assert res_absurd is None
    captured = capsys.readouterr()
    assert "exceeds max allowed" in captured.err

    # 0.35 fraction is normal -> splits cleanly into (65.0, 35.0, 'windows')
    res_normal = get_aws_license_split("test.normal", "Windows", 100.0)
    assert res_normal is not None
    base, lic, os_key = res_normal
    assert base == 65.0
    assert lic == 35.0
    assert os_key == "windows"


# ---------------------------------------------------------------------------
# A-2: .metal shapes borrow sibling fraction
# ---------------------------------------------------------------------------
def test_metal_shape_borrows_sibling_license_fraction(monkeypatch):
    # Test real specs or mock specs where .metal borrows from sibling
    specs = load_ec2_specs()
    # c5.metal has sibling c5.24xlarge / c5.18xlarge with windows license_fraction
    split = get_aws_license_split("c5.metal", "Windows", 200.0)
    assert split is not None
    base, lic, os_key = split
    assert os_key == "windows"
    assert lic > 0
    assert round(base + lic, 2) == 200.0


# ---------------------------------------------------------------------------
# A-3: Accelerator instances skip OS-license components
# ---------------------------------------------------------------------------
def test_accelerator_instances_skip_license_entries():
    # Accelerator rows with RHEL/Windows OS must not emit a separate license entry
    gpu_row = {
        "aws_li_key": "k_gpu",
        "instance_type": "g4dn.xlarge",
        "operating_system": "Red Hat Enterprise Linux",
        "product": "Amazon Elastic Compute Cloud",
        "operation": "RunInstances:0010",
        "usage_type": "BoxUsage:g4dn.xlarge",
    }
    assert _is_accelerator_instance(gpu_row) is True
    entries = _license_entries(gpu_row, "k_gpu", "us-central1", 4, 16.0)
    assert entries == []

    trn_row = {
        "aws_li_key": "k_trn",
        "instance_type": "trn1.2xlarge",
        "operating_system": "Ubuntu Pro",
        "product": "Amazon Elastic Compute Cloud",
        "operation": "RunInstances",
        "usage_type": "BoxUsage:trn1.2xlarge",
    }
    assert _is_accelerator_instance(trn_row) is True
    entries = _license_entries(trn_row, "k_trn", "us-central1", 8, 32.0)
    assert entries == []

    # Non-accelerator instance (m5.xlarge) DOES emit a license entry
    m5_row = {
        "aws_li_key": "k_m5",
        "instance_type": "m5.xlarge",
        "operating_system": "Red Hat Enterprise Linux",
        "product": "Amazon Elastic Compute Cloud",
        "operation": "RunInstances:0010",
        "usage_type": "BoxUsage:m5.xlarge",
    }
    assert _is_accelerator_instance(m5_row) is False
    m5_entries = _license_entries(m5_row, "k_m5", "us-central1", 4, 16.0)
    assert len(m5_entries) == 1
    assert m5_entries[0]["component"] == "license"


# ---------------------------------------------------------------------------
# A-4: Oracle License-Included vs BYOL
# ---------------------------------------------------------------------------
def test_oracle_license_model_branching():
    # 1. License Included row produces License Included sku name and no_gcp_equivalent reason
    lic_inc_row = {
        "aws_li_key": "k_ora1",
        "instance_type": "db.m5.large",
        "database_engine": "Oracle Enterprise Edition",
        "license_model": "License Included",
        "product": "Amazon Relational Database Service",
    }
    entries = _license_entries(lic_inc_row, "k_ora1", "us-central1", 2, 8.0)
    assert len(entries) == 1
    assert entries[0]["gcp_sku_name"] == "Oracle Database License (License Included)"
    assert entries[0]["passthrough_reason"] == "no_gcp_equivalent"
    assert "License Included" in entries[0]["projection_note"]

    # 2. BYOL row produces BYOL sku name
    byol_row = {
        "aws_li_key": "k_ora2",
        "instance_type": "db.m5.large",
        "database_engine": "Oracle Enterprise Edition",
        "license_model": "Bring your own license",
        "product": "Amazon Relational Database Service",
    }
    byol_entries = _license_entries(byol_row, "k_ora2", "us-central1", 2, 8.0)
    assert len(byol_entries) == 1
    assert byol_entries[0]["gcp_sku_name"] == "Oracle Database License (BYOL)"
    assert byol_entries[0]["passthrough_reason"] == "no_gcp_equivalent"
    assert "BYOL" in byol_entries[0]["projection_note"]


# ---------------------------------------------------------------------------
# C-3a: Assumptions disclosure in XLSX and CSV export
# ---------------------------------------------------------------------------
def test_export_report_assumptions_sheet_and_csv(tmp_path):
    import openpyxl

    job_dir = tmp_path / "job"
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
            mapping_confidence DOUBLE,
            is_assumption BOOLEAN,
            assumption_note TEXT
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
        INSERT INTO aws_li_catalog VALUES (
            'k1', 'AmazonEC2', 'm5.large', true, 'BoxUsage:m5.large', 'm5.large', 100.0, 'Hrs', 'Usage', 'compute', 'us-east-1'
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k1', 'compute', 'map', 'Compute Engine', 'n2-standard-2', 'sku-1', 'N2 Instance', 0.95, true, 'Custom ACU assumption'
        );
        INSERT INTO gcp_projection VALUES (
            'k1', 'compute', 'Compute Engine', 'us-central1', 80.0, 50.0, 40.0
        );
    """)
    conn.close()

    script = os.path.join(SCRIPTS_DIR, "export_report.py")

    # 1. XLSX export must contain the Assumptions sheet
    xlsx_out = tmp_path / "export.xlsx"
    res_xlsx = subprocess.run([
        sys.executable, script, "--format", "xlsx", "--db", str(db_path), "--out", str(xlsx_out)
    ], cwd=str(job_dir), capture_output=True, text=True)
    assert res_xlsx.returncode == 0, res_xlsx.stderr
    assert xlsx_out.exists()

    wb = openpyxl.load_workbook(str(xlsx_out))
    assert "Assumptions" in wb.sheetnames
    ws_assump = wb["Assumptions"]
    first_row = [cell.value for cell in ws_assump[1]]
    assert "Assumption Key" in first_row
    assert "Category" in first_row
    assert "Active Value" in first_row
    assert ws_assump.max_row >= 10

    # 2. CSV export with --by-assumptions must output the assumptions table
    csv_out = tmp_path / "export_assumptions.csv"
    res_csv = subprocess.run([
        sys.executable, script, "--format", "csv", "--by-assumptions", "--db", str(db_path), "--out", str(csv_out)
    ], cwd=str(job_dir), capture_output=True, text=True)
    assert res_csv.returncode == 0, res_csv.stderr
    assert csv_out.exists()

    with open(csv_out, newline="", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert len(reader) >= 10
        headers = reader[0]
        assert "Assumption Key" in headers
        assert "Category" in headers
        assert "Active Value" in headers
