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
from ingest import _is_workload_py, _nonworkload_sql, _WL_TYPES, _NONWL_PATTERNS


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
