#!/usr/bin/env python3
"""
classify_mechanics.py — stamp each row in aws_li_catalog with mechanic_group.

Usage:
    python3 classify_mechanics.py <projection.duckdb>

Rules applied in order (first match wins):
  1. compute_breakdown
  2. managed_db
  3. block_storage
  4. data_transfer
  5. flat_hourly
  6. per_request
  7. object_storage
  8. commitment_discount
  9. misc (fallback)

Exits 0 on success. Prints WARNING if misc > 15% of total aws_amortized_cost.
"""

import sys
import re
import duckdb

# ---------------------------------------------------------------------------
# Rule definitions — each rule is (group_name, test_fn).
# test_fn(row: dict) -> bool
# ---------------------------------------------------------------------------

def _ilike(value: str | None, pattern: str) -> bool:
    """Case-insensitive substring match (SQL ILIKE '%pattern%' semantics)."""
    if value is None:
        return False
    return pattern.lower() in value.lower()

def _re(value: str | None, pattern: str) -> bool:
    if value is None:
        return False
    return bool(re.search(pattern, value, re.IGNORECASE))

RULES = [
    (
        "compute_breakdown",
        lambda r: (
            (_ilike(r["product"], "Elastic Compute") or _ilike(r["product"], "EC2"))
            and _re(r["usage_type"], r"BoxUsage|SpotUsage|ReservedInstances")
            and r["unit"] in ("Hrs", "hours")
        ),
    ),
    (
        "managed_db",
        lambda r: (
            _ilike(r["product"], "RDS")
            or _ilike(r["product"], "Aurora")
            or _ilike(r["product"], "ElastiCache")
            or _ilike(r["product"], "DocumentDB")
            or _ilike(r["product"], "MemoryDB")
        ),
    ),
    (
        "block_storage",
        lambda r: (
            _ilike(r["product"], "Elastic Block")
            or _re(r["usage_type"], r"EBS:Volume|EBS:Snapshot|gp2|gp3|io1|io2|sc1|st1")
        ),
    ),
    (
        "data_transfer",
        lambda r: (
            _ilike(r["product"], "DataTransfer")
            or _re(r["usage_type"], r"DataTransfer|NatGateway-Bytes")
        ),
    ),
    (
        "flat_hourly",
        lambda r: (
            _re(r["usage_type"], r"LoadBalancerUsage|NatGateway-Hours|ElasticIP")
            and r["unit"] in ("Hrs", "hours")
        ),
    ),
    (
        "per_request",
        lambda r: (
            r["unit"] in ("Requests", "Lambda-GB-Second", "Count")
            or _re(r["usage_type"], r"Requests|Invocations")
        ),
    ),
    (
        "object_storage",
        lambda r: (
            _ilike(r["product"], "S3")
            and r["unit"] in ("GB-Mo", "GB Month")
            and _re(r["usage_type"], r"TimedStorage")
        ),
    ),
    (
        "commitment_discount",
        lambda r: (
            r["line_item_type"] in ("RIFee", "SavingsPlanRecurringFee", "EdpDiscount")
            or r["pricing_model"] in ("Reserved", "SavingsPlan")
        ),
    ),
]


def classify(row: dict) -> str:
    for group, test in RULES:
        try:
            if test(row):
                return group
        except Exception:
            pass
    return "misc"


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    con = duckdb.connect(db_path)

    # --- Check table exists ---
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "aws_li_catalog" not in tables:
        print("ERROR: table aws_li_catalog not found in database.", file=sys.stderr)
        sys.exit(1)

    # --- Add column if missing ---
    existing_cols = {
        r[1].lower()
        for r in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()
    }
    if "mechanic_group" not in existing_cols:
        con.execute("ALTER TABLE aws_li_catalog ADD COLUMN mechanic_group TEXT")
        print("Added column mechanic_group to aws_li_catalog.")

    # --- Load rows ---
    rows = con.execute(
        """
        SELECT
            aws_li_key,
            product,
            usage_type,
            operation,
            unit,
            line_item_type,
            pricing_model,
            aws_amortized_cost
        FROM aws_li_catalog
        """
    ).fetchall()

    col_names = [
        "aws_li_key", "product", "usage_type", "operation",
        "unit", "line_item_type", "pricing_model", "aws_amortized_cost",
    ]

    # --- Classify ---
    updates: list[tuple[str, str]] = []
    for raw in rows:
        row = dict(zip(col_names, raw))
        group = classify(row)
        updates.append((group, row["aws_li_key"]))

    # Bulk update
    con.executemany(
        "UPDATE aws_li_catalog SET mechanic_group = ? WHERE aws_li_key = ?",
        updates,
    )
    con.commit()

    # --- Breakdown report ---
    stats = con.execute(
        """
        SELECT
            mechanic_group,
            COUNT(*) AS row_count,
            COALESCE(SUM(aws_amortized_cost), 0) AS group_spend
        FROM aws_li_catalog
        GROUP BY mechanic_group
        ORDER BY group_spend DESC
        """
    ).fetchall()

    total_spend = sum(r[2] for r in stats)
    total_rows = sum(r[1] for r in stats)

    print(f"\n{'mechanic_group':<25} {'rows':>8}  {'% rows':>8}  {'spend_usd':>14}  {'% spend':>8}")
    print("-" * 70)
    misc_spend = 0.0
    misc_rows: list[dict] = []
    for group, row_count, group_spend in stats:
        pct_rows = 100.0 * row_count / total_rows if total_rows else 0.0
        pct_spend = 100.0 * group_spend / total_spend if total_spend else 0.0
        print(f"{group:<25} {row_count:>8}  {pct_rows:>7.1f}%  {group_spend:>14,.2f}  {pct_spend:>7.1f}%")
        if group == "misc":
            misc_spend = group_spend

    print("-" * 70)
    print(f"{'TOTAL':<25} {total_rows:>8}  {'100.0%':>8}  {total_spend:>14,.2f}  {'100.0%':>8}")

    # --- Gate: misc > 15% of total spend ---
    misc_pct = 100.0 * misc_spend / total_spend if total_spend else 0.0
    if misc_pct > 15.0:
        print(f"\nWARNING: misc group is {misc_pct:.1f}% of total spend (threshold: 15%).")
        print("Misc rows:")
        misc_detail = con.execute(
            """
            SELECT aws_li_key, product, usage_type, operation,
                   line_item_type, pricing_model, aws_amortized_cost
            FROM aws_li_catalog
            WHERE mechanic_group = 'misc'
            ORDER BY aws_amortized_cost DESC
            """
        ).fetchall()
        header = f"  {'aws_li_key':<34} {'product':<30} {'usage_type':<35} {'operation':<35} {'line_item_type':<25} {'pricing_model':<15} {'amortized_cost':>14}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for r in misc_detail:
            print(
                f"  {str(r[0]):<34} {str(r[1] or ''):<30} {str(r[2] or ''):<35} "
                f"{str(r[3] or ''):<35} {str(r[4] or ''):<25} {str(r[5] or ''):<15} {r[6]:>14,.2f}"
            )

    # --- Emit phase2_manifest.json alongside the DB ---
    import json, os, collections
    manifest: dict[str, list] = collections.defaultdict(list)
    row_data_full = con.execute(
        """
        SELECT
            aws_li_key, mechanic_group, product, usage_type, operation,
            unit, line_item_type, pricing_model,
            aws_amortized_cost, instance_type, instance_vcpus,
            instance_ram_gb, instance_arch, workload_class,
            billing_days, instance_count, aws_effective_unit_rate,
            region, gcp_region, is_workload
        FROM aws_li_catalog
        ORDER BY mechanic_group, aws_amortized_cost DESC
        """
    ).fetchall()
    full_cols = [
        "aws_li_key", "mechanic_group", "product", "usage_type", "operation",
        "unit", "line_item_type", "pricing_model",
        "aws_amortized_cost", "instance_type", "instance_vcpus",
        "instance_ram_gb", "instance_arch", "workload_class",
        "billing_days", "instance_count", "aws_effective_unit_rate",
        "region", "gcp_region", "is_workload",
    ]
    for raw in row_data_full:
        row = dict(zip(full_cols, raw))
        manifest[row["mechanic_group"]].append(row)

    # Skip groups that need no LLM work — commitment_discount is always ignore
    skip_groups = {"commitment_discount"}
    manifest_out = {
        g: {"rows": rows, "row_count": len(rows),
            "total_spend": sum(r.get("aws_amortized_cost") or 0 for r in rows),
            "needs_llm": g not in skip_groups}
        for g, rows in manifest.items()
    }

    manifest_path = os.path.join(os.path.dirname(db_path), "phase2_manifest.json")
    with open(manifest_path, "w") as fh:
        json.dump(manifest_out, fh, indent=2, default=str)
    print(f"\nWrote {manifest_path}  ({len(manifest_out)} groups)")

    con.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
