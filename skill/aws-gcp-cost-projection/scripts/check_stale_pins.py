#!/usr/bin/env python3
"""
check_stale_pins.py — Revalidates pinned directives against updated GCP catalog.

Run after catalog_refresher.go finishes a refresh.
For each job with directives, checks if pinned families, shapes, regions, or services
are still offered in the refreshed catalog.
If a pin is no longer available:
- Preserves the directive (does NOT drop or silently remap).
- Raises a blocking clarification in refinement_thread.json with reason: "catalog_refresh_invalidated".
- Flags affected rows in DuckDB with review_flag = "pinned target no longer offered — awaiting your choice".
- Preserves projected cost until user answers.
- Emits structured JSON summary for the notification system.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from directives import Directive, load_ledger, save_ledger
from refinement_thread import load_thread, save_thread
from directive_resolver import matches_selector, extract_row_identity
from directive_translator import load_known_gcp_services
import row_state as rs

try:
    import duckdb
except ImportError:
    duckdb = None


def find_nearest_region(regs: Dict[str, Dict[str, bool]], fam_key: str, req_reg: str) -> str:
    """Find the nearest region offering the family (same geography prefix first)."""
    prefix = req_reg.split("-")[0] if "-" in req_reg else ""
    if prefix:
        for r, m in regs.items():
            if m.get(fam_key) is True and r.startswith(prefix) and r != req_reg:
                return r
    for r, m in regs.items():
        if m.get(fam_key) is True and r != req_reg:
            return r
    return "us-central1"


def check_directive_staleness(
    d: Directive,
    data_dir: str,
    matched_regions: Optional[List[str]] = None
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Check if a directive's pinned target/family/shape/region is no longer offered in the catalog.
    Returns (is_stale, stale_info_dict).
    """
    if d.status == "superseded":
        return False, None

    act = d.action
    act_type = act.get("type")

    # 1. Pinned family
    if act_type == "set_target_family":
        fam = str(act.get("value") or act.get("target_family") or "").strip()
        fam_key = fam.lower().replace("amd", "").replace("arm", "").strip()

        avail_path = os.path.join(data_dir, "compute_family_availability.json")
        if os.path.exists(avail_path):
            try:
                with open(avail_path, "r", encoding="utf-8") as f:
                    avail_data = json.load(f)
                regs = avail_data.get("regions", {})

                # Determine regions to check
                target_regions = set()
                if "region" in d.selector:
                    target_regions.add(d.selector["region"])
                if act.get("params", {}).get("region"):
                    target_regions.add(act["params"]["region"])
                if matched_regions:
                    target_regions.update(matched_regions)
                if not target_regions:
                    target_regions.add("us-central1")

                for reg in target_regions:
                    reg_map = regs.get(reg)
                    if reg_map and fam_key in reg_map and reg_map[fam_key] is False:
                        nearest = find_nearest_region(regs, fam_key, reg)
                        return True, {
                            "directive_id": d.id,
                            "pinned_item": fam,
                            "region": reg,
                            "nearest": nearest,
                            "reason": "catalog_refresh_invalidated",
                            "question": f"{fam} isn't available in {reg} anymore — nearest ({nearest}), global, or keep current?",
                            "options": [f"Price in {nearest}", "Use global rate", "Keep current mapping"],
                        }
            except Exception as exc:
                print(f"check_stale_pins: error checking family {fam}: {exc}", file=sys.stderr)

    # 2. Pinned region
    elif act_type == "set_region":
        reg = str(act.get("value") or act.get("region") or "").strip()
        avail_path = os.path.join(data_dir, "compute_family_availability.json")
        if os.path.exists(avail_path):
            try:
                with open(avail_path, "r", encoding="utf-8") as f:
                    avail_data = json.load(f)
                regs = avail_data.get("regions", {})
                if regs and reg not in regs:
                    nearest = "us-central1"
                    return True, {
                        "directive_id": d.id,
                        "pinned_item": reg,
                        "region": reg,
                        "nearest": nearest,
                        "reason": "catalog_refresh_invalidated",
                        "question": f"Region {reg} isn't available anymore — nearest ({nearest}), global, or keep current?",
                        "options": [f"Price in {nearest}", "Use global rate", "Keep current mapping"],
                    }
            except Exception:
                pass

    # 3. Pinned service
    elif act_type == "set_target_service":
        svc = str(act.get("value") or "").strip()
        known = load_known_gcp_services()
        if svc and svc not in known:
            return True, {
                "directive_id": d.id,
                "pinned_item": svc,
                "region": "global",
                "nearest": "Compute Engine",
                "reason": "catalog_refresh_invalidated",
                "question": f"Service '{svc}' is not recognized in GCP catalog — map to Compute Engine, passthrough, or keep current?",
                "options": ["Map to Compute Engine", "Passthrough", "Keep current mapping"],
            }

    return False, None


def check_job_stale_pins(job_dir: str, data_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Check all directive ledgers in job_dir for staleness.
    Flags DuckDB rows and writes blocking clarifications to refinement_thread.
    """
    if not job_dir or not os.path.exists(job_dir):
        return []

    if not data_dir:
        skill_dir = os.environ.get("SKILL_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(skill_dir, "data")

    # Find all directive files: user-scoped and unscoped
    files_to_check: List[Tuple[Optional[str], List[Directive]]] = []
    directives_subdir = os.path.join(job_dir, "directives")
    if os.path.exists(directives_subdir):
        for fname in os.listdir(directives_subdir):
            if fname.endswith(".json") and not fname.endswith(".baseline.json"):
                u_id = fname[:-5]
                led = load_ledger(job_dir, user_id=u_id)
                if led:
                    files_to_check.append((u_id, led))

    unscoped = load_ledger(job_dir)
    if unscoped:
        files_to_check.append((None, unscoped))

    if not files_to_check:
        return []

    # Load matched row regions from DuckDB if available
    db_path = os.path.join(job_dir, "projection-audit", "projection.duckdb")
    rows: List[Dict[str, Any]] = []
    con = None
    if os.path.exists(db_path) and duckdb is not None:
        try:
            con = duckdb.connect(db_path)
            tables = {r[0].lower() for r in con.execute("SHOW TABLES").fetchall()}
            if "aws_li_catalog" in tables:
                cat_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
                reg_col = "region" if "region" in cat_cols else ("aws_region" if "aws_region" in cat_cols else "''")
                q = f"""
                    SELECT aws_li_key, product, COALESCE(product_code, ''), COALESCE(canonical_service, ''),
                           COALESCE(usage_type, ''), COALESCE(operation, ''), COALESCE({reg_col}, '')
                    FROM aws_li_catalog
                """
                raw = con.execute(q).fetchall()
                for r in raw:
                    rows.append({
                        "aws_li_key": r[0], "product": r[1], "product_code": r[2],
                        "canonical_service": r[3], "usage_type": r[4], "operation": r[5],
                        "region": r[6], "account_id": "", "tags": "{}"
                    })
        except Exception as exc:
            print(f"check_stale_pins: DB read error: {exc}", file=sys.stderr)

    stale_reports: List[Dict[str, Any]] = []
    job_id = os.path.basename(os.path.abspath(job_dir))

    for user_id, directives in files_to_check:
        for d in directives:
            # Find matched regions from rows
            matched_regs = []
            matched_keys = []
            if rows:
                for r in rows:
                    ident = extract_row_identity(r)
                    if matches_selector(d, ident):
                        matched_keys.append(r["aws_li_key"])
                        if r.get("region"):
                            matched_regs.append(r["region"])

            is_stale, stale_info = check_directive_staleness(d, data_dir, matched_regs)
            if is_stale and stale_info:
                # 1. Prepare blocking clarification
                clarif_id = f"clarif_stale_{d.id}"
                clarif = {
                    "id": clarif_id,
                    "reason": stale_info["reason"],
                    "question": stale_info["question"],
                    "options": stale_info["options"],
                    "blocking": True,
                    "directive_ref": d.id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }

                # 2. Add to user thread
                thread = load_thread(job_dir, user_id=user_id)
                open_c = thread.setdefault("open_clarifications", [])
                if not any(c.get("id") == clarif_id for c in open_c):
                    open_c.append(clarif)
                    # Add AGY notice to thread messages
                    thread.setdefault("messages", []).append({
                        "id": f"msg_stale_{clarif_id}",
                        "role": "agy",
                        "content": f"⚠️ Notice: {stale_info['question']}",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })
                    save_thread(job_dir, thread, user_id=user_id)

                # 3. Flag rows in DuckDB without changing projected cost
                if con is not None and matched_keys:
                    try:
                        rs.ensure_schema(con)
                        map_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
                        flag_msg = "pinned target no longer offered — awaiting your choice"
                        placeholders = ",".join(["?"] * len(matched_keys))
                        # Flag goes to review_flags only — a structured state column
                        # that rs.ensure_schema(con) guarantees exists. Never write the
                        # derived, output-only projection_note directly (render_notes
                        # owns it; notes-are-output-only invariant).
                        if "review_flags" in map_cols:
                            con.execute(
                                f"UPDATE aws_li_to_gcp_li SET review_flags = CASE WHEN COALESCE(review_flags, '') = '' THEN ? ELSE review_flags || ',' || ? END WHERE aws_li_key IN ({placeholders})",
                                [flag_msg, flag_msg] + matched_keys
                            )
                    except Exception as exc:
                        print(f"check_stale_pins: row flag error: {exc}", file=sys.stderr)

                stale_reports.append({
                    "job_id": job_id,
                    "user_id": user_id or d.author or "user",
                    "directive_id": d.id,
                    "pinned_item": stale_info["pinned_item"],
                    "region": stale_info["region"],
                    "question": stale_info["question"],
                    "affected_keys": matched_keys,
                })

    if con is not None:
        try:
            con.close()
        except Exception:
            pass

    return stale_reports


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <job_dir_or_jobs_parent_dir> [--skill-dir <DIR>]", file=sys.stderr)
        return 1

    target_path = sys.argv[1]
    skill_dir = None
    if "--skill-dir" in sys.argv:
        idx = sys.argv.index("--skill-dir")
        if idx + 1 < len(sys.argv):
            skill_dir = sys.argv[idx + 1]

    data_dir = os.path.join(skill_dir, "data") if skill_dir else None

    all_stale = []
    # Check if target_path is a single job dir or jobs parent directory
    if os.path.exists(os.path.join(target_path, "directives.json")) or os.path.exists(os.path.join(target_path, "directives")):
        all_stale.extend(check_job_stale_pins(target_path, data_dir))
    else:
        # Scan subdirectories
        for entry in os.listdir(target_path):
            sub = os.path.join(target_path, entry)
            if os.path.isdir(sub) and (os.path.exists(os.path.join(sub, "directives.json")) or os.path.exists(os.path.join(sub, "directives"))):
                all_stale.extend(check_job_stale_pins(sub, data_dir))

    print(json.dumps(all_stale, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
