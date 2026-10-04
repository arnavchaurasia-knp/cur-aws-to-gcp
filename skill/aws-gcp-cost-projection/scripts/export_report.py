#!/usr/bin/env python3
"""Export projection data as XLSX or CSV."""
import csv
import os
import sys

import duckdb

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")

_QUERY = """
SELECT
    ROW_NUMBER() OVER (ORDER BY c.aws_amortized_cost DESC) AS "#",
    c.product                                              AS "AWS Service",
    COALESCE(c.instance_type, '')                          AS "AWS Instance",
    COALESCE(p.gcp_service, '')                            AS "GCP Service",
    COALESCE(p.gcp_machine_type, '')                       AS "GCP Mapping",
    COALESCE(p.gcp_region, '')                             AS "Region",
    ROUND(c.aws_amortized_cost, 2)                         AS "AWS Cost ($)",
    ROUND(p.gcp_projected_cost, 2)                         AS "GCP OD ($)",
    ROUND(c.aws_amortized_cost
          - COALESCE(p.gcp_projected_cost, c.aws_amortized_cost), 2) AS "Diff ($)",
    ROUND(COALESCE(p.gcp_cost_1yr_cud, p.gcp_projected_cost), 2)     AS "GCP 1yr CUD ($)",
    ROUND(COALESCE(p.gcp_cost_3yr_cud, p.gcp_projected_cost), 2)     AS "GCP 3yr CUD ($)",
    COALESCE(m.strategy, '')                               AS "Strategy",
    ROUND(COALESCE(m.mapping_confidence, 0), 2)            AS "Confidence"
FROM aws_li_catalog c
LEFT JOIN aws_li_to_gcp_li m USING (aws_li_key)
LEFT JOIN gcp_projection p USING (aws_li_key)
ORDER BY c.aws_amortized_cost DESC
"""


def _safe_prospect(conn):
    try:
        row = conn.execute(
            "SELECT prospect FROM run_results ORDER BY ts_utc DESC LIMIT 1"
        ).fetchone()
        return (row[0] or "").strip() if row else ""
    except Exception:
        return ""


def main():
    fmt = "xlsx"
    out_path = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--format" and i + 1 < len(args):
            fmt = args[i + 1]
            i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out_path = args[i + 1]
            i += 2
        else:
            i += 1

    if not out_path:
        print("Usage: export_report.py --format [xlsx|csv] --out <path>", file=sys.stderr)
        sys.exit(1)

    conn = duckdb.connect(DB_PATH, read_only=True)
    result = conn.execute(_QUERY)
    headers = [d[0] for d in result.description]
    rows = result.fetchall()
    conn.close()

    if fmt == "csv":
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)

    elif fmt == "xlsx":
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "GCP Cost Analysis"

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

        # Auto-fit column widths (approximate)
        for col in ws.columns:
            max_len = max(
                (len(str(cell.value)) if cell.value is not None else 0)
                for cell in col
            )
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)

        wb.save(out_path)

    else:
        print(f"Unknown format: {fmt}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
