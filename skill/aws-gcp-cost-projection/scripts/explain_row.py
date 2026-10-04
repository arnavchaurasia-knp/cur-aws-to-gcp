#!/usr/bin/env python3
"""
explain_row.py — self-serve "why is this row priced the way it is" tool.

Investigates a report row the same way a human would when asked to explain
it: pulls the REAL underlying line items straight from projection.duckdb
(never trust an exported report/spreadsheet's own text — confirmed real this
session that an export tool mangled multiple unrelated rows' instance lists
together), shows real AWS vs GCP cost per instance, the real effective rate
on both sides, and cross-checks the raw bill to tell a genuine AWS list-price
gap apart from a discount-vs-on-demand mismatch.

Usage:
    python3 explain_row.py <job_dir> "<search term>"

<job_dir> is a job's root directory (the one containing projection-audit/
and input.csv or input.gz) — e.g. the job folder under
~/.cur-web/jobs/<job-id> or, on this machine, the path under
%LOCALAPPDATA%\\Temp\\cur-web-data\\jobs\\<job-id>.

<search term> matches against product OR usage_type (case-insensitive
substring) — e.g. "mapreduce", "m8g.4xlarge", "elastic file system".

Exits 0 always; prints a human-readable report to stdout.
"""
import os
import sys

try:
    import duckdb
except Exception as e:
    print(f"explain_row: duckdb not available ({e})", file=sys.stderr)
    sys.exit(1)


def fmt_money(v):
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <job_dir> \"<search term>\"", file=sys.stderr)
        sys.exit(1)

    job_dir = sys.argv[1]
    term = sys.argv[2]
    db_path = os.path.join(job_dir, "projection-audit", "projection.duckdb")
    if not os.path.exists(db_path):
        print(f"No projection.duckdb found at {db_path}", file=sys.stderr)
        sys.exit(1)

    con = duckdb.connect(db_path, read_only=True)
    like = f"%{term}%"

    # ---- 1. Real underlying rows (never the exported report's own text) ----
    rows = con.execute(
        """
        SELECT c.aws_li_key, c.product, c.usage_type, c.operation, c.gcp_region,
               c.line_item_type, c.pricing_model, c.total_usage, c.pricing_unit,
               ROUND(c.aws_amortized_cost, 2) AS aws_cost
        FROM aws_li_catalog c
        WHERE (c.product ILIKE ? OR c.usage_type ILIKE ?) AND c.aws_amortized_cost > 0
        ORDER BY aws_cost DESC
        """,
        [like, like],
    ).fetchall()

    if not rows:
        print(f"No positive-cost rows found matching {term!r} in {db_path}")
        sys.exit(0)

    print(f"=== Real rows matching {term!r} ({len(rows)} found) ===\n")

    total_aws = 0.0
    total_gcp = 0.0
    flagged_rate_checks = []  # (usage_type, aws_region, gcp_region)
    distinct_usage_types = sorted({r[2] for r in rows if r[2]})

    for r in rows:
        (aws_li_key, product, usage_type, operation, gcp_region,
         line_item_type, pricing_model, total_usage, pricing_unit, aws_cost) = r

        gcp_rows = con.execute(
            """
            SELECT m.component, m.gcp_service, m.gcp_sku_id, m.gcp_sku_name,
                   m.unit_multiplier, m.strategy,
                   (SELECT SUM(p.gcp_projected_cost) FROM gcp_projection p
                    WHERE p.aws_li_key = m.aws_li_key
                      AND p.component IS NOT DISTINCT FROM m.component) AS gcp_cost
            FROM aws_li_to_gcp_li m WHERE m.aws_li_key = ?
            """,
            [aws_li_key],
        ).fetchall()

        gcp_cost_total = sum((g[6] or 0.0) for g in gcp_rows if g[5] == "map") or None
        strategy = gcp_rows[0][5] if gcp_rows else "UNMAPPED"

        total_aws += aws_cost
        if gcp_cost_total:
            total_gcp += gcp_cost_total

        print(f"  {usage_type}  [{product}]  region={gcp_region}  "
              f"pricing_model={pricing_model}/{line_item_type}")
        print(f"    total_usage={total_usage!r} {pricing_unit or ''}  "
              f"AWS cost={fmt_money(aws_cost)}"
              + (f"  (eff. rate ${aws_cost/total_usage:.4f}/{pricing_unit or 'unit'})"
                 if total_usage else ""))
        if not gcp_rows:
            print("    GCP: ** no mapping entry at all ** (unmapped — see ensure_full_coverage gate)")
        for comp, svc, sku_id, sku_name, mult, strat, gcost in gcp_rows:
            tag = f"{comp or ''}: {sku_name}" if sku_name else "(no SKU)"
            print(f"    GCP [{strat}] {tag}  x{mult}  sku_id={sku_id}  "
                  f"-> {fmt_money(gcost)}")
            if strat == "map" and sku_id:
                flagged_rate_checks.append((usage_type, gcp_region, sku_id, mult, comp))
        print(f"    => row total: AWS {fmt_money(aws_cost)} vs GCP {fmt_money(gcp_cost_total)}")
        print()

    print(f"=== TOTAL: AWS {fmt_money(total_aws)} vs GCP {fmt_money(total_gcp)}  "
          f"(diff {fmt_money(total_aws - total_gcp)}) ===\n")

    # ---- 2. Real GCP rate used, from this job's own resolved rate table ----
    print("=== GCP rates actually applied (gcp_sku_rates, OnDemand) ===")
    seen_skus = set()
    for usage_type, gcp_region, sku_id, mult, comp in flagged_rate_checks:
        if sku_id in seen_skus:
            continue
        seen_skus.add(sku_id)
        rate_row = con.execute(
            """
            SELECT gcp_sku_name, region, unit, rate_usd FROM gcp_sku_rates
            WHERE gcp_sku_id = ? AND pricing_type = 'OnDemand'
            ORDER BY (region = ?) DESC LIMIT 1
            """,
            [sku_id, gcp_region],
        ).fetchone()
        if rate_row:
            print(f"  {sku_id}  {rate_row[0]}  region={rate_row[1]}  "
                  f"rate=${rate_row[3]}/{rate_row[2]}")

    # ---- 3. Cross-check the raw bill: is the AWS rate a genuine list price,
    #         or a discount/commitment rate being compared unfairly against
    #         GCP's undiscounted on-demand number? ----
    # Matched against the EXACT usage_type values found in step 1, not the
    # original search term — the term often matches on `product` (e.g.
    # "mapreduce"), which usage_type never contains, so re-searching by it
    # here would silently return nothing.
    raw_path = None
    for candidate in ("input.csv", "input.gz", "input.csv.gz"):
        p = os.path.join(job_dir, candidate)
        if os.path.exists(p):
            raw_path = p
            break

    if raw_path:
        print(f"\n=== Raw-bill cross-check ({os.path.basename(raw_path)}) ===")
        raw_con = duckdb.connect()
        try:
            raw_path_sql = raw_path.replace("\\", "/")
            raw_con.execute(
                f"CREATE TABLE t AS SELECT * FROM read_csv_auto('{raw_path_sql}', sample_size=-1)"
            )
            cols = {c[1].lower() for c in raw_con.execute("PRAGMA table_info(t)").fetchall()}
            has_public_rate = "pricing_public_on_demand_rate" in cols
            has_unblended_rate = "line_item_unblended_rate" in cols
            has_ut = "line_item_usage_type" in cols
            has_lit = "line_item_line_item_type" in cols
            # Restrict to the primary "Usage"/"DiscountedUsage" line — the same
            # usage_type string also appears on separate Credit/EdpDiscount/Tax
            # adjustment line items (confirmed real: those carry a NEGATIVE
            # unblended_rate, which would otherwise falsely look like "AWS
            # applied a discount" when it's actually an unrelated credit row,
            # not the usage charge itself).
            lit_filter = "AND line_item_line_item_type IN ('Usage', 'DiscountedUsage')" if has_lit else ""
            if has_public_rate and has_unblended_rate and has_ut and distinct_usage_types:
                placeholders = ", ".join(["?"] * len(distinct_usage_types))
                check = raw_con.execute(
                    f"""
                    SELECT DISTINCT line_item_usage_type,
                           pricing_public_on_demand_rate, line_item_unblended_rate
                    FROM t WHERE line_item_usage_type IN ({placeholders}) {lit_filter}
                    LIMIT 20
                    """,
                    distinct_usage_types,
                ).fetchall()
                for ut, pub_rate, unbl_rate in check:
                    try:
                        same = abs(float(pub_rate) - float(unbl_rate)) < 1e-9
                    except (TypeError, ValueError):
                        same = None
                    verdict = (
                        "genuine AWS on-demand list price (no discount on this line)"
                        if same else
                        "DISCOUNTED vs AWS's own public rate — comparing this to GCP's "
                        "undiscounted on-demand rate is not apples-to-apples; check if "
                        "pricing_model/line_item_type should route this through the "
                        "CUD-rate comparison instead"
                        if same is False else
                        "could not compare (missing/non-numeric rate fields)"
                    )
                    print(f"  {ut}: public_on_demand_rate={pub_rate}  "
                          f"unblended_rate={unbl_rate}  -> {verdict}")
            else:
                print("  (raw bill doesn't have both rate columns — skipping discount check)")
        except Exception as e:
            print(f"  (could not cross-check raw bill: {e})")
        finally:
            raw_con.close()
    else:
        print("\n(no input.csv/input.gz found in job_dir — skipping raw-bill cross-check)")

    print(
        "\n=== How to read this ===\n"
        "- If GCP cost >> AWS cost and the raw-bill check above says 'genuine AWS\n"
        "  on-demand list price', this is a real pricing-tier gap between the two\n"
        "  clouds for this exact instance shape/region — not a mapping bug.\n"
        "- If the raw-bill check flags a discount mismatch, the real story is AWS's\n"
        "  discounted/committed rate being compared to GCP's full on-demand rate —\n"
        "  look at whether this row's pricing_model/line_item_type should be\n"
        "  'Committed'/'DiscountedUsage' so the pipeline's CUD-rate fallback applies.\n"
        "- If GCP shows '** no mapping entry at all **', that's the mapping_coverage\n"
        "  gate's job to catch (see ensure_full_coverage.py) — flag it as a real gap."
    )


if __name__ == "__main__":
    main()
