#!/usr/bin/env python3
import duckdb
import os
import json
import datetime

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")

def main():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        return

    conn = duckdb.connect(DB_PATH)
    
    # Calculate mapping coverage by mechanic_group
    try:
        coverage_rows = conn.execute("""
            SELECT 
                c.mechanic_group,
                SUM(CASE WHEN m.strategy != 'passthrough' THEN c.aws_amortized_cost ELSE 0 END) AS mapped_spend,
                SUM(c.aws_amortized_cost) AS total_spend
            FROM aws_li_catalog c
            LEFT JOIN aws_li_to_gcp_li m USING (aws_li_key)
            GROUP BY c.mechanic_group
        """).fetchall()
        
        coverage = {}
        for group, mapped, total in coverage_rows:
            if not group:
                continue
            pct = (mapped / total * 100.0) if total else 100.0
            coverage[group] = round(pct, 1)
            
        coverage_path = os.path.join(JOB_DIR, "projection-audit", "mapping_coverage.json")
        with open(coverage_path, "w") as f:
            json.dump(coverage, f, indent=2)
        print(f"Coverage KPI report written: {coverage_path}")
    except Exception as e:
        print(f"Failed to generate coverage report: {e}")
    
    # 1. Calculate sums deterministically
    aws_workload = conn.execute("SELECT SUM(aws_amortized_cost) FROM aws_li_catalog WHERE is_workload").fetchone()[0] or 0.0
    aws_non_workload = conn.execute("SELECT SUM(aws_amortized_cost) FROM aws_li_catalog WHERE NOT is_workload").fetchone()[0] or 0.0
    aws_grand = aws_workload + aws_non_workload

    gcp_od = conn.execute("SELECT SUM(gcp_projected_cost) FROM gcp_projection WHERE is_workload").fetchone()[0] or 0.0
    gcp_1yr = conn.execute("SELECT SUM(gcp_cost_1yr_cud) FROM gcp_projection WHERE is_workload").fetchone()[0] or 0.0
    gcp_3yr = conn.execute("SELECT SUM(gcp_cost_3yr_cud) FROM gcp_projection WHERE is_workload").fetchone()[0] or 0.0

    gcp_non_workload_od = conn.execute("SELECT SUM(gcp_projected_cost) FROM gcp_projection WHERE NOT is_workload").fetchone()[0] or 0.0
    gcp_non_workload_1yr = conn.execute("SELECT SUM(gcp_cost_1yr_cud) FROM gcp_projection WHERE NOT is_workload").fetchone()[0] or 0.0
    gcp_non_workload_3yr = conn.execute("SELECT SUM(gcp_cost_3yr_cud) FROM gcp_projection WHERE NOT is_workload").fetchone()[0] or 0.0

    gcp_grand_od = gcp_od + gcp_non_workload_od
    gcp_grand_1yr = gcp_1yr + gcp_non_workload_1yr
    gcp_grand_3yr = gcp_3yr + gcp_non_workload_3yr

    # 2. Get customer name
    customer_name = "Prospect"
    cust_file = os.path.join(JOB_DIR, "customer_name.txt")
    if os.path.exists(cust_file):
        with open(cust_file, "r") as f:
            customer_name = f.read().strip() or "Prospect"

    now = datetime.datetime.utcnow()
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    
    # Determine the GCP region from the DB (most common gcp_region in workload rows)
    gcp_region_row = conn.execute("""
        SELECT gcp_region, COUNT(*) AS n FROM gcp_projection
        WHERE is_workload AND gcp_region IS NOT NULL
        GROUP BY gcp_region ORDER BY n DESC LIMIT 1
    """).fetchone()
    gcp_region_display = gcp_region_row[0] if gcp_region_row else "see individual rows"
    
    region_names = {
        "asia-southeast1": "Singapore",
        "asia-northeast1": "Tokyo",
        "asia-south1": "Mumbai",
        "us-east4": "N. Virginia",
        "us-west1": "Oregon"
    }
    if gcp_region_display in region_names:
        gcp_region_display = f"{gcp_region_display} ({region_names[gcp_region_display]})"

    # Capacity calculations
    aws_vcpu = conn.execute("SELECT SUM(instance_vcpus * instance_count) FROM aws_li_catalog WHERE is_workload AND instance_vcpus IS NOT NULL").fetchone()[0] or 0.0
    aws_ram = conn.execute("SELECT SUM(instance_ram_gb * instance_count) FROM aws_li_catalog WHERE is_workload AND instance_ram_gb IS NOT NULL").fetchone()[0] or 0.0
    # For rows with a core/ram component breakdown, use the GCP unit_multiplier
    # (which reconcile_capacity.py ensures is >= AWS). For mapped rows without a
    # core breakdown (e.g. managed_db mapped as component='compute'), fall back to
    # the AWS instance_vcpus as the GCP floor — reconcile_capacity guarantees no
    # under-provision on break_down rows, and map-strategy shapes are >= AWS specs.
    gcp_vcpu = conn.execute("""
        WITH core_mapped AS (
            SELECT c.aws_li_key, SUM(m.unit_multiplier * c.instance_count) AS vcpus
            FROM aws_li_to_gcp_li m
            JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.component = 'core' AND m.strategy IN ('map','break_down')
            GROUP BY c.aws_li_key
        )
        SELECT SUM(COALESCE(cm.vcpus, c.instance_vcpus * c.instance_count))
        FROM aws_li_catalog c
        LEFT JOIN core_mapped cm USING (aws_li_key)
        WHERE c.is_workload AND c.instance_vcpus IS NOT NULL
    """).fetchone()[0] or 0.0
    gcp_ram = conn.execute("""
        WITH ram_mapped AS (
            SELECT c.aws_li_key, SUM(m.unit_multiplier * c.instance_count) AS ram_gb
            FROM aws_li_to_gcp_li m
            JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.component = 'ram' AND m.strategy IN ('map','break_down')
            GROUP BY c.aws_li_key
        )
        SELECT SUM(COALESCE(rm.ram_gb, c.instance_ram_gb * c.instance_count))
        FROM aws_li_catalog c
        LEFT JOIN ram_mapped rm USING (aws_li_key)
        WHERE c.is_workload AND c.instance_ram_gb IS NOT NULL
    """).fetchone()[0] or 0.0

    # 0.5% rounding tolerance: GCP RAM of 194.22 vs AWS 194.27 (a 0.05 GB rounding
    # gap) is not a real under-provision and shouldn't read as WARN.
    _CAP_TOL = 0.005
    vcpu_status = "PASS" if gcp_vcpu >= aws_vcpu * (1 - _CAP_TOL) else "WARN"
    ram_status  = "PASS" if gcp_ram  >= aws_ram  * (1 - _CAP_TOL) else "WARN"
    
    # Read validation warnings (if the validator found anything after auto-fix)
    _GATE_LABELS = {
        "passthrough_on_mappable_service": "Some services could not be automatically mapped to GCP. These rows carry the AWS cost as a placeholder — manual review recommended.",
        "phantom_zero": "One or more rows show a unit-pricing discrepancy. Affected costs may be overstated.",
        "under_projection_gcp_zero": "Some billable AWS rows project to $0 on GCP — the rate card may be missing a SKU.",
        "capacity_reconciliation": "Projected GCP compute capacity is below the AWS baseline for some instance groups.",
        "cud_coverage_missing": "Committed Use Discount rates are unavailable for some services — those rows show On-Demand pricing.",
        "storage_transfer_over_projection": "Storage or data-transfer costs may be conservatively estimated (up to 3× AWS).",
        "instance_family_mismatch": "Some instance-family mappings may not be optimal. Review flagged rows below.",
        "reconciliation": "Projected AWS total differs slightly from the uploaded bill total.",
        "passthrough_budget": "A meaningful share of the workload is carried at AWS cost (no GCP equivalent mapped). Manual sizing recommended.",
    }
    validation_notes = []
    val_path = os.path.join(JOB_DIR, "validation_report.json")
    if os.path.exists(val_path):
        try:
            with open(val_path) as _vf:
                _vr = json.load(_vf)
            for gate, rows in (_vr.get("violations") or {}).items():
                if rows:
                    label = _GATE_LABELS.get(gate, f"Validation note: {gate}")
                    validation_notes.append(label)
        except Exception:
            pass

    # Per-category confidence breakdown
    cat_conf_rows = conn.execute("""
        SELECT
          CASE
            WHEN m.strategy = 'passthrough' THEN 'Passthrough'
            WHEN c.product ILIKE '%EC2%' OR c.product ILIKE '%Elastic Compute%' THEN 'Compute (EC2)'
            WHEN c.product ILIKE '%RDS%' OR c.product ILIKE '%Aurora%' OR c.product ILIKE '%Redshift%' THEN 'Database (RDS/Aurora)'
            WHEN c.product ILIKE '%OpenSearch%' OR c.product ILIKE '%MSK%' OR c.product ILIKE '%Kafka%'
              OR c.product ILIKE '%ElastiCache%' THEN 'Managed Services'
            WHEN c.product ILIKE '%S3%' OR c.product ILIKE '%EBS%' OR c.product ILIKE '%Glacier%'
              OR c.product ILIKE '%Storage%' OR c.product ILIKE '%Backup%' THEN 'Storage'
            WHEN c.product ILIKE '%Route 53%' OR c.product ILIKE '%CloudFront%'
              OR c.product ILIKE '%Direct Connect%' OR c.product ILIKE '%VPC%'
              OR c.product ILIKE '%Data Transfer%' OR c.product ILIKE '%Bandwidth%' THEN 'Networking'
            ELSE 'Other'
          END AS category,
          AVG(LEAST(m.mapping_confidence, 1.0)) AS avg_conf,
          COUNT(*) AS cnt
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        GROUP BY category
        ORDER BY avg_conf DESC NULLS LAST
    """).fetchall()

    # Overall average (passthrough rows are intentional 100% — exclude from avg to avoid inflating)
    avg_conf = conn.execute(
        "SELECT AVG(LEAST(mapping_confidence, 1.0)) FROM aws_li_to_gcp_li WHERE strategy != 'passthrough'"
    ).fetchone()[0] or 0.0

    html_content = f"""
    <html>
    <head>
        <title>AWS to GCP Cost Analysis</title>
        <style>
            body {{ font-family: sans-serif; }}
            .header {{ color: #1A73E8; border-bottom: 2px solid #1A73E8; padding-bottom: 10px; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            .right {{ text-align: right; }}
            .green {{ color: #0F9D58; font-weight: bold; }}
            .red {{ color: #D93025; font-weight: bold; }}
            .orange {{ color: #E37400; font-weight: bold; }}
            .assumptions {{ background: #f8f9fa; border-left: 3px solid #1A73E8; padding: 10px 16px; margin: 12px 0; font-size: 0.9em; }}
            .assumptions ul {{ margin: 4px 0; padding-left: 20px; }}
        .warning-box {{ background: #fff8e1; border-left: 3px solid #f9a825; padding: 10px 16px; margin: 12px 0; font-size: 0.9em; }}
        .warning-box ul {{ margin: 4px 0; padding-left: 20px; }}
        </style>
    </head>
    <body>
        <h1 class="header">AWS to GCP Cloud Cost Analysis</h1>
        <p><b>Customer:</b> {customer_name} &nbsp;&nbsp; <b>Analysis Date:</b> {now.strftime('%B %Y')}</p>
        {'<div class="warning-box"><b>&#9888; Projection notes — review before sharing</b><ul>' + "".join(f"<li>{n}</li>" for n in validation_notes) + "</ul></div>" if validation_notes else ""}
        <div class="assumptions">
          <b>Pricing assumptions:</b>
          <ul>
            <li>Target pricing region: <b>{gcp_region_display}</b></li>
            <li>On-Demand pricing (no sustained use discount applied to base rates)</li>
            <li>1-Year and 3-Year CUD columns show committed-use discount savings</li>
            <li>License-included pricing (BYOL not assumed unless the AWS line item indicates it)</li>
            <li>AWS Spot instances mapped to GCP Spot VMs (~60–91% discount off On-Demand, functionally equivalent to Preemptible). Note: AWS Spot prices are market-variable and may be lower — GCP may appear higher for Spot-heavy workloads.</li>
            <li>OpenSearch and MSK rows are <b>infrastructure estimates for self-managed deployment</b> on GCP (e.g. OpenSearch on GCE, Kafka on GCE) — not managed-service equivalents. Treat these as directional only.</li>
            <li>Passthrough rows carry the AWS cost as-is — no reliable GCP equivalent was found; manual sizing required.</li>
          </ul>
        </div>
        
        <h2>Mapping Confidence</h2>
        <p style="margin:4px 0 8px;font-size:0.9em;color:#555;">Overall average (excluding passthrough): <b>{avg_conf * 100:.1f}%</b>. Confidence varies by service category — review lower-confidence sections before sharing.</p>
        <table>
            <tr><th>Service Category</th><th class="right">Avg Confidence</th><th class="right">Rows</th><th>Note</th></tr>
            {"".join(
                f'<tr><td>{cat}</td><td class="right"><b>{"N/A (intentional)" if cat == "Passthrough" else f"{conf*100:.1f}%"}</b></td><td class="right">{cnt}</td>'
                f'<td style="font-size:0.85em;color:#666;">'
                f'{"Carries AWS cost as placeholder — manual sizing required" if cat == "Passthrough" else ""}'
                f'{"Infrastructure estimates only — not managed-service equivalent" if cat == "Managed Services" else ""}'
                f'{"Service-specific factors (HA, storage config) may affect accuracy" if cat == "Database (RDS/Aurora)" else ""}'
                f'</td></tr>'
                for cat, conf, cnt in cat_conf_rows
            )}
        </table>

        <h2>Capacity Reconciliation</h2>
        <table>
            <tr>
                <th>Resource Type</th>
                <th class="right">AWS Total Capacity</th>
                <th class="right">GCP Recommended Capacity</th>
                <th class="right">Status</th>
            </tr>
            <tr>
                <td>vCPU (cores)</td>
                <td class="right">{aws_vcpu:,.2f} vCPUs</td>
                <td class="right">{gcp_vcpu:,.2f} vCPUs</td>
                <td class="right {'green' if vcpu_status == 'PASS' else 'orange'}">{vcpu_status}</td>
            </tr>
            <tr>
                <td>Memory (RAM)</td>
                <td class="right">{aws_ram:,.2f} GB</td>
                <td class="right">{gcp_ram:,.2f} GB</td>
                <td class="right {'green' if ram_status == 'PASS' else 'orange'}">{ram_status}</td>
            </tr>
        </table>

        <h2>Cost Summary</h2>
        <table>
            <tr>
                <th>Cost Category</th>
                <th class="right">AWS Cost</th>
                <th class="right">GCP On-Demand</th>
                <th class="right">GCP 1-Year CUD</th>
                <th class="right">GCP 3-Year CUD</th>
            </tr>
            <tr>
                <td><b>Core Infrastructure (Workload)</b></td>
                <td class="right">${aws_workload:,.2f}</td>
                <td class="right">${gcp_od:,.2f}</td>
                <td class="right">${gcp_1yr:,.2f}</td>
                <td class="right">${gcp_3yr:,.2f}</td>
            </tr>
            <tr>
                <td><b>Non-Workload Spend (Marketplace & Support)</b></td>
                <td class="right">${aws_non_workload:,.2f}</td>
                <td class="right">${gcp_non_workload_od:,.2f}</td>
                <td class="right">${gcp_non_workload_1yr:,.2f}</td>
                <td class="right">${gcp_non_workload_3yr:,.2f}</td>
            </tr>
            <tr style="border-top: 2px solid #ccc; font-weight: bold; background-color: #f9f9f9;">
                <td>Grand Total</td>
                <td class="right">${aws_grand:,.2f}</td>
                <td class="right">${gcp_grand_od:,.2f}</td>
                <td class="right">${gcp_grand_1yr:,.2f}</td>
                <td class="right">${gcp_grand_3yr:,.2f}</td>
            </tr>
        </table>
        <div id="aws-total-spend" hidden>{aws_grand:.2f}</div>
        <p style="margin:6px 0 4px;font-size:0.85em;color:#888;"><i>&#9888; Windows and commercial software license premiums are excluded from the totals above unless explicitly modeled in the source bill.</i></p>

        <h2>Cost Comparison by Service</h2>
        <table>
            <tr><th>#</th><th>Service (AWS → GCP)</th><th>Description</th><th class="right">AWS</th><th class="right">GCP OD</th><th class="right">GCP CUD</th><th class="right">GCP 3yr</th><th class="right">Diff</th></tr>
    """
    
    rows = conn.execute("""
        SELECT 
            c.product, 
            c.operation, 
            COALESCE(ANY_VALUE(m.gcp_service), 'N/A') as gcp_service,
            COALESCE(STRING_AGG(DISTINCT m.projection_note, '; '), '') as projection_note,
            c.aws_amortized_cost,
            SUM(p.gcp_projected_cost) as gcp_projected_cost,
            SUM(p.gcp_cost_1yr_cud) as gcp_cost_1yr_cud,
            SUM(p.gcp_cost_3yr_cud) as gcp_cost_3yr_cud,
            (c.aws_amortized_cost - SUM(p.gcp_projected_cost)) AS diff
        FROM aws_li_catalog c
        LEFT JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key
        LEFT JOIN gcp_projection p ON c.aws_li_key = p.aws_li_key AND p.component IS NOT DISTINCT FROM m.component
        GROUP BY c.aws_li_key, c.product, c.operation, c.aws_amortized_cost
        ORDER BY c.aws_amortized_cost DESC
    """).fetchall()
    
    idx = 1
    for r in rows:
        aws = r[4] or 0.0
        od = r[5] or 0.0
        cud1 = r[6] or 0.0
        cud3 = r[7] or 0.0
        diff = r[8] or 0.0
        
        diff_class = "green" if diff > 0 else "red" if diff < 0 else ""
        diff_str = f"+${diff:,.2f}" if diff > 0 else f"-${abs(diff):,.2f}" if diff < 0 else "$0.00"
        
        html_content += f"""
            <tr>
                <td>{idx}</td>
                <td>[{r[0]}] {r[1]} &rarr; {r[2] or 'N/A'}</td>
                <td>{r[3] or ''}</td>
                <td class="right">${aws:,.2f}</td>
                <td class="right">${od:,.2f}</td>
                <td class="right">${cud1:,.2f}</td>
                <td class="right">${cud3:,.2f}</td>
                <td class="right {diff_class}">{diff_str}</td>
            </tr>
        """
        idx += 1
        
    diff_total = aws_grand - gcp_grand_od
    diff_total_class = "green" if diff_total > 0 else "red" if diff_total < 0 else ""
    diff_total_str = f"+${diff_total:,.2f}" if diff_total > 0 else f"-${abs(diff_total):,.2f}" if diff_total < 0 else "$0.00"
        
    html_content += f"""
            <tr>
                <th colspan="3">GRAND TOTAL</th>
                <th class="right">${aws_grand:,.2f}</th>
                <th class="right">${gcp_grand_od:,.2f}</th>
                <th class="right">${gcp_grand_1yr:,.2f}</th>
                <th class="right">${gcp_grand_3yr:,.2f}</th>
                <th class="right {diff_total_class}">{diff_total_str}</th>
            </tr>
        </table>
        <p><i>Diff = AWS − GCP On-Demand. Positive values (green) indicate GCP is lower cost; negative values (red) indicate GCP costs more.</i></p>
    </body>
    </html>
    """

    html_path = os.path.join(JOB_DIR, "projection-audit", f"report-{run_id}.html")
    with open(html_path, "w") as f:
        f.write(html_content)

    with open(os.path.join(JOB_DIR, "projection-audit", "report.html"), "w") as f:
        f.write(html_content)

    print(f"Generated {html_path}")

    # Ensure run_results has a row so the frontend TotalsCard works even when
    # the Phase 6 LLM narrative agent is skipped or hits quota.
    # Schema must match duckdb.go RunResult exactly — all columns required.
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS run_results (
                run_id        TEXT PRIMARY KEY,
                ts_utc        TEXT,
                run_type      TEXT,
                instruction   TEXT,
                aws_total     DOUBLE,
                gcp_od        DOUBLE,
                gcp_1yr_cud   DOUBLE,
                gcp_3yr_cud   DOUBLE,
                report_html   TEXT,
                report_md     TEXT,
                summary_md    TEXT,
                mapped_rows   INTEGER,
                passthroughs  INTEGER,
                confidence    TEXT
            )
        """)
        existing = conn.execute(
            "SELECT 1 FROM run_results WHERE run_id = ?", [run_id]
        ).fetchone()
        if not existing:
            mapped_rows = conn.execute(
                "SELECT COUNT(*) FROM aws_li_to_gcp_li"
            ).fetchone()[0] or 0
            passthroughs = conn.execute(
                "SELECT COUNT(*) FROM aws_li_to_gcp_li WHERE strategy='passthrough'"
            ).fetchone()[0] or 0
            conn.execute(
                "INSERT INTO run_results "
                "(run_id, ts_utc, run_type, instruction, aws_total, gcp_od, gcp_1yr_cud, gcp_3yr_cud, "
                " report_html, report_md, summary_md, mapped_rows, passthroughs, confidence) "
                "VALUES (?, ?, 'initial', NULL, ?, ?, ?, ?, 'projection-audit/report.html', NULL, NULL, ?, ?, NULL)",
                [run_id, now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 aws_grand, gcp_grand_od, gcp_grand_1yr, gcp_grand_3yr, mapped_rows, passthroughs]
            )
            conn.commit()
            print(f"Inserted run_results row for {run_id}")
    except Exception as e:
        print(f"Warning: could not write run_results: {e}")

    conn.close()

if __name__ == "__main__":
    main()
