#!/usr/bin/env python3
"""run_diag_summary.py <jobdir>

Aggregates projection-audit/diagnostics.jsonl into run_diagnostics.json and
prints a short summary to the job log. Internal engineering artifact — not read
by render_report.py, not shown to a customer.

ALWAYS exits 0. This is a reporting step, never a gate.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from diagnostics import read_all

NOT_EVALUATED_CODES = {"check_not_evaluated"}


def main():
    job_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    recs = read_all(job_dir)

    workload = 0.0
    try:
        import duckdb
        db = os.path.join(job_dir, "projection-audit", "projection.duckdb")
        if os.path.exists(db):
            con = duckdb.connect(db, read_only=True)
            try:
                workload = float(con.execute(
                    "SELECT COALESCE(SUM(aws_amortized_cost),0) "
                    "FROM aws_li_catalog WHERE is_workload"
                ).fetchone()[0] or 0.0)
            finally:
                con.close()
    except Exception as e:
        print("run_diag_summary: could not read workload total (%s)" % e)

    errors = [r for r in recs if r.get("level") == "error"]
    warns = [r for r in recs if r.get("level") == "warn"]
    error_usd = sum(float(r.get("usd") or 0) for r in errors)
    pct = (100.0 * error_usd / workload) if workload else 0.0
    not_evaluated = [r for r in recs if r.get("code") in NOT_EVALUATED_CODES]

    by_code = {}
    for r in errors + warns:
        k = r.get("code", "?")
        e = by_code.setdefault(k, {"level": r.get("level"), "count": 0,
                                   "rows": 0, "usd": 0.0, "sources": []})
        e["count"] += 1
        e["rows"] += int(r.get("rows") or 0)
        e["usd"] = round(e["usd"] + float(r.get("usd") or 0), 2)
        if r.get("source") and r["source"] not in e["sources"]:
            e["sources"].append(r["source"])

    summary = {
        "workload_usd": round(workload, 2),
        "error_usd": round(error_usd, 2),
        "error_pct_of_workload": round(pct, 2),
        "error_count": len(errors),
        "warn_count": len(warns),
        "checks_not_evaluated": len(not_evaluated),
        "by_code": by_code,
    }
    try:
        with open(os.path.join(job_dir, "run_diagnostics.json"), "w",
                  encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
    except Exception as e:
        print("run_diag_summary: could not write run_diagnostics.json (%s)" % e)

    print("run diagnostics: %d error, %d warn, %d check(s) not evaluated; "
          "$%s of $%s workload affected (%.2f%%)" % (
              len(errors), len(warns), len(not_evaluated),
              format(error_usd, ",.2f"), format(workload, ",.2f"), pct))
    for code, e in sorted(by_code.items(), key=lambda kv: -kv[1]["usd"]):
        print("  %-28s %-5s rows=%-6d usd=%-14s [%s]" % (
            code, e["level"], e["rows"], format(e["usd"], ",.2f"),
            ", ".join(e["sources"])))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("run_diag_summary: failed (%s) — continuing" % e)
        sys.exit(0)
