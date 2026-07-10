#!/usr/bin/env python3
from __future__ import annotations
"""
apply_outlier_fixes.py — Phase 5 single application point.

Reads:
  outlier_fixes.json      — LLM output (confirm / override / veto per aws_li_key)
  triage_candidates.json  — auto_triage.py pre-computed candidates

For each fix:
  confirm  → apply auto_triage's pre-validated candidate
  override → apply LLM's values (schema-validated before DB write)
  veto     → leave row unchanged, append to mapping-notes.md under "Rate gaps"

All DB writes happen here — auto_triage.py and the LLM never touch the DB.
"""
import duckdb
import json
import os
import sys

JOB_DIR         = os.getcwd()
DB_PATH         = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
FIXES_FILE      = os.path.join(JOB_DIR, "outlier_fixes.json")
CANDIDATES_FILE = os.path.join(JOB_DIR, "triage_candidates.json")
NOTES_FILE      = os.path.join(JOB_DIR, "mapping-notes.md")

VALID_ACTIONS = {"set_sku", "set_multiplier", "set_service", "set_region", "needs_sku"}


def validate_override(fix: dict) -> str | None:
    """Return an error string if the override is malformed, None if valid."""
    has_sku   = fix.get("gcp_sku_id") and fix.get("gcp_sku_name")
    has_mult  = fix.get("unit_multiplier") is not None
    has_svc   = fix.get("gcp_service")
    has_region = fix.get("gcp_region")
    has_strat  = fix.get("strategy")
    if not (has_sku or has_mult or has_svc or has_region or has_strat):
        return "override must include at least one of: gcp_sku_id+gcp_sku_name, unit_multiplier, gcp_service, gcp_region, strategy"
    return None


def apply_candidate(conn, key: str, cand: dict, notes: list):
    action = cand.get("action")
    if action == "set_sku":
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET gcp_sku_id = ?, gcp_sku_name = ?
            WHERE aws_li_key = ?
        """, [cand["gcp_sku_id"], cand["gcp_sku_name"], key])
        notes.append(f"- `{key}`: confirmed SKU → {cand['gcp_sku_id']} ({cand['gcp_sku_name']})")

    elif action == "set_multiplier":
        component = cand.get("component")
        mult = float(cand["unit_multiplier"])
        if component:
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET unit_multiplier = ?
                WHERE aws_li_key = ? AND component = ?
            """, [mult, key, component])
        else:
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET unit_multiplier = ?
                WHERE aws_li_key = ?
            """, [mult, key])
        notes.append(f"- `{key}`: confirmed multiplier → {mult}" +
                     (f" ({component})" if component else ""))

    elif action == "set_service":
        conn.execute("""
            UPDATE aws_li_to_gcp_li SET gcp_service = ?
            WHERE aws_li_key = ?
        """, [cand["gcp_service"], key])
        notes.append(f"- `{key}`: confirmed service → {cand['gcp_service']}")

    elif action == "set_region":
        conn.execute("""
            UPDATE aws_li_catalog SET gcp_region = ?
            WHERE aws_li_key = ?
        """, [cand["gcp_region"], key])
        notes.append(f"- `{key}`: confirmed region → {cand['gcp_region']}")

    elif action == "needs_sku":
        # Candidate flagged as needing SKU but couldn't compute one — should have been overridden
        notes.append(f"- `{key}`: confirm on 'needs_sku' candidate — no-op, LLM should have overridden")
    else:
        notes.append(f"- `{key}`: unknown candidate action '{action}' — skipped")


def apply_override(conn, key: str, fix: dict, notes: list):
    gcp_sku_id   = fix.get("gcp_sku_id")
    gcp_sku_name = fix.get("gcp_sku_name")
    unit_mult    = fix.get("unit_multiplier")
    gcp_service  = fix.get("gcp_service")
    gcp_region   = fix.get("gcp_region")
    component    = fix.get("component")
    strategy     = fix.get("strategy")
    reason       = fix.get("reason", "")

    if strategy:
        conn.execute("""
            UPDATE aws_li_to_gcp_li SET strategy = ?
            WHERE aws_li_key = ?
        """, [strategy, key])
        notes.append(f"- `{key}`: override strategy → {strategy} — {reason}")

    if gcp_sku_id and gcp_sku_name:
        if component:
            conn.execute("""
                UPDATE aws_li_to_gcp_li
                SET gcp_sku_id = ?, gcp_sku_name = ?
                WHERE aws_li_key = ? AND component = ?
            """, [gcp_sku_id, gcp_sku_name, key, component])
        else:
            conn.execute("""
                UPDATE aws_li_to_gcp_li
                SET gcp_sku_id = ?, gcp_sku_name = ?
                WHERE aws_li_key = ?
            """, [gcp_sku_id, gcp_sku_name, key])
        notes.append(f"- `{key}`: override SKU → {gcp_sku_id} ({gcp_sku_name})" +
                     (f" ({component})" if component else "") + f" — {reason}")

    if unit_mult is not None:
        mult = float(unit_mult)
        if component:
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET unit_multiplier = ?
                WHERE aws_li_key = ? AND component = ?
            """, [mult, key, component])
        else:
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET unit_multiplier = ?
                WHERE aws_li_key = ?
            """, [mult, key])
        notes.append(f"- `{key}`: override multiplier → {mult}" +
                     (f" ({component})" if component else "") + f" — {reason}")

    if gcp_service:
        conn.execute("""
            UPDATE aws_li_to_gcp_li SET gcp_service = ?
            WHERE aws_li_key = ?
        """, [gcp_service, key])
        notes.append(f"- `{key}`: override service → {gcp_service} — {reason}")

    if gcp_region:
        conn.execute("""
            UPDATE aws_li_catalog SET gcp_region = ?
            WHERE aws_li_key = ?
        """, [gcp_region, key])
        notes.append(f"- `{key}`: override region → {gcp_region} — {reason}")


def main():
    if not os.path.exists(FIXES_FILE):
        print("outlier_fixes.json not found — LLM may not have written it (possibly 0 outliers). Skipping.")
        sys.exit(0)

    try:
        with open(FIXES_FILE) as f:
            fixes = json.load(f)
    except Exception as e:
        print(f"WARNING: Could not parse outlier_fixes.json: {e} — skipping outlier fixes.", file=sys.stderr)
        sys.exit(0)

    if not isinstance(fixes, list):
        print("WARNING: outlier_fixes.json must be a JSON array — skipping outlier fixes.", file=sys.stderr)
        sys.exit(0)

    candidates = {}
    if os.path.exists(CANDIDATES_FILE):
        try:
            with open(CANDIDATES_FILE) as f:
                candidates = json.load(f)
        except Exception:
            pass

    conn = duckdb.connect(DB_PATH)
    notes = []
    rate_gaps = []
    applied = vetoed = errors = 0

    for fix in fixes:
        key      = fix.get("aws_li_key")
        decision = (fix.get("decision") or "").lower()
        reason   = fix.get("reason", "")

        if not key or decision not in ("confirm", "override", "veto"):
            print(f"WARNING: Skipping malformed fix entry (missing key or bad decision): {fix}")
            errors += 1
            continue

        if decision == "veto":
            vetoed += 1
            rate_gaps.append(f"- `{key}`: {reason}")
            continue

        if decision == "confirm":
            cand_info = candidates.get(key, {})
            cand = cand_info.get("candidate")
            if not cand:
                # Candidate was LOW/NONE and LLM should have used override instead.
                print(f"WARNING: confirm for {key} but auto_triage had no candidate — treating as veto")
                rate_gaps.append(f"- `{key}`: LLM confirmed but no candidate existed — {reason}")
                vetoed += 1
                continue
            try:
                apply_candidate(conn, key, cand, notes)
                applied += 1
            except Exception as e:
                print(f"ERROR applying candidate for {key}: {e}", file=sys.stderr)
                errors += 1

        elif decision == "override":
            err = validate_override(fix)
            if err:
                print(f"WARNING: Invalid override for {key}: {err} — skipping")
                errors += 1
                continue
            try:
                apply_override(conn, key, fix, notes)
                applied += 1
            except Exception as e:
                print(f"ERROR applying override for {key}: {e}", file=sys.stderr)
                errors += 1

    conn.close()

    # Append to mapping-notes.md
    with open(NOTES_FILE, "a") as f:
        if notes:
            f.write("\n\n## Phase 5 Outlier Fixes\n\n")
            for note in notes:
                f.write(note + "\n")
        if rate_gaps:
            f.write("\n\n## Rate gaps (vetoed rows)\n\n")
            f.write("These rows could not be resolved — cost uses passthrough/best-effort.\n\n")
            for gap in rate_gaps:
                f.write(gap + "\n")

    print(f"apply_outlier_fixes: applied={applied} vetoed={vetoed} errors={errors}")

if __name__ == "__main__":
    main()
