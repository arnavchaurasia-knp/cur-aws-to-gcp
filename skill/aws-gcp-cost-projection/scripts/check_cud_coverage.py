#!/usr/bin/env python3
"""
check_cud_coverage.py — CUD coverage completeness checker.

Verifies that all cud_eligible services declared in data/cud_pct.json have
Commit1Yr/Commit3Yr SKUs in data/catalog.duckdb.
If any eligible service lacks commit SKUs, raises completeness anomalies and
flags the result as partial so the lifecycle orchestrator can retry/escalate.
"""

from __future__ import annotations
import argparse
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_SCRIPTS_DIR)
_DATA_DIR = os.path.join(_SKILL_DIR, "data")


def check_cud_coverage(
    catalog_path: str | None = None,
    cud_pct_path: str | None = None,
    threshold: float = 100.0,
) -> dict:
    cat_file = catalog_path or os.path.join(_DATA_DIR, "catalog.duckdb")
    pct_file = cud_pct_path or os.path.join(_DATA_DIR, "cud_pct.json")

    # 1. Load cud_eligible list
    if not os.path.exists(pct_file):
        return {
            "source": "cud_coverage",
            "state": "failed",
            "reason": f"cud_pct.json not found at {pct_file}",
            "complete": False,
            "exit_code": 1,
            "anomalies": [f"Missing {pct_file}"],
        }

    try:
        with open(pct_file, "r", encoding="utf-8") as f:
            raw_pct = json.load(f)
        eligible = raw_pct.get("cud_eligible", [])
    except Exception as e:
        return {
            "source": "cud_coverage",
            "state": "failed",
            "reason": f"Failed to parse cud_pct.json: {e}",
            "complete": False,
            "exit_code": 1,
            "anomalies": [f"Corrupt {pct_file}: {e}"],
        }

    # 2. Check catalog.duckdb
    if not os.path.exists(cat_file):
        return {
            "source": "cud_coverage",
            "state": "failed",
            "reason": f"catalog.duckdb not found at {cat_file}",
            "complete": False,
            "expected_count": len(eligible),
            "fetched_count": 0,
            "coverage_pct": 0.0,
            "exit_code": 1,
            "anomalies": [f"Missing {cat_file}"],
        }

    try:
        import duckdb
        con = duckdb.connect(cat_file, read_only=True)
        try:
            total_commits = con.execute("""
                SELECT COUNT(*) FROM skus
                WHERE usage_type IN ('Commit1Yr', 'Commit3Yr')
            """).fetchone()[0]

            db_services = {r[0] for r in con.execute("""
                SELECT DISTINCT service_name FROM skus
                WHERE usage_type IN ('Commit1Yr', 'Commit3Yr')
            """).fetchall()}
        finally:
            con.close()
    except Exception as e:
        return {
            "source": "cud_coverage",
            "state": "failed",
            "reason": f"Failed to query catalog.duckdb: {e}",
            "complete": False,
            "expected_count": len(eligible),
            "fetched_count": 0,
            "coverage_pct": 0.0,
            "exit_code": 1,
            "anomalies": [f"Database error: {e}"],
        }

    # 3. Evaluate each eligible service
    item_outcomes: dict[str, str] = {}
    anomalies: list[str] = []

    for svc in eligible:
        # Check direct service_name match
        if svc in db_services:
            item_outcomes[svc] = "OK"
        else:
            item_outcomes[svc] = "MISSING"
            anomalies.append(f"Eligible service missing Commit1Yr/Commit3Yr SKUs: {svc}")

    expected_count = len(eligible)
    fetched_count = sum(1 for status in item_outcomes.values() if status == "OK")
    coverage_pct = round(100.0 * fetched_count / max(1, expected_count), 2)
    complete = (coverage_pct >= threshold and len(anomalies) == 0)

    state = "success" if complete else "partial"
    reason = ""
    if not complete:
        reason = (
            f"CUD coverage {coverage_pct}% is below {threshold}% threshold "
            f"({len(anomalies)} eligible service(s) missing commit SKUs in catalog)"
        )

    return {
        "source": "cud_coverage",
        "state": state,
        "reason": reason,
        "expected_count": expected_count,
        "fetched_count": fetched_count,
        "coverage_pct": coverage_pct,
        "complete": complete,
        "record_count": total_commits,
        "anomalies": anomalies,
        "item_outcomes": item_outcomes,
        "exit_code": 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Check GCP catalog CUD coverage completeness.")
    parser.add_argument("--catalog-path", help="Path to catalog.duckdb")
    parser.add_argument("--cud-pct-path", help="Path to cud_pct.json")
    parser.add_argument("--threshold", type=float, default=100.0, help="Completeness threshold percent")
    parser.add_argument("--json", action="store_true", help="Output JSON result")
    args = parser.parse_args()

    result = check_cud_coverage(
        catalog_path=args.catalog_path,
        cud_pct_path=args.cud_pct_path,
        threshold=args.threshold,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"CUD Coverage: {result['fetched_count']}/{result['expected_count']} "
              f"({result['coverage_pct']}%) — State: {result['state']}")
        if result["anomalies"]:
            print("Anomalies:")
            for a in result["anomalies"]:
                print(f"  - {a}")

    print(f"REFRESH_SUMMARY {json.dumps(result)}")

    sys.exit(0 if result["exit_code"] == 0 else 1)


if __name__ == "__main__":
    main()
