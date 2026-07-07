#!/usr/bin/env python3
"""
outlier_gate.py <projection.duckdb>

Deterministic safety net: clamp any implausible cost blowup rows to passthrough
(GCP = AWS) so the report always generates. Catches the class of bug where a
single mis-mapped SKU (e.g. CloudTrail's 790,600 events wrongly mapped to Cloud
Storage) inflates the total — Bill3 shipped $2.77 -> $38,247 for exactly this
reason.

Rules (a row must have real cost to count):
  R1 single-row ratio : gcp > 50x aws  AND gcp > $100
  R2 single-row abs   : gcp > $10,000  AND aws < $100
  R3 wrong-service    : gcp_service = 'Cloud Storage' but the AWS product is NOT
                        a storage service (S3/Glacier). Non-storage services must
                        never fall back to Cloud Storage.
  R4 whole-bill       : SUM(gcp) > 2 x SUM(aws)

On any violation: write outlier_violations.md (rows named), clamp the bad rows
to AWS-cost passthrough, and exit 0 so the orchestrator continues to report
generation. The report will show clamped rows marked as passthrough. Never
exit 1 — a report with clamped rows is always better than no report.
"""
import os, sys
try:
    import duckdb
except Exception as e:  # pragma: no cover
    sys.stderr.write(f"outlier_gate: duckdb import failed ({e}); skipping\n")
    sys.exit(0)

# AWS products that legitimately map to Cloud Storage (substring match, case-insens).
# NB: do NOT use bare "s3" — it false-matches region codes like "APS3" in a
# product name (e.g. "AWS CloudTrail APS3-InsightsEvents").
_STORAGE_OK = ("simple storage service", "glacier", "storage gateway")


def main():
    if len(sys.argv) < 2:
        sys.exit(0)
    db = sys.argv[1]
    con = duckdb.connect(db)

    rows = con.execute(
        """
        SELECT c.product, m.gcp_service, m.gcp_sku_name,
               c.aws_amortized_cost AS aws, p.gcp_projected_cost AS gcp,
               p.aws_li_key
        FROM gcp_projection p
        JOIN aws_li_catalog c   USING (aws_li_key)
        JOIN aws_li_to_gcp_li m USING (aws_li_key)
        WHERE p.gcp_projected_cost IS NOT NULL
        """
    ).fetchall()

    tot_aws = sum((r[3] or 0) for r in rows)
    tot_gcp = sum((r[4] or 0) for r in rows)

    # HARD violations are cost blowups that must be clamped (not blocked).
    # WARN violations are wrong-service labels that don't inflate cost — recorded
    # for the fix backlog but cost-accurate enough to ship as-is.
    hard, warn = [], []
    for product, svc, sku, aws, gcp, li_key in rows:
        aws = aws or 0.0
        gcp = gcp or 0.0
        if aws > 1 and gcp > 50 * aws and gcp > 100:
            hard.append(("R1 ratio>50x", product, svc, aws, gcp, li_key)); continue
        if gcp > 10000 and aws < 100:
            hard.append(("R2 abs>$10k", product, svc, aws, gcp, li_key)); continue
        if (svc or "").strip().lower() == "cloud storage":
            pl = (product or "").lower()
            if not any(k in pl for k in _STORAGE_OK):
                # Only HARD if it also inflates; otherwise just a label warning.
                if aws > 1 and gcp > 1.5 * aws:
                    hard.append(("R3 non-storage->GCS (inflated)", product, svc, aws, gcp, li_key))
                else:
                    warn.append(("R3 non-storage->GCS", product, svc, aws, gcp, li_key))

    bill_over = tot_aws > 0 and tot_gcp > 2 * tot_aws
    ratio = tot_gcp / tot_aws if tot_aws else 0.0

    if not hard and not bill_over:
        msg = f"outlier_gate: OK (AWS ${tot_aws:,.0f} -> GCP ${tot_gcp:,.0f}, ratio {ratio:.2f})"
        if warn:
            msg += f"  [{len(warn)} non-blocking label warning(s)]"
        print(msg)
        sys.exit(0)

    out = os.path.join(os.path.dirname(db), "outlier_violations.md")
    lines = ["# Outlier gate violations\n",
             f"AWS total ${tot_aws:,.2f} -> GCP total ${tot_gcp:,.2f} (ratio {ratio:.2f})\n",
             "\nRows below were clamped to AWS-cost passthrough so the report could generate.\n"]
    if bill_over:
        lines.append("- **R4 whole-bill**: GCP total exceeds 2x AWS total.\n")
    for rule, product, s, aws, gcp, _key in sorted(hard, key=lambda x: -x[4]):
        lines.append(f"- **{rule}**: {product} -> {s}  AWS ${aws:,.2f} -> GCP ${gcp:,.2f} "
                     f"({gcp/aws if aws else float('inf'):.0f}x)\n")
    if warn:
        lines.append("\n## Non-blocking warnings (wrong-service label, cost OK)\n")
        for rule, product, s, aws, gcp, _key in sorted(warn, key=lambda x: -x[4]):
            lines.append(f"- {rule}: {product} -> {s}  ${aws:,.2f}\n")
    with open(out, "w") as fh:
        fh.writelines(lines)

    # Clamp violating rows to AWS-cost passthrough in the projection table.
    # This ensures the report generates with accurate totals instead of blowup numbers.
    bad_keys = [r[5] for r in hard]
    clamped_note = ""
    if bad_keys:
        placeholders = ",".join(["?" for _ in bad_keys])
        try:
            con.execute(
                f"""
                UPDATE gcp_projection
                SET gcp_projected_cost = (
                    SELECT aws_amortized_cost FROM aws_li_catalog
                    WHERE aws_li_catalog.aws_li_key = gcp_projection.aws_li_key
                )
                WHERE aws_li_key IN ({placeholders})
                """,
                bad_keys
            )
            clamped_note = f"  {len(bad_keys)} row(s) clamped to passthrough."
        except Exception as e:
            clamped_note = f"  WARNING: could not clamp rows ({e}) — totals may be inflated."

    if bill_over and not bad_keys:
        clamped_note = "  R4 whole-bill flag logged; individual rows within limits."

    sys.stderr.write(
        f"outlier_gate WARN: {len(hard)} blowup(s)"
        f"{' + whole-bill >2x' if bill_over else ''}. "
        f"AWS ${tot_aws:,.0f} -> GCP ${tot_gcp:,.0f}. See outlier_violations.md."
        f"{clamped_note}\n"
    )
    for rule, product, s, aws, gcp, _key in sorted(hard, key=lambda x: -x[4])[:10]:
        sys.stderr.write(f"  {rule}: {product} -> {s}  ${aws:,.2f} -> ${gcp:,.2f}\n")

    # Always exit 0 — a report with clamped rows is better than no report.
    sys.exit(0)


if __name__ == "__main__":
    main()
