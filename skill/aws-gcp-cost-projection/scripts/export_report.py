#!/usr/bin/env python3
"""Export projection data as XLSX or CSV."""
import csv
import os
import sys

import duckdb
from currency_utils import load_currency_info
from license_split import get_aws_license_split


def _augment_aws_license_split(headers, rows, cur_sym):
    """Insert AWS 'instance + license' split columns (L-5) so CSV/XLSX match the HTML
    report. Uses the shared license_fraction logic; splits the row's AWS cost into
    aws_instance_cost + aws_license_cost. Rows with no authoritative fraction keep the
    full AWS cost as the instance cost and a blank license (never fabricated)."""
    headers = list(headers)
    try:
        aws_cost_idx = headers.index(f"AWS Cost{cur_sym}")
        inst_idx = headers.index("AWS Instance")
        licname_idx = headers.index("license_name")
    except ValueError:
        return headers, [list(r) for r in rows]  # expected columns absent — no-op

    ins_at = aws_cost_idx + 1
    headers[ins_at:ins_at] = [f"AWS Instance Cost{cur_sym}", f"AWS License Cost{cur_sym}"]

    out_rows = []
    for r in rows:
        r = list(r)
        itype = r[inst_idx]
        lic_name = r[licname_idx]
        aws_cost = r[aws_cost_idx]
        split = get_aws_license_split(itype, lic_name, aws_cost)
        if split:
            aws_base, aws_lic, _ = split
        else:
            aws_base, aws_lic = aws_cost, ""   # no fraction → full cost, blank license
        r[ins_at:ins_at] = [aws_base, aws_lic]
        out_rows.append(r)
    return headers, out_rows

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")


def _build_query(fx_rate=1.0, curr_code="USD", aws_fx_rate=1.0, conn=None):
    cur_sym = f" ({curr_code})" if curr_code != "USD" else " ($)"
    sku_name_col = "m.gcp_sku_id"
    comp_col = "m.component"
    machine_type_col = "NULL"
    if conn is not None:
        try:
            m_cols = {r[1].lower() for r in conn.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
            if "gcp_sku_name" in m_cols:
                sku_name_col = "COALESCE(NULLIF(m.gcp_sku_name, ''), m.gcp_sku_id)"
            if "component" not in m_cols:
                comp_col = "'default'"
            if "gcp_machine_type" in m_cols:
                machine_type_col = "m.gcp_machine_type"
        except Exception:
            pass

    return f"""
WITH per_component AS (
    SELECT
        c.aws_li_key,
        c.aws_amortized_cost,
        c.product,
        c.instance_type,
        c.is_workload,
        p.gcp_service,
        {machine_type_col} AS gcp_machine_type,
        p.gcp_region,
        m.strategy,
        m.mapping_confidence,
        {comp_col} AS comp,
        {sku_name_col} AS sku_name,
        CASE
            WHEN m.strategy = 'ignore' THEN 0.0
            WHEN m.strategy IN ('map', 'break_down') AND p.gcp_projected_cost IS NOT NULL
                THEN p.gcp_projected_cost * {fx_rate}
            ELSE NULL
        END AS comp_od_cost,
        CASE
            WHEN m.strategy = 'ignore' THEN 0.0
            WHEN m.strategy IN ('map', 'break_down') AND p.gcp_projected_cost IS NOT NULL
                THEN COALESCE(p.gcp_cost_1yr_cud, p.gcp_projected_cost) * {fx_rate}
            ELSE NULL
        END AS comp_1yr_cost,
        CASE
            WHEN m.strategy = 'ignore' THEN 0.0
            WHEN m.strategy IN ('map', 'break_down') AND p.gcp_projected_cost IS NOT NULL
                THEN COALESCE(p.gcp_cost_3yr_cud, p.gcp_projected_cost) * {fx_rate}
            ELSE NULL
        END AS comp_3yr_cost
    FROM aws_li_catalog c
    LEFT JOIN aws_li_to_gcp_li m USING (aws_li_key)
    LEFT JOIN gcp_projection p ON p.aws_li_key = m.aws_li_key AND p.component = m.component
),
per_item AS (
    SELECT
        aws_li_key,
        ANY_VALUE(product) AS product,
        ANY_VALUE(instance_type) AS instance_type,
        ANY_VALUE(gcp_service) AS gcp_service,
        ANY_VALUE(gcp_machine_type) AS gcp_machine_type,
        ANY_VALUE(gcp_region) AS gcp_region,
        ANY_VALUE(strategy) AS strategy,
        ANY_VALUE(mapping_confidence) AS mapping_confidence,
        ANY_VALUE(aws_amortized_cost) AS raw_aws_cost,
        ANY_VALUE(aws_amortized_cost) * {aws_fx_rate} AS aws_cost,
        CASE
            WHEN ANY_VALUE(strategy) = 'ignore' THEN 0.0
            WHEN ANY_VALUE(strategy) IN ('map', 'break_down') AND COUNT(comp_od_cost) > 0
                THEN SUM(COALESCE(comp_od_cost, 0.0))
            ELSE ANY_VALUE(aws_amortized_cost) * {aws_fx_rate}
        END AS gcp_od_cost,
        CASE
            WHEN ANY_VALUE(strategy) = 'ignore' THEN 0.0
            WHEN ANY_VALUE(strategy) IN ('map', 'break_down') AND COUNT(comp_1yr_cost) > 0
                THEN SUM(COALESCE(comp_1yr_cost, 0.0))
            ELSE ANY_VALUE(aws_amortized_cost) * {aws_fx_rate}
        END AS gcp_1yr_cost,
        CASE
            WHEN ANY_VALUE(strategy) = 'ignore' THEN 0.0
            WHEN ANY_VALUE(strategy) IN ('map', 'break_down') AND COUNT(comp_3yr_cost) > 0
                THEN SUM(COALESCE(comp_3yr_cost, 0.0))
            ELSE ANY_VALUE(aws_amortized_cost) * {aws_fx_rate}
        END AS gcp_3yr_cost,
        CASE
            WHEN ANY_VALUE(strategy) IN ('map', 'break_down') AND BOOL_OR(is_workload) AND COUNT(comp_od_cost) > 0 AND ANY_VALUE(aws_amortized_cost) > 0
                THEN 'A'
            ELSE 'B'
        END AS section,
        SUM(CASE WHEN comp NOT IN ('license', 'sql_license') THEN COALESCE(comp_od_cost, 0.0) ELSE 0.0 END) AS base_cost,
        STRING_AGG(DISTINCT CASE WHEN comp IN ('license', 'sql_license') THEN COALESCE(NULLIF(sku_name, ''), comp) ELSE NULL END, ', ') AS lic_name,
        NULLIF(SUM(CASE WHEN comp IN ('license', 'sql_license') THEN comp_od_cost ELSE NULL END), NULL) AS lic_cost
    FROM per_component
    GROUP BY aws_li_key
)
SELECT
    ROW_NUMBER() OVER (ORDER BY raw_aws_cost DESC)       AS "#",
    product                                              AS "AWS Service",
    COALESCE(instance_type, '')                          AS "AWS Instance",
    COALESCE(gcp_service, '')                            AS "GCP Service",
    COALESCE(gcp_machine_type, '')                       AS "GCP Mapping",
    COALESCE(gcp_region, '')                             AS "Region",
    ROUND(aws_cost, 2)                                   AS "AWS Cost{cur_sym}",
    ROUND(gcp_od_cost, 2)                                AS "GCP OD{cur_sym}",
    ROUND(base_cost, 2)                                  AS "instance_cost",
    COALESCE(lic_name, '')                               AS "license_name",
    ROUND(lic_cost, 2)                                   AS "license_cost",
    ROUND(aws_cost - gcp_od_cost, 2)                     AS "Diff{cur_sym}",
    ROUND(gcp_1yr_cost, 2)                               AS "GCP 1yr CUD{cur_sym}",
    ROUND(gcp_3yr_cost, 2)                               AS "GCP 3yr CUD{cur_sym}",
    section                                              AS "Section",
    COALESCE(strategy, '')                               AS "Strategy"
FROM per_item
ORDER BY raw_aws_cost DESC
"""


def _build_query_by_instance(fx_rate=1.0, curr_code="USD", aws_fx_rate=1.0, conn=None):
    cur_sym = f" ({curr_code})" if curr_code != "USD" else " ($)"
    sku_name_col = "m.gcp_sku_id"
    comp_col = "m.component"
    machine_type_col = "NULL"
    if conn is not None:
        try:
            m_cols = {r[1].lower() for r in conn.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
            if "gcp_sku_name" in m_cols:
                sku_name_col = "COALESCE(NULLIF(m.gcp_sku_name, ''), m.gcp_sku_id)"
            if "component" not in m_cols:
                comp_col = "'default'"
            if "gcp_machine_type" in m_cols:
                machine_type_col = "m.gcp_machine_type"
        except Exception:
            pass

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
        COALESCE({machine_type_col}, '')                       AS gcp_mapping,
        COALESCE(p.gcp_region, c.gcp_region, '')                AS region,
        (CASE WHEN COALESCE(cr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END) * {aws_fx_rate}
                                                                AS aws_cost,
        COALESCE(p.gcp_projected_cost, 0) * {fx_rate}          AS gcp_od_cost,
        (CASE WHEN {comp_col} NOT IN ('license', 'sql_license') THEN COALESCE(p.gcp_projected_cost, 0) ELSE 0 END) * {fx_rate} AS base_cost,
        (CASE WHEN {comp_col} IN ('license', 'sql_license') THEN COALESCE(NULLIF({sku_name_col}, ''), {comp_col}) ELSE NULL END) AS lic_name,
        (CASE WHEN {comp_col} IN ('license', 'sql_license') THEN COALESCE(p.gcp_projected_cost, 0) ELSE NULL END) * {fx_rate} AS lic_cost,
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
    ROUND(SUM(base_cost), 2)                            AS "instance_cost",
    COALESCE(STRING_AGG(DISTINCT lic_name, ', '), '')    AS "license_name",
    ROUND(SUM(lic_cost), 2)                             AS "license_cost",
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
    db_path = None
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
        elif args[i] == "--db" and i + 1 < len(args):
            db_path = args[i + 1]
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
            "Usage: export_report.py --format [xlsx|csv] --out <path> [--db <duckdb_path>] [--by-instance] [--by-discounts]\n"
            "  --by-instance    CSV: emit the per-instance-type rollup instead of raw line items.\n"
            "  --by-discounts   CSV: emit the discounts-by-service rollup instead of raw line items.\n"
            "                   XLSX: always included as an extra 'Discounts by Service' sheet.",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved_db_path = db_path
    if not resolved_db_path or not os.path.exists(resolved_db_path):
        candidates = [
            os.path.join(JOB_DIR, "projection-audit", "projection.duckdb"),
            os.path.join(JOB_DIR, "projection.duckdb"),
            DB_PATH,
        ]
        for c in candidates:
            if os.path.exists(c):
                resolved_db_path = c
                break
        if not resolved_db_path:
            resolved_db_path = DB_PATH

    conn = duckdb.connect(resolved_db_path, read_only=True)
    job_dir = JOB_DIR if os.path.exists(os.path.join(JOB_DIR, "projection-audit")) else os.path.dirname(os.path.dirname(os.path.abspath(resolved_db_path)))
    currency_info = load_currency_info(job_dir, conn)
    curr_code = currency_info.get("currency", "USD")
    fx_rate = float(currency_info.get("fx_rate", 1.0))
    aws_fx_rate = float(currency_info.get("aws_fx_rate", 1.0))

    query = _build_query(fx_rate, curr_code, aws_fx_rate, conn=conn)
    inst_query = _build_query_by_instance(fx_rate, curr_code, aws_fx_rate, conn=conn)
    disc_query = _build_query_discounts(curr_code, aws_fx_rate)

    cur_sym = f" ({curr_code})" if curr_code != "USD" else " ($)"

    result = conn.execute(query)
    headers = [d[0] for d in result.description]
    rows = result.fetchall()
    headers, rows = _augment_aws_license_split(headers, rows, cur_sym)

    inst_result = conn.execute(inst_query)
    inst_headers = [d[0] for d in inst_result.description]
    inst_rows = inst_result.fetchall()
    inst_headers, inst_rows = _augment_aws_license_split(inst_headers, inst_rows, cur_sym)

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
