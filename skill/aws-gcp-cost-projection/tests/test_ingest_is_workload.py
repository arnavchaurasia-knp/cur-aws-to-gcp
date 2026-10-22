"""
Cross-format is_workload unification tests (NEW-3).

Asserts that is_workload derivation is identical across bill formats
(raw-CUR, spreadsheet, flat-CSV, PDF, and usage-units) for identical economic rows,
and tests the helper functions _is_workload_py and _nonworkload_sql.
"""
import os
import subprocess
import sys
import duckdb
import pytest

_SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INGEST = os.path.join(_SKILL, "scripts", "ingest.py")

sys.path.insert(0, os.path.join(_SKILL, "scripts"))
import json
from ingest import _is_workload_py, _nonworkload_sql, _WL_TYPES, _NONWL_PATTERNS
from diagnostics import read_all as read_diagnostics


def test_is_workload_py_direct():
    # Workload types
    assert _is_workload_py("Usage", "Amazon EC2", "RunInstances:0002") is True
    assert _is_workload_py("DiscountedUsage", "Amazon EC2", "RunInstances") is True
    assert _is_workload_py("SavingsPlanCoveredUsage", "Amazon EC2", "RunInstances") is True

    # Non-workload line item types
    assert _is_workload_py("Tax", "Tax", "Tax for US") is False
    assert _is_workload_py("Credit", "Amazon EC2", "Credit for SLA") is False
    assert _is_workload_py("Refund", "Amazon S3", "Refund") is False
    assert _is_workload_py("RIFee", "Amazon EC2", "HeavyUsage") is False

    # Patterns overriding Usage
    assert _is_workload_py("Usage", "AWS Marketplace", "SaaS contract") is False
    assert _is_workload_py("Usage", "Amazon EC2", "Developer Support") is False
    assert _is_workload_py(None, "AWS Support (Enterprise)", "Support charge") is False
    assert _is_workload_py(None, "Amazon Registrar", "Domain registration") is False
    assert _is_workload_py(None, "Amazon EC2", "Late fee") is False


def test_nonworkload_sql_direct():
    sql = _nonworkload_sql("cat_col", "desc_col")
    # substring patterns use ILIKE
    assert "desc_col ILIKE '%marketplace%'" in sql
    assert "cat_col ILIKE '%rifee%'" in sql
    # short words use word-boundary regexp (not bare ILIKE '%tax%' which hits 'syntax')
    assert r"regexp_matches(cat_col, '\btaxe?s?\b', 'i')" in sql
    assert r"regexp_matches(cat_col, '\bcredits?\b', 'i')" in sql
    assert "cat_col ILIKE '%tax%'" not in sql  # guard against the FP regression


def test_is_workload_covered_usage_stays_workload():
    # RI/SP-COVERED USAGE is real consumption (DiscountedUsage/SavingsPlanCoveredUsage)
    # -> must be workload, even on formats without a LineItemType column.
    assert _is_workload_py(None, "Amazon EC2",
                           "$0.10 per Reserved Instance applied m5.xlarge instance-hour") is True
    assert _is_workload_py(None, "Amazon EC2", "EC2 covered by Compute Savings Plans") is True
    # ...but the FEE rows are non-workload.
    assert _is_workload_py(None, "Savings Plans", "SavingsPlanRecurringFee") is False
    assert _is_workload_py(None, "Savings Plans", "Savings Plan recurring monthly fee") is False
    assert _is_workload_py(None, "Amazon EC2", "RIFee for m5.xlarge") is False


def test_is_workload_word_boundary_false_positives():
    # Short non-workload words must not match inside longer words.
    assert _is_workload_py(None, "Amazon Comprehend", "syntax analysis of documents") is True
    assert _is_workload_py(None, "Amazon Service", "taxonomy classification") is True
    assert _is_workload_py(None, "Amazon EC2", "supporting workload instance hour") is True
    # ...while the real words still match.
    assert _is_workload_py(None, "Tax", "sales taxes") is False
    assert _is_workload_py(None, "Billing", "promotional credits applied") is False
    assert _is_workload_py(None, "AWS Support (Enterprise)", "Support charge") is False


def _run_ingest(tmp_path, filename, content):
    job = tmp_path
    (job / filename).write_text(content, encoding="utf-8")
    env = {**os.environ, "SKILL_DIR": _SKILL}
    res = subprocess.run([sys.executable, _INGEST], cwd=str(job), env=env,
                         capture_output=True, text=True, timeout=120)
    db = job / "projection-audit" / "projection.duckdb"
    assert db.exists(), f"ingest failed ({res.stderr}):\n{res.stdout}"
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT product, operation, line_item_type, is_workload, aws_amortized_cost "
            "FROM aws_li_catalog"
        ).fetchall()
    finally:
        con.close()
    return rows


def test_cross_format_credit_row(tmp_path_factory):
    # 1. Raw CUR
    p_cur = tmp_path_factory.mktemp("cur")
    cur_csv = (
        "identity/LineItemId,lineItem/LineItemType,lineItem/ProductCode,lineItem/UsageType,lineItem/Operation,lineItem/UsageAmount,lineItem/UnblendedCost,pricing/publicOnDemandCost\n"
        "1,Credit,AmazonEC2,USE2-BoxUsage:t3.medium,RunInstances,1.0,-15.00,0.0\n"
    )
    rows_cur = _run_ingest(p_cur, "input.csv", cur_csv)
    assert len(rows_cur) == 1
    assert rows_cur[0][3] is False, f"CUR Credit is_workload should be False, got {rows_cur[0]}"

    # 2. Spreadsheet
    p_sheet = tmp_path_factory.mktemp("sheet")
    sheet_csv = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Amazon Elastic Compute Cloud,,Credit adjustment,1,-15.0,15.0,USD,123456789012,us-east-1,\n"
    )
    rows_sheet = _run_ingest(p_sheet, "input.csv", sheet_csv)
    assert len(rows_sheet) == 1
    assert rows_sheet[0][3] is False, f"Spreadsheet Credit is_workload should be False, got {rows_sheet[0]}"

    # 3. Flat CSV (service, description, cost)
    p_flat = tmp_path_factory.mktemp("flat")
    flat_csv = (
        "Service,Description,Cost\n"
        "Amazon Elastic Compute Cloud,Credit for monthly SLA outage,-15.00\n"
    )
    rows_flat = _run_ingest(p_flat, "input.csv", flat_csv)
    assert len(rows_flat) == 1
    assert rows_flat[0][3] is False, f"Flat CSV Credit is_workload should be False, got {rows_flat[0]}"

    # 4. Usage with units
    p_units = tmp_path_factory.mktemp("units")
    units_csv = (
        "Month,Service,Usage (with Units),Cost (USD)\n"
        "2026-06,Credit Adjustment,1 count,-15.00\n"
    )
    rows_units = _run_ingest(p_units, "input.csv", units_csv)
    assert len(rows_units) == 1
    assert rows_units[0][3] is False, f"Usage-units Credit is_workload should be False, got {rows_units[0]}"


def test_cross_format_ec2_usage_row(tmp_path_factory):
    # Raw CUR EC2 usage -> True
    p_cur = tmp_path_factory.mktemp("cur_ec2")
    cur_csv = (
        "identity/LineItemId,lineItem/LineItemType,lineItem/ProductCode,lineItem/UsageType,lineItem/Operation,lineItem/UsageAmount,lineItem/UnblendedCost,pricing/publicOnDemandCost\n"
        "1,Usage,AmazonEC2,USE2-BoxUsage:t3.medium,RunInstances,730.0,30.00,30.00\n"
    )
    rows_cur = _run_ingest(p_cur, "input.csv", cur_csv)
    assert len(rows_cur) == 1
    assert rows_cur[0][3] is True

    # Flat CSV EC2 usage -> True
    p_flat = tmp_path_factory.mktemp("flat_ec2")
    flat_csv = (
        "Service,Description,Cost\n"
        "Amazon Elastic Compute Cloud,t3.medium Linux instance hour,30.00\n"
    )
    rows_flat = _run_ingest(p_flat, "input.csv", flat_csv)
    assert len(rows_flat) == 1
    assert rows_flat[0][3] is True


def test_cross_format_marketplace_override(tmp_path_factory):
    # LineItemType=Usage, but ProductCode=AWSMarketplace -> False
    p_cur = tmp_path_factory.mktemp("cur_mp")
    cur_csv = (
        "identity/LineItemId,lineItem/LineItemType,lineItem/ProductCode,lineItem/UsageType,lineItem/Operation,lineItem/UsageAmount,lineItem/UnblendedCost,pricing/publicOnDemandCost\n"
        "1,Usage,AWSMarketplace,USE2-Software,Subscription,1.0,100.00,100.00\n"
    )
    rows_cur = _run_ingest(p_cur, "input.csv", cur_csv)
    assert len(rows_cur) == 1
    assert rows_cur[0][3] is False

    # Flat CSV Marketplace -> False
    p_flat = tmp_path_factory.mktemp("flat_mp")
    flat_csv = (
        "Service,Description,Cost\n"
        "AWS Marketplace,Datadog Pro Subscription,100.00\n"
    )
    rows_flat = _run_ingest(p_flat, "input.csv", flat_csv)
    assert len(rows_flat) == 1
    assert rows_flat[0][3] is False


def test_cross_format_tax_and_rifee_rows(tmp_path_factory):
    # Tax in spreadsheet -> False
    p_sheet = tmp_path_factory.mktemp("sheet_tax")
    sheet_csv = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Tax,,US Sales Tax,1,25.0,25.0,USD,123456789012,us-east-1,\n"
    )
    rows_sheet = _run_ingest(p_sheet, "input.csv", sheet_csv)
    assert len(rows_sheet) == 1
    assert rows_sheet[0][3] is False

    # RIFee in flat CSV -> False
    p_flat = tmp_path_factory.mktemp("flat_rifee")
    flat_csv = (
        "Service,Description,Cost\n"
        "Amazon Elastic Compute Cloud,RIFee heavy usage commitment,100.00\n"
    )
    rows_flat = _run_ingest(p_flat, "input.csv", flat_csv)
    assert len(rows_flat) == 1
    assert rows_flat[0][3] is False


# ---------------------------------------------------------------------------
# Credit & Savings Plan coverage lines in non-CUR formats
# ---------------------------------------------------------------------------

def test_negative_line_is_not_workload_service_format(tmp_path_factory):
    p = tmp_path_factory.mktemp("svc_format")
    csv_content = (
        "Service,Region,Custom Usage Type,Description,Usage Quantity,Cost ($)\n"
        "Amazon Elastic Compute Cloud,us-east-1,a1.2xlarge,On Demand Linux a1.2xlarge Instance Hour,1488.00,349.98\n"
        "Amazon Elastic Compute Cloud,us-east-1,a1.2xlarge,a1.2xlarge Linux instance usage covered by Compute Savings Plans,1488.00,-349.98\n"
    )
    rows = _run_ingest(p, "input.csv", csv_content)
    assert len(rows) == 2
    # rows are: product, operation, line_item_type, is_workload, aws_amortized_cost
    workload_rows = [r for r in rows if r[3] is True]
    assert len(workload_rows) == 1
    assert pytest.approx(sum(r[4] for r in workload_rows), rel=1e-3) == 349.98


def test_negative_line_is_not_workload_meter_format(tmp_path_factory):
    p = tmp_path_factory.mktemp("meter_format")
    csv_content = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Amazon Elastic Compute Cloud,,BoxUsage:t3.medium,730.0,30.0,30.0,USD,123456789012,us-east-1,\n"
        "Amazon Elastic Compute Cloud,,Discount credit,1.0,-30.0,30.0,USD,123456789012,us-east-1,\n"
    )
    rows = _run_ingest(p, "input.csv", csv_content)
    assert len(rows) == 2
    workload_rows = [r for r in rows if r[3] is True]
    assert len(workload_rows) == 1
    assert pytest.approx(sum(r[4] for r in workload_rows), rel=1e-3) == 30.0


def test_savings_plan_credit_is_labelled(tmp_path_factory):
    p = tmp_path_factory.mktemp("sp_label")
    csv_content = (
        "Service,Region,Custom Usage Type,Description,Usage Quantity,Cost ($)\n"
        "Amazon Elastic Compute Cloud,us-east-1,a1.2xlarge,a1.2xlarge Linux instance usage covered by Compute Savings Plans,1488.00,-349.98\n"
        "Amazon Elastic Compute Cloud,us-east-1,discount,Enterprise Discount,1.00,-100.00\n"
    )
    rows = _run_ingest(p, "input.csv", csv_content)
    assert len(rows) == 2
    by_desc = {r[1]: r for r in rows}
    sp_row = by_desc["a1.2xlarge Linux instance usage covered by Compute Savings Plans"]
    assert sp_row[2] == "SavingsPlanCoveredUsage"
    assert sp_row[3] is False

    disc_row = by_desc["Enterprise Discount"]
    assert disc_row[2] == "Credit"
    assert disc_row[3] is False


def test_bill_total_unchanged_when_credits_reclassified(tmp_path_factory):
    p = tmp_path_factory.mktemp("bill_total")
    csv_content = (
        "Service,Region,Custom Usage Type,Description,Usage Quantity,Cost ($)\n"
        "Amazon Elastic Compute Cloud,us-east-1,a1.2xlarge,On Demand Linux a1.2xlarge Instance Hour,1488.00,349.98\n"
        "Amazon Elastic Compute Cloud,us-east-1,a1.2xlarge,a1.2xlarge Linux instance usage covered by Compute Savings Plans,1488.00,-349.98\n"
        "Amazon Simple Storage Service,us-east-1,StandardStorage,Standard storage in us-east-1,100.00,10.00\n"
        "Amazon Elastic Compute Cloud,us-east-1,credit,Monthly promo credit,1.00,-5.00\n"
    )
    rows = _run_ingest(p, "input.csv", csv_content)
    assert len(rows) == 4
    total_cost = sum(r[4] for r in rows)
    # 349.98 - 349.98 + 10.00 - 5.00 = 5.00
    assert pytest.approx(total_cost, rel=1e-3) == 5.00


# ---------------------------------------------------------------------------
# Unit Price Outlier Detection Tests (WORKORDER_unit_price_outlier.md)
# ---------------------------------------------------------------------------

def test_unit_price_outlier_detected(tmp_path_factory):
    # 1. Five rows on one meter, four at price p and one at 1000p with cost > $50,
    # produces one unit_price_outlier diagnostic naming that meter.
    p = tmp_path_factory.mktemp("outlier_detected")
    csv_content = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,63930.16,0.85,0.00001333,USD,123456789012,ap-southeast-5,res-1\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,377694.12,5.04,0.00001333,USD,123456789012,ap-southeast-5,res-2\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,953703.71,12.72,0.00001333,USD,123456789012,ap-southeast-5,res-3\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,23579810.90,314.40,0.00001333,USD,123456789012,ap-southeast-5,res-4\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,480703.23,6409.41,0.0133334,USD,123456789012,ap-southeast-5,res-5\n"
    )
    _run_ingest(p, "input.csv", csv_content)
    diags = [d for d in read_diagnostics(str(p)) if d.get("code") == "unit_price_outlier"]
    assert len(diags) == 1
    assert "APS5-Lambda-GB-Second-ARM" in diags[0]["message"]
    assert "1000x" in diags[0]["message"]
    assert diags[0]["rows"] == 1
    assert pytest.approx(diags[0]["usd"], rel=1e-3) == 6409.41


def test_unit_price_outlier_not_flagged_below_threshold(tmp_path_factory):
    # 2. Same fixture with the odd row at 12.5p produces no diagnostic.
    p = tmp_path_factory.mktemp("outlier_below_threshold")
    # Reference price ~ 0.00001333; 12.5x price ~ 0.00016666
    # 400,000 consumed * 0.00016666 = $66.66 (> $50 cost, but factor 12.5x < 100x)
    csv_content = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,63930.16,0.85,0.00001333,USD,123456789012,ap-southeast-5,res-1\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,377694.12,5.04,0.00001333,USD,123456789012,ap-southeast-5,res-2\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,953703.71,12.72,0.00001333,USD,123456789012,ap-southeast-5,res-3\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,23579810.90,314.40,0.00001333,USD,123456789012,ap-southeast-5,res-4\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,400000.00,66.66,0.00016666,USD,123456789012,ap-southeast-5,res-5\n"
    )
    _run_ingest(p, "input.csv", csv_content)
    diags = [d for d in read_diagnostics(str(p)) if d.get("code") == "unit_price_outlier"]
    assert len(diags) == 0


def test_unit_price_outlier_ignores_zero_cost_rows(tmp_path_factory):
    # 3. A meter whose rows are mostly zero-cost does not flag (the 1.6e16 regression).
    p = tmp_path_factory.mktemp("outlier_zero_cost")
    csv_content = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,100000.0,0.00,0.0,USD,123456789012,ap-southeast-5,res-1\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,100000.0,0.00,0.0,USD,123456789012,ap-southeast-5,res-2\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,100000.0,0.00,0.0,USD,123456789012,ap-southeast-5,res-3\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,100000.0,0.00,0.0,USD,123456789012,ap-southeast-5,res-4\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,100000.0,60.00,0.0006,USD,123456789012,ap-southeast-5,res-5\n"
    )
    _run_ingest(p, "input.csv", csv_content)
    diags = [d for d in read_diagnostics(str(p)) if d.get("code") == "unit_price_outlier"]
    assert len(diags) == 0


def test_unit_price_outlier_changes_no_totals(tmp_path_factory):
    # 4. Catalog total and workload total are identical with and without the flagged row's anomaly present.
    # We verify that whether the 5th line has normal price ($6.41) or outlier price ($6409.41),
    # the catalog faithfully preserves the exact total sum of the input rows (zero dollar movement).
    p1 = tmp_path_factory.mktemp("totals_normal")
    csv_normal = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,63930.16,0.85,0.00001333,USD,123456789012,ap-southeast-5,res-1\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,377694.12,5.04,0.00001333,USD,123456789012,ap-southeast-5,res-2\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,953703.71,12.72,0.00001333,USD,123456789012,ap-southeast-5,res-3\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,23579810.90,314.40,0.00001333,USD,123456789012,ap-southeast-5,res-4\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,480703.23,6.41,0.00001333,USD,123456789012,ap-southeast-5,res-5\n"
    )
    rows1 = _run_ingest(p1, "input.csv", csv_normal)
    raw_sum1 = 0.85 + 5.04 + 12.72 + 314.40 + 6.41
    cat_sum1 = sum(r[4] for r in rows1)
    wl_sum1 = sum(r[4] for r in rows1 if r[3] is True)
    assert pytest.approx(cat_sum1, rel=1e-3) == raw_sum1
    assert pytest.approx(wl_sum1, rel=1e-3) == raw_sum1

    p2 = tmp_path_factory.mktemp("totals_outlier")
    csv_outlier = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,63930.16,0.85,0.00001333,USD,123456789012,ap-southeast-5,res-1\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,377694.12,5.04,0.00001333,USD,123456789012,ap-southeast-5,res-2\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,953703.71,12.72,0.00001333,USD,123456789012,ap-southeast-5,res-3\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,23579810.90,314.40,0.00001333,USD,123456789012,ap-southeast-5,res-4\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,480703.23,6409.41,0.0133334,USD,123456789012,ap-southeast-5,res-5\n"
    )
    rows2 = _run_ingest(p2, "input.csv", csv_outlier)
    raw_sum2 = 0.85 + 5.04 + 12.72 + 314.40 + 6409.41
    cat_sum2 = sum(r[4] for r in rows2)
    wl_sum2 = sum(r[4] for r in rows2 if r[3] is True)
    assert pytest.approx(cat_sum2, rel=1e-3) == raw_sum2
    assert pytest.approx(wl_sum2, rel=1e-3) == raw_sum2


def test_unit_price_outlier_reports_source_line(tmp_path_factory):
    # 5. Diagnostic's message contains the file line number of the offending row,
    # and detail["lines"][0] carries file_line, meter, unit_price and reference_price.
    p = tmp_path_factory.mktemp("outlier_source_line")
    csv_content = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,63930.16,0.85,0.00001333,USD,123456789012,ap-southeast-5,res-1\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,377694.12,5.04,0.00001333,USD,123456789012,ap-southeast-5,res-2\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,953703.71,12.72,0.00001333,USD,123456789012,ap-southeast-5,res-3\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,23579810.90,314.40,0.00001333,USD,123456789012,ap-southeast-5,res-4\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,480703.23,6409.41,0.0133334,USD,123456789012,ap-southeast-5,res-5\n"
    )
    _run_ingest(p, "input.csv", csv_content)
    diags = [d for d in read_diagnostics(str(p)) if d.get("code") == "unit_price_outlier"]
    assert len(diags) == 1
    d = diags[0]
    # Header is line 1, rows are 2..6, row 5 is line 6
    assert "file line 6" in d["message"]
    detail_lines = d["detail"].get("lines", [])
    assert len(detail_lines) == 1
    item = detail_lines[0]
    assert item["file_line"] == 6
    assert item["meter"] == "APS5-Lambda-GB-Second-ARM"
    assert pytest.approx(item["unit_price"], rel=1e-3) == 0.0133334
    assert pytest.approx(item["reference_price"], rel=1e-3) == 0.00001333
    assert item.get("resource") == "res-5"
    assert item.get("account_id") == "123456789012"


def test_unit_price_outlier_appends_to_data_gaps(tmp_path_factory):
    # 6. Existing data_gaps.json containing an unrelated item still contains that item afterwards,
    # plus the new one (read-modify-write requirement).
    p = tmp_path_factory.mktemp("outlier_data_gaps")
    gaps_file = p / "data_gaps.json"
    initial_gap = {
        "count": 1,
        "total_cost": 100.0,
        "items": [
            {
                "aws_li_key": "unrelated_key_123",
                "product": "Unrelated Product",
                "usage_type": "UnrelatedUsage",
                "aws_cost": 100.0,
                "gap_type": "unmapped_row",
            }
        ]
    }
    gaps_file.write_text(json.dumps(initial_gap), encoding="utf-8")

    csv_content = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,63930.16,0.85,0.00001333,USD,123456789012,ap-southeast-5,res-1\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,377694.12,5.04,0.00001333,USD,123456789012,ap-southeast-5,res-2\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,953703.71,12.72,0.00001333,USD,123456789012,ap-southeast-5,res-3\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,23579810.90,314.40,0.00001333,USD,123456789012,ap-southeast-5,res-4\n"
        "Amazon Elastic Compute Cloud,,APS5-Lambda-GB-Second-ARM,480703.23,6409.41,0.0133334,USD,123456789012,ap-southeast-5,res-5\n"
    )
    _run_ingest(p, "input.csv", csv_content)

    assert gaps_file.exists()
    updated_data = json.loads(gaps_file.read_text(encoding="utf-8"))
    items = updated_data.get("items", [])
    # Unrelated item is still present
    unrelated = [it for it in items if it.get("aws_li_key") == "unrelated_key_123"]
    assert len(unrelated) == 1
    # New outlier item is appended
    outlier_items = [it for it in items if it.get("meter") == "APS5-Lambda-GB-Second-ARM"]
    assert len(outlier_items) == 1
    assert outlier_items[0]["file_line"] == 6
    assert pytest.approx(outlier_items[0]["cost"], rel=1e-3) == 6409.41
    assert updated_data["count"] == 2
    assert pytest.approx(updated_data["total_cost"], rel=1e-3) == 100.0 + 6409.41



def test_data_dir_resolves_without_skill_dir(tmp_path, monkeypatch):
    """ingest.DATA_DIR must find the data directory even when SKILL_DIR is unset.

    The Go spawner (internal/jobs/spawner.go, skillEnv) sets SKILL_DIR in
    production, but DATA_DIR previously had no fallback: unset SKILL_DIR made it
    the relative path "data", resolved against the job directory. The
    instance-spec tables then loaded as {} and every instance type came out with
    NULL vcpus/ram_gb -- silently, with the bill still reconciling. apply_rates.py
    and apply_static_mappings.py already derive SKILL_DIR from __file__; this
    pins ingest.py to the same behaviour.
    """
    import importlib

    monkeypatch.delenv("SKILL_DIR", raising=False)
    monkeypatch.chdir(tmp_path)          # a cwd with no ./data directory

    import ingest
    reloaded = importlib.reload(ingest)
    try:
        assert os.path.isabs(reloaded.DATA_DIR), (
            f"DATA_DIR must be absolute, got {reloaded.DATA_DIR!r}")
        for name in ("ec2-instance-types.json", "service_map.json"):
            assert os.path.exists(os.path.join(reloaded.DATA_DIR, name)), (
                f"{name} not found under DATA_DIR={reloaded.DATA_DIR!r} -- "
                "instance specs would silently load as empty")
    finally:
        # leave the module as the rest of the suite expects to find it
        importlib.reload(ingest)


def test_sp_recurring_fee_not_workload_spreadsheet(tmp_path_factory):
    """A SavingsPlanRecurringFee row in the spreadsheet format comes out is_workload = false."""
    p_sheet = tmp_path_factory.mktemp("sheet_sp_fee")
    sheet_csv = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Savings Plans for AWS Compute usage,,aps3-ec2sp:m6a.1yrnoupfront,1,100.0,100.0,USD,123456789012,ap-south-1,\n"
    )
    rows_sheet = _run_ingest(p_sheet, "input.csv", sheet_csv)
    assert len(rows_sheet) == 1
    assert rows_sheet[0][2] == "SavingsPlanRecurringFee"
    assert rows_sheet[0][3] is False


def test_sp_recurring_fee_workload_matches_cur(tmp_path_factory):
    """The same SavingsPlan recurring fee in CUR and spreadsheet formats produces the same is_workload (False)."""
    # 1. CUR format
    p_cur = tmp_path_factory.mktemp("cur_sp_fee")
    cur_csv = (
        "identity/LineItemId,lineItem/LineItemType,lineItem/ProductCode,lineItem/UsageType,lineItem/Operation,lineItem/UsageAmount,lineItem/UnblendedCost,pricing/publicOnDemandCost\n"
        "li1,SavingsPlanRecurringFee,AWSSavingsPlan,aps3-ec2sp:m6a.1yrnoupfront,,1.0,100.0,100.0\n"
    )
    rows_cur = _run_ingest(p_cur, "input.csv", cur_csv)
    assert len(rows_cur) == 1
    assert rows_cur[0][2] == "SavingsPlanRecurringFee"
    assert rows_cur[0][3] is False

    # 2. Spreadsheet format
    p_sheet = tmp_path_factory.mktemp("sheet_sp_fee_cross")
    sheet_csv = (
        "Category,Subcategory,Meter,Consumed,Cost,Unit Price,Currency,Account ID,Location,Resource\n"
        "Savings Plans for AWS Compute usage,,aps3-ec2sp:m6a.1yrnoupfront,1,100.0,100.0,USD,123456789012,ap-south-1,\n"
    )
    rows_sheet = _run_ingest(p_sheet, "input.csv", sheet_csv)
    assert len(rows_sheet) == 1
    assert rows_sheet[0][2] == "SavingsPlanRecurringFee"
    assert rows_sheet[0][3] is False

    # Verify identical is_workload
    assert rows_cur[0][3] == rows_sheet[0][3]

