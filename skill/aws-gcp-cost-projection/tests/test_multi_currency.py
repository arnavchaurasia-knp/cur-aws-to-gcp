import os
import csv
import json
import duckdb
import pytest
from currency_utils import (
    detect_currency_and_rate,
    save_currency_info,
    load_currency_info,
    load_fallback_fx,
)


def test_currency_detection_and_stated_rate(monkeypatch):
    import currency_utils
    monkeypatch.setattr(currency_utils, "fetch_live_fx_rates", lambda: None)

    # 1. Detection via column values using fallback table
    info = detect_currency_and_rate(column_values=["EUR"])
    assert info["currency"] == "EUR"
    assert info["symbol"] == "€"
    assert info["fx_rate"] == 0.92
    assert info["source"] == "fallback_table"

    # 2. Detection via text with stated rate: 1 USD = 0.95 EUR
    text = "Invoice details: Currency EUR. Note: 1 USD = 0.95 EUR conversion applied."
    info2 = detect_currency_and_rate(raw_text=text)
    assert info2["currency"] == "EUR"
    assert info2["fx_rate"] == 0.95
    assert info2["source"] == "bill_stated"
    assert "0.95" in info2["label"]

    # 3. GBP symbol detection
    info_gbp = detect_currency_and_rate(raw_text="Total due: £500.00")
    assert info_gbp["currency"] == "GBP"
    assert info_gbp["symbol"] == "£"
    assert info_gbp["fx_rate"] == 0.79


def test_render_report_eur_bill(tmp_path, monkeypatch):
    from render_report import main as render_main

    job_dir = tmp_path / "job_eur"
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_file = audit_dir / "projection.duckdb"

    # Save EUR currency info: 1 USD = 0.92 EUR
    curr_info = {
        "currency": "EUR",
        "symbol": "€",
        "fx_rate": 0.92,
        "source": "bill_stated",
        "label": "1 USD = 0.92 EUR (stated on bill)",
    }
    with open(audit_dir / "currency.json", "w", encoding="utf-8") as f:
        json.dump(curr_info, f)

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
        CREATE TABLE run_results (
            run_id TEXT,
            ts_utc TEXT,
            prospect TEXT
        );
    """)

    # Bill has 1000 EUR EC2 cost
    # GCP projected cost is 800 USD (which converts to 800 * 0.92 = 736 EUR)
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'Amazon Elastic Compute Cloud', 'm5.xlarge', 4, 16.0, 1.0, 'BoxUsage:m5.xlarge', 'RunInstances', 'eu-west-1', 'europe-west1', 'Usage', 'compute_breakdown', 'OnDemand', 'Hrs', true, 100, 1000.0);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k1', 1, 'map', 'Compute Engine', 'n2-standard-4', 'sku-1', 'N2 Instance Core', 'default', 1.0, 'OK', 0.95);
        INSERT INTO gcp_projection VALUES
            ('k1', 'default', 'Compute Engine', 'n2-standard-4', 'europe-west1', true, 800.0, 650.0, 500.0, 'mapped', 'OK');
        INSERT INTO run_results VALUES
            ('run-eur', '2026-10-14 00:00:00', 'European Corp');
    """)
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

    # 1. Currency header and notice banner
    assert "<b>Currency:</b> EUR" in html_content
    assert "Currency Notice:" in html_content
    assert "1 USD = 0.92 EUR" in html_content

    # 2. Both AWS and GCP rendered in EUR with € symbol
    # AWS List Total: €1,000.00
    assert "€1,000.00" in html_content
    # GCP On-Demand: 800 * 0.92 = €736.00
    assert "€736.00" in html_content

    # 3. Summary Markdown check
    summary_files = glob.glob(str(audit_dir / "summary-*.md"))
    assert len(summary_files) > 0
    with open(summary_files[0], encoding="utf-8") as f:
        md_content = f.read()
    assert "CURRENCY" in md_content
    assert "EUR" in md_content
    assert "€1,000" in md_content
    assert "€736" in md_content


def test_export_report_eur(tmp_path, monkeypatch):
    from export_report import main as export_main

    job_dir = tmp_path / "job_export_eur"
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_file = audit_dir / "projection.duckdb"

    curr_info = {
        "currency": "EUR",
        "symbol": "€",
        "fx_rate": 0.92,
        "source": "bill_stated",
        "label": "1 USD = 0.92 EUR (stated on bill)",
    }
    with open(audit_dir / "currency.json", "w", encoding="utf-8") as f:
        json.dump(curr_info, f)

    conn = duckdb.connect(str(db_file))
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT PRIMARY KEY,
            product TEXT,
            instance_type TEXT,
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
            gcp_projected_cost DOUBLE,
            gcp_cost_1yr_cud DOUBLE,
            gcp_cost_3yr_cud DOUBLE,
            projection_status TEXT,
            projection_note TEXT
        );
        CREATE TABLE run_results (
            run_id TEXT,
            ts_utc TEXT,
            prospect TEXT
        );
    """)

    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'Amazon Elastic Compute Cloud', 'm5.xlarge', 'BoxUsage', 'RunInstances', 'eu-west-1', 'europe-west1', 'Usage', 'compute_breakdown', 'OnDemand', 'Hrs', true, 100, 1000.0);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k1', 1, 'map', 'Compute Engine', 'n2-standard-4', 'sku-1', 'default', 1.0, 'OK', 0.95);
        INSERT INTO gcp_projection VALUES
            ('k1', 'default', 'Compute Engine', 'n2-standard-4', 'europe-west1', 800.0, 650.0, 500.0, 'mapped', 'OK');
        INSERT INTO run_results VALUES
            ('run-eur', '2026-10-14 00:00:00', 'European Corp');
    """)
    conn.close()

    monkeypatch.chdir(job_dir)
    monkeypatch.setattr("export_report.JOB_DIR", str(job_dir))
    monkeypatch.setattr("export_report.DB_PATH", str(db_file))

    # Test CSV export
    csv_out = str(job_dir / "report_eur.csv")
    monkeypatch.setattr("sys.argv", ["export_report.py", "--format", "csv", "--out", csv_out])
    export_main()

    with open(csv_out, encoding="utf-8") as f:
        reader = list(csv.reader(f))
    headers = reader[0]
    row = reader[1]

    # Verify column headers indicate EUR
    assert "AWS Cost (EUR)" in headers
    assert "GCP OD (EUR)" in headers
    assert "Diff (EUR)" in headers

    # Verify GCP cost was multiplied by 0.92 (800 * 0.92 = 736)
    gcp_idx = headers.index("GCP OD (EUR)")
    assert float(row[gcp_idx]) == 736.0


def test_rv_d3_inverted_quotes_and_symbol_disambiguation(monkeypatch):
    import currency_utils
    monkeypatch.setattr(currency_utils, "fetch_live_fx_rates", lambda: None)

    # Inverted quote: 1 EUR = 1.08 USD -> fx_rate is 1 / 1.08 ≈ 0.9259
    info_inv = detect_currency_and_rate(raw_text="Payment in EUR. FX rate: 1 EUR = 1.08 USD.")
    assert info_inv["currency"] == "EUR"
    assert abs(info_inv["fx_rate"] - (1.0 / 1.08)) < 1e-4
    assert info_inv["source"] == "bill_stated"

    # Inverted quote generic: "EUR exchange rate: 1.08"
    info_inv2 = detect_currency_and_rate(raw_text="Currency: EUR. Exchange rate: 1.08.")
    assert info_inv2["currency"] == "EUR"
    assert abs(info_inv2["fx_rate"] - (1.0 / 1.08)) < 1e-4

    # Yen disambiguation: CNY when Chinese context present
    info_cny = detect_currency_and_rate(raw_text="Total ¥50000. Invoice for China Beijing region.")
    assert info_cny["currency"] == "CNY"
    assert info_cny["symbol"] == "¥"
    assert info_cny["fx_rate"] == 7.25

    # Yen disambiguation: JPY by default
    info_jpy = detect_currency_and_rate(raw_text="Total ¥50000. Tokyo datacenter hosting.")
    assert info_jpy["currency"] == "JPY"
    assert info_jpy["symbol"] == "¥"
    assert info_jpy["fx_rate"] == 155.0

    # Stray symbol protection: A USD bill mentioning € in a text note should stay USD
    info_stray = detect_currency_and_rate(raw_text="Invoice Total: $12,500.00 USD. Competitor was €15,000.")
    assert info_stray["currency"] == "USD"
    assert info_stray["symbol"] == "$"
    assert info_stray["fx_rate"] == 1.0


def test_rv_d3_dual_side_conversion(tmp_path, monkeypatch):
    from render_report import main as render_main
    from export_report import main as export_main

    job_dir = tmp_path / "job_dual_fx"
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_file = audit_dir / "projection.duckdb"

    # Bill is displayed in EUR, but CUR costs were in USD
    curr_info = {
        "currency": "EUR",
        "symbol": "€",
        "fx_rate": 0.90,
        "aws_cost_currency": "USD",
        "aws_fx_rate": 0.90,
        "source": "bill_stated",
        "label": "1 USD = 0.90 EUR (stated on bill)",
    }
    with open(audit_dir / "currency.json", "w", encoding="utf-8") as f:
        json.dump(curr_info, f)

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
        CREATE TABLE run_results (
            run_id TEXT,
            ts_utc TEXT,
            prospect TEXT
        );
    """)

    # 1000 USD AWS EC2 cost -> 1000 * 0.90 = €900.00
    # 800 USD GCP cost -> 800 * 0.90 = €720.00
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'Amazon Elastic Compute Cloud', 'm5.xlarge', 4, 16.0, 1.0, 'BoxUsage:m5.xlarge', 'RunInstances', 'eu-west-1', 'europe-west1', 'Usage', 'compute_breakdown', 'OnDemand', 'Hrs', true, 100, 1000.0);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k1', 1, 'map', 'Compute Engine', 'n2-standard-4', 'sku-1', 'N2 Instance Core', 'default', 1.0, 'OK', 0.95);
        INSERT INTO gcp_projection VALUES
            ('k1', 'default', 'Compute Engine', 'n2-standard-4', 'europe-west1', true, 800.0, 650.0, 500.0, 'mapped', 'OK');
        INSERT INTO run_results VALUES
            ('run-dual', '2026-10-14 00:00:00', 'Dual FX Corp');
    """)
    conn.close()

    monkeypatch.chdir(job_dir)
    monkeypatch.setattr("render_report.JOB_DIR", str(job_dir))
    monkeypatch.setattr("render_report.DB_PATH", str(db_file))
    monkeypatch.setattr("export_report.JOB_DIR", str(job_dir))
    monkeypatch.setattr("export_report.DB_PATH", str(db_file))

    render_main()

    import glob
    html_files = glob.glob(str(audit_dir / "report-*.html"))
    assert len(html_files) > 0
    with open(html_files[0], encoding="utf-8") as f:
        html_content = f.read()

    # AWS costs converted to EUR: €900.00
    assert "€900.00" in html_content
    # GCP costs converted to EUR: €720.00
    assert "€720.00" in html_content
    # Currency notice includes conversion of AWS costs
    assert "AWS costs were converted from USD to EUR at 0.9" in html_content

    # Now export CSV
    csv_out = str(job_dir / "export_dual.csv")
    monkeypatch.setattr("sys.argv", ["export_report.py", "--format", "csv", "--out", csv_out])
    export_main()

    with open(csv_out, encoding="utf-8") as f:
        reader = list(csv.reader(f))
    headers = reader[0]
    row = reader[1]

    aws_idx = headers.index("AWS Cost (EUR)")
    gcp_idx = headers.index("GCP OD (EUR)")
    diff_idx = headers.index("Diff (EUR)")

    assert float(row[aws_idx]) == 900.0
    assert float(row[gcp_idx]) == 720.0
    assert float(row[diff_idx]) == 180.0

