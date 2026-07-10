#!/usr/bin/env python3
import duckdb
import os
import json
import datetime
import html as _html

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")


# ── service-type pill classifier ──────────────────────────────────────────────
_PILL_RULES = [
    ("compute",    ["compute engine", "gce", "cloud run", "vertex ai", "cloud functions",
                    "batch", "app engine", "preemptible"]),
    ("database",   ["cloud sql", "cloud spanner", "alloydb", "bigtable", "firestore",
                    "datastore", "memorystore", "cloud memorystore", "database migration"]),
    ("storage",    ["cloud storage", "filestore", "backup", "persistent disk",
                    "hyperdisk", "storage transfer"]),
    ("network",    ["cloud nat", "network connectivity", "ncc", "load balanc",
                    "cloud cdn", "cloud dns", "cloud armor", "vpc", "interconnect",
                    "network services", "cloud vpn", "traffic director"]),
    ("monitoring", ["cloud logging", "cloud monitoring", "cloud trace", "cloud profiler",
                    "cloud audit", "error reporting", "cloud debugger"]),
    ("container",  ["kubernetes", "gke", "artifact registry", "container registry"]),
    ("messaging",  ["pub/sub", "pubsub", "cloud tasks", "dataflow", "cloud composer",
                    "eventarc", "workflows"]),
]

def pill_type(gcp_service, product=""):
    svc = (gcp_service or "").lower()
    prod = (product or "").lower()
    for ptype, keywords in _PILL_RULES:
        if any(k in svc for k in keywords):
            return ptype
        if any(k in prod for k in keywords):
            return ptype
    return "other"

_PILL_COLORS = {
    "compute":    "#1A73E8",
    "storage":    "#0D9D58",
    "database":   "#7B2FBE",
    "network":    "#E37400",
    "messaging":  "#C5221F",
    "monitoring": "#137333",
    "container":  "#1558D6",
    "other":      "#80868B",
}

_PILL_LABELS = {
    "compute":    "Compute",
    "storage":    "Storage",
    "database":   "Database",
    "network":    "Network",
    "messaging":  "Messaging",
    "monitoring": "Monitoring",
    "container":  "Container",
    "other":      "Other",
}


def pill_html(ptype):
    color = _PILL_COLORS.get(ptype, "#80868B")
    label = _PILL_LABELS.get(ptype, "Other")
    return (f'<span style="display:inline-block;font-size:10px;font-weight:600;'
            f'color:#fff;background:{color};border-radius:3px;padding:1px 5px;'
            f'margin-right:5px;vertical-align:middle;letter-spacing:0.3px">'
            f'{label}</span>')


def pct_badge(aws, gcp):
    if not aws or aws == 0:
        return ""
    pct = (gcp - aws) / aws * 100
    if abs(pct) < 0.5:
        return '<span style="color:#5F6368;font-size:11px">(≈flat)</span>'
    color = "#0D9D58" if pct < 0 else "#D93025"
    sign  = "−" if pct < 0 else "+"
    return (f'<span style="color:{color};font-size:11px;font-weight:600">'
            f'({sign}{abs(pct):.1f}%)</span>')


def fmt(v, prefix="$"):
    if v is None:
        return "—"
    return f"{prefix}{v:,.2f}"


CSS = """
<style>
  *, *::before, *::after { box-sizing: border-box; }
  body {
    font-family: 'Google Sans', 'Roboto', Arial, sans-serif;
    margin: 0; padding: 28px 36px;
    color: #202124; background: #fff;
    font-size: 13px; line-height: 1.5;
  }
  h1 {
    color: #1A73E8; font-size: 26px; font-weight: 500;
    border-bottom: 3px solid #1A73E8;
    padding-bottom: 10px; margin: 0 0 6px;
  }
  .subhead { color: #5F6368; margin: 0 0 28px; font-size: 13px; }
  .subhead b { color: #202124; }
  h2 {
    color: #1A73E8; font-size: 16px; font-weight: 500;
    margin: 32px 0 10px; padding-bottom: 4px;
    border-bottom: 1px solid #e8eaed;
  }
  h3 { font-size: 13px; font-weight: 600; margin: 16px 0 6px; color: #202124; }

  /* ── tables ── */
  table { border-collapse: collapse; width: 100%; }
  th {
    background: #1A73E8; color: #fff;
    padding: 8px 12px; text-align: left;
    font-weight: 500; font-size: 12px; white-space: nowrap;
  }
  td { border-bottom: 1px solid #e8eaed; padding: 7px 12px; vertical-align: top; }
  tr:hover td { background: #f8f9fa; }
  .total-row td {
    background: #f1f3f4 !important; font-weight: 600;
    border-top: 2px solid #1A73E8; border-bottom: none;
  }
  .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .green { color: #0D9D58; }
  .red   { color: #D93025; }

  /* ── summary card grid ── */
  .summary-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 12px; margin-bottom: 28px;
  }
  .card {
    border: 1px solid #e8eaed; border-radius: 8px;
    padding: 14px 16px; background: #fff;
  }
  .card-label { font-size: 11px; color: #5F6368; text-transform: uppercase;
                letter-spacing: 0.5px; margin-bottom: 4px; }
  .card-value { font-size: 20px; font-weight: 600; color: #202124; }
  .card-sub   { font-size: 11px; color: #5F6368; margin-top: 2px; }
  .card-accent { border-top: 3px solid #1A73E8; }

  /* ── info boxes ── */
  .info-box {
    background: #f8f9fa; border-left: 3px solid #1A73E8;
    padding: 10px 16px; margin: 10px 0; font-size: 12px;
  }
  .warn-box {
    background: #fff8e1; border-left: 3px solid #F9A825;
    padding: 10px 16px; margin: 10px 0; font-size: 12px;
  }
  .info-box ul, .warn-box ul { margin: 4px 0; padding-left: 18px; }
  .info-box li, .warn-box li { margin-bottom: 3px; }

  /* ── desc cell ── */
  .desc { font-size: 11px; color: #5F6368; line-height: 1.45; max-width: 380px; }

  /* ── passthrough / ignore indicators ── */
  .strat-pt { font-size: 10px; color: #80868B; margin-left: 4px; }

  /* ── legend ── */
  .legend { font-style: italic; color: #5F6368; font-size: 12px; margin-top: 8px; }

  /* ── method list ── */
  ul.method { margin: 6px 0; padding-left: 20px; font-size: 13px; line-height: 1.8; }

  /* ── section divider ── */
  .section-meta { font-size: 11px; color: #80868B; margin: -6px 0 10px; }
</style>
"""


def main():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        return

    conn = duckdb.connect(DB_PATH)

    # ── coverage KPI ──────────────────────────────────────────────────────────
    try:
        coverage_rows = conn.execute("""
            SELECT c.mechanic_group,
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
        with open(os.path.join(JOB_DIR, "projection-audit", "mapping_coverage.json"), "w") as f:
            json.dump(coverage, f, indent=2)
    except Exception as e:
        print(f"Coverage report failed: {e}")

    # ── totals ────────────────────────────────────────────────────────────────
    aws_workload     = conn.execute("SELECT COALESCE(SUM(aws_amortized_cost),0) FROM aws_li_catalog WHERE is_workload").fetchone()[0]
    aws_non_workload = conn.execute("SELECT COALESCE(SUM(aws_amortized_cost),0) FROM aws_li_catalog WHERE NOT is_workload").fetchone()[0]
    aws_grand = aws_workload + aws_non_workload

    # Marketplace spend: non-workload rows that are passthrough with "Marketplace" in note
    aws_marketplace = conn.execute("""
        SELECT COALESCE(SUM(c.aws_amortized_cost), 0)
        FROM aws_li_catalog c
        JOIN aws_li_to_gcp_li m USING (aws_li_key)
        WHERE NOT c.is_workload
          AND (m.projection_note ILIKE '%marketplace%' OR c.product ILIKE '%marketplace%')
    """).fetchone()[0]
    # Infrastructure baseline = everything except marketplace (the "true" IaaS+PaaS spend)
    aws_infra_baseline = aws_grand - aws_marketplace

    gcp_od   = conn.execute("SELECT COALESCE(SUM(gcp_projected_cost),0) FROM gcp_projection WHERE is_workload").fetchone()[0]
    gcp_1yr  = conn.execute("SELECT COALESCE(SUM(gcp_cost_1yr_cud),0)  FROM gcp_projection WHERE is_workload").fetchone()[0]
    gcp_3yr  = conn.execute("SELECT COALESCE(SUM(gcp_cost_3yr_cud),0)  FROM gcp_projection WHERE is_workload").fetchone()[0]

    # Mapped AWS cost = workload rows that received a real GCP projected cost
    # (strategy map or break_down). Passthrough rows carry AWS cost 1:1 and
    # inflate the baseline if included — comparing GCP vs mapped-only is apples-to-apples.
    # EXISTS subquery ensures each aws_li_key is counted once even for break_down rows
    # that join multiple components. SUM(DISTINCT ...) is wrong here — it deduplicates
    # by value, not by key, which mis-sums rows with coincident costs.
    aws_mapped_cost = conn.execute("""
        SELECT COALESCE(SUM(c.aws_amortized_cost), 0)
        FROM aws_li_catalog c
        WHERE c.is_workload
          AND EXISTS (
            SELECT 1 FROM aws_li_to_gcp_li m
            WHERE m.aws_li_key = c.aws_li_key
              AND m.strategy IN ('map', 'break_down')
          )
    """).fetchone()[0]

    gcp_nw_od  = conn.execute("SELECT COALESCE(SUM(gcp_projected_cost),0) FROM gcp_projection WHERE NOT is_workload").fetchone()[0]
    gcp_nw_1yr = conn.execute("SELECT COALESCE(SUM(gcp_cost_1yr_cud),0)  FROM gcp_projection WHERE NOT is_workload").fetchone()[0]
    gcp_nw_3yr = conn.execute("SELECT COALESCE(SUM(gcp_cost_3yr_cud),0)  FROM gcp_projection WHERE NOT is_workload").fetchone()[0]

    gcp_grand_od  = gcp_od  + gcp_nw_od
    gcp_grand_1yr = gcp_1yr + gcp_nw_1yr
    gcp_grand_3yr = gcp_3yr + gcp_nw_3yr

    # ── metadata ──────────────────────────────────────────────────────────────
    customer_name = "Prospect"
    cust_file = os.path.join(JOB_DIR, "customer_name.txt")
    if os.path.exists(cust_file):
        with open(cust_file) as f:
            customer_name = f.read().strip() or "Prospect"

    now    = datetime.datetime.utcnow()
    run_id = now.strftime("%Y%m%dT%H%M%SZ")

    region_row = conn.execute("""
        SELECT gcp_region, COUNT(*) n FROM gcp_projection
        WHERE is_workload AND gcp_region IS NOT NULL
        GROUP BY gcp_region ORDER BY n DESC LIMIT 1
    """).fetchone()
    gcp_region_display = region_row[0] if region_row else "see individual rows"
    _REGION_NAMES = {
        "asia-southeast1": "Singapore",  "asia-northeast1": "Tokyo",
        "asia-south1": "Mumbai",          "us-east4": "N. Virginia",
        "us-central1": "Iowa",            "us-west1": "Oregon",
        "us-west2": "Los Angeles",        "europe-west1": "Belgium",
        "europe-west4": "Netherlands",
    }
    if gcp_region_display in _REGION_NAMES:
        gcp_region_display = f"{gcp_region_display} ({_REGION_NAMES[gcp_region_display]})"

    total_li = conn.execute("SELECT COUNT(*) FROM aws_li_catalog").fetchone()[0]

    # ── capacity ──────────────────────────────────────────────────────────────
    aws_vcpu = conn.execute(
        "SELECT COALESCE(SUM(instance_vcpus*instance_count),0) FROM aws_li_catalog WHERE is_workload AND instance_vcpus IS NOT NULL"
    ).fetchone()[0]
    aws_ram  = conn.execute(
        "SELECT COALESCE(SUM(instance_ram_gb*instance_count),0) FROM aws_li_catalog WHERE is_workload AND instance_ram_gb IS NOT NULL"
    ).fetchone()[0]
    gcp_vcpu = conn.execute("""
        WITH cm AS (
            SELECT c.aws_li_key, SUM(m.unit_multiplier*c.instance_count) AS v
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.component='core' AND m.strategy IN ('map','break_down') GROUP BY c.aws_li_key
        )
        SELECT COALESCE(SUM(COALESCE(cm.v, c.instance_vcpus*c.instance_count)),0)
        FROM aws_li_catalog c LEFT JOIN cm USING (aws_li_key)
        WHERE c.is_workload AND c.instance_vcpus IS NOT NULL
    """).fetchone()[0]
    gcp_ram = conn.execute("""
        WITH rm AS (
            SELECT c.aws_li_key, SUM(m.unit_multiplier*c.instance_count) AS r
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.component='ram' AND m.strategy IN ('map','break_down') GROUP BY c.aws_li_key
        )
        SELECT COALESCE(SUM(COALESCE(rm.r, c.instance_ram_gb*c.instance_count)),0)
        FROM aws_li_catalog c LEFT JOIN rm USING (aws_li_key)
        WHERE c.is_workload AND c.instance_ram_gb IS NOT NULL
    """).fetchone()[0]

    _CAP_TOL = 0.005
    vcpu_ok = gcp_vcpu >= aws_vcpu * (1 - _CAP_TOL)
    ram_ok  = gcp_ram  >= aws_ram  * (1 - _CAP_TOL)

    # ── confidence ────────────────────────────────────────────────────────────
    avg_conf = conn.execute(
        "SELECT COALESCE(AVG(LEAST(mapping_confidence,1.0)),0) FROM aws_li_to_gcp_li WHERE strategy!='passthrough'"
    ).fetchone()[0]

    cat_conf_rows = conn.execute("""
        SELECT
          CASE
            WHEN m.strategy = 'passthrough' THEN 'Passthrough'
            WHEN c.product ILIKE '%EC2%' OR c.product ILIKE '%Elastic Compute%' THEN 'Compute (EC2)'
            WHEN c.product ILIKE '%RDS%' OR c.product ILIKE '%Aurora%' OR c.product ILIKE '%Redshift%' THEN 'Database (RDS/Aurora)'
            WHEN c.product ILIKE '%OpenSearch%' OR c.product ILIKE '%MSK%' OR c.product ILIKE '%ElastiCache%' THEN 'Managed Services'
            WHEN c.product ILIKE '%S3%' OR c.product ILIKE '%EBS%' OR c.product ILIKE '%Glacier%'
              OR c.product ILIKE '%Storage%' OR c.product ILIKE '%Backup%' THEN 'Storage'
            WHEN c.product ILIKE '%Route 53%' OR c.product ILIKE '%CloudFront%'
              OR c.product ILIKE '%VPC%' OR c.product ILIKE '%Data Transfer%' THEN 'Networking'
            ELSE 'Other'
          END AS category,
          AVG(LEAST(m.mapping_confidence, 1.0)) AS avg_conf,
          COUNT(*) AS cnt
        FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
        GROUP BY category ORDER BY avg_conf DESC NULLS LAST
    """).fetchall()

    # ── validation warnings ───────────────────────────────────────────────────
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
            with open(val_path) as vf:
                vr = json.load(vf)
            for gate, rows in (vr.get("violations") or {}).items():
                if rows:
                    validation_notes.append(_GATE_LABELS.get(gate, f"Validation note: {gate}"))
        except Exception:
            pass

    # ── detail rows (one per aws_li_key, components aggregated) ──────────────
    rows = conn.execute("""
        SELECT
            c.product,
            c.operation,
            COALESCE(ANY_VALUE(m.gcp_service), 'N/A')                        AS gcp_service,
            ANY_VALUE(m.strategy)                                              AS strategy,
            COALESCE(STRING_AGG(DISTINCT m.projection_note, ' | '), '')       AS notes,
            c.aws_amortized_cost,
            COALESCE(SUM(p.gcp_projected_cost), c.aws_amortized_cost)         AS gcp_od,
            COALESCE(SUM(p.gcp_cost_1yr_cud),   c.aws_amortized_cost)         AS gcp_1yr,
            COALESCE(SUM(p.gcp_cost_3yr_cud),   c.aws_amortized_cost)         AS gcp_3yr,
            c.aws_region,
            c.is_workload,
            c.pricing_model
        FROM aws_li_catalog c
        LEFT JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key
        LEFT JOIN gcp_projection    p ON c.aws_li_key = p.aws_li_key
                                      AND p.component IS NOT DISTINCT FROM m.component
        GROUP BY c.aws_li_key, c.product, c.operation, c.aws_amortized_cost, c.aws_region, c.is_workload, c.pricing_model
        ORDER BY c.aws_amortized_cost DESC
    """).fetchall()

    # ── passthrough spend for KPI card ───────────────────────────────────────
    passthrough_spend = conn.execute("""
        SELECT COALESCE(SUM(c.aws_amortized_cost), 0)
        FROM aws_li_catalog c
        JOIN aws_li_to_gcp_li m USING (aws_li_key)
        WHERE m.strategy = 'passthrough' AND c.is_workload
    """).fetchone()[0]
    passthrough_pct = (passthrough_spend / aws_infra_baseline * 100) if aws_infra_baseline else 0

    # ── rate provenance coverage (P0-C) ──────────────────────────────────────
    rate_source_rows = []
    try:
        rate_source_rows = conn.execute("""
            SELECT
                COALESCE(m.rate_source, 'unknown')                     AS source,
                COUNT(*)                                                AS row_count,
                COALESCE(SUM(c.aws_amortized_cost), 0)                 AS spend
            FROM aws_li_to_gcp_li m
            JOIN aws_li_catalog c USING (aws_li_key)
            GROUP BY source
            ORDER BY spend DESC
        """).fetchall()
    except Exception:
        pass  # rate_source column not yet present (old job)

    word_overlap_by_service = []
    try:
        word_overlap_by_service = conn.execute("""
            SELECT m.gcp_service,
                   COUNT(*)                        AS rows,
                   COALESCE(SUM(c.aws_amortized_cost), 0) AS spend
            FROM aws_li_to_gcp_li m
            JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.rate_source = 'word_overlap'
            GROUP BY m.gcp_service
            ORDER BY spend DESC
            LIMIT 10
        """).fetchall()
    except Exception:
        pass

    # ── wins/losses (top 5 each, workload only, mapped rows) ─────────────────
    wl_rows = conn.execute("""
        WITH agg AS (
            SELECT c.product,
                   ANY_VALUE(m.gcp_service) AS gcp_service,
                   c.aws_amortized_cost     AS aws,
                   SUM(p.gcp_cost_1yr_cud) AS gcp
            FROM aws_li_catalog c
            JOIN aws_li_to_gcp_li m USING (aws_li_key)
            JOIN gcp_projection    p ON p.aws_li_key = c.aws_li_key
                                    AND p.component IS NOT DISTINCT FROM m.component
            WHERE c.is_workload AND m.strategy IN ('map','break_down')
              AND c.aws_amortized_cost > 5
            GROUP BY c.aws_li_key, c.product, c.aws_amortized_cost
        )
        SELECT product, gcp_service, aws, gcp, (aws - gcp) AS savings
        FROM agg WHERE savings IS NOT NULL
        ORDER BY savings DESC
    """).fetchall()

    gcp_wins  = [r for r in wl_rows if r[4] > 0][:6]
    gcp_loses = [r for r in reversed(wl_rows) if r[4] < 0][:6]

    # ── under-projection suspects (R5) ────────────────────────────────────────
    # Rows where GCP < 10% of AWS on material spend (aws > $50), strategy='map'.
    # These may reflect wrong SKU, missing components, or unit mismatch.
    # We do NOT clamp — GCP may genuinely be cheaper — but we flag them for review.
    under_proj_rows = conn.execute("""
        SELECT c.product,
               ANY_VALUE(m.gcp_service)      AS gcp_service,
               ANY_VALUE(m.gcp_sku_name)     AS gcp_sku,
               c.aws_amortized_cost          AS aws,
               SUM(p.gcp_projected_cost)     AS gcp,
               STRING_AGG(DISTINCT m.projection_note, ' | ') AS notes
        FROM aws_li_catalog c
        JOIN aws_li_to_gcp_li m USING (aws_li_key)
        JOIN gcp_projection    p ON p.aws_li_key = c.aws_li_key
                                AND p.component IS NOT DISTINCT FROM m.component
        WHERE c.aws_amortized_cost > 50
          AND m.strategy IN ('map', 'break_down')
        GROUP BY c.aws_li_key, c.product, c.aws_amortized_cost
        HAVING SUM(p.gcp_projected_cost) > 0
           AND SUM(p.gcp_projected_cost) < 0.10 * c.aws_amortized_cost
        ORDER BY c.aws_amortized_cost DESC
        LIMIT 20
    """).fetchall()

    # ── build HTML ────────────────────────────────────────────────────────────
    def diff_td(aws, gcp):
        if gcp is None or aws is None:
            return '<td class="num">—</td>'
        d = aws - gcp
        if abs(d) < 0.005:
            return f'<td class="num">$0.00</td>'
        if d > 0:
            return f'<td class="num green">+{fmt(d)}</td>'
        return f'<td class="num red">−{fmt(abs(d))}</td>'

    # summary card values — compare GCP against infra baseline (excl. marketplace)
    def _pct(aws, gcp):
        if not aws:
            return ""
        p = (gcp - aws) / aws * 100
        s = "+" if p >= 0 else "−"
        return f"{s}{abs(p):.1f}% vs AWS"

    def _pct_infra(gcp):
        """% change vs infra baseline (excl. marketplace passthrough)."""
        if not aws_infra_baseline:
            return ""
        p = (gcp - aws_infra_baseline) / aws_infra_baseline * 100
        s = "+" if p >= 0 else "−"
        return f"{s}{abs(p):.1f}% vs infra baseline"

    # outlier note (from outlier_violations.md if present)
    outlier_note = ""
    outlier_path = os.path.join(JOB_DIR, "projection-audit", "outlier_violations.md")
    if os.path.exists(outlier_path):
        with open(outlier_path) as of:
            outlier_note = of.read().strip()

    # ── wins/losses table HTML ────────────────────────────────────────────────
    def wins_table(data, header_label, color):
        if not data:
            return f"<p style='color:#80868B;font-size:12px'>No significant {header_label.lower()} found.</p>"
        rows_html = ""
        for product, gcp_service, aws, gcp, diff in data:
            ptype = pill_type(gcp_service, product)
            pct = abs(diff) / aws * 100 if aws else 0
            rows_html += (
                f"<tr>"
                f"<td>{pill_html(ptype)}{_html.escape(str(product or ''))}</td>"
                f"<td style='color:#5F6368;font-size:12px'>{_html.escape(str(gcp_service or ''))}</td>"
                f"<td class='num'>{fmt(aws)}</td>"
                f"<td class='num'>{fmt(gcp)}</td>"
                f"<td class='num' style='color:{color};font-weight:600'>{fmt(abs(diff))} ({pct:.1f}%)</td>"
                f"</tr>"
            )
        return (
            f"<table><tr><th>AWS Service</th><th>GCP Service</th>"
            f"<th class='num'>AWS Cost</th><th class='num'>GCP 1yr CUD</th>"
            f"<th class='num'>{header_label}</th></tr>"
            f"{rows_html}</table>"
        )

    # ── confidence table ──────────────────────────────────────────────────────
    conf_rows_html = ""
    for cat, conf, cnt in cat_conf_rows:
        if cat == "Passthrough":
            conf_str = "N/A (intentional)"
            note = "Carries AWS cost as placeholder — manual sizing required"
            badge = ""
        else:
            conf_str = f"{conf*100:.1f}%"
            badge_color = "#0D9D58" if conf >= 0.85 else "#E37400" if conf >= 0.70 else "#D93025"
            badge = f'<span style="color:{badge_color};font-weight:600">{conf_str}</span>'
            note = {
                "Managed Services":      "Infrastructure estimates only — not managed-service equivalents",
                "Database (RDS/Aurora)": "HA config, storage, and IOPS may affect accuracy",
            }.get(cat, "")
            conf_str = conf_str  # keep for non-badge display
        conf_rows_html += (
            f"<tr><td>{cat}</td>"
            f"<td class='num'>{badge or conf_str}</td>"
            f"<td class='num'>{cnt}</td>"
            f"<td style='color:#5F6368;font-size:11px'>{note}</td></tr>"
        )

    # ── detail table rows ─────────────────────────────────────────────────────
    detail_rows_html = ""
    idx = 1
    for r in rows:
        product, operation, gcp_svc, strategy, notes, aws, od, cud1, cud3, aws_region, is_wl, pricing_model = r
        ptype  = pill_type(gcp_svc, product)
        p_html = pill_html(ptype)

        aws  = aws  or 0.0
        od   = od   or 0.0
        cud1 = cud1 or 0.0
        cud3 = cud3 or 0.0

        is_spot = (pricing_model or "").lower() == "spot"
        cud1_str = '—' if is_spot else fmt(cud1)
        cud3_str = '—' if is_spot else fmt(cud3)
        cud1_td_extra = ' title="Spot/Preemptible — CUDs not applicable on GCP"' if is_spot else ''
        cud3_td_extra = ' title="Spot/Preemptible — CUDs not applicable on GCP"' if is_spot else ''

        strategy_badge = ""
        if strategy == "passthrough":
            strategy_badge = '<span class="strat-pt">[passthrough]</span>'
        elif strategy == "ignore":
            strategy_badge = '<span class="strat-pt">[no GCP charge]</span>'

        # Truncate long notes
        note_text = (notes or "").replace(" | ", " · ")
        if len(note_text) > 200:
            note_text = note_text[:200] + "…"

        op_str = _html.escape(str(operation or ""))
        note_str = _html.escape(note_text)

        desc_html = f'<div class="desc">'
        if op_str:
            desc_html += f'{op_str}'
        if note_str:
            desc_html += f'{"<br>" if op_str else ""}{note_str}'
        desc_html += '</div>'

        diff = aws - od
        diff_td = ""
        if abs(diff) < 0.005:
            diff_td = '<td class="num">—</td>'
        elif diff > 0:
            diff_td = f'<td class="num green">+{fmt(diff)}</td>'
        else:
            diff_td = f'<td class="num red">−{fmt(abs(diff))}</td>'

        detail_rows_html += (
            f"<tr>"
            f"<td style='color:#80868B;font-size:11px'>{idx}</td>"
            f"<td style='white-space:nowrap'>"
            f"  {p_html}"
            f"  <span style='font-size:12px'>{_html.escape(str(product or ''))}</span>"
            f"  {strategy_badge}<br>"
            f"  <span style='font-size:11px;color:#1A73E8'>{_html.escape(str(gcp_svc or 'N/A'))}</span>"
            f"</td>"
            f"<td>{desc_html}</td>"
            f'<td class="num">{fmt(aws)}</td>'
            f'<td class="num">{fmt(od)}</td>'
            f'<td class="num"{cud1_td_extra}>{cud1_str}</td>'
            f'<td class="num"{cud3_td_extra}>{cud3_str}</td>'
            f"{diff_td}"
            f"</tr>\n"
        )
        idx += 1

    # grand total row
    diff_tot  = aws_grand - gcp_grand_od
    diff_tot_class = "green" if diff_tot > 0 else "red" if diff_tot < 0 else ""
    diff_tot_str = ("—" if abs(diff_tot) < 0.005
                    else (f"+{fmt(diff_tot)}" if diff_tot > 0 else f"−{fmt(abs(diff_tot))}"))

    detail_rows_html += (
        f'<tr class="total-row">'
        f'<td colspan="3" style="font-weight:600">GRAND TOTAL</td>'
        f'<td class="num">{fmt(aws_grand)}</td>'
        f'<td class="num">{fmt(gcp_grand_od)}</td>'
        f'<td class="num">{fmt(gcp_grand_1yr)}</td>'
        f'<td class="num">{fmt(gcp_grand_3yr)}</td>'
        f'<td class="num {diff_tot_class}" style="font-weight:600">{diff_tot_str}</td>'
        f'</tr>'
    )

    # ── summary cards ─────────────────────────────────────────────────────────
    def card(label, value, sub="", accent=False):
        a = ' card-accent' if accent else ''
        return (f'<div class="card{a}">'
                f'<div class="card-label">{label}</div>'
                f'<div class="card-value">{value}</div>'
                f'{"<div class=card-sub>" + sub + "</div>" if sub else ""}'
                f'</div>')

    mkt_note = f"incl. {fmt(aws_marketplace)} Marketplace passthrough" if aws_marketplace > 0 else ""
    # GCP cards compare against mapped AWS cost (rows with real GCP projected costs only).
    # Passthrough rows carry AWS cost 1:1 and would inflate the baseline, making
    # GCP look artificially cheaper — comparing against mapped-only is apples-to-apples.
    infra_sub = f"{_pct_infra(gcp_od)} · Passthrough: {fmt(passthrough_spend)} ({passthrough_pct:.1f}%)"
    cards_html = (
        card("AWS Total (Bill)", fmt(aws_grand), infra_sub, accent=True) +
        card("AWS Mapped Cost", fmt(aws_mapped_cost), "AWS spend with GCP rates applied (excl. passthrough)") +
        card("GCP On-Demand", fmt(gcp_od), _pct(aws_mapped_cost, gcp_od)) +
        card("GCP 1-Year CUD", fmt(gcp_1yr), _pct(aws_mapped_cost, gcp_1yr)) +
        card("GCP 3-Year CUD", fmt(gcp_3yr), _pct(aws_mapped_cost, gcp_3yr)) +
        card("Mapping Confidence", f"{avg_conf*100:.1f}%", "excl. passthrough rows")
    )

    # ── assemble page ─────────────────────────────────────────────────────────
    warn_block = ""
    if validation_notes:
        items = "".join(f"<li>{n}</li>" for n in validation_notes)
        warn_block = f'<div class="warn-box"><b>&#9888; Projection notes — review before sharing</b><ul>{items}</ul></div>'

    if outlier_note:
        warn_block += f'<div class="warn-box"><b>&#9888; Cost outliers clamped to passthrough</b><pre style="white-space:pre-wrap;font-size:11px;margin:6px 0">{_html.escape(outlier_note[:1200])}</pre></div>'

    # ── rate provenance section HTML ──────────────────────────────────────────
    _SOURCE_META = {
        "exact_sku":  ("#0D9D58", "Exact SKU",      "SKU pinned by static mapper or LLM exact match — highest accuracy"),
        "passthrough":("#1A73E8", "Passthrough",     "No GCP rate applied; AWS cost carried as placeholder"),
        "word_overlap":("#E37400","Word-Overlap SKU","SKU resolved by description search — verify if cost looks wrong"),
        "no_rate":    ("#D93025", "No Rate Found",   "Rate missing for resolved SKU; row fell back to passthrough"),
        "unknown":    ("#80868B", "Unknown",          "Rate source not recorded (pre-P0-B run)"),
    }
    coverage_section_html = ""
    if rate_source_rows:
        total_rows_rs  = sum(r[1] for r in rate_source_rows)
        total_spend_rs = sum(r[2] for r in rate_source_rows)
        cov_rows_html = ""
        for source, row_cnt, spend in rate_source_rows:
            color, label, desc = _SOURCE_META.get(source, ("#80868B", source, ""))
            row_pct   = 100.0 * row_cnt  / total_rows_rs  if total_rows_rs  else 0
            spend_pct = 100.0 * spend    / total_spend_rs if total_spend_rs else 0
            badge_style = (f"display:inline-block;font-size:10px;font-weight:600;color:#fff;"
                           f"background:{color};border-radius:3px;padding:1px 5px;margin-right:4px")
            warn = ""
            if source == "word_overlap":
                warn = ' <span style="color:#E37400;font-size:10px">⚠ verify if inflated</span>'
            elif source == "no_rate":
                warn = ' <span style="color:#D93025;font-size:10px">⚠ check SKU catalog</span>'
            cov_rows_html += (
                f"<tr>"
                f"<td><span style='{badge_style}'>{label}</span>{warn}</td>"
                f"<td style='color:#5F6368;font-size:11px'>{desc}</td>"
                f"<td class='num'>{row_cnt:,} ({row_pct:.1f}%)</td>"
                f"<td class='num'><b>${spend:,.2f}</b> ({spend_pct:.1f}%)</td>"
                f"</tr>"
            )
        if word_overlap_by_service:
            wo_rows = ""
            for svc, cnt, spend in word_overlap_by_service:
                wo_rows += f"<tr><td>{_html.escape(str(svc or ''))}</td><td class='num'>{cnt}</td><td class='num'>{fmt(spend)}</td></tr>"
            word_overlap_detail_html = f"""
<details style="margin-top:8px">
  <summary style="cursor:pointer;font-size:12px;color:#E37400">⚠ Word-overlap resolved rows by service (review for SKU accuracy)</summary>
  <table style="margin-top:8px;font-size:12px">
    <tr><th>GCP Service</th><th class="num">Rows</th><th class="num">AWS Spend</th></tr>
    {wo_rows}
  </table>
</details>"""
        else:
            word_overlap_detail_html = ""

        coverage_section_html = f"""
<h2>Rate Provenance Coverage</h2>
<p class="section-meta">How each row's GCP rate was obtained. Word-Overlap rows are lowest trust — inflate when description match is loose.</p>
<table style="width:auto;min-width:640px">
  <tr><th>Source</th><th>Description</th><th class="num">Rows</th><th class="num">AWS Spend</th></tr>
  {cov_rows_html}
</table>
{word_overlap_detail_html}
"""

    wins_html  = wins_table(gcp_wins,  "GCP Savings", "#0D9D58")
    loses_html = wins_table(gcp_loses, "Extra Cost on GCP", "#D93025")

    # Under-projection warning block
    if under_proj_rows:
        up_rows_html = ""
        for product, gcp_service, gcp_sku, aws, gcp, notes in under_proj_rows:
            ratio = gcp / aws if aws else 0
            note_text = (notes or "")[:120]
            up_rows_html += (
                f"<tr>"
                f"<td>{_html.escape(str(product or ''))}</td>"
                f"<td style='color:#5F6368;font-size:12px'>{_html.escape(str(gcp_service or ''))}</td>"
                f"<td class='num'>{fmt(aws)}</td>"
                f"<td class='num'>{fmt(gcp)}</td>"
                f"<td class='num' style='color:#E37400;font-weight:600'>{ratio:.1%}</td>"
                f"<td style='color:#5F6368;font-size:11px'>{_html.escape(note_text)}</td>"
                f"</tr>"
            )
        under_proj_html = f"""
<div style="background:#FEF7E0;border-left:4px solid #E37400;border-radius:4px;padding:16px 20px;margin:16px 0">
  <b style="color:#E37400">&#9888; Potential Understatement — GCP &lt;10% of AWS on {len(under_proj_rows)} row(s)</b>
  <p style="margin:6px 0 12px;font-size:13px;color:#444">
    These rows project very low GCP cost relative to AWS spend. This may indicate a wrong SKU, missing
    components, or a unit mismatch — not necessarily genuine GCP savings. Review before presenting.
  </p>
  <div style="overflow-x:auto">
  <table style="font-size:12px">
    <tr><th>AWS Product</th><th>GCP Service</th><th class="num">AWS Cost</th>
        <th class="num">GCP OD</th><th class="num">Ratio</th><th>Notes</th></tr>
  {up_rows_html}
  </table>
  </div>
</div>"""
    else:
        under_proj_html = ""

    vcpu_color = "#0D9D58" if vcpu_ok else "#E37400"
    ram_color  = "#0D9D58" if ram_ok  else "#E37400"
    vcpu_badge = "✓ PASS" if vcpu_ok else "⚠ WARN"
    ram_badge  = "✓ PASS" if ram_ok  else "⚠ WARN"
    capacity_rows = (
        f"<tr><td>vCPU (cores)</td>"
        f"<td class='num'>{aws_vcpu:,.1f}</td><td class='num'>{gcp_vcpu:,.1f}</td>"
        f"<td class='num' style='color:{vcpu_color}'>{vcpu_badge}</td></tr>"
        f"<tr><td>Memory (GB)</td>"
        f"<td class='num'>{aws_ram:,.1f}</td><td class='num'>{gcp_ram:,.1f}</td>"
        f"<td class='num' style='color:{ram_color}'>{ram_badge}</td></tr>"
    )
    if aws_vcpu == 0 and gcp_vcpu == 0:
        capacity_rows = "<tr><td colspan='4' style='color:#80868B;font-size:12px;text-align:center'>Instance specs not available in this CUR export — capacity reconciliation requires BoxUsage line items with instance type.</td></tr>"

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AWS → GCP Cost Analysis — {customer_name}</title>
<meta name="description" content="AWS to GCP cloud cost projection for {customer_name}">
{CSS}
</head>
<body>

<h1>AWS → GCP Cloud Cost Analysis</h1>
<p class="subhead">
  <b>Customer:</b> {_html.escape(customer_name)} &nbsp;·&nbsp;
  <b>Analysis Date:</b> {now.strftime('%B %Y')} &nbsp;·&nbsp;
  <b>Line Items:</b> {total_li:,} &nbsp;·&nbsp;
  <b>Primary GCP Region:</b> {_html.escape(gcp_region_display)}
</p>

{warn_block}

<div class="summary-grid">
{cards_html}
</div>

<h2>Cost Summary</h2>
<table>
  <tr>
    <th>Cost Category</th>
    <th class="num">AWS Cost</th>
    <th class="num">GCP On-Demand</th>
    <th class="num">GCP 1-Year CUD</th>
    <th class="num">GCP 3-Year CUD</th>
  </tr>
  <tr>
    <td><b>Core Infrastructure (Workload)</b></td>
    <td class="num">{fmt(aws_workload)}</td>
    <td class="num">{fmt(gcp_od)}</td>
    <td class="num">{fmt(gcp_1yr)}</td>
    <td class="num">{fmt(gcp_3yr)}</td>
  </tr>
  <tr>
    <td><b>Non-Workload (Marketplace &amp; Support)</b>
      <div class="desc">Passed through 1:1 — no GCP equivalent; excluded from comparison metrics</div>
    </td>
    <td class="num">{fmt(aws_non_workload)}</td>
    <td class="num">{fmt(gcp_nw_od)}</td>
    <td class="num">{fmt(gcp_nw_1yr)}</td>
    <td class="num">{fmt(gcp_nw_3yr)}</td>
  </tr>
  <tr class="total-row">
    <td>Grand Total (Bill)</td>
    <td class="num">{fmt(aws_grand)}</td>
    <td class="num">{fmt(gcp_grand_od)}</td>
    <td class="num">{fmt(gcp_grand_1yr)}</td>
    <td class="num">{fmt(gcp_grand_3yr)}</td>
  </tr>
  <tr style="background:#e8f5e9">
    <td><b>Infrastructure Baseline</b> <span style="font-size:11px;color:#5F6368">(excl. {fmt(aws_marketplace)} Marketplace)</span></td>
    <td class="num" style="font-weight:600">{fmt(aws_infra_baseline)}</td>
    <td class="num" style="font-weight:600">{fmt(gcp_od)}</td>
    <td class="num" style="font-weight:600">{fmt(gcp_1yr)}</td>
    <td class="num" style="font-weight:600">{fmt(gcp_3yr)}</td>
  </tr>
  <tr style="background:#e3f2fd">
    <td><b>Mapped AWS Cost</b>
      <div class="desc">AWS spend on rows with a real GCP projected cost (excl. passthrough). GCP % is calculated against this — apples-to-apples comparison.</div>
    </td>
    <td class="num" style="font-weight:600">{fmt(aws_mapped_cost)}</td>
    <td class="num" style="font-weight:600">{fmt(gcp_od)} {pct_badge(aws_mapped_cost, gcp_od)}</td>
    <td class="num" style="font-weight:600">{fmt(gcp_1yr)} {pct_badge(aws_mapped_cost, gcp_1yr)}</td>
    <td class="num" style="font-weight:600">{fmt(gcp_3yr)} {pct_badge(aws_mapped_cost, gcp_3yr)}</td>
  </tr>
</table>
<div id="aws-total-spend" hidden>{aws_grand:.2f}</div>
<div id="aws-infra-baseline" hidden>{aws_infra_baseline:.2f}</div>
<p class="legend">&#9888; Windows / commercial software license premiums excluded unless explicitly modeled in the source bill. GCP % change is calculated against <b>Mapped AWS Cost</b> (rows with actual GCP pricing applied) — not the full bill total. This gives an apples-to-apples comparison since passthrough rows carry AWS cost unchanged on both sides.</p>

{coverage_section_html}
<h2>Where GCP Wins &amp; Loses</h2>
<p class="section-meta">Top services where GCP is cheaper / more expensive than current AWS spend (1-Year CUD vs AWS amortized, workload rows only).</p>
<h3 style="color:#0D9D58">&#9660; GCP Savings Opportunities</h3>
{wins_html}
<h3 style="color:#D93025">&#9650; GCP Cost Increases</h3>
{loses_html}
{under_proj_html}
<h2>Capacity Reconciliation</h2>
<p class="section-meta">GCP recommended capacity vs AWS baseline. WARN means the GCP mapping is below the AWS spec by more than 0.5%.</p>
<table style="width:auto;min-width:480px">
  <tr><th>Resource</th><th class="num">AWS Baseline</th><th class="num">GCP Recommended</th><th class="num">Status</th></tr>
  {capacity_rows}
</table>

<h2>Mapping Confidence</h2>
<p class="section-meta">Overall average (excl. passthrough): <b>{avg_conf*100:.1f}%</b></p>
<table style="width:auto;min-width:560px">
  <tr><th>Service Category</th><th class="num">Avg Confidence</th><th class="num">Rows</th><th>Notes</th></tr>
  {conf_rows_html}
</table>

<div class="info-box">
  <b>Pricing assumptions:</b>
  <ul>
    <li>Target region: <b>{_html.escape(gcp_region_display)}</b></li>
    <li>On-Demand list prices — no sustained-use discount applied to base rates</li>
    <li>1-Year and 3-Year CUD columns apply committed-use multipliers per service</li>
    <li>License-included pricing (BYOL not assumed unless the AWS bill indicates it)</li>
    <li>AWS Spot instances → GCP Spot/Preemptible VMs (~60–91% off On-Demand). AWS Spot prices are market-variable and may be lower — GCP may appear higher for Spot-heavy workloads.</li>
    <li>OpenSearch and MSK rows are <b>self-managed infrastructure estimates</b> on GCP, not managed-service equivalents. Treat as directional only.</li>
    <li>Passthrough rows carry the AWS cost as-is — no reliable GCP equivalent found; manual sizing required.</li>
  </ul>
</div>

<h2>Cost Comparison by Service</h2>
<p class="section-meta">Sorted by AWS spend descending. Diff = AWS − GCP On-Demand; <span class="green">green = GCP cheaper</span>, <span class="red">red = GCP more expensive</span>.</p>
<table>
  <tr>
    <th>#</th>
    <th>AWS Service → GCP Service</th>
    <th>Description</th>
    <th class="num">AWS</th>
    <th class="num">GCP OD</th>
    <th class="num">GCP 1yr</th>
    <th class="num">GCP 3yr</th>
    <th class="num">Diff</th>
  </tr>
  {detail_rows_html}
</table>
<p class="legend">* Diff = AWS − GCP On-Demand. Positive (green) = GCP cheaper; negative (red) = GCP costs more.</p>

<h2>Methodology</h2>
<ul class="method">
  <li>AWS Cost and Usage Report (CUR) line items ingested and classified by billing mechanic (compute, storage, data transfer, managed DB, etc.).</li>
  <li>Deterministic mappings applied first (EBS storage types → Persistent Disk, I/O-only charges → ignore, NAT/TGW/ALB → GCP networking equivalents).</li>
  <li>EC2 and RDS instance families mapped deterministically using instance-family rules (e.g., c-family → C3/C3D, r-family → N4/N2, t-family → E2, ARM → T2A/C4A).</li>
  <li>Remaining dynamic rows (misc, managed services) mapped by LLM agents with GCP billing catalog constraints and confidence scoring.</li>
  <li>GCP list prices sourced from the Cloud Billing API catalog (bundled at report generation time). CUD rates are applied from <code>cud_pct.json</code> per service class.</li>
  <li>Outlier safety net: any mapped row with projected GCP/AWS ratio &gt;50× or absolute &gt;$10,000 on &lt;$100 AWS spend is clamped to AWS-cost passthrough.</li>
  <li>AWS Marketplace and Support charges passed through at 1:1 cost (no GCP equivalent).</li>
</ul>

</body>
</html>"""

    # ── write outputs ─────────────────────────────────────────────────────────
    html_path = os.path.join(JOB_DIR, "projection-audit", f"report-{run_id}.html")
    for path in [html_path, os.path.join(JOB_DIR, "projection-audit", "report.html")]:
        with open(path, "w", encoding="utf-8") as f:
            f.write(page)
    print(f"Generated {html_path}")

    # ── AI summary markdown (shown in frontend JobStatus card) ────────────────
    summary_md_rel = None
    try:
        od_pct  = (gcp_od  - aws_infra_baseline) / aws_infra_baseline * 100 if aws_infra_baseline else 0
        c1_pct  = (gcp_1yr - aws_infra_baseline) / aws_infra_baseline * 100 if aws_infra_baseline else 0
        c3_pct  = (gcp_3yr - aws_infra_baseline) / aws_infra_baseline * 100 if aws_infra_baseline else 0
        arrow   = lambda p: ("▲" if p > 0 else "▼") + f" {abs(p):.1f}%"
        verdict = "GCP On-Demand is cost-neutral" if abs(od_pct) < 2 else \
                  f"GCP On-Demand is {arrow(od_pct)} vs AWS" + (" — GCP more expensive" if od_pct > 0 else " — GCP cheaper")

        top_saves = "\n".join(
            f"- **{r[0]}** → {r[1]}: save ${r[4]:,.0f}/mo ({r[4]/r[2]*100:.0f}%)"
            for r in gcp_wins[:3] if r[2]
        ) or "_No significant savings identified._"

        top_costs = "\n".join(
            f"- **{r[0]}** → {r[1]}: +${abs(r[4]):,.0f}/mo ({abs(r[4])/r[2]*100:.0f}% more)"
            for r in gcp_loses[:3] if r[2]
        ) or "_No significant cost increases identified._"

        passthrough_rows = conn.execute("""
            SELECT c.product, c.aws_amortized_cost
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.strategy = 'passthrough' AND c.is_workload AND c.aws_amortized_cost > 0
            GROUP BY c.product, c.aws_amortized_cost
            ORDER BY c.aws_amortized_cost DESC LIMIT 4
        """).fetchall()
        passthrough_note = ", ".join(r[0] for r in passthrough_rows) if passthrough_rows else "none"

        summary_lines = [
            f"## {customer_name} — AWS → GCP Cost Projection",
            "",
            f"**{verdict}**",
            "",
            "| Pricing Tier | Monthly Estimate | vs AWS |",
            "|---|---|---|",
            f"| AWS Infra Baseline | ${aws_infra_baseline:,.0f} | — |",
            f"| GCP On-Demand | ${gcp_od:,.0f} | {arrow(od_pct)} |",
            f"| GCP 1-Year CUD | ${gcp_1yr:,.0f} | {arrow(c1_pct)} |",
            f"| GCP 3-Year CUD | ${gcp_3yr:,.0f} | {arrow(c3_pct)} |",
            "",
            "### Where GCP saves",
            top_saves,
            "",
            "### Where GCP costs more",
            top_costs,
            "",
            f"### Mapping coverage",
            f"- **{total_li:,}** AWS line items · **{avg_conf*100:.0f}%** average mapping confidence",
            f"- Passthroughs (no GCP equivalent): {passthrough_note}",
            f"- Primary GCP region: {gcp_region_display}",
        ]
        summary_text = "\n".join(summary_lines)
        summary_rel  = os.path.join("projection-audit", f"summary-{run_id}.md")
        summary_abs  = os.path.join(JOB_DIR, summary_rel)
        with open(summary_abs, "w", encoding="utf-8") as sf:
            sf.write(summary_text)
        summary_md_rel = summary_rel
        print(f"Generated summary: {summary_abs}")
    except Exception as e:
        print(f"Warning: could not write summary_md: {e}")

    # ── run_results row (for frontend TotalsCard) ─────────────────────────────
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS run_results (
                run_id TEXT PRIMARY KEY, ts_utc TEXT, run_type TEXT, instruction TEXT,
                aws_total DOUBLE, gcp_od DOUBLE, gcp_1yr_cud DOUBLE, gcp_3yr_cud DOUBLE,
                report_html TEXT, report_md TEXT, summary_md TEXT,
                mapped_rows INTEGER, passthroughs INTEGER, confidence TEXT
            )
        """)
        if not conn.execute("SELECT 1 FROM run_results WHERE run_id=?", [run_id]).fetchone():
            mapped_rows  = conn.execute("SELECT COUNT(*) FROM aws_li_to_gcp_li").fetchone()[0] or 0
            passthroughs = conn.execute("SELECT COUNT(*) FROM aws_li_to_gcp_li WHERE strategy='passthrough'").fetchone()[0] or 0
            conn.execute(
                "INSERT INTO run_results "
                "(run_id,ts_utc,run_type,instruction,aws_total,gcp_od,gcp_1yr_cud,gcp_3yr_cud,"
                " report_html,report_md,summary_md,mapped_rows,passthroughs,confidence) "
                "VALUES (?,?,?,NULL,?,?,?,?,'projection-audit/report.html',NULL,?,?,?,NULL)",
                # aws_total stores the infrastructure baseline (excl. marketplace) so
                # the frontend TotalsCard % comparison reflects only what we map and price.
                # gcp_od/1yr/3yr are workload-only — marketplace passes through 1:1
                # and is not included in either side of the comparison.
                [run_id, now.strftime("%Y-%m-%dT%H:%M:%SZ"), "initial",
                 aws_infra_baseline, gcp_od, gcp_1yr, gcp_3yr, summary_md_rel, mapped_rows, passthroughs]
            )
            conn.commit()
            print(f"Inserted run_results row for {run_id}")
    except Exception as e:
        print(f"Warning: could not write run_results: {e}")

    conn.close()


if __name__ == "__main__":
    main()
