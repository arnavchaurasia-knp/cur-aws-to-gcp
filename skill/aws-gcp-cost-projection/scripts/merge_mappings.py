#!/usr/bin/env python3
"""
merge_mappings.py — Bulk-INSERT all mechanic-group mapping temp files into
aws_li_to_gcp_li in one transaction.

Usage:
    python3 merge_mappings.py <projection.duckdb> <mappings_dir>

The mappings_dir must contain one or more <group>_mappings.json files, each
a JSON array of mapping objects matching the aws_li_to_gcp_li schema.

Exits 0 on success. Exits 1 if any expected group file is missing — the
orchestrator should retry the missing group's agent before merging.
"""

import json
import os
import sys
import duckdb

# Columns that can appear in a mapping JSON object.
# Order matches the INSERT statement below.
COLUMNS = [
    "aws_li_key",
    "gcp_service",
    "gcp_sku_id",
    "gcp_sku_name",
    "component",
    "strategy",
    "unit_multiplier",
    "gcp_region",
    "projection_note",
    "mapping_confidence",
    "is_workload",
    "break_down",
]


def load_mappings(mappings_dir: str) -> tuple[list[dict], list[str]]:
    """Return (all_rows, missing_groups).

    missing_groups is non-empty when the manifest lists a group but its
    *_mappings.json file does not exist yet (agent didn't finish).
    """
    # Discover which groups were dispatched via the manifest
    manifest_path = os.path.join(os.path.dirname(mappings_dir.rstrip("/")), "phase2_manifest.json")
    expected_groups: list[str] = []
    if os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path))
        expected_groups = [g for g, meta in manifest.items() if meta.get("needs_llm", True)]

    all_rows: list[dict] = []
    missing: list[str] = []

    # Load all *_mappings.json files present
    found_files = sorted(f for f in os.listdir(mappings_dir) if f.endswith("_mappings.json"))
    for fname in found_files:
        path = os.path.join(mappings_dir, fname)
        try:
            rows = json.load(open(path))
            if not isinstance(rows, list):
                print(f"WARNING: {fname} is not a JSON array — skipping", file=sys.stderr)
                continue
            all_rows.extend(rows)
        except Exception as e:
            print(f"WARNING: could not read {fname}: {e}", file=sys.stderr)

    # Check for groups expected but absent
    found_groups = {f.replace("_mappings.json", "") for f in found_files}
    for g in expected_groups:
        if g not in found_groups:
            missing.append(g)

    return all_rows, missing


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS aws_li_to_gcp_li (
            aws_li_key          TEXT,
            gcp_service         TEXT,
            gcp_sku_id          TEXT,
            gcp_sku_name        TEXT,
            component           TEXT,
            strategy            TEXT,
            unit_multiplier     DOUBLE,
            gcp_region          TEXT,
            projection_note     TEXT,
            mapping_confidence  DOUBLE,
            is_workload         BOOLEAN,
            break_down          BOOLEAN
        )
    """)


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <projection.duckdb> <mappings_dir>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    mappings_dir = sys.argv[2]

    if not os.path.exists(mappings_dir):
        print(f"ERROR: mappings dir not found: {mappings_dir}", file=sys.stderr)
        sys.exit(1)

    rows, missing = load_mappings(mappings_dir)

    if missing:
        print(f"ERROR: {len(missing)} group(s) have no mapping file — cannot merge:", file=sys.stderr)
        for g in missing:
            print(f"  missing: {g}_mappings.json", file=sys.stderr)
        print("Re-run failed agents, then retry merge_mappings.py.", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("WARNING: no mapping rows found in any file — aws_li_to_gcp_li will be empty.")

    con = duckdb.connect(db_path)
    ensure_table(con)

    # Truncate existing mappings so re-runs are idempotent
    con.execute("DELETE FROM aws_li_to_gcp_li")

    # Build tuples in column order
    records = []
    for r in rows:
        records.append(tuple(r.get(col) for col in COLUMNS))

    placeholders = ", ".join(["?"] * len(COLUMNS))
    col_list = ", ".join(COLUMNS)
    con.executemany(
        f"INSERT INTO aws_li_to_gcp_li ({col_list}) VALUES ({placeholders})",
        records,
    )
    con.commit()

    # Per-group summary
    stats = con.execute("""
        SELECT
            m.mechanic_group,
            COUNT(*) AS mapped_rows
        FROM aws_li_to_gcp_li li
        JOIN aws_li_catalog m USING (aws_li_key)
        GROUP BY m.mechanic_group
        ORDER BY mapped_rows DESC
    """).fetchall()

    total = sum(r[1] for r in stats)
    print(f"\n{'mechanic_group':<25}  {'mapped_rows':>12}")
    print("-" * 40)
    for group, cnt in stats:
        print(f"{group or 'unknown':<25}  {cnt:>12}")
    print("-" * 40)
    print(f"{'TOTAL':<25}  {total:>12}")

    con.close()
    print(f"\nMerge complete. {total} rows inserted into aws_li_to_gcp_li.")
    sys.exit(0)


if __name__ == "__main__":
    main()
