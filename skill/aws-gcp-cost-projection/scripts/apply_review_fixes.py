#!/usr/bin/env python3
"""
apply_review_fixes.py — Phase 3 single application point.

Reads:
  review_fixes.json       — LLM output (confirm / override / veto per aws_li_key)
  review_candidates.json  — auto_review.py pre-computed candidates

For each fix:
  confirm  → apply auto_review's pre-validated candidate
  override → apply LLM's values (schema-validated before DB write)
  veto     → leave row unchanged (log reason)

All DB writes happen here — auto_review.py and the LLM never touch the DB.
"""
import duckdb
import json
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_override_guard import validate_override

JOB_DIR         = os.getcwd()
DB_PATH         = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
FIXES_FILE      = os.path.join(JOB_DIR, "review_fixes.json")
CANDIDATES_FILE = os.path.join(JOB_DIR, "review_candidates.json")
NOTES_FILE      = os.path.join(JOB_DIR, "mapping-notes.md")


def apply_candidate(conn, key: str, cand: dict, notes: list):
    action = cand.get("action")

    if action == "set_sku_and_map":
        try:
            cols = {c[1].lower() for c in conn.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
            if "needs_reprice" in cols:
                conn.execute("""
                    UPDATE aws_li_to_gcp_li
                    SET gcp_sku_id = ?, gcp_sku_name = ?, gcp_service = ?, strategy = 'map', needs_reprice = FALSE
                    WHERE aws_li_key = ?
                """, [cand["gcp_sku_id"], cand["gcp_sku_name"], cand.get("gcp_service"), key])
            else:
                conn.execute("""
                    UPDATE aws_li_to_gcp_li
                    SET gcp_sku_id = ?, gcp_sku_name = ?, gcp_service = ?, strategy = 'map'
                    WHERE aws_li_key = ?
                """, [cand["gcp_sku_id"], cand["gcp_sku_name"], cand.get("gcp_service"), key])
        except Exception:
            conn.execute("""
                UPDATE aws_li_to_gcp_li
                SET gcp_sku_id = ?, gcp_sku_name = ?, gcp_service = ?, strategy = 'map'
                WHERE aws_li_key = ?
            """, [cand["gcp_sku_id"], cand["gcp_sku_name"], cand.get("gcp_service"), key])
        notes.append(f"- `{key}`: confirmed — SKU {cand['gcp_sku_id']} ({cand['gcp_sku_name']}), strategy=map")


    elif action == "fix_multipliers":
        if cand.get("core_multiplier") is not None:
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET unit_multiplier = ?
                WHERE aws_li_key = ? AND component = 'core'
            """, [cand["core_multiplier"], key])
        if cand.get("ram_multiplier") is not None:
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET unit_multiplier = ?
                WHERE aws_li_key = ? AND component = 'ram'
            """, [cand["ram_multiplier"], key])
        notes.append(f"- `{key}`: confirmed spec fix — "
                     f"core={cand.get('core_multiplier')}, ram={cand.get('ram_multiplier')}")
    elif action == "penalize_confidence":
        penalty = float(cand.get("penalty", 0.0))
        note = cand.get("note", "")
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET mapping_confidence = GREATEST(0.1, COALESCE(mapping_confidence, 0.8) - ?),
                pipeline_note = CASE
                    WHEN COALESCE(pipeline_note, '') = '' THEN ?
                    WHEN COALESCE(pipeline_note, '') NOT LIKE '%' || ? || '%' THEN pipeline_note || ' · ' || ?
                    ELSE pipeline_note
                END
            WHERE aws_li_key = ?
        """, [penalty, note, note, note, key])
        notes.append(f"- {key}: penalized confidence by {penalty} due to unsupported feature")
    else:
        notes.append(f"- `{key}`: unknown candidate action '{action}' — skipped")


def apply_override(conn, key: str, fix: dict, notes: list):
    gcp_sku_id   = fix.get("gcp_sku_id")
    gcp_sku_name = fix.get("gcp_sku_name")
    unit_mult    = fix.get("unit_multiplier")
    component    = fix.get("component")  # Default to None to keep backward compatibility
    gcp_service  = fix.get("gcp_service")
    reason       = fix.get("reason", "")

    if gcp_sku_id and gcp_sku_name:
        updates = {"gcp_sku_id": gcp_sku_id, "gcp_sku_name": gcp_sku_name, "strategy": "map"}
        if gcp_service:
            updates["gcp_service"] = gcp_service
        if component is not None:
            updates["component"] = component
        try:
            cols = {c[1].lower() for c in conn.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
            if "needs_reprice" in cols:
                updates["needs_reprice"] = False
        except Exception:
            pass
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        if component is not None:
            values = list(updates.values()) + [key, component]
            conn.execute(f"UPDATE aws_li_to_gcp_li SET {set_clause} WHERE aws_li_key = ? AND (component = ? OR component IS NULL)", values)
            notes.append(f"- `{key}`: override — SKU {gcp_sku_id} ({gcp_sku_name}) [{component}] — {reason}")
        else:
            values = list(updates.values()) + [key]
            conn.execute(f"UPDATE aws_li_to_gcp_li SET {set_clause} WHERE aws_li_key = ?", values)
            notes.append(f"- `{key}`: override — SKU {gcp_sku_id} ({gcp_sku_name}) — {reason}")


    if unit_mult is not None:
        if component is not None:
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET unit_multiplier = ?
                WHERE aws_li_key = ? AND (component = ? OR component IS NULL)
            """, [float(unit_mult), key, component])
            notes.append(f"- `{key}`: override multiplier — {unit_mult} ({component}) — {reason}")
        else:
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET unit_multiplier = ?
                WHERE aws_li_key = ?
            """, [float(unit_mult), key])
            notes.append(f"- `{key}`: override multiplier — {unit_mult} — {reason}")


def main():
    candidates = {}
    if os.path.exists(CANDIDATES_FILE):
        try:
            with open(CANDIDATES_FILE) as f:
                candidates = json.load(f)
        except Exception:
            pass

    fixes = []
    if os.path.exists(FIXES_FILE):
        try:
            with open(FIXES_FILE) as f:
                fixes = json.load(f)
        except Exception as e:
            print(f"WARNING: Could not parse review_fixes.json: {e} — skipping review fixes.", file=sys.stderr)

    if not isinstance(fixes, list):
        fixes = []

    if not fixes and not candidates:
        print("review_fixes.json and candidates empty — skipping review fixes.")
        sys.exit(0)

    conn = duckdb.connect(DB_PATH)
    try:
        import row_state
        row_state.ensure_schema(conn)
    except Exception:
        pass
    notes = []
    applied = vetoed = errors = 0

    # Auto-apply deterministic confidence penalties from candidates
    for key, cinfo in candidates.items():
        cand = cinfo.get("candidate")
        if isinstance(cand, dict) and cand.get("action") == "penalize_confidence":
            apply_candidate(conn, key, cand, notes)
            applied += 1

    for fix in fixes:
        key      = fix.get("aws_li_key")
        decision = (fix.get("decision") or "").lower()
        reason   = fix.get("reason", "")

        if not key or decision not in ("confirm", "override", "veto"):
            print(f"WARNING: Skipping malformed fix entry: {fix}")
            errors += 1
            continue

        if decision == "veto":
            vetoed += 1
            # G-3: If the row was needs_reprice / couldn't be resolved, fallback to legitimate passthrough
            try:
                conn.execute("""
                    UPDATE aws_li_to_gcp_li
                    SET strategy = 'passthrough',
                        passthrough_reason = 'llm_veto',
                        decided_by = 'apply_review_fixes.py',
                        needs_reprice = FALSE
                    WHERE aws_li_key = ? AND (strategy = 'needs_reprice' OR needs_reprice = TRUE)
                """, [key])
            except Exception:
                pass
            notes.append(f"- `{key}`: vetoed — {reason}")
            continue


        if decision == "confirm":
            cand_info = candidates.get(key, {})
            cand = cand_info.get("candidate")
            if not cand:
                print(f"WARNING: confirm for {key} but no pre-computed candidate — treating as veto")
                vetoed += 1
                continue
            try:
                apply_candidate(conn, key, cand, notes)
                applied += 1
            except Exception as e:
                print(f"ERROR applying candidate for {key}: {e}", file=sys.stderr)
                errors += 1

        elif decision == "override":
            # auto_review.py's review_candidates.json is the authoritative
            # flagged-row list. "confirm" already implicitly requires the key to
            # have a real candidate there, but "override" applied the LLM's own
            # fields with no such check — letting the LLM freelance a fix on any
            # row it liked. Observed in practice: the LLM invented its own
            # "violation" on a row that was never flagged (an intentional,
            # correct S3 Glacier Early-Delete passthrough) and overrode it to a
            # wrong SKU, producing a real ~50x under-projection outside any
            # review that was actually asked for.
            if candidates and key not in candidates:
                print(f"WARNING: {key} was never flagged by auto_review.py (not "
                      f"in review_candidates.json) — ignoring out-of-scope "
                      f"override, row left untouched.")
                errors += 1
                continue
            has_sku  = fix.get("gcp_sku_id") and fix.get("gcp_sku_name")
            has_mult = fix.get("unit_multiplier") is not None
            if not has_sku and not has_mult:
                print(f"WARNING: override for {key} has no actionable fields — skipping")
                errors += 1
                continue
            # Content-check the override itself — the scope check above only
            # verifies the ROW was allowed to be touched, not that the VALUES
            # being written are real (a hallucinated gcp_sku_id) or safe (un-
            # ignoring an intentionally-protected passthrough, or silently
            # crossing from a sustained AWS source onto a burstable GCP family).
            ok, why = validate_override(conn, key, fix.get("component"), fix)
            if not ok:
                print(f"WARNING: override for {key} rejected — {why}")
                errors += 1
                continue
            try:
                apply_override(conn, key, fix, notes)
                applied += 1
            except Exception as e:
                print(f"ERROR applying override for {key}: {e}", file=sys.stderr)
                errors += 1

    try:
        import render_notes
        render_notes.render_notes_for_db(conn)
    except Exception:
        pass

    conn.close()

    with open(NOTES_FILE, "a") as f:
        if notes:
            f.write("\n\n## Phase 3 Review Fixes\n\n")
            for note in notes:
                f.write(note + "\n")

    print(f"apply_review_fixes: applied={applied} vetoed={vetoed} errors={errors}")


if __name__ == "__main__":
    main()
