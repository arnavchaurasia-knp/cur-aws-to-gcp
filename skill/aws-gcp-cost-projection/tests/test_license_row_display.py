"""Regression tests for the Windows/OS-license row display in the HTML report.

Covers three reported bugs:
  1. The main instance row's "GCP Mapping" must show the GCP compute SKU(s),
     never the license SKU (which belongs on the "+" sub-row).
  2. Diff / "GCP as % of AWS" on the main row must be OD-vs-OD compute-vs-compute
     (so the % matches the AWS and GCP OD numbers actually shown on that row), and
     the license "+" sub-row carries its own OD-vs-OD comparison.
  3. The GCP license SKU name is shown in the sub-row's "GCP Mapping" column.
"""
import glob
import duckdb
import pytest


def _build_windows_license_job(job_dir):
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_file = audit_dir / "projection.duckdb"

    conn = duckdb.connect(str(db_file))
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT PRIMARY KEY,
            product TEXT,
            instance_type TEXT,
            instance_vcpus INTEGER,
            instance_ram_gb DOUBLE,
            instance_count DOUBLE,
            usage_type TEXT,
            operation TEXT,
            aws_region TEXT,
            gcp_region TEXT,
            line_item_type TEXT,
            mechanic_group TEXT,
            pricing_model TEXT,
            pricing_unit TEXT,
            is_workload BOOLEAN,
            total_usage DOUBLE,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key TEXT,
            rowid BIGINT,
            strategy TEXT,
            gcp_service TEXT,
            gcp_machine_type TEXT,
            gcp_sku_id TEXT,
            gcp_sku_name TEXT,
            component TEXT,
            unit_multiplier DOUBLE,
            projection_note TEXT,
            mapping_confidence DOUBLE
        );
        CREATE TABLE gcp_projection (
            aws_li_key TEXT,
            component TEXT,
            gcp_service TEXT,
            gcp_machine_type TEXT,
            gcp_region TEXT,
            is_workload BOOLEAN,
            gcp_projected_cost DOUBLE,
            gcp_cost_1yr_cud DOUBLE,
            gcp_cost_3yr_cud DOUBLE,
            projection_status TEXT,
            projection_note TEXT
        );
        CREATE TABLE run_results (run_id TEXT, ts_utc TEXT, prospect TEXT);
    """)

    # One Windows EC2 row: aws_amortized_cost is license-INCLUSIVE (244.11).
    # m5.xlarge has a windows license_fraction (0.4894) in ec2-instance-types.json,
    # so the AWS split path (Phase 2) fires: base 124.64 + license 119.47 = 244.11.
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'Amazon Elastic Compute Cloud running Windows', 'm5.xlarge', 4, 16.0, 1.0,
             'BoxUsage:m5.xlarge', 'RunInstances:0002', 'us-east-1', 'us-central1', 'Usage',
             'compute_breakdown', 'OnDemand', 'Hrs', true, 100, 244.11);
        -- Two components for the SAME li_key: compute + license.
        -- gcp_machine_type left '' so the renderer must fall back to the SKU name;
        -- the compute SKU must win the main row, never the license SKU.
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k1', 1, 'break_down', 'Compute Engine', '', 'sku-core', 'N2 Instance Core + Ram', 'core', 1.0, 'OK', 0.95),
            ('k1', 2, 'map', 'Compute Engine', '', 'sku-lic', 'Licensing Fee for Windows Server 2019 Standard', 'license', 1.0, 'OS license', 0.95);
        INSERT INTO gcp_projection VALUES
            ('k1', 'core', 'Compute Engine', '', 'us-central1', true, 120.29, 89.02, 67.37, 'mapped', 'OK'),
            ('k1', 'license', 'Compute Engine', '', 'us-central1', true, 13.24, 13.24, 13.24, 'mapped', 'OS license');
        INSERT INTO run_results VALUES ('run-test', '2026-11-11 00:00:00', 'Acme Corp');
    """)
    conn.close()
    return db_file, audit_dir


def _render(job_dir, db_file, monkeypatch):
    import render_report
    from render_report import main as render_main
    monkeypatch.chdir(job_dir)
    monkeypatch.setattr("render_report.JOB_DIR", str(job_dir))
    monkeypatch.setattr("render_report.DB_PATH", str(db_file))
    # Enable the optional Diff / "% of AWS" columns so this test can assert them.
    monkeypatch.setattr("render_report._ACTIVE_COLUMNS", list(render_report.COLUMNS))
    render_main()
    html_files = glob.glob(str(job_dir / "projection-audit" / "report-*.html"))
    assert html_files, "no report HTML produced"
    with open(html_files[0], encoding="utf-8") as f:
        return f.read()


def test_windows_license_row_mapping_and_percent(tmp_path, monkeypatch):
    job_dir = tmp_path / "winjob"
    db_file, _ = _build_windows_license_job(job_dir)
    html = _render(job_dir, db_file, monkeypatch)

    # (1) Main row GCP Mapping shows the compute SKU, not the license SKU.
    assert "N2 Instance Core + Ram" in html
    # The license SKU must appear only inside the license sub-row.
    assert "row-license-sub" in html
    lic_sku = "Licensing Fee for Windows Server 2019 Standard"
    assert lic_sku in html
    # The license SKU must NOT be the main instance mapping: it should sit after
    # the sub-row marker, and the compute SKU must come before it.
    assert html.index("N2 Instance Core + Ram") < html.index(lic_sku)

    # (2) Main row compares compute-vs-compute on OD basis: 120.29 / 124.64 = 96%.
    assert "$124.64" in html   # AWS compute base (244.11 - license)
    assert "$120.29" in html   # GCP compute OD
    # 120.29 / 124.64 = 96.5% -> 97%. The main row now compares compute-vs-compute;
    # the old bug showed 55% here (base-only cells with an including-license total %).
    assert ">97%<" in html

    # (3) License sub-row has its own OD-vs-OD split: +119.47 AWS, +13.24 GCP, 11%.
    assert "$119.47" in html
    assert "$13.24" in html
    assert "11%" in html


def _build_free_row_job(job_dir):
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_file = audit_dir / "projection.duckdb"
    conn = duckdb.connect(str(db_file))
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT PRIMARY KEY, product TEXT, instance_type TEXT,
            instance_vcpus INTEGER, instance_ram_gb DOUBLE, instance_count DOUBLE,
            usage_type TEXT, operation TEXT, aws_region TEXT, gcp_region TEXT,
            line_item_type TEXT, mechanic_group TEXT, pricing_model TEXT,
            pricing_unit TEXT, is_workload BOOLEAN, total_usage DOUBLE, aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key TEXT, rowid BIGINT, strategy TEXT, gcp_service TEXT,
            gcp_machine_type TEXT, gcp_sku_id TEXT, gcp_sku_name TEXT, component TEXT,
            unit_multiplier DOUBLE, projection_note TEXT, mapping_confidence DOUBLE
        );
        CREATE TABLE gcp_projection (
            aws_li_key TEXT, component TEXT, gcp_service TEXT, gcp_machine_type TEXT,
            gcp_region TEXT, is_workload BOOLEAN, gcp_projected_cost DOUBLE,
            gcp_cost_1yr_cud DOUBLE, gcp_cost_3yr_cud DOUBLE, projection_status TEXT, projection_note TEXT
        );
        CREATE TABLE run_results (run_id TEXT, ts_utc TEXT, prospect TEXT);
    """)
    # A free CloudWatch workload row (strategy='ignore' -> $0 on GCP) plus a priced
    # workload row so Section A exists.
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('kc', 'Amazon Elastic Compute Cloud', 'm5.large', 2, 8.0, 1.0, 'BoxUsage', 'RunInstances',
             'us-east-1', 'us-central1', 'Usage', 'compute_breakdown', 'OnDemand', 'Hrs', true, 100, 500.0),
            ('kf', 'Amazon CloudWatch', '', NULL, NULL, NULL, 'DashboardsUsageHour', 'MetricStorage',
             'us-east-1', 'us-central1', 'Usage', 'cloudwatch', 'OnDemand', 'Hrs', true, 100, 10.08);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('kc', 1, 'break_down', 'Compute Engine', 'n2-standard-2', 'sku-c', 'N2 Instance Core', 'core', 1.0, 'OK', 0.95),
            ('kf', 1, 'ignore', 'Cloud Monitoring', '', NULL, 'Cloud Monitoring', 'default', NULL, 'Free tier on GCP', 0.9);
        INSERT INTO gcp_projection VALUES
            ('kc', 'core', 'Compute Engine', 'n2-standard-2', 'us-central1', true, 400.0, 350.0, 300.0, 'mapped', 'OK'),
            ('kf', 'default', 'Cloud Monitoring', '', 'us-central1', true, 0.0, 0.0, 0.0, 'ignored', 'Free tier on GCP');
        INSERT INTO run_results VALUES ('run-test', '2026-11-11 00:00:00', 'Acme Corp');
    """)
    conn.close()
    return db_file


def test_free_row_in_section_a_not_carried_as_passthrough(tmp_path, monkeypatch):
    job_dir = tmp_path / "freejob"
    db_file = _build_free_row_job(job_dir)
    html = _render(job_dir, db_file, monkeypatch)

    # The free CloudWatch row must sit in Section A (mapped), before Section B.
    sec_a = html.index("SECTION A — MAPPED")
    sec_b = html.index("SECTION B — PASSTHROUGH")
    cw = html.index("CloudWatch")
    assert sec_a < cw < sec_b, "free CloudWatch row should render inside Section A"

    # It must show as a free mapping, not a passthrough.
    assert "(Free tier)" in html
    assert "[no GCP charge]" in html
    # The old contradiction (a free row tagged [passthrough] carried 1:1) is gone:
    # Section B must not carry the $10.08 free row at AWS cost.
    assert "[passthrough]" not in html[cw - 400:cw + 400]

