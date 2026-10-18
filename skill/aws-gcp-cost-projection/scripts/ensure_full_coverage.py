#!/usr/bin/env python3
"""
ensure_full_coverage.py <projection.duckdb>

Deterministic backstop for the mapping_coverage gate. Runs ONLY after Phase 2's
normal LLM pass + one retry have both had a chance to map every row — it is the
last resort, not a substitute for real mapping.

Why this exists
----------------
Phase 2 asks the LLM to map three groups (compute_breakdown, managed_db, misc).
On a large bill's misc group, the LLM can drop rows — token budget, an off-by-one
in its own bookkeeping, or simply missing a line in a long response. Before this
script existed, ANY row left unmapped after the retry caused the entire report
to fail with no report at all, even when 99%+ of the bill's spend was mapped
correctly. That is disproportionate: an honest passthrough for a handful of
leftover rows is a far better outcome than no report.

What it does
------------
Finds every aws_li_catalog row with no corresponding aws_li_to_gcp_li row and
inserts ONE passthrough mapping for it: AWS cost carried forward 1:1 (never an
invented GCP figure), clearly labeled "Manual Review — Unmapped" so it is
visible in the report and easy to audit, never silently blended into a
confident-looking number.

This mirrors the existing precedent in this pipeline (service_classifier.py's
"review" mode, fix_storage_misroute.py's backstop) — passthrough-and-flag is
the established honest fallback here, not a new pattern.

Idempotent. Never fatal — prints what it backstopped and exits 0.
"""
import json
import os
import sys

try:
    import duckdb
except Exception as e:  # pragma: no cover
    sys.stderr.write(f"ensure_full_coverage: duckdb import failed ({e}); skipping\n")
    sys.exit(0)

try:
    from diagnostics import diag, ERROR
except ImportError:
    def diag(*a, **k): pass
    ERROR = "error"

NOTE = "[backstop] LLM left this row unmapped after retry — carried at AWS cost for manual review (ensure_full_coverage.py)"

# commitment_discount/negative_cost rows are NOT ordinary unmapped rows: their
# whole point is strategy='ignore' (apply_commitment_ignores.py's own docstring:
# "amortized costs already reflected in effective rates — mapping would
# double-count"). If apply_commitment_ignores.py ever misses one (e.g. a job
# resumed from a checkpoint captured before it ran), backstopping it as a normal
# passthrough would carry its full AWS cost onto the GCP side too — inflating
# the GCP total for a cost that's already baked into other rows' rates, and
# desyncing from render_report.py's AWS-side total (which deliberately excludes
# commitment_discount to avoid the same double-count). Ignore, not passthrough.
_IGNORE_GROUPS = ("commitment_discount", "negative_cost")


def _is_ignore(is_workload, mechanic_group, cost):
    # Authoritative: non-workload cost never lands on the GCP side.
    if not bool(is_workload):
        return True
    # Belt-and-suspenders: known ignore groups even if is_workload got stamped true.
    if mechanic_group in _IGNORE_GROUPS:
        return True
    # A negative cost (refund/credit) mis-grouped into misc must not become a
    # negative GCP passthrough that silently offsets the total.
    if (cost or 0) < 0:
        return True
    return False


def main():
    if len(sys.argv) < 2:
        sys.exit(0)
    db = sys.argv[1]

    try:
        con = duckdb.connect(db)

        rows = con.execute("""
            SELECT c.aws_li_key, c.gcp_region, c.is_workload, c.aws_amortized_cost,
                   c.product, c.mechanic_group
            FROM aws_li_catalog c
            WHERE NOT EXISTS (SELECT 1 FROM aws_li_to_gcp_li m WHERE m.aws_li_key = c.aws_li_key)
        """).fetchall()

        if not rows:
            print("ensure_full_coverage: no gaps found, nothing to do")
            sys.exit(0)

        records = [
            (
                aws_li_key,
                "Non-workload / Commitment (ignored)" if _is_ignore(is_workload, mechanic_group, cost) else "Manual Review — Unmapped",
                None, None, None,
                "ignore" if _is_ignore(is_workload, mechanic_group, cost) else "passthrough",
                1.0, gcp_region, NOTE, 0.10, bool(is_workload), False,
                None if _is_ignore(is_workload, mechanic_group, cost) else "llm_dropped",
                "ensure_full_coverage.py",
            )
            for (aws_li_key, gcp_region, is_workload, cost, product, mechanic_group) in rows
        ]
        insert_ok = False
        try:
            con.executemany(
                """
                INSERT INTO aws_li_to_gcp_li
                    (aws_li_key, gcp_service, gcp_sku_id, gcp_sku_name, gcp_sku_unit,
                     strategy, unit_multiplier, gcp_region, projection_note,
                     mapping_confidence, is_workload, break_down,
                     passthrough_reason, decided_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
            con.commit()
            insert_ok = True
        except Exception as _insert_err:
            sys.stderr.write(f"ensure_full_coverage: non-fatal error ({_insert_err}); skipping DB INSERT\n")

        total_cost = sum(r[3] or 0 for r in rows)
        ignored_n = sum(1 for r in rows if _is_ignore(r[2], r[5], r[3]))
        zero_n = sum(1 for r in rows if (r[3] or 0) == 0)
        passthrough_n = len(rows) - ignored_n

        if rows:
            diag(ERROR, "rows_unmapped_after_retry", "ensure_full_coverage.py",
                 f"{len(rows)} row(s) had no mapping after the Phase-2 "
                 f"retry; carried at AWS cost",
                 rows=len(rows), usd=total_cost, phase=2)

        if insert_ok:
            print(f"ensure_full_coverage: backstopped {len(rows)} row(s) totaling "
                  f"${total_cost:,.2f} ({ignored_n} as ignore/commitment, "
                  f"{passthrough_n} as passthrough/Manual Review; {zero_n} zero-cost) — loud, not silent:")
            for aws_li_key, _region, is_workload, cost, product, mechanic_group in sorted(rows, key=lambda r: -(r[3] or 0))[:20]:
                tag = "ignore" if _is_ignore(is_workload, mechanic_group, cost) else "passthrough"
                print(f"  {aws_li_key[:12]}  ${cost or 0:>10,.2f}  [{tag}]  {product}")
        else:
            print(f"ensure_full_coverage: INSERT FAILED — {len(rows)} row(s) totaling ${total_cost:,.2f} "
                  f"remain UNMAPPED; coverage gate will flag them")

        # FIX-3, FIX-5, FIX-6: Surface backstopped rows in data_gaps.json next to the DB
        try:
            gaps_path = os.path.join(os.path.dirname(os.path.abspath(db)), "data_gaps.json")
            existing_data = None
            existing_rows = []
            if os.path.exists(gaps_path):
                with open(gaps_path, "r", encoding="utf-8") as _gf:
                    existing_data = json.load(_gf)
                if isinstance(existing_data, dict):
                    existing_rows = existing_data.get("rows", [])
                elif isinstance(existing_data, list):
                    existing_rows = existing_data

            existing_by_key = {
                r.get("aws_li_key"): r for r in existing_rows
                if isinstance(r, dict) and "aws_li_key" in r
            }
            # Fallback identity ONLY for legacy/foreign rows that lack aws_li_key
            # (e.g. a data_gaps.json written by an older classify_mechanics before it
            # stamped the key). classify_mechanics now includes aws_li_key, so the
            # common path de-duplicates exactly by key above; restricting this
            # (product, cost) fallback to KEYLESS rows prevents a false-positive dedup
            # between two genuinely different rows that happen to share (product, cost).
            existing_product_cost = {
                (r.get("product"), round(float(r.get("cost") or 0), 2))
                for r in existing_rows
                if isinstance(r, dict) and "aws_li_key" not in r
                and "product" in r and "cost" in r
            }

            for aws_li_key, _region, is_workload, cost, product, mechanic_group in rows:
                c_round = round(cost or 0.0, 2)
                if not insert_ok:
                    resolution = "INSERT_FAILED"
                else:
                    resolution = "ignore" if _is_ignore(is_workload, mechanic_group, cost) else "passthrough"

                prior = existing_by_key.get(aws_li_key)
                if prior is not None:
                    # Already recorded. If an earlier run marked it INSERT_FAILED and
                    # this run actually committed the mapping, refresh the stale
                    # resolution so the data-gap banner doesn't keep over-reporting it.
                    if insert_ok and prior.get("resolution") == "INSERT_FAILED":
                        prior["resolution"] = resolution
                    continue
                if (product, c_round) in existing_product_cost:
                    continue

                new_row = {
                    "aws_li_key": aws_li_key,
                    "product": product,
                    "cost": c_round,
                    "mechanic_group": mechanic_group,
                    "reason": "LLM left row unmapped after Phase 2 retry",
                    "resolution": resolution,
                    "source": "ensure_full_coverage.py",
                }
                existing_rows.append(new_row)
                existing_by_key[aws_li_key] = new_row
                existing_product_cost.add((product, c_round))

            if isinstance(existing_data, list):
                out = existing_rows
            else:
                # FIX-6a: Exclude "ignore" rows from banner count and total_cost
                active_rows = [
                    r for r in existing_rows
                    if isinstance(r, dict) and r.get("resolution") != "ignore"
                ]
                out = {
                    "count": len(active_rows),
                    "total_cost": round(sum(r.get("cost", 0.0) for r in active_rows), 2),
                    "rows": existing_rows,
                }
            with open(gaps_path, "w", encoding="utf-8") as _gf:
                json.dump(out, _gf, indent=2)
        except Exception as _ge:
            sys.stderr.write(f"ensure_full_coverage: warning: could not write data_gaps.json ({_ge})\n")

    except Exception as e:
        sys.stderr.write(f"ensure_full_coverage: non-fatal error ({e}); skipping\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
