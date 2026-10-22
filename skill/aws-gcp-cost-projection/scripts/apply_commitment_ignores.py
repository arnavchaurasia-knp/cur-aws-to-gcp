#!/usr/bin/env python3
"""
apply_commitment_ignores.py — Write ignore mappings for commitment_discount and negative_cost rows.

commitment_discount: RIFee, SavingsPlanRecurringFee, EdpDiscount — amortized costs
already reflected in effective rates. Mapping would double-count.

negative_cost: credits, refunds, RI/SP negations — AWS-specific billing mechanics
with no GCP equivalent. Ignored so they don't consume LLM tokens.

Usage:
    python3 apply_commitment_ignores.py <projection.duckdb>

Writes: projection-audit/mappings/commitment_discount_mappings.json
        projection-audit/mappings/negative_cost_mappings.json
"""

import json, os, sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import duckdb
try:
    from diagnostics import diag, WARN
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    def diag(*a, **k): pass
    WARN = "warn"


def _safe_path(base: str, *parts: str) -> str:
    """Resolve path and verify it stays within base (path traversal guard)."""
    p = os.path.realpath(os.path.join(base, *parts))
    if not p.startswith(os.path.realpath(base) + os.sep) and p != os.path.realpath(base):
        raise ValueError(f"Path escapes base directory: {p}")
    return p


def process_database(db_path: str) -> tuple[int, int]:
    """Process DB and write commitment and negative cost ignore mappings.

    Returns:
        (num_commitment_mappings, num_negative_mappings)
    """
    con = duckdb.connect(db_path)

    cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
    if "mechanic_group" not in cols:
        return (0, 0)

    product_col = "product" if "product" in cols else "NULL AS product"
    product_code_col = "product_code" if "product_code" in cols else "NULL AS product_code"
    cost_col = "COALESCE(aws_amortized_cost, 0.0)" if "aws_amortized_cost" in cols else "0.0 AS aws_amortized_cost"
    line_item_type_col = "line_item_type" if "line_item_type" in cols else "NULL AS line_item_type"

    commitment_rows = con.execute(f"""
        SELECT aws_li_key, {product_col}, {product_code_col}, {cost_col}
        FROM aws_li_catalog
        WHERE mechanic_group = 'commitment_discount'
    """).fetchall()

    negative_rows = con.execute(f"""
        SELECT aws_li_key, {cost_col}, {line_item_type_col}, {product_col}, {product_code_col}
        FROM aws_li_catalog
        WHERE mechanic_group = 'negative_cost'
    """).fetchall()

    con.close()

    out_dir = _safe_path(os.path.dirname(db_path), "mappings")
    os.makedirs(out_dir, exist_ok=True)

    commitment_mappings = [
        {
            "aws_li_key": r[0],
            "strategy": "ignore",
            "mapping_confidence": 1.0,
            "projection_note": "commitment_discount: amortized RI/SP/EDP cost, already in effective rates",
            "aws_service": r[1],
            "aws_product_code": r[2],
            "aws_amortized_cost": float(r[3] or 0.0),
        }
        for r in commitment_rows
    ]
    out_path_comm = _safe_path(out_dir, "commitment_discount_mappings.json")
    with open(out_path_comm, "w", encoding="utf-8") as f:
        json.dump(commitment_mappings, f, indent=2)
    print(f"Wrote {len(commitment_mappings)} commitment_discount ignore rows → {out_path_comm}")

    # Diag: catalog has commitment_discount rows but mappings file is empty
    commitment_cost = sum(float(r[3] or 0.0) for r in commitment_rows)
    if len(commitment_rows) > 0 and len(commitment_mappings) == 0:
        diag(WARN, "commitment_rows_unclaimed", "apply_commitment_ignores.py",
             f"{len(commitment_rows)} commitment_discount row(s) (${commitment_cost:,.2f}) have no mapping",
             rows=len(commitment_rows), usd=commitment_cost,
             detail={"mechanic_group": "commitment_discount", "catalog_rows": len(commitment_rows), "mapped_rows": 0})

    negative_mappings = []
    for r in negative_rows:
        cost_val = float(r[1] or 0.0)
        lit_val = r[2] or "unknown type"
        negative_mappings.append({
            "aws_li_key": r[0],
            "strategy": "ignore",
            "mapping_confidence": 1.0,
            "projection_note": f"negative_cost: credit/refund/negation (${cost_val:.2f}, {lit_val}) — no GCP equivalent",
            "aws_service": r[3],
            "aws_product_code": r[4],
            "aws_amortized_cost": cost_val,
        })

    out_path_neg = _safe_path(out_dir, "negative_cost_mappings.json")
    with open(out_path_neg, "w", encoding="utf-8") as f:
        json.dump(negative_mappings, f, indent=2)
    print(f"Wrote {len(negative_mappings)} negative_cost ignore rows → {out_path_neg}")

    return len(commitment_mappings), len(negative_mappings)


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    process_database(db_path)


if __name__ == "__main__":
    main()
