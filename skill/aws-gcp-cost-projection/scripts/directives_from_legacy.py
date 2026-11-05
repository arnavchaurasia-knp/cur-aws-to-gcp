#!/usr/bin/env python3
"""
directives_from_legacy.py — Translation shim for legacy override files.

Converts existing:
  - review_fixes.json (+ review_candidates.json)
  - outlier_fixes.json (+ triage_candidates.json)
  - assumptions.json
  - commitment-discount & negative-cost catalog rows
into an equivalent directives.json ledger.

Enables legacy jobs to run through the single apply_directives.py pipeline
and produce identical projections.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from directives import Directive, load_ledger, save_ledger

try:
    import duckdb
except ImportError:
    duckdb = None


def convert_legacy_to_directives(job_dir: str, db_path: Optional[str] = None, con: Optional[Any] = None) -> List[Directive]:
    """Read all legacy override files in job_dir and return equivalent Directive objects."""
    directives: List[Directive] = []

    if not job_dir or not os.path.exists(job_dir):
        return directives

    if not con and not db_path:
        candidate_db = os.path.join(job_dir, "projection-audit", "projection.duckdb")
        if os.path.exists(candidate_db):
            db_path = candidate_db

    # 1. Commitment discounts & negative costs from DB
    should_close_con = False
    active_con = con
    if active_con is None and db_path and os.path.exists(db_path) and duckdb is not None:
        try:
            active_con = duckdb.connect(db_path, read_only=True)
            should_close_con = True
        except Exception:
            active_con = None

    if active_con is not None:
        try:
            cols = {r[1].lower() for r in active_con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
            if "mechanic_group" in cols:
                comm_rows = active_con.execute("""
                    SELECT aws_li_key FROM aws_li_catalog WHERE mechanic_group = 'commitment_discount'
                """).fetchall()
                for r in comm_rows:
                    key = r[0]
                    directives.append(Directive(
                        id=f"legacy_comm_{key}",
                        author="legacy_shim",
                        source_utterance="commitment_discount: amortized RI/SP/EDP cost, already in effective rates",
                        selector={"aws_li_keys": [key]},
                        action={"type": "set_strategy", "value": "ignore"},
                        status="applied",
                    ))

                neg_rows = active_con.execute("""
                    SELECT aws_li_key FROM aws_li_catalog WHERE mechanic_group = 'negative_cost'
                """).fetchall()
                for r in neg_rows:
                    key = r[0]
                    directives.append(Directive(
                        id=f"legacy_neg_{key}",
                        author="legacy_shim",
                        source_utterance="negative_cost: credits/refunds/reversals with no GCP equivalent",
                        selector={"aws_li_keys": [key]},
                        action={"type": "set_strategy", "value": "ignore"},
                        status="applied",
                    ))
        except Exception as exc:
            print(f"directives_from_legacy: warning reading commitment rows: {exc}", file=sys.stderr)
        finally:
            if should_close_con and active_con:
                try:
                    active_con.close()
                except Exception:
                    pass

    # 2. review_fixes.json (+ review_candidates.json)
    rev_fixes_path = os.path.join(job_dir, "review_fixes.json")
    rev_cands_path = os.path.join(job_dir, "review_candidates.json")
    if os.path.exists(rev_fixes_path):
        try:
            with open(rev_fixes_path, "r", encoding="utf-8") as f:
                fixes_data = json.load(f)
            cands_map: Dict[str, Any] = {}
            if os.path.exists(rev_cands_path):
                with open(rev_cands_path, "r", encoding="utf-8") as f:
                    cands_raw = json.load(f)
                    if isinstance(cands_raw, list):
                        cands_map = {c.get("aws_li_key"): c for c in cands_raw if c.get("aws_li_key")}
                    elif isinstance(cands_raw, dict):
                        cands_map = cands_raw

            fixes_list = fixes_data if isinstance(fixes_data, list) else fixes_data.get("fixes", [])
            for fix in fixes_list:
                key = fix.get("aws_li_key")
                if not key:
                    continue
                decision = fix.get("decision", "confirm").lower()
                if decision == "veto":
                    continue

                if decision == "confirm":
                    cand = cands_map.get(key, {})
                    c_action = cand.get("action")
                    if c_action == "set_sku_and_map":
                        directives.append(Directive(
                            id=f"legacy_rev_{key}",
                            author="legacy_shim",
                            source_utterance=f"Review candidate confirmed for {key}",
                            selector={"aws_li_keys": [key]},
                            action={
                                "type": "set_target_service",
                                "value": cand.get("gcp_service") or "Compute Engine",
                                "params": {
                                    "gcp_sku_id": cand.get("gcp_sku_id"),
                                    "gcp_sku_name": cand.get("gcp_sku_name"),
                                    "strategy": "map"
                                }
                            },
                        ))
                    elif cand.get("gcp_service"):
                        directives.append(Directive(
                            id=f"legacy_rev_{key}",
                            author="legacy_shim",
                            source_utterance=f"Review candidate confirmed for {key}",
                            selector={"aws_li_keys": [key]},
                            action={"type": "set_target_service", "value": cand["gcp_service"]},
                        ))

                elif decision == "override":
                    svc = fix.get("gcp_service")
                    strat = fix.get("strategy")
                    if svc:
                        directives.append(Directive(
                            id=f"legacy_rev_ovr_{key}",
                            author="legacy_shim",
                            source_utterance=fix.get("reason") or f"Review override for {key}",
                            selector={"aws_li_keys": [key]},
                            action={
                                "type": "set_target_service",
                                "value": svc,
                                "params": {
                                    "gcp_sku_id": fix.get("gcp_sku_id"),
                                    "gcp_sku_name": fix.get("gcp_sku_name"),
                                    "unit_multiplier": fix.get("unit_multiplier"),
                                    "component": fix.get("component")
                                }
                            },
                        ))
                    elif strat in ("map", "passthrough", "ignore", "break_down"):
                        directives.append(Directive(
                            id=f"legacy_rev_ovr_strat_{key}",
                            author="legacy_shim",
                            source_utterance=fix.get("reason") or f"Review strategy override for {key}",
                            selector={"aws_li_keys": [key]},
                            action={"type": "set_strategy", "value": strat},
                        ))
        except Exception as exc:
            print(f"directives_from_legacy: warning reading review_fixes: {exc}", file=sys.stderr)

    # 3. outlier_fixes.json (+ triage_candidates.json)
    outlier_fixes_path = os.path.join(job_dir, "outlier_fixes.json")
    triage_cands_path = os.path.join(job_dir, "triage_candidates.json")
    if os.path.exists(outlier_fixes_path):
        try:
            with open(outlier_fixes_path, "r", encoding="utf-8") as f:
                outlier_data = json.load(f)
            tc_map: Dict[str, Any] = {}
            if os.path.exists(triage_cands_path):
                with open(triage_cands_path, "r", encoding="utf-8") as f:
                    tc_raw = json.load(f)
                    if isinstance(tc_raw, list):
                        tc_map = {c.get("aws_li_key"): c for c in tc_raw if c.get("aws_li_key")}
                    elif isinstance(tc_raw, dict):
                        tc_map = tc_raw

            out_list = outlier_data if isinstance(outlier_data, list) else outlier_data.get("fixes", [])
            for fix in out_list:
                key = fix.get("aws_li_key")
                if not key:
                    continue
                decision = fix.get("decision", "confirm").lower()
                if decision == "veto":
                    continue

                if decision == "confirm":
                    cand = tc_map.get(key, {})
                    c_act = cand.get("action")
                    if c_act == "set_service" and cand.get("gcp_service"):
                        directives.append(Directive(
                            id=f"legacy_out_{key}",
                            author="legacy_shim",
                            source_utterance=f"Outlier candidate confirmed service for {key}",
                            selector={"aws_li_keys": [key]},
                            action={"type": "set_target_service", "value": cand["gcp_service"]},
                        ))
                    elif c_act == "set_sku" and cand.get("gcp_sku_id"):
                        directives.append(Directive(
                            id=f"legacy_out_sku_{key}",
                            author="legacy_shim",
                            source_utterance=f"Outlier candidate confirmed SKU for {key}",
                            selector={"aws_li_keys": [key]},
                            action={
                                "type": "set_target_service",
                                "value": cand.get("gcp_service") or "Compute Engine",
                                "params": {
                                    "gcp_sku_id": cand.get("gcp_sku_id"),
                                    "gcp_sku_name": cand.get("gcp_sku_name"),
                                    "component": cand.get("component")
                                }
                            },
                        ))

                elif decision == "override":
                    svc = fix.get("gcp_service")
                    strat = fix.get("strategy")
                    if svc:
                        directives.append(Directive(
                            id=f"legacy_out_ovr_{key}",
                            author="legacy_shim",
                            source_utterance=fix.get("reason") or f"Outlier override for {key}",
                            selector={"aws_li_keys": [key]},
                            action={"type": "set_target_service", "value": svc},
                        ))
                    elif strat in ("map", "passthrough", "ignore", "break_down"):
                        directives.append(Directive(
                            id=f"legacy_out_ovr_strat_{key}",
                            author="legacy_shim",
                            source_utterance=fix.get("reason") or f"Outlier strategy override for {key}",
                            selector={"aws_li_keys": [key]},
                            action={"type": "set_strategy", "value": strat},
                        ))
        except Exception as exc:
            print(f"directives_from_legacy: warning reading outlier_fixes: {exc}", file=sys.stderr)

    # 4. assumptions.json
    assump_path = os.path.join(job_dir, "assumptions.json")
    if os.path.exists(assump_path):
        try:
            with open(assump_path, "r", encoding="utf-8") as f:
                assumps = json.load(f)
            if isinstance(assumps, dict):
                for a_key, a_val in assumps.items():
                    directives.append(Directive(
                        id=f"legacy_assump_{a_key}",
                        author="legacy_shim",
                        source_utterance=f"Assumption override: {a_key}={a_val}",
                        selector={},
                        action={"type": "set_assumption", "key": a_key, "value": a_val},
                    ))
        except Exception as exc:
            print(f"directives_from_legacy: warning reading assumptions: {exc}", file=sys.stderr)

    return directives


def main() -> int:
    job_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    db_path = sys.argv[2] if len(sys.argv) > 2 else None

    # Load existing ledger if present
    existing = load_ledger(job_dir)
    legacy = convert_legacy_to_directives(job_dir, db_path)

    # Merge avoiding duplicate IDs
    existing_ids = {d.id for d in existing}
    merged = list(existing)
    added = 0
    for d in legacy:
        if d.id not in existing_ids:
            merged.append(d)
            existing_ids.add(d.id)
            added += 1

    if added > 0 or not os.path.exists(os.path.join(job_dir, "directives.json")):
        save_ledger(job_dir, merged)
        print(f"directives_from_legacy: saved {len(merged)} total directives ({added} new from legacy) → {job_dir}")
    else:
        print(f"directives_from_legacy: ledger up to date ({len(merged)} directives)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
