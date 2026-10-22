#!/usr/bin/env python3
"""build_corpus.py — build an audit corpus of FULLY PRICED job DBs, with no LLM.

Why this exists
---------------
The audit corpus used by spec_parity.py and the ad-hoc audits contained job DBs
with only `aws_li_catalog` — ingest output and nothing else. No
`aws_li_to_gcp_li`, no `gcp_projection`. That corpus can measure classification
parity and nothing else: every question about *dollars* (is this SKU right, is
this ratio plausible, did this fee get deduped across months) had no evidence
base, and had to be answered by reading code.

Most of the pipeline is deterministic. Per internal/jobs/orchestrate.go, agy is
spawned for only three LLM groups in Phase 2; everything before it
(classify_mechanics, apply_commitment_ignores, apply_static_mappings) and
everything after it (merge_mappings, service_classifier, apply_rates) is plain
Python. merge_mappings inserts whatever group files exist, so skipping agy
simply leaves the LLM groups unmapped rather than breaking the chain.

Measured on one corpus bill: 94.1% of workload spend is mapped and priced with
no LLM at all, offline, in seconds.

What you get, and what you do not
---------------------------------
You get real `aws_li_to_gcp_li` and `gcp_projection` tables for everything the
deterministic layer claims — which is the layer nearly every work order touches.

You do NOT get the LLM-routed remainder (the `misc` group and friends). Those
rows exist in `aws_li_catalog` with no mapping. **This is a known, deliberate
hole, not a failure.** Any audit built on this corpus must report coverage
alongside its result, or it will quietly understate. `coverage_report()` below
exists so that number is always at hand.

Usage
-----
    # build from raw bill files (they are only read, never written to)
    python scripts/build_corpus.py --bills <dir-of-bills> --out <corpus-dir>

    # re-run the deterministic chain over job dirs that already have ingest output
    python scripts/build_corpus.py --from-jobs <corpus-dir>

    # coverage only, no rebuild
    python scripts/build_corpus.py --from-jobs <corpus-dir> --report-only
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))

# The deterministic chain, in orchestrator order. Each entry is
# (script, args_template, needs_db_arg). $DB and $MAPPINGS are substituted.
# Every one of these runs with cwd set to the job directory, because
# ingest.py derives JOB_DIR from os.getcwd() and apply_rates.py takes no
# arguments at all and resolves the DB relative to cwd.
# Mirrors internal/jobs/orchestrate.go phases 1-5, minus the agy/LLM calls.
# Keep it in that order and keep it in sync: a chain that stops after
# apply_rates measures a pipeline nobody runs. validate_fix.py in particular
# applies autofixes that MOVE DOLLARS (regional-SKU repair, CUD synthesis,
# map-with-no-rate downgrade, echo clearing), and reconcile_capacity.py
# enforces the never-underprovision guarantee -- both were missing from an
# earlier version of this file, so every ratio measured with it was pre-autofix.
CHAIN = [
    # Phase 1 - Ingestion
    ("ingest.py", []),
    ("aws_normalizer.py", ["$DB"]),
    ("resolve_unknown_product_codes.py", ["$DB"]),
    # Phase 2 - Mapping (pre-LLM deterministic)
    ("classify_mechanics.py", ["$DB"]),
    ("apply_commitment_ignores.py", ["$DB"]),
    ("apply_static_mappings.py", ["$DB"]),
    # Phase 2 - post-merge deterministic passes
    ("merge_mappings.py", ["$DB", "$MAPPINGS"]),
    ("service_classifier.py", ["$DB"]),
    ("fix_storage_misroute.py", ["$DB"]),
    ("calibrate_confidence.py", ["$DB"]),
    ("reconcile_capacity.py", ["$DB"]),
    ("verify_golden_mappings.py", ["$DB"]),
    # Phase 4 - Rate-card fill
    ("ensure_catalog_coverage.py", []),
    ("apply_rates.py", []),
    ("validate_fix.py", ["$JOBDIR"]),
    ("render_notes.py", ["$DB"]),
    # Phase 5 - Outlier triage (deterministic parts only)
    ("detect_outliers.py", []),
    ("incremental_rerate.py", []),
]

# Steps that are allowed to fail without aborting the bill. These mirror the
# "?" prefix the orchestrator uses for soft scripts.
SOFT = {
    "aws_normalizer.py",
    "resolve_unknown_product_codes.py",
    "apply_commitment_ignores.py",
    "apply_static_mappings.py",
    "merge_mappings.py",
    "service_classifier.py",
    "fix_storage_misroute.py",
    "calibrate_confidence.py",
    "reconcile_capacity.py",
    "verify_golden_mappings.py",
    "ensure_catalog_coverage.py",
    "validate_fix.py",
    "render_notes.py",
    "detect_outliers.py",
    "incremental_rerate.py",
}


def run_chain(job_dir, skip_ingest=False, verbose=False):
    """Run the deterministic pipeline in job_dir. Returns list of failed steps."""
    db = os.path.join(job_dir, "projection-audit", "projection.duckdb")
    mappings = os.path.join(job_dir, "projection-audit", "mappings")
    failed = []

    for script, args in CHAIN:
        if skip_ingest and script == "ingest.py":
            continue
        path = os.path.join(SCRIPTS, script)
        if not os.path.exists(path):
            continue
        argv = [sys.executable, path] + [
            a.replace("$DB", db).replace("$MAPPINGS", mappings)
             .replace("$JOBDIR", job_dir) for a in args
        ]
        # SKILL_DIR must be set exactly as the Go spawner sets it
        # (internal/jobs/spawner.go, skillEnv). ingest.py resolves DATA_DIR as
        # os.environ.get("SKILL_DIR", "") + "/data" with no __file__ fallback,
        # so without it ingest silently loads an EMPTY instance-spec table and
        # every instance type comes out with NULL vcpus/ram. That looks like a
        # product defect in any audit built on this corpus; it is not.
        env = dict(os.environ)
        env["SKILL_DIR"] = os.path.dirname(SCRIPTS)
        proc = subprocess.run(
            argv, cwd=job_dir, capture_output=True, text=True,
            errors="replace", env=env
        )
        if proc.returncode != 0:
            failed.append(script)
            if script not in SOFT:
                print(f"    HARD FAIL {script}: {proc.stderr.strip()[-300:]}")
                break
            if verbose:
                print(f"    soft fail {script}")
        elif verbose:
            print(f"    ok {script}")
    return failed


def coverage_report(corpus_dir):
    """Per-bill and total coverage: how much workload spend actually got mapped.

    This is the number every audit on this corpus must quote. Unmapped spend is
    the LLM-routed remainder, not an error -- but an audit that ignores it is
    reporting on a subset while sounding like it reported on the whole.
    """
    import duckdb

    def _fingerprint(con):
        """Identify duplicate bills: row count + rounded workload total.

        Two job dirs holding the same bill inflate any corpus total and make a
        single defect look like two independent confirmations. This corpus had
        such a pair (job6/job7) go unnoticed through several analyses.
        """
        try:
            r = con.execute(
                "SELECT COUNT(*), ROUND(COALESCE(SUM(aws_amortized_cost),0),2) "
                "FROM aws_li_catalog").fetchone()
            return (r[0], r[1])
        except Exception:
            return None

    rows = []
    for db in sorted(glob.glob(os.path.join(corpus_dir, "*", "projection-audit",
                                            "projection.duckdb"))):
        job = os.path.basename(os.path.dirname(os.path.dirname(db)))
        try:
            con = duckdb.connect(db, read_only=True)
        except Exception as exc:
            rows.append((job, 0, 0.0, 0.0, f"unreadable: {exc}", None))
            continue
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
        if "aws_li_catalog" not in tables:
            rows.append((job, 0, 0.0, 0.0, "no catalog", None))
            con.close()
            continue
        total = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(aws_amortized_cost),0) "
            "FROM aws_li_catalog WHERE is_workload").fetchone()
        if "aws_li_to_gcp_li" not in tables:
            rows.append((job, total[0], total[1], 0.0, "NOT MAPPED", _fingerprint(con)))
            con.close()
            continue
        mapped = con.execute("""
            SELECT COALESCE(SUM(cost),0) FROM (
              SELECT ANY_VALUE(c.aws_amortized_cost) AS cost
              FROM aws_li_catalog c JOIN aws_li_to_gcp_li m USING (aws_li_key)
              WHERE c.is_workload GROUP BY c.aws_li_key)
        """).fetchone()[0]
        priced = "gcp_projection" in tables
        note = "" if priced else "mapped but NOT priced"

        # Synthetic-fixture detection. A real bill repeats a handful of
        # instance types across many lines; a combinatorial coverage fixture
        # has roughly one line per type, and includes types that do not exist
        # (c1.12xlarge, p4d.xlarge). Averaging one into a corpus total silently
        # dominates it -- a fixture in this corpus once produced a 2.5x ratio
        # that read as a pipeline defect. Flag, never silently drop.
        n_types = con.execute(
            "SELECT COUNT(DISTINCT instance_type) FROM aws_li_catalog "
            "WHERE instance_type IS NOT NULL").fetchone()[0]
        if total[0] and n_types >= 100 and n_types > 0.5 * total[0]:
            note = (f"SYNTHETIC? {n_types} instance types in {total[0]} rows "
                    f"-- exclude from totals") + (f"; {note}" if note else "")

        rows.append((job, total[0], total[1], mapped, note,
                     _fingerprint(con)))
        con.close()

    seen = {}
    for r in rows:
        fp = r[5]
        if fp:
            seen.setdefault(fp, []).append(r[0])
    dup_of = {}
    for fp, jobs in seen.items():
        for j in jobs[1:]:
            dup_of[j] = jobs[0]

    print(f"{'job':<24} {'rows':>6} {'workload $':>13} {'mapped $':>13} "
          f"{'cov':>7}  note")
    tw = tm = 0.0
    excluded = []
    for job, n, tot, mapped, note, _fp in rows:
        cov = f"{100*mapped/tot:.1f}%" if tot else "-"
        if job in dup_of:
            note = (f"DUPLICATE of {dup_of[job]} -- excluded from totals"
                    + (f"; {note}" if note else ""))
        skip = job in dup_of or note.startswith("SYNTHETIC?")
        print(f"{job:<24} {n:>6} {tot:>13,.0f} {mapped:>13,.0f} {cov:>7}  {note}")
        if skip:
            excluded.append(job)
            continue
        tw += tot
        tm += mapped
    print("-" * 78)
    cov = f"{100*tm/tw:.1f}%" if tw else "-"
    print(f"{'TOTAL':<24} {'':>6} {tw:>13,.0f} {tm:>13,.0f} {cov:>7}")
    print()
    if excluded:
        print(f"EXCLUDED from the total: {', '.join(excluded)} "
              f"(duplicate or synthetic -- see notes above).")
    print("Unmapped spend is the LLM-routed remainder. Quote this coverage "
          "number in any audit built on this corpus.")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--bills", help="directory of raw bill files (read-only)")
    src.add_argument("--from-jobs", help="corpus directory of existing job dirs")
    ap.add_argument("--out", help="output corpus directory (required with --bills)")
    ap.add_argument("--report-only", action="store_true",
                    help="print coverage without rebuilding")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.bills:
        if not args.out:
            ap.error("--out is required with --bills")
        corpus = os.path.abspath(args.out)
        os.makedirs(corpus, exist_ok=True)
        bills = [p for p in sorted(glob.glob(os.path.join(args.bills, "*")))
                 if os.path.isfile(p)]
        if not bills:
            print(f"No bill files in {args.bills}", file=sys.stderr)
            return 1
        print(f"Building corpus from {len(bills)} bill(s) -> {corpus}\n")
        for i, bill in enumerate(bills, 1):
            name = os.path.splitext(os.path.basename(bill))[0][:40]
            job = os.path.join(corpus, name)
            os.makedirs(os.path.join(job, "projection-audit"), exist_ok=True)
            ext = os.path.splitext(bill)[1] or ".csv"
            # The source bill directory is the user's own; only ever read it.
            shutil.copy2(bill, os.path.join(job, "input" + ext))
            print(f"[{i}/{len(bills)}] {name}")
            failed = run_chain(job, verbose=args.verbose)
            if failed:
                print(f"    soft failures: {', '.join(failed)}")
        corpus_dir = corpus
    else:
        corpus_dir = os.path.abspath(args.from_jobs)
        if not args.report_only:
            jobs = sorted(d for d in glob.glob(os.path.join(corpus_dir, "*"))
                          if os.path.isdir(d))
            print(f"Re-running deterministic chain over {len(jobs)} job dir(s)\n")
            for i, job in enumerate(jobs, 1):
                print(f"[{i}/{len(jobs)}] {os.path.basename(job)}")
                failed = run_chain(job, skip_ingest=True, verbose=args.verbose)
                if failed:
                    print(f"    soft failures: {', '.join(failed)}")

    print()
    coverage_report(corpus_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
