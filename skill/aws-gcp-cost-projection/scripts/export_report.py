#!/usr/bin/env python3
"""Export projection data as XLSX or CSV."""
import csv
import os
import sys

import duckdb
from currency_utils import load_currency_info

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")


def _build_query(fx_rate=1.0, curr_code="USD", aws_fx_rate=1.0):
    cur_sym = f" ({curr_code})" if curr_code != "USD" else " ($)"
    return f"""
WITH component_rank AS (
    SELECT rowid AS row_id, aws_li_key,
           ROW_NUMBER() OVER (PARTITION BY aws_li_key ORDER BY rowid) AS rn
    FROM aws_li_to_gcp_li
),
per_item AS (
    SELECT
        c.aws_amortized_cost,
        c.product,
        c.instance_type,
        p.gcp_service,
        p.gcp_machine_type,
        p.gcp_region,
        m.strategy,
        m.mapping_confidence,
        (CASE WHEN COALESCE(cr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END) * {aws_fx_rate} AS aws_cost,
        CASE
            WHEN m.strategy = 'ignore' THEN 0.0
            WHEN m.strategy IN ('map', 'break_down') AND p.gcp_projected_cost IS NOT NULL
                THEN p.gcp_projected_cost * {fx_rate}
            ELSE (CASE WHEN COALESCE(cr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END) * {aws_fx_rate}
        END AS gcp_od_cost,
        CASE
            WHEN m.strategy = 'ignore' THEN 0.0
            WHEN m.strategy IN ('map', 'break_down') AND p.gcp_projected_cost IS NOT NULL
                THEN COALESCE(p.gcp_cost_1yr_cud, p.gcp_projected_cost) * {fx_rate}
            ELSE (CASE WHEN COALESCE(cr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END) * {aws_fx_rate}
        END AS gcp_1yr_cost,
        CASE
            WHEN m.strategy = 'ignore' THEN 0.0
            WHEN m.strategy IN ('map', 'break_down') AND p.gcp_projected_cost IS NOT NULL
                THEN COALESCE(p.gcp_cost_3yr_cud, p.gcp_projected_cost) * {fx_rate}
            ELSE (CASE WHEN COALESCE(cr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END) * {aws_fx_rate}
        END AS gcp_3yr_cost,
        CASE
            WHEN m.strategy IN ('map', 'break_down') AND c.is_workload AND p.gcp_projected_cost IS NOT NULL AND c.aws_amortized_cost > 0
                THEN 'A'
            ELSE 'B'
        END AS section
    FROM aws_li_catalog c
    LEFT JOIN aws_li_to_gcp_li m USING (aws_li_key)
    LEFT JOIN gcp_projection p ON p.aws_li_key = m.aws_li_key AND p.component = m.component
    LEFT JOIN component_rank cr ON cr.row_id = m.rowid
)
SELECT
    ROW_NUMBER() OVER (ORDER BY aws_amortized_cost DESC) AS "#",
    product                                              AS "AWS Service",
    COALESCE(instance_type, '')                          AS "AWS Instance",
    COALESCE(gcp_service, '')                            AS "GCP Service",
    COALESCE(gcp_machine_type, '')                       AS "GCP Mapping",
    COALESCE(gcp_region, '')                             AS "Region",
    ROUND(aws_cost, 2)                                   AS "AWS Cost{cur_sym}",
    ROUND(gcp_od_cost, 2)                                AS "GCP OD{cur_sym}",
    ROUND(aws_cost - gcp_od_cost, 2)                     AS "Diff{cur_sym}",
    ROUND(gcp_1yr_cost, 2)                               AS "GCP 1yr CUD{cur_sym}",
    ROUND(gcp_3yr_cost, 2)                               AS "GCP 3yr CUD{cur_sym}",
    section                                              AS "Section",
    COALESCE(strategy, '')                               AS "Strategy",
    ROUND(COALESCE(mapping_confidence, 0), 2)            AS "Confidence"
FROM per_item
ORDER BY aws_amortized_cost DESC
"""


def _build_query_by_instance(fx_rate=1.0, curr_code="USD", aws_fx_rate=1.0):
    cur_sym = f" ({curr_code})" if curr_code != "USD" else " ($)"
    return f"""
WITH component_rank AS (
    SELECT rowid AS row_id, aws_li_key,
           ROW_NUMBER() OVER (PARTITION BY aws_li_key ORDER BY rowid) AS rn
    FROM aws_li_to_gcp_li
),
per_line_item AS (
    SELECT
        c.product                                              AS product,
        COALESCE(c.instance_type, '')                          AS instance_type,
        COALESCE(p.gcp_machine_type, '')                       AS gcp_mapping,
        COALESCE(p.gcp_region, c.gcp_region, '')                AS region,
        (CASE WHEN COALESCE(cr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END) * {aws_fx_rate}
                                                                AS aws_cost,
        COALESCE(p.gcp_projected_cost, 0) * {fx_rate}          AS gcp_od_cost,
        COALESCE(p.gcp_cost_1yr_cud, p.gcp_projected_cost, 0) * {fx_rate} AS gcp_1yr_cost,
        COALESCE(p.gcp_cost_3yr_cud, p.gcp_projected_cost, 0) * {fx_rate} AS gcp_3yr_cost
    FROM aws_li_catalog c
    LEFT JOIN aws_li_to_gcp_li m USING (aws_li_key)
    LEFT JOIN gcp_projection p ON p.aws_li_key = m.aws_li_key AND p.component = m.component
    LEFT JOIN component_rank cr ON cr.row_id = m.rowid
    WHERE COALESCE(c.instance_type, '') != ''
)
SELECT
    ROW_NUMBER() OVER (ORDER BY SUM(aws_cost) DESC)   AS "#",
    product                                            AS "AWS Service",
    instance_type                                      AS "AWS Instance",
    gcp_mapping                                        AS "GCP Mapping",
    region                                              AS "Region",
    COUNT(*)                                            AS "Line Items",
    ROUND(SUM(aws_cost), 2)                             AS "AWS Cost{cur_sym}",
    ROUND(SUM(gcp_od_cost), 2)                          AS "GCP OD{cur_sym}",
    ROUND(SUM(aws_cost) - SUM(gcp_od_cost), 2)          AS "Diff{cur_sym}",
    ROUND(SUM(gcp_1yr_cost), 2)                         AS "GCP 1yr CUD{cur_sym}",
    ROUND(SUM(gcp_3yr_cost), 2)                         AS "GCP 3yr CUD{cur_sym}"
FROM per_line_item
GROUP BY product, instance_type, gcp_mapping, region
ORDER BY SUM(aws_cost) DESC
"""


def _build_query_discounts(curr_code="USD", aws_fx_rate=1.0):
    cur_sym = f" ({curr_code})" if curr_code != "USD" else " ($)"
    return f"""
SELECT
    ROW_NUMBER() OVER (ORDER BY discount_amount DESC) AS "#",
    service                                            AS "AWS Service",
    discount_types                                     AS "Discount Type",
    item_count                                         AS "Line Items",
    ROUND(discount_amount, 2)                          AS "Discount Amount{cur_sym}"
FROM (
    SELECT
        COALESCE(c.product, 'Discounts & Credits') AS service,
        STRING_AGG(DISTINCT COALESCE(NULLIF(c.line_item_type, ''), c.mechanic_group), ', ') AS discount_types,
        COUNT(*)                                   AS item_count,
        SUM(ABS(c.aws_amortized_cost)) * {aws_fx_rate}             AS discount_amount
    FROM aws_li_catalog c
    WHERE c.aws_amortized_cost < 0
    GROUP BY c.product
    HAVING discount_amount > 0.005
)
ORDER BY discount_amount DESC
"""


_QUERY = _build_query()
_QUERY_BY_INSTANCE = _build_query_by_instance()
_QUERY_DISCOUNTS = _build_query_discounts()


def _safe_prospect(conn):
    try:
        row = conn.execute(
            "SELECT prospect FROM run_results ORDER BY ts_utc DESC LIMIT 1"
        ).fetchone()
        return (row[0] or "").strip() if row else ""
    except Exception:
        return ""


def _write_sheet(wb, title, headers, rows):
    from openpyxl.styles import Alignment, Font, PatternFill

    ws = wb.create_sheet(title)

    header_fill = PatternFill(start_color="1A73E8", end_color="1A73E8", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", name="Arial", size=10)
    alt_fill = PatternFill(start_color="F8F9FA", end_color="F8F9FA", fill_type="solid")
    data_font = Font(name="Arial", size=10)

    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for ri, row in enumerate(rows, 2):
        fill = alt_fill if ri % 2 == 0 else None
        for ci, val in enumerate(row, 1):
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.font = data_font
            if fill:
                cell.fill = fill

    for col in ws.columns:
        max_len = max(
            (len(str(cell.value)) if cell.value is not None else 0)
            for cell in col
        )
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)
    return ws


def main():
    fmt = "xlsx"
    out_path = None
    by_instance = False
    by_discounts = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--format" and i + 1 < len(args):
            fmt = args[i + 1]
            i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out_path = args[i + 1]
            i += 2
        elif args[i] == "--by-instance":
            by_instance = True
            i += 1
        elif args[i] == "--by-discounts":
            by_discounts = True
            i += 1
        else:
            i += 1

    if not out_path:
        print(
            "Usage: export_report.py --format [xlsx|csv] --out <path> [--by-instance] [--by-discounts]\n"
            "  --by-instance    CSV: emit the per-instance-type rollup instead of raw line items.\n"
            "  --by-discounts   CSV: emit the discounts-by-service rollup instead of raw line items.\n"
            "                   XLSX: always included as an extra 'Discounts by Service' sheet.",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = duckdb.connect(DB_PATH, read_only=True)
    job_dir = JOB_DIR if os.path.exists(os.path.join(JOB_DIR, "projection-audit")) else os.path.dirname(os.path.dirname(os.path.abspath(DB_PATH)))
    currency_info = load_currency_info(job_dir, conn)
    curr_code = currency_info.get("currency", "USD")
    fx_rate = float(currency_info.get("fx_rate", 1.0))
    aws_fx_rate = float(currency_info.get("aws_fx_rate", 1.0))

    query = _build_query(fx_rate, curr_code, aws_fx_rate)
    inst_query = _build_query_by_instance(fx_rate, curr_code, aws_fx_rate)
    disc_query = _build_query_discounts(curr_code, aws_fx_rate)

    result = conn.execute(query)
    headers = [d[0] for d in result.description]
    rows = result.fetchall()

    inst_result = conn.execute(inst_query)
    inst_headers = [d[0] for d in inst_result.description]
    inst_rows = inst_result.fetchall()

    try:
        disc_result = conn.execute(disc_query)
        disc_headers = [d[0] for d in disc_result.description]
        disc_rows = disc_result.fetchall()
    except Exception:
        cur_sym = f" ({curr_code})" if curr_code != "USD" else " ($)"
        disc_headers = ["#", "AWS Service", "Discount Type", "Line Items", f"Discount Amount{cur_sym}"]
        disc_rows = []
    conn.close()

    if fmt == "csv":
        if by_instance:
            headers, rows = inst_headers, inst_rows
        elif by_discounts:
            headers, rows = disc_headers, disc_rows
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)

    elif fmt == "xlsx":
        import openpyxl

        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        _write_sheet(wb, "GCP Cost Analysis", headers, rows)
        _write_sheet(wb, "Per-Instance Summary", inst_headers, inst_rows)
        if disc_rows:
            _write_sheet(wb, "Discounts by Service", disc_headers, disc_rows)
        wb.save(out_path)

    else:
        print(f"Unknown format: {fmt}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
