#!/usr/bin/env python3
"""
apply_outlier_fixes.py — Phase 5 single application point for outlier triage fixes.

Reads:
  outlier_fixes.json       — LLM output (confirm / override / veto per aws_li_key)
  triage_candidates.json  — auto_triage.py pre-computed candidates

For each fix:
  confirm  → apply auto_triage's pre-computed candidate
  override → apply LLM's values (schema-validated before DB write)
  veto     → leave row unchanged (log reason in mapping-notes.md)

All DB writes happen here — auto_triage.py and the LLM never touch the DB.
Triggers an incremental rate refresh after applying fixes.
"""
from __future__ import annotations
import json
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import duckdb
except ImportError:
    print("WARNING: duckdb not installed — skipping apply_outlier_fixes.", file=sys.stderr)
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_override_guard import validate_override

JOB_DIR = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
FIXES_FILE = os.path.join(JOB_DIR, "outlier_fixes.json")
CANDIDATES_FILE = os.path.join(JOB_DIR, "triage_candidates.json")
NOTES_FILE = os.path.join(JOB_DIR, "mapping-notes.md")

ALLOWED_STRATEGIES = {"map", "break_down", "passthrough", "ignore"}


def dict_key(k: str, component: str | None) -> str:
    return f"{k}|{component}" if component else k


def apply_candidate(conn, key: str, cand: dict, notes: list):
    action = cand.get("action")
    component = cand.get("component")

    if action == "set_multiplier":
        mult = cand.get("unit_multiplier")
        if mult is not None:
            mult_f = float(mult)
            if component:
                conn.execute(
                    "UPDATE aws_li_to_gcp_li SET unit_multiplier = ? WHERE aws_li_key = ? AND component = ?",
                    [mult_f, key, component],
                )
                notes.append(f"- `{key}`: confirmed multiplier fix — {mult_f} ({component})")
            else:
                conn.execute(
                    "UPDATE aws_li_to_gcp_li SET unit_multiplier = ? WHERE aws_li_key = ?",
                    [mult_f, key],
                )
                notes.append(f"- `{key}`: confirmed multiplier fix — {mult_f}")

    elif action == "set_sku":
        sku_id = cand.get("gcp_sku_id")
        sku_name = cand.get("gcp_sku_name")
        if sku_id and sku_name:
            if component:
                conn.execute(
                    "UPDATE aws_li_to_gcp_li SET gcp_sku_id = ?, gcp_sku_name = ?, strategy = 'map' WHERE aws_li_key = ? AND component = ?",
                    [sku_id, sku_name, key, component],
                )
                notes.append(f"- `{key}`: confirmed SKU fix — {sku_id} ({sku_name}) [{component}]")
            else:
                conn.execute(
                    "UPDATE aws_li_to_gcp_li SET gcp_sku_id = ?, gcp_sku_name = ?, strategy = 'map' WHERE aws_li_key = ?",
                    [sku_id, sku_name, key],
                )
                notes.append(f"- `{key}`: confirmed SKU fix — {sku_id} ({sku_name})")

    elif action == "set_service":
        service = cand.get("gcp_service")
        if service:
            if component:
                conn.execute(
                    "UPDATE aws_li_to_gcp_li SET gcp_service = ? WHERE aws_li_key = ? AND component = ?",
                    [service, key, component],
                )
                notes.append(f"- `{key}`: confirmed service fix — {service} [{component}]")
            else:
                conn.execute(
                    "UPDATE aws_li_to_gcp_li SET gcp_service = ? WHERE aws_li_key = ?",
                    [service, key],
                )
                notes.append(f"- `{key}`: confirmed service fix — {service}")

    elif action == "set_region":
        region = cand.get("gcp_region")
        if region:
            conn.execute(
                "UPDATE aws_li_catalog SET gcp_region = ? WHERE aws_li_key = ?",
                [region, key],
            )
            notes.append(f"- `{key}`: confirmed region fix — {region}")

    else:
        notes.append(f"- `{key}`: candidate action '{action}' requires no DB write")


def apply_override(conn, key: str, fix: dict, notes: list):
    gcp_sku_id = fix.get("gcp_sku_id")
    gcp_sku_name = fix.get("gcp_sku_name")
    unit_mult = fix.get("unit_multiplier")
    component = fix.get("component")
    gcp_service = fix.get("gcp_service")
    gcp_region = fix.get("gcp_region")
    strategy = fix.get("strategy")
    reason = fix.get("reason", "")

    updates = {}
    if gcp_sku_id and gcp_sku_name:
        updates["gcp_sku_id"] = gcp_sku_id
        updates["gcp_sku_name"] = gcp_sku_name
        if not strategy:
            updates["strategy"] = "map"
    if strategy:
        updates["strategy"] = strategy
    if gcp_service:
        updates["gcp_service"] = gcp_service
    if unit_mult is not None:
        updates["unit_multiplier"] = float(unit_mult)

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        if component is not None:
            values = list(updates.values()) + [key, component]
            conn.execute(f"UPDATE aws_li_to_gcp_li SET {set_clause} WHERE aws_li_key = ? AND component = ?", values)
            notes.append(f"- `{key}`: override [{component}] — {updates} — {reason}")
        else:
            values = list(updates.values()) + [key]
            conn.execute(f"UPDATE aws_li_to_gcp_li SET {set_clause} WHERE aws_li_key = ?", values)
            notes.append(f"- `{key}`: override — {updates} — {reason}")

    if gcp_region:
        conn.execute("UPDATE aws_li_catalog SET gcp_region = ? WHERE aws_li_key = ?", [gcp_region, key])
        notes.append(f"- `{key}`: override region — {gcp_region} — {reason}")


def trigger_rerate(conn):
    try:
        from incremental_rerate import fill_missing_skus
        fill_missing_skus(conn)
    except Exception as e:
        print(f"DEBUG: incremental rerate in apply_outlier_fixes: {e}")
    try:
        from projection_view import create_projection_view
        create_projection_view(conn)
    except Exception as e:
        print(f"DEBUG: create_projection_view in apply_outlier_fixes: {e}")


def main():
    if not os.path.exists(FIXES_FILE):
        print("outlier_fixes.json not found — skipping outlier fixes.")
        sys.exit(0)

    try:
        with open(FIXES_FILE, encoding="utf-8") as f:
            fixes = json.load(f)
    except Exception as e:
        print(f"WARNING: Could not parse outlier_fixes.json: {e} — skipping outlier fixes.", file=sys.stderr)
        sys.exit(0)

    if not isinstance(fixes, list):
        print("WARNING: outlier_fixes.json must be a JSON array — skipping outlier fixes.", file=sys.stderr)
        sys.exit(0)

    if not os.path.exists(DB_PATH):
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    candidates = {}
    if os.path.exists(CANDIDATES_FILE):
        try:
            with open(CANDIDATES_FILE, encoding="utf-8") as f:
                candidates = json.load(f)
        except Exception:
            pass

    conn = duckdb.connect(DB_PATH)
    notes = []
    applied = vetoed = errors = 0

    # Fetch all known aws_li_keys
    known_keys = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT aws_li_key FROM aws_li_to_gcp_li UNION SELECT DISTINCT aws_li_key FROM aws_li_catalog"
        ).fetchall()
    }

    try:
        conn.execute("BEGIN TRANSACTION")

        for fix in fixes:
            if not isinstance(fix, dict):
                print(f"WARNING: Invalid fix item (not a dict): {fix}", file=sys.stderr)
                errors += 1
                continue

            key = fix.get("aws_li_key")
            decision = (fix.get("decision") or "").lower()
            component = fix.get("component")
            reason = fix.get("reason", "")

            if not key or key not in known_keys:
                print(f"WARNING: Unknown or missing aws_li_key: {key} — skipping fix", file=sys.stderr)
                errors += 1
                continue

            if decision not in ("confirm", "override", "veto"):
                print(f"WARNING: Malformed decision '{decision}' for {key} — skipping fix", file=sys.stderr)
                errors += 1
                continue

            if decision == "veto":
                vetoed += 1
                notes.append(f"- `{key}`: vetoed — {reason}")
                continue

            if decision == "confirm":
                dkey = dict_key(key, component)
                cand_info = candidates.get(dkey) or candidates.get(key)
                if not cand_info or not isinstance(cand_info, dict):
                    print(f"WARNING: confirm for {key} ({component}) but no pre-computed candidate — treating as veto")
                    vetoed += 1
                    notes.append(f"- `{key}`: confirmed but candidate missing — treated as veto")
                    continue
                cand = cand_info.get("candidate")
                if not cand:
                    print(f"WARNING: candidate entry for {key} has no candidate payload — treating as veto")
                    vetoed += 1
                    notes.append(f"- `{key}`: candidate has no action payload — treated as veto")
                    continue
                try:
                    apply_candidate(conn, key, cand, notes)
                    applied += 1
                except Exception as e:
                    print(f"ERROR applying candidate for {key}: {e}", file=sys.stderr)
                    errors += 1

            elif decision == "override":
                strategy = fix.get("strategy")
                if strategy and strategy not in ALLOWED_STRATEGIES:
                    print(f"WARNING: override for {key} has unknown strategy '{strategy}' — skipping", file=sys.stderr)
                    errors += 1
                    continue

                # Scope check against triage_candidates if candidates file is non-empty
                dkey = dict_key(key, component)
                if candidates and (dkey not in candidates and key not in candidates):
                    print(f"WARNING: {key} was not flagged in triage_candidates.json — ignoring out-of-scope override")
                    errors += 1
                    continue

                has_sku = fix.get("gcp_sku_id") and fix.get("gcp_sku_name")
                has_mult = fix.get("unit_multiplier") is not None
                has_svc = bool(fix.get("gcp_service"))
                has_region = bool(fix.get("gcp_region"))
                has_strat = bool(strategy)

                if not (has_sku or has_mult or has_svc or has_region or has_strat):
                    print(f"WARNING: override for {key} has no actionable fields — skipping", file=sys.stderr)
                    errors += 1
                    continue

                ok, why = validate_override(conn, key, component, fix)
                if not ok:
                    print(f"WARNING: override for {key} rejected by guard — {why}")
                    errors += 1
                    continue

                try:
                    apply_override(conn, key, fix, notes)
                    applied += 1
                except Exception as e:
                    print(f"ERROR applying override for {key}: {e}", file=sys.stderr)
                    errors += 1

        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        conn.close()
        print(f"ERROR: Exception during outlier fixes batch, transaction rolled back: {e}", file=sys.stderr)
        sys.exit(1)

    # Post-apply rate refresh
    if applied > 0:
        trigger_rerate(conn)

    conn.close()

    if notes:
        try:
            with open(NOTES_FILE, "a", encoding="utf-8") as f:
                f.write("\n\n## Phase 5 Outlier Triage Fixes\n\n")
                for note in notes:
                    f.write(note + "\n")
        except Exception:
            pass

    print(f"apply_outlier_fixes: applied={applied} vetoed={vetoed} errors={errors}")


if __name__ == "__main__":
    main()

