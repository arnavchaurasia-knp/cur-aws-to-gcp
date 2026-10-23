"""
test_confidence_model_cleanup.py — Regression tests for confidence model cleanup.

Verifies:
  1. test_no_confidence_in_report: Rendered HTML report and summary-*.md contain no
     confidence percentage.
  2. test_no_confidence_in_export: Export report query (_build_query, _build_query_by_instance)
     contains no "Confidence" column in the header.
  3. test_corpus_dollars_unchanged: Calculation of bill totals and projections remains
     identical before and after confidence removal.
"""

import os
import sys
import json
import duckdb
import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def test_no_confidence_in_report(tmp_path, monkeypatch):
    """Assert that render_report produces summaries and HTML without confidence percentages."""
    import render_report
    from render_report import main as render_main

    job_dir = tmp_path / "job_report_test"
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_file = audit_dir / "projection.duckdb"

    curr_info = {
        "currency": "USD",
        "symbol": "$",
        "fx_rate": 1.0,
        "source": "default",
        "label": "USD",
    }
    with open(audit_dir / "currency.json", "w", encoding="utf-8") as f:
        json.dump(curr_info, f)

    conn = duckdb.connect(str(db_file))
    conn.execute("""
    CREATE TABLE aws_li_catalog (
        aws_li_key TEXT,
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
        rate_source TEXT,
        rate_note TEXT
    );
    CREATE TABLE run_results (
        run_id TEXT,
        run_timestamp TEXT,
        customer_name TEXT
    );
    """)

    conn.execute("""
    INSERT INTO aws_li_catalog VALUES (
        'k1', 'Amazon Elastic Compute Cloud', 'm5.large', 2, 8.0, 1.0,
        'BoxUsage:m5.large', 'RunInstances', 'us-east-1', 'us-east1',
        'Usage', 'compute_breakdown', 'OnDemand', 'Hrs', true, 730.0, 100.0
    );
    INSERT INTO aws_li_to_gcp_li VALUES (
        'k1', 1, 'map', 'Compute Engine', 'n2-standard-2', 'sku1', 'N2 Instance Core',
        'core', 2.0, 'Standard core mapping', 0.85
    );
    INSERT INTO gcp_projection VALUES (
        'k1', 'core', 'Compute Engine', 'n2-standard-2', 'us-east1', true, 80.0, 60.0, 45.0, 'sku_catalog', 'OK'
    );
    INSERT INTO run_results VALUES (
        'run-test', '2026-10-23 00:00:00', 'Test Prospect'
    );
    """)
    conn.close()

    monkeypatch.chdir(job_dir)
    monkeypatch.setattr("render_report.JOB_DIR", str(job_dir))
    monkeypatch.setattr("render_report.DB_PATH", str(db_file))
    render_main()

    # Check generated summary markdown
    summary_files = list(audit_dir.glob("summary-*.md"))
    assert len(summary_files) > 0, "Summary markdown was not generated"
    summary_content = summary_files[0].read_text(encoding="utf-8")
    assert "average mapping confidence" not in summary_content
    assert "%" not in summary_content.split("Mapping coverage")[1]

    # Check generated HTML report
    html_files = list(audit_dir.glob("report-*.html"))
    assert len(html_files) > 0, "HTML report was not generated"
    html_content = html_files[0].read_text(encoding="utf-8")
    assert "average mapping confidence" not in html_content
    assert "Mapping Confidence" not in html_content


def test_no_confidence_in_export():
    """Assert that export_report does not include Confidence column in headers."""
    import export_report

    q_main = export_report._build_query(fx_rate=1.0, curr_code="USD", aws_fx_rate=1.0)
    q_inst = export_report._build_query_by_instance(fx_rate=1.0, curr_code="USD", aws_fx_rate=1.0)

    # Neither query should project a column named Confidence or Mapping Confidence
    assert '"Confidence"' not in q_main
    assert '"mapping_confidence"' not in q_main
    assert '"Mapping Confidence"' not in q_main

    assert '"Confidence"' not in q_inst
    assert '"mapping_confidence"' not in q_inst
    assert '"Mapping Confidence"' not in q_inst


def test_corpus_dollars_unchanged():
    """Assert that removing confidence machinery does not alter dollar calculations."""
    import export_report

    con = duckdb.connect(":memory:")
    con.execute("""
    CREATE TABLE aws_li_catalog (
        aws_li_key TEXT,
        product TEXT,
        instance_type TEXT,
        aws_amortized_cost DOUBLE,
        is_workload BOOLEAN
    );
    CREATE TABLE aws_li_to_gcp_li (
        aws_li_key TEXT,
        strategy TEXT,
        mapping_confidence DOUBLE,
        component TEXT,
        gcp_machine_type TEXT,
        gcp_sku_id TEXT,
        gcp_sku_name TEXT
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
    """)

    con.execute("""
    INSERT INTO aws_li_catalog VALUES
        ('item1', 'AmazonEC2', 'c5.large', 120.0, true),
        ('item2', 'AmazonS3', '', 50.0, true);

    INSERT INTO aws_li_to_gcp_li VALUES
        ('item1', 'map', 0.85, 'core', 'c2-standard-4', 'sku1', 'C2 Core'),
        ('item2', 'passthrough', 0.30, 'default', NULL, NULL, NULL);

    INSERT INTO gcp_projection VALUES
        ('item1', 'core', 'Compute Engine', 'us-central1', 105.0, 75.0, 55.0),
        ('item2', 'default', 'Cloud Storage', 'us-central1', NULL, NULL, NULL);
    """)

    query = export_report._build_query(fx_rate=1.0, curr_code="USD", aws_fx_rate=1.0, conn=con)
    rows = con.execute(query).fetchall()

    # item1: aws=120, gcp_od=105, diff=15
    # item2: aws=50, passthrough gcp_od=50, diff=0
    item1_row = [r for r in rows if r[1] == 'AmazonEC2'][0]
    item2_row = [r for r in rows if r[1] == 'AmazonS3'][0]

    assert item1_row[6] == 120.0   # AWS Cost
    assert item1_row[7] == 105.0   # GCP OD
    assert item1_row[11] == 15.0   # Diff

    assert item2_row[6] == 50.0    # AWS Cost
    assert item2_row[7] == 50.0    # GCP OD (passthrough at parity)
    assert item2_row[11] == 0.0    # Diff
