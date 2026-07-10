#!/usr/bin/env python3
"""
apply_commitment_ignores.py — Write ignore mappings for all commitment_discount rows.

These rows (RIFee, SavingsPlanRecurringFee, EdpDiscount) represent amortized
commitment costs already reflected in effective rates. Mapping them would
double-count. Write strategy='ignore' to the temp-file output for merge_mappings.py.

Usage:
    python3 apply_commitment_ignores.py <projection.duckdb>

Writes: projection-audit/mappings/commitment_discount_mappings.json
"""

import json, os, sys
import duckdb


def _safe_path(base: str, *parts: str) -> str:
    """Resolve path and verify it stays within base (path traversal guard)."""
    p = os.path.realpath(os.path.join(base, *parts))
    if not p.startswith(os.path.realpath(base) + os.sep) and p != os.path.realpath(base):
        raise ValueError(f"Path escapes base directory: {p}")
    return p


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    con = duckdb.connect(db_path)

    rows = con.execute("""
        SELECT aws_li_key
        FROM aws_li_catalog
        WHERE mechanic_group = 'commitment_discount'
    """).fetchall()
    con.close()

    mappings = [
        {"aws_li_key": r[0], "strategy": "ignore", "mapping_confidence": 1.0,
         "projection_note": "commitment_discount: amortized RI/SP/EDP cost, already in effective rates"}
        for r in rows
    ]

    out_dir = _safe_path(os.path.dirname(db_path), "mappings")
    os.makedirs(out_dir, exist_ok=True)
    out_path = _safe_path(out_dir, "commitment_discount_mappings.json")
    with open(out_path, "w") as f:
        json.dump(mappings, f, indent=2)

    print(f"Wrote {len(mappings)} ignore rows → {out_path}")


if __name__ == "__main__":
    main()
