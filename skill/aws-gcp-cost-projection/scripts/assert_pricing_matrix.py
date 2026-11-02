#!/usr/bin/env python3
"""
assert_pricing_matrix.py — Soft diagnostic asserting post-fill rows align with data/pricing_matrix.json.

Phase 4 PostLLMScript:
Validates that active matrix entries (services and charge types declared as SKU,
FORMULA, PASS, or IGNORE) are honored in gcp_projection. Emits internal diagnostics
(`diag(WARN, 'matrix_mismatch', ...)`) when drift is detected.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from diagnostics import diag, WARN
except Exception:
    def diag(*args, **kwargs): pass
    WARN = "warn"

try:
    from aws_normalizer import canonical_service
except Exception:
    def canonical_service(product, product_code=None):
        return None

_SKILL_DIR = os.environ.get("SKILL_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_SKILL_DIR, "data")
_MATRIX_PATH = os.path.join(_DATA_DIR, "pricing_matrix.json")


def load_active_matrix(matrix_path: str = _MATRIX_PATH) -> List[dict]:
    """Load and return all entries with status='active' from pricing_matrix.json."""
    if not os.path.exists(matrix_path):
        return []
    with open(matrix_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    active = []
    for section, entries in data.items():
        if isinstance(entries, list):
            for entry in entries:
                if entry.get("status") == "active":
                    active.append(entry)
    return active


def get_expected_strategy(decision: str) -> set[str]:
    """Map matrix decision type to accepted gcp_projection strategies."""
    d = (decision or "").upper()
    if d in ("SKU", "FORMULA"):
        return {"map", "break_down"}
    elif d == "PASS":
        return {"passthrough"}
    elif d == "IGNORE":
        return {"ignore"}
    return set()


def _match_charge_type(canonical: str, charge_type: str, usage_type: str, operation: str, product: str) -> bool:
    """Best-effort match of a row against an active charge_type definition."""
    blob = f"{usage_type} {operation} {product}".lower()
    ct = charge_type.lower()

    if canonical == "kms":
        is_req = "request" in blob or "operation" in blob or "cryptographic" in blob
        if "request" in ct or "operation" in ct:
            return is_req
        if "key" in ct or "month" in ct:
            return not is_req
    elif canonical == "route53":
        is_zone = "hosted" in blob or "zone" in blob
        if "zone" in ct:
            return is_zone
        if "quer" in ct:
            return not is_zone
    elif canonical == "secretsmanager":
        is_req = "request" in blob or "api" in blob or "operation" in blob
        if "request" in ct or "access" in ct or "operation" in ct:
            return is_req
        if "secret" in ct or "month" in ct or "version" in ct:
            return not is_req
    elif canonical == "cloudwatch":
        if "dashboard" in ct:
            return "dashboard" in blob
        if "alarm" in ct:
            return "alarm" in blob
        if "log" in ct:
            return "log" in blob or "ingest" in blob
    elif canonical == "cloudtrail":
        if "management" in ct or "free" in ct:
            return "management" in blob or "free" in blob

    return False


def assert_pricing_matrix(db_path: str, matrix_path: str = _MATRIX_PATH) -> dict:
    """
    Check rows in DuckDB against active pricing matrix entries.
    Returns summary dict: {checked_rules, matched_rows, mismatches: [...]}.
    """
    import duckdb

    active_rules = load_active_matrix(matrix_path)
    if not active_rules or not os.path.exists(db_path):
        return {"checked_rules": len(active_rules), "matched_rows": 0, "mismatches": []}

    con = duckdb.connect(db_path, read_only=True)
    try:
        # Check if gcp_projection exists
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        table_name = "gcp_projection" if "gcp_projection" in tables else "aws_li_to_gcp_li"
        
        # Verify columns
        cols = {r[0] for r in con.execute(f"DESCRIBE {table_name}").fetchall()}
        has_cat = "aws_li_catalog" in tables

        q = f"""
            SELECT 
                p.aws_li_key,
                COALESCE(c.canonical_service, c.product, ''),
                COALESCE(c.usage_type, ''),
                COALESCE(c.operation, ''),
                COALESCE(c.product, ''),
                p.strategy,
                COALESCE(p.gcp_service, ''),
                COALESCE(p.gcp_sku_id, ''),
                COALESCE(c.aws_amortized_cost, 0.0),
                COALESCE(p.gcp_projected_cost, 0.0)
            FROM {table_name} p
            {"LEFT JOIN aws_li_catalog c USING (aws_li_key)" if has_cat else ""}
        """
        rows = con.execute(q).fetchall()
    finally:
        con.close()

    mismatches = []
    matched_rows = 0

    # Index active rules by canonical_service
    rules_by_service = defaultdict(list)
    for r in active_rules:
        rules_by_service[r["canonical_service"].lower()].append(r)

    for aws_li_key, cs_raw, ut, op, prod, strat, gcp_svc, sku_id, aws_cost, gcp_cost in rows:
        norm_cs = cs_raw.lower() if cs_raw else ""
        if not norm_cs or norm_cs not in rules_by_service:
            # Try normalizer
            resolved_cs = canonical_service(prod)
            if resolved_cs:
                norm_cs = resolved_cs.lower()

        if norm_cs not in rules_by_service:
            continue

        for rule in rules_by_service[norm_cs]:
            ct = rule.get("charge_type", "")
            if not _match_charge_type(norm_cs, ct, ut, op, prod):
                continue

            matched_rows += 1
            expected_strategies = get_expected_strategy(rule.get("decision", ""))
            actual_strategy = (strat or "").lower()

            if expected_strategies and actual_strategy not in expected_strategies:
                mismatch_record = {
                    "aws_li_key": aws_li_key,
                    "canonical_service": norm_cs,
                    "charge_type": ct,
                    "expected_decision": rule.get("decision"),
                    "expected_strategies": list(expected_strategies),
                    "actual_strategy": actual_strategy,
                    "product": prod,
                    "usage_type": ut,
                    "aws_cost": float(aws_cost),
                }
                mismatches.append(mismatch_record)
                diag(
                    WARN,
                    "matrix_mismatch",
                    "assert_pricing_matrix.py",
                    f"Pricing matrix mismatch for {norm_cs} ({ct}): expected {rule.get('decision')} "
                    f"({','.join(expected_strategies)}), got actual strategy '{actual_strategy}'",
                    rows=1,
                    usd=float(aws_cost),
                    detail=mismatch_record,
                )

    return {
        "checked_rules": len(active_rules),
        "matched_rows": matched_rows,
        "mismatches": mismatches,
    }


def main():
    job_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    db_path = os.path.join(job_dir, "projection-audit", "projection.duckdb")
    if not os.path.exists(db_path):
        db_path = os.path.join(job_dir, "projection.duckdb")

    if not os.path.exists(db_path):
        print(f"assert_pricing_matrix: DB not found at {db_path}; skipping.")
        sys.exit(0)

    res = assert_pricing_matrix(db_path)
    print(
        f"assert_pricing_matrix: {res['checked_rules']} active rules, "
        f"{res['matched_rows']} matched rows, {len(res['mismatches'])} mismatches."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()

