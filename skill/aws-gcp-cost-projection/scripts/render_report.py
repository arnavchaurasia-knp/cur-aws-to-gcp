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
        </style>
    </head>
    <body>
        <h1 class="header">AWS to GCP Cloud Cost Analysis</h1>
        <p><b>Customer:</b> {customer_name} &nbsp;&nbsp; <b>Analysis Date:</b> {now.strftime('%B %Y')}</p>
        
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
            c.product, c.operation, m.gcp_service,
            m.projection_note,
            c.aws_amortized_cost,
            p.gcp_projected_cost, p.gcp_cost_1yr_cud, p.gcp_cost_3yr_cud,
            (c.aws_amortized_cost - p.gcp_projected_cost) AS diff
        FROM aws_li_catalog c
        LEFT JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key
        LEFT JOIN gcp_projection p ON c.aws_li_key = p.aws_li_key
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

if __name__ == "__main__":
    main()
