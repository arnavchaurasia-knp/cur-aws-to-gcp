#!/usr/bin/env python3
"""Export projection data as XLSX or CSV."""
import csv
import os
import sys

import duckdb

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")

_QUERY = """
-- A line item can fan out into multiple GCP mapping components (e.g. an
-- EC2 instance-hour split into 'core' + 'ram'). aws_li_catalog carries
-- aws_amortized_cost at the LINE-ITEM level, not per component, so a plain
-- join against aws_li_to_gcp_li/gcp_projection repeats that same AWS cost
-- on every component row while gcp_projected_cost is correctly split —
-- summing the exported "AWS Cost ($)" column then overcounts AWS spend by
-- Nx for every multi-component line item. Rank components per aws_li_key
-- (mirroring projection_view.py's passthrough_rank) so only the first
-- component row carries the real AWS cost; the rest show $0, keeping
-- per-row totals additive on both the AWS and GCP side.
WITH component_rank AS (
    SELECT rowid AS row_id, aws_li_key,
           ROW_NUMBER() OVER (PARTITION BY aws_li_key ORDER BY rowid) AS rn
    FROM aws_li_to_gcp_li
)
SELECT
    ROW_NUMBER() OVER (ORDER BY c.aws_amortized_cost DESC) AS "#",
    c.product                                              AS "AWS Service",
    COALESCE(c.instance_type, '')                          AS "AWS Instance",
    COALESCE(p.gcp_service, '')                            AS "GCP Service",
    COALESCE(p.gcp_machine_type, '')                       AS "GCP Mapping",
    COALESCE(p.gcp_region, '')                             AS "Region",
    ROUND(CASE WHEN COALESCE(cr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END, 2)
                                                            AS "AWS Cost ($)",
    ROUND(p.gcp_projected_cost, 2)                         AS "GCP OD ($)",
    ROUND(CASE WHEN COALESCE(cr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END
          - COALESCE(p.gcp_projected_cost, 0), 2)          AS "Diff ($)",
    ROUND(COALESCE(p.gcp_cost_1yr_cud, p.gcp_projected_cost), 2)     AS "GCP 1yr CUD ($)",
    ROUND(COALESCE(p.gcp_cost_3yr_cud, p.gcp_projected_cost), 2)     AS "GCP 3yr CUD ($)",
    COALESCE(m.strategy, '')                               AS "Strategy",
    ROUND(COALESCE(m.mapping_confidence, 0), 2)            AS "Confidence"
FROM aws_li_catalog c
LEFT JOIN aws_li_to_gcp_li m USING (aws_li_key)
LEFT JOIN gcp_projection p ON p.aws_li_key = m.aws_li_key AND p.component = m.component
LEFT JOIN component_rank cr ON cr.row_id = m.rowid
ORDER BY c.aws_amortized_cost DESC
"""

# Per-instance-type rollup: groups the same de-duplicated per-line-item costs
# above by AWS instance type, so every number here is additive straight from
# the pipeline's own ground-truth gcp_projection view — no proportional-split
# estimation from aggregated bill totals, which is what introduces the
# "AWS-discount vs GCP-list-price" row-level mismatches seen when per-instance
# detail is reconstructed from the rendered HTML report + raw CUR instead.
_QUERY_BY_INSTANCE = """
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
        CASE WHEN COALESCE(cr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END
                                                                AS aws_cost,
        COALESCE(p.gcp_projected_cost, 0)                      AS gcp_od_cost,
        COALESCE(p.gcp_cost_1yr_cud, p.gcp_projected_cost, 0)  AS gcp_1yr_cost,
        COALESCE(p.gcp_cost_3yr_cud, p.gcp_projected_cost, 0)  AS gcp_3yr_cost
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
    ROUND(SUM(aws_cost), 2)                             AS "AWS Cost ($)",
    ROUND(SUM(gcp_od_cost), 2)                          AS "GCP OD ($)",
    ROUND(SUM(aws_cost) - SUM(gcp_od_cost), 2)          AS "Diff ($)",
    ROUND(SUM(gcp_1yr_cost), 2)                         AS "GCP 1yr CUD ($)",
    ROUND(SUM(gcp_3yr_cost), 2)                         AS "GCP 3yr CUD ($)"
FROM per_line_item
GROUP BY product, instance_type, gcp_mapping, region
ORDER BY SUM(aws_cost) DESC
"""


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
        else:
            i += 1

    if not out_path:
        print(
            "Usage: export_report.py --format [xlsx|csv] --out <path> [--by-instance]\n"
            "  --by-instance   CSV: emit the per-instance-type rollup instead of raw line items.\n"
            "                  XLSX: always included as an extra 'Per-Instance Summary' sheet.",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = duckdb.connect(DB_PATH, read_only=True)
    result = conn.execute(_QUERY)
    headers = [d[0] for d in result.description]
    rows = result.fetchall()

    inst_result = conn.execute(_QUERY_BY_INSTANCE)
    inst_headers = [d[0] for d in inst_result.description]
    inst_rows = inst_result.fetchall()
    conn.close()

    if fmt == "csv":
        if by_instance:
            headers, rows = inst_headers, inst_rows
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
        wb.save(out_path)

    else:
        print(f"Unknown format: {fmt}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
