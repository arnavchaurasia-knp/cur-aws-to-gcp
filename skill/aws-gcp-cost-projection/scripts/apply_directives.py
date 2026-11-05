#!/usr/bin/env python3
"""
apply_directives.py — Unified application phase for the Directive Layer.

Replaces per-mechanism writers (apply_review_fixes, apply_outlier_fixes,
apply_commitment_ignores, apply_architecture_notes) with ONE single apply phase:
1. Loads catalog rows & existing mappings from projection.duckdb.
2. Loads directive ledger (<job>/directives.json) + architecture disclosures.
3. Resolves overrides deterministically (specificity > recency).
4. Applies service, strategy, workload, and annotation changes to aws_li_to_gcp_li.
5. Stamps 'applied_directives' provenance column on aws_li_to_gcp_li.

Usage:
    python3 apply_directives.py <projection.duckdb> [--check-only] [--job-dir <DIR>]
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from directives import Directive, load_ledger, save_ledger
from directive_resolver import resolve, EffectiveOverride
from directives_from_legacy import convert_legacy_to_directives
import row_state as rs

try:
    import duckdb
except ImportError:
    print("apply_directives: duckdb not installed; skipping", file=sys.stderr)
    sys.exit(0)


import re

def load_architecture_disclosures() -> List[Directive]:
    """Load architecture disclosures from data/architecture-notes.json as annotate directives."""
    skill_dir = os.environ.get("SKILL_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(skill_dir, "data", "architecture-notes.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        disclosures = data.get("disclosures", [])
        directives: List[Directive] = []
        for idx, d in enumerate(disclosures):
            note = (d.get("note") or "").strip()
            pats = d.get("product_ilike_patterns") or []
            if not note or not pats:
                continue
            # Convert SQL ILIKE %X% patterns to regex on product
            escaped = [re.escape(p.strip("%")) for p in pats]
            product_re = "|".join(escaped)
            selector: Dict[str, Any] = {"product_re": product_re}
            if d.get("gcp_service_ilike"):
                selector["gcp_service"] = d["gcp_service_ilike"].strip("%")

            directives.append(Directive(
                id=f"arch_note_{d.get('label', idx)}",
                author="system",
                source_utterance=f"Architecture disclosure: {d.get('label')}",
                selector=selector,
                action={"type": "annotate", "value": note},
                status="applied",
            ))
        return directives
    except Exception as exc:
        print(f"apply_directives: could not read architecture-notes.json: {exc}", file=sys.stderr)
        return []


def re_escape(s: str) -> str:
    # helper to preserve regex wildcards while escaping literal characters
    return s.replace("(", r"\(").replace(")", r"\)").replace("[", r"\[").replace("]", r"\]")


def apply_directives_to_db(db_path: str, check_only: bool = False, job_dir: Optional[str] = None) -> int:
    if not os.path.exists(db_path):
        print(f"apply_directives: DB {db_path} does not exist", file=sys.stderr)
        return 0

    if not job_dir:
        # Infer job_dir from db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if os.path.basename(parent) == "projection-audit":
            job_dir = os.path.dirname(parent)
        else:
            job_dir = os.getcwd()

    con = duckdb.connect(db_path)

    # Check tables
    tables = {r[0].lower() for r in con.execute("SHOW TABLES").fetchall()}
    if "aws_li_to_gcp_li" not in tables or "aws_li_catalog" not in tables:
        con.close()
        return 0

    # Ensure the applied_directives provenance column exists. row_state.py is the
    # ONLY sanctioned owner of DDL on aws_li_to_gcp_li; ensure_schema() idempotently
    # ALTERs in any registry column missing from an existing table.
    rs.ensure_schema(con)

    # 2. Read catalog and mapping rows
    catalog_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
    mapping_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
    has_workload_col = "is_workload" in mapping_cols
    is_workload_sel = "m.is_workload" if has_workload_col else "TRUE AS is_workload"

    canonical_sel = "COALESCE(c.canonical_service, '') AS canonical_service" if "canonical_service" in catalog_cols else "'' AS canonical_service"
    account_sel = "COALESCE(c.account_id, '') AS account_id" if "account_id" in catalog_cols else "'' AS account_id"
    tags_sel = "COALESCE(c.tags, '{}') AS tags" if "tags" in catalog_cols else "'{}' AS tags"
    if "aws_region" in catalog_cols:
        region_sel = "COALESCE(c.aws_region, '') AS region"
    elif "region" in catalog_cols:
        region_sel = "COALESCE(c.region, '') AS region"
    else:
        region_sel = "'' AS region"

    query = f"""
        SELECT c.aws_li_key,
               COALESCE(c.product, '') AS product,
               COALESCE(c.product_code, '') AS product_code,
               {canonical_sel},
               COALESCE(c.usage_type, '') AS usage_type,
               COALESCE(c.operation, '') AS operation,
               {region_sel},
               {account_sel},
               {tags_sel},
               m.gcp_service,
               m.strategy,
               {is_workload_sel}
        FROM aws_li_catalog c
        JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key
    """

    # 1. Load directives (pass open connection to legacy converter if needed)
    directives = load_ledger(job_dir)
    if not directives:
        legacy = convert_legacy_to_directives(job_dir, con=con)
        if legacy:
            directives = legacy
            save_ledger(job_dir, directives)

    # Fold in architecture disclosures
    arch_notes = load_architecture_disclosures()
    all_directives = list(directives) + arch_notes

    if not all_directives:
        con.close()
        return 0
    rows_raw = con.execute(query).fetchall()
    rows: List[Dict[str, Any]] = []
    for r in rows_raw:
        rows.append({
            "aws_li_key": r[0],
            "product": r[1],
            "product_code": r[2],
            "canonical_service": r[3],
            "usage_type": r[4],
            "operation": r[5],
            "region": r[6],
            "account_id": r[7],
            "tags": r[8],
            "gcp_service": r[9],
            "strategy": r[10],
            "is_workload": r[11],
        })

    # 3. Resolve overrides
    overrides = resolve(rows, all_directives)
    if not overrides:
        con.close()
        print("apply_directives: 0 overrides applicable")
        return 0

    if check_only:
        con.close()
        print(f"apply_directives (check-only): {len(overrides)} row(s) would be modified")
        return len(overrides)

    # 4. Apply updates
    updated = 0
    for key, eff in overrides.items():
        set_clauses: List[str] = []
        params: List[Any] = []

        if eff.applied_directives:
            set_clauses.append("applied_directives = ?")
            params.append(json.dumps(eff.applied_directives))

        if eff.target_service:
            set_clauses.append("gcp_service = ?")
            params.append(eff.target_service)

        if eff.strategy:
            set_clauses.append("strategy = ?")
            params.append(eff.strategy)
            if eff.strategy == "ignore" and has_workload_col:
                set_clauses.append("is_workload = FALSE")

        if has_workload_col and eff.is_workload is not None:
            set_clauses.append("is_workload = ?")
            params.append(eff.is_workload)

        if eff.notes:
            # Annotation append mirrors the legacy apply_architecture_notes idiom:
            # idempotent concat with a dedup guard, every column reference wrapped in
            # COALESCE(...) so the note is never read as a bare predicate — the
            # notes-are-output-only invariant (test_notes_are_output_only) forbids
            # that. This reproduces legacy behaviour byte-for-byte (parity test).
            for n in eff.notes:
                set_clauses.append("""
                    projection_note = CASE
                        WHEN COALESCE(projection_note, '') = '' THEN ?
                        WHEN COALESCE(projection_note, '') NOT ILIKE ? THEN COALESCE(projection_note, '') || '; ' || ?
                        ELSE projection_note
                    END
                """)
                params.extend([n, f"%{n[:50]}%", n])

        if set_clauses:
            params.append(key)
            sql = f"UPDATE aws_li_to_gcp_li SET {', '.join(set_clauses)} WHERE aws_li_key = ?"
            con.execute(sql, params)
            updated += 1

    con.close()
    print(f"apply_directives: applied {len(all_directives)} directive(s) across {updated} row(s)")
    return updated


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb> [--check-only] [--job-dir <DIR>]", file=sys.stderr)
        return 2

    db_path = sys.argv[1]
    check_only = "--check-only" in sys.argv

    job_dir = None
    if "--job-dir" in sys.argv:
        idx = sys.argv.index("--job-dir")
        if idx + 1 < len(sys.argv):
            job_dir = sys.argv[idx + 1]

    apply_directives_to_db(db_path, check_only=check_only, job_dir=job_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
