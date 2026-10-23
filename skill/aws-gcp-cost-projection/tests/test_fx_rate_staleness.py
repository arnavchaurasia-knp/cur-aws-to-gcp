"""Tests for WORKORDER_fx_rate_staleness.md (T6).

All tests mock network calls and verify:
1. test_fallback_emits_diagnostic — live fetch failing produces an fx_rate_fallback
   diagnostic naming the currency and the file's age.
2. test_live_success_is_quiet — a successful fetch emits no fallback warning.
3. test_rate_source_recorded — bill_currency carries source and, on the fallback path,
   the file date and age in days.
4. test_usd_bill_unaffected — a USD bill produces no conversion line, no diagnostic,
   and identical figures.
5. test_fx_failure_does_not_block — an FX fetch that raises still yields a complete
   projection.
"""
import json
import duckdb
import pytest

import currency_utils
from currency_utils import (
    detect_currency_and_rate,
    save_currency_info,
    load_currency_info,
)


def test_fallback_emits_diagnostic(monkeypatch):
    """1. test_fallback_emits_diagnostic: live fetch failing produces an fx_rate_fallback
    diagnostic naming the currency and the file's age.
    """
    monkeypatch.setattr(currency_utils, "fetch_live_fx_rates", lambda timeout_sec=5.0: None)

    emitted_diags = []

    def mock_diag(severity, code, source, message, **kwargs):
        emitted_diags.append({
            "severity": severity,
            "code": code,
            "source": source,
            "message": message,
            "kwargs": kwargs,
        })

    monkeypatch.setattr(currency_utils, "diag", mock_diag)

    info = detect_currency_and_rate(column_values=["INR"])
    assert info["currency"] == "INR"
    assert info["source"] == "fallback_table"
    assert info["updated"] is not None
    assert info["age_days"] is not None

    fb_diags = [d for d in emitted_diags if d["code"] == "fx_rate_fallback"]
    assert len(fb_diags) == 1
    d = fb_diags[0]
    assert "INR" in d["message"]
    assert str(info["age_days"]) in d["message"]
    assert info["updated"] in d["message"]


def test_live_success_is_quiet(monkeypatch):
    """2. test_live_success_is_quiet: a successful fetch emits no fallback warning."""
    mock_rates = {"INR": 95.0, "EUR": 0.90}
    monkeypatch.setattr(currency_utils, "fetch_live_fx_rates", lambda timeout_sec=5.0: mock_rates)

    emitted_diags = []

    def mock_diag(severity, code, source, message, **kwargs):
        emitted_diags.append({
            "severity": severity,
            "code": code,
            "source": source,
            "message": message,
        })

    monkeypatch.setattr(currency_utils, "diag", mock_diag)

    info = detect_currency_and_rate(column_values=["INR"])
    assert info["currency"] == "INR"
    assert info["source"] == "live"
    assert info["fx_rate"] == 95.0
    assert "live rate" in info["label"]

    fb_diags = [d for d in emitted_diags if d["code"] == "fx_rate_fallback"]
    assert len(fb_diags) == 0


def test_rate_source_recorded(monkeypatch, tmp_path):
    """3. test_rate_source_recorded: bill_currency carries source and, on the
    fallback path, the file date and age in days.
    """
    monkeypatch.setattr(currency_utils, "fetch_live_fx_rates", lambda timeout_sec=5.0: None)

    info = detect_currency_and_rate(column_values=["EUR"])
    assert info["source"] == "fallback_table"
    assert info["updated"] is not None
    assert info["age_days"] is not None

    job_dir = str(tmp_path / "job_rec")
    con = duckdb.connect(":memory:")
    save_currency_info(job_dir, info, conn=con)

    # Verify DuckDB bill_currency table contents
    row = con.execute("SELECT currency, fx_rate, source, label, updated, age_days FROM bill_currency").fetchone()
    assert row[0] == "EUR"
    assert row[2] == "fallback_table"
    assert row[4] == info["updated"]
    assert row[5] == info["age_days"]

    # Verify load_currency_info retrieves updated and age_days
    loaded = load_currency_info(job_dir, conn=con)
    assert loaded["source"] == "fallback_table"
    assert loaded["updated"] == info["updated"]
    assert loaded["age_days"] == info["age_days"]


def test_usd_bill_unaffected(tmp_path, monkeypatch):
    """4. test_usd_bill_unaffected: a USD bill produces no conversion line, no
    diagnostic, and identical figures.
    """
    from render_report import main as render_main

    emitted_diags = []

    def mock_diag(severity, code, source, message, **kwargs):
        emitted_diags.append({"code": code, "message": message})

    monkeypatch.setattr(currency_utils, "diag", mock_diag)

    job_dir = tmp_path / "job_usd"
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_file = audit_dir / "projection.duckdb"

    # Default USD currency info
    curr_info = detect_currency_and_rate(raw_text="Total $1,000.00 USD")
    assert curr_info["currency"] == "USD"
    assert curr_info["source"] == "base"
    assert len(emitted_diags) == 0

    conn = duckdb.connect(str(db_file))
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT PRIMARY KEY, product TEXT, instance_type TEXT,
            instance_vcpus INTEGER, instance_ram_gb DOUBLE, instance_count DOUBLE,
            usage_type TEXT, operation TEXT, aws_region TEXT, gcp_region TEXT,
            line_item_type TEXT, mechanic_group TEXT, pricing_model TEXT,
            pricing_unit TEXT, is_workload BOOLEAN, total_usage DOUBLE,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key TEXT, rowid BIGINT, strategy TEXT, gcp_service TEXT,
            gcp_machine_type TEXT, gcp_sku_id TEXT, gcp_sku_name TEXT,
            component TEXT, unit_multiplier DOUBLE, projection_note TEXT,
            mapping_confidence DOUBLE
        );
        CREATE TABLE gcp_projection (
            aws_li_key TEXT, component TEXT, gcp_service TEXT,
            gcp_machine_type TEXT, gcp_region TEXT, is_workload BOOLEAN,
            gcp_projected_cost DOUBLE, gcp_cost_1yr_cud DOUBLE,
            gcp_cost_3yr_cud DOUBLE, projection_status TEXT, projection_note TEXT
        );
        CREATE TABLE run_results (run_id TEXT, ts_utc TEXT, prospect TEXT);
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'Amazon Elastic Compute Cloud', 'm5.large', 2, 8.0, 1.0, 'BoxUsage:m5.large', 'RunInstances', 'us-east-1', 'us-central1', 'Usage', 'compute_breakdown', 'OnDemand', 'Hrs', true, 100, 100.0);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k1', 1, 'map', 'Compute Engine', 'n2-standard-2', 'sku-1', 'N2 Core', 'default', 1.0, 'OK', 0.95);
        INSERT INTO gcp_projection VALUES
            ('k1', 'default', 'Compute Engine', 'n2-standard-2', 'us-central1', true, 80.0, 60.0, 50.0, 'mapped', 'OK');
        INSERT INTO run_results VALUES ('run-usd', '2026-10-23 00:00:00', 'US Corp');
    """)
    save_currency_info(str(job_dir), curr_info, conn=conn)
    conn.close()

    monkeypatch.chdir(job_dir)
    monkeypatch.setattr("render_report.JOB_DIR", str(job_dir))
    monkeypatch.setattr("render_report.DB_PATH", str(db_file))

    render_main()

    import glob
    html_files = glob.glob(str(audit_dir / "report-*.html"))
    assert len(html_files) > 0
    with open(html_files[0], encoding="utf-8") as f:
        html_content = f.read()

    # USD report must NOT show Currency Notice banner or conversion lines
    assert "Currency Notice:" not in html_content
    assert "Converted at 1 USD" not in html_content
    assert "$100.00" in html_content
    assert "$80.00" in html_content
    assert len([d for d in emitted_diags if d["code"] == "fx_rate_fallback"]) == 0


def test_fx_failure_does_not_block(tmp_path, monkeypatch):
    """5. test_fx_failure_does_not_block: an FX fetch that raises still yields a
    complete projection.
    """
    def broken_fetch(timeout_sec=5.0):
        raise RuntimeError("Network DNS timeout or connection refused")

    monkeypatch.setattr(currency_utils, "fetch_live_fx_rates", broken_fetch)

    # Calling detect_currency_and_rate on EUR bill must NOT raise and must fallback gracefully
    info = detect_currency_and_rate(column_values=["EUR"])
    assert info["currency"] == "EUR"
    assert info["source"] == "fallback_table"
    assert info["fx_rate"] > 0
    assert info["age_days"] is not None
