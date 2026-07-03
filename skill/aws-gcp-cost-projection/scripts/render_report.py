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
    
    # 1. Calculate sums deterministically
    aws_total = conn.execute("SELECT SUM(aws_amortized_cost) FROM aws_li_catalog").fetchone()[0] or 0.0
    gcp_od = conn.execute("SELECT SUM(gcp_projected_cost) FROM gcp_projection WHERE is_workload").fetchone()[0] or 0.0
    gcp_1yr = conn.execute("SELECT SUM(gcp_cost_1yr_cud) FROM gcp_projection WHERE is_workload").fetchone()[0] or 0.0
    gcp_3yr = conn.execute("SELECT SUM(gcp_cost_3yr_cud) FROM gcp_projection WHERE is_workload").fetchone()[0] or 0.0

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
    gcp_vcpu = conn.execute("SELECT SUM(m.unit_multiplier * c.instance_count) FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key) WHERE m.component = 'core' AND m.strategy IN ('map', 'break_down')").fetchone()[0] or 0.0
    gcp_ram = conn.execute("SELECT SUM(m.unit_multiplier * c.instance_count) FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key) WHERE m.component = 'ram' AND m.strategy IN ('map', 'break_down')").fetchone()[0] or 0.0

    vcpu_status = "PASS" if gcp_vcpu >= aws_vcpu else "WARN"
    ram_status = "PASS" if gcp_ram >= aws_ram else "WARN"
    
    # Confidence metrics
    avg_conf = conn.execute("SELECT AVG(mapping_confidence) FROM aws_li_to_gcp_li").fetchone()[0] or 0.0
    exact_cnt = conn.execute("SELECT COUNT(*) FROM aws_li_to_gcp_li WHERE mapping_confidence >= 0.9").fetchone()[0] or 0
    near_cnt = conn.execute("SELECT COUNT(*) FROM aws_li_to_gcp_li WHERE mapping_confidence >= 0.7 AND mapping_confidence < 0.9").fetchone()[0] or 0
    low_cnt = conn.execute("SELECT COUNT(*) FROM aws_li_to_gcp_li WHERE mapping_confidence < 0.7 OR strategy = 'passthrough'").fetchone()[0] or 0

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
        </style>
    </head>
    <body>
        <h1 class="header">AWS to GCP Cloud Cost Analysis</h1>
        <p><b>Customer:</b> {customer_name} &nbsp;&nbsp; <b>Analysis Date:</b> {now.strftime('%B %Y')}</p>
        
        <div class="assumptions">
          <b>Pricing assumptions:</b>
          <ul>
            <li>Target pricing region: <b>{gcp_region_display}</b></li>
            <li>On-Demand pricing (no sustained use discount applied to base rates)</li>
            <li>1-Year and 3-Year CUD columns show committed-use discount savings</li>
            <li>License-included pricing (BYOL not assumed unless the AWS line item indicates it)</li>
            <li>No Spot / Preemptible discounts applied</li>
            <li>Passthrough rows (e.g. MSK, OpenSearch self-hosted) carry AWS cost as-is — manual sizing required</li>
          </ul>
        </div>
        
        <h2>Mapping Confidence</h2>
        <table>
            <tr><th>Average Confidence</th><td><b>{avg_conf * 100:.1f}%</b></td></tr>
            <tr><th>Mapping Breakdown</th><td>Exact Matches (&ge;90%): <b>{exact_cnt}</b> | Near Matches (70-90%): <b>{near_cnt}</b> | Manual Review / Low Confidence: <b>{low_cnt}</b></td></tr>
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
            <tr><th>AWS Total</th><td class="right">${aws_total:,.2f}</td></tr>
            <tr><th>GCP On-Demand</th><td class="right">${gcp_od:,.2f}</td></tr>
            <tr><th>GCP with 1-Year Commitment</th><td class="right">${gcp_1yr:,.2f}</td></tr>
            <tr><th>GCP with 3-Year Commitment</th><td class="right">${gcp_3yr:,.2f}</td></tr>
        </table>
        <div id="aws-total-spend" hidden>{aws_total:.2f}</div>
        
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
        LEFT JOIN gcp_projection p ON c.aws_li_key = p.aws_li_key AND p.component = m.component
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
        
    diff_total = aws_total - gcp_od
    diff_total_class = "green" if diff_total > 0 else "red" if diff_total < 0 else ""
    diff_total_str = f"+${diff_total:,.2f}" if diff_total > 0 else f"-${abs(diff_total):,.2f}" if diff_total < 0 else "$0.00"
        
    html_content += f"""
            <tr>
                <th colspan="3">TOTAL</th>
                <th class="right">${aws_total:,.2f}</th>
                <th class="right">${gcp_od:,.2f}</th>
                <th class="right">${gcp_1yr:,.2f}</th>
                <th class="right">${gcp_3yr:,.2f}</th>
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
                 aws_total, gcp_od, gcp_1yr, gcp_3yr, mapped_rows, passthroughs]
            )
            conn.commit()
            print(f"Inserted run_results row for {run_id}")
    except Exception as e:
        print(f"Warning: could not write run_results: {e}")

    conn.close()

if __name__ == "__main__":
    main()
