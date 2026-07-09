#!/usr/bin/env python3
"""
auto_triage.py — Phase 5 suggestion engine. NEVER touches the database.

Reads outliers_data.json (written by detect_outliers.py) and the DB (read-only).
Computes candidate fixes for structural outliers (D/E/G/B/C/H/I) with confidence labels.
Enriches pricing outliers (A1/A2/F) with full context — NO candidate suggested
(word-overlap re-resolution is the mechanism behind Glacier 120x and S3 50x inflation).

Writes:
  triage_suggestions.md   — LLM input: all rows with candidates or enriched context
  triage_candidates.json  — machine-readable candidates for apply_outlier_fixes.py
"""
import duckdb
import json
import os
import sys

SKILL_DIR = os.environ.get("SKILL_DIR", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))
from apply_static_mappings import resolve_sku

JOB_DIR        = os.getcwd()
DB_PATH        = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
DATA_FILE      = os.path.join(JOB_DIR, "outliers_data.json")
SUGGESTIONS_MD = os.path.join(JOB_DIR, "triage_suggestions.md")
CANDIDATES_JSON = os.path.join(JOB_DIR, "triage_candidates.json")


def main():
    if not os.path.exists(DATA_FILE):
        print("outliers_data.json not found — detect_outliers.py may have failed.")
        sys.exit(1)

    with open(DATA_FILE) as f:
        data = json.load(f)

    total = data.get("total", 0)
    if total == 0:
        # No outliers — write empty suggestion file and exit cleanly.
        with open(SUGGESTIONS_MD, "w") as f:
            f.write("# Triage Suggestions\n\nNo outliers detected. Return `[]`.\n")
        with open(CANDIDATES_JSON, "w") as f:
            json.dump({}, f)
        print("auto_triage: 0 outliers — nothing to triage.")
        return

    # Open DB read-only for context lookups.
    conn = duckdb.connect(DB_PATH, read_only=True)

    candidates = {}  # aws_li_key -> candidate dict

    # ------------------------------------------------------------------ #
    # Structural outliers — compute candidates                             #
    # ------------------------------------------------------------------ #

    # B: Phantom cost — AWS ~$0 but GCP cost is large → unit_multiplier bug
    for row in data.get("B", []):
        key = row["aws_li_key"]
        candidates[key] = {
            "query": "B",
            "confidence": "HIGH",
            "candidate": {
                "action": "set_multiplier",
                "unit_multiplier": 0.0,
                "reason": "Phantom cost: AWS cost ~$0 but GCP cost non-zero. unit_multiplier=0 eliminates phantom."
            }
        }

    # C: Zero rate on billable row → try resolve_sku for a different SKU
    for row in data.get("C", []):
        key = row["aws_li_key"]
        gcp_service = row.get("gcp_service", "")
        gcp_sku_name = row.get("gcp_sku_name", "")
        # Try to find a SKU in the catalog with a non-zero rate
        region = _lookup_region(conn, key)
        candidate = None
        confidence = "LOW"
        if gcp_service and gcp_sku_name:
            try:
                sku_id = resolve_sku(gcp_service, gcp_sku_name, region)
                if sku_id and sku_id != row.get("gcp_sku_id"):
                    candidate = {
                        "action": "set_sku",
                        "gcp_sku_id": sku_id,
                        "gcp_sku_name": gcp_sku_name,
                        "reason": f"Zero rate on current SKU {row.get('gcp_sku_id')} — found alternate SKU with same name"
                    }
            except Exception:
                pass
        candidates[key] = {
            "query": "C",
            "confidence": confidence,
            "candidate": candidate,
        }

    # D: Cross-service mismatch → fix gcp_service to match catalog value
    for row in data.get("D", []):
        key = row["aws_li_key"]
        correct_service = row.get("sku_actually", "")
        candidates[key] = {
            "query": "D",
            "confidence": "HIGH",
            "candidate": {
                "action": "set_service",
                "gcp_service": correct_service,
                "reason": f"SKU catalog says service is '{correct_service}', mapping says '{row.get('mapping_says')}'"
            }
        }

    # E: Missing CUD alias → synthesize from OnDemand SKU (LOW — requires catalog search)
    for row in data.get("E", []):
        key = row["aws_li_key"]
        # Can't compute the Commit1Yr SKU ID without a catalog search.
        # Provide the OnDemand SKU ID as context so LLM can find the paired CUD SKU.
        candidates[key] = {
            "query": "E",
            "confidence": "LOW",
            "candidate": None,
            "context": {
                "gcp_service": row.get("gcp_service"),
                "od_sku_id": row.get("gcp_sku_id"),
                "hint": "Find the Commit1Yr SKU paired with this OnDemand SKU and INSERT into gcp_sku_rates."
            }
        }

    # G: break_down multiplier mismatch → spec_value is ground truth
    for row in data.get("G", []):
        key = row["aws_li_key"]
        component = row.get("component", "core")
        spec_val = row.get("spec_value")
        if spec_val is not None:
            candidates[key] = {
                "query": "G",
                "confidence": "HIGH",
                "candidate": {
                    "action": "set_multiplier",
                    "component": component,
                    "unit_multiplier": float(spec_val),
                    "reason": f"Instance spec says {component}={spec_val}, mapping has {row.get('mapped')}"
                }
            }
        else:
            candidates[key] = {"query": "G", "confidence": "NONE", "candidate": None}

    # H: RI/CUD parity — CUD rate row missing in gcp_sku_rates
    for row in data.get("H", []):
        key = row["aws_li_key"]
        candidates[key] = {
            "query": "H",
            "confidence": "LOW",
            "candidate": None,
            "context": {
                "gcp_service": row.get("gcp_service"),
                "od_sku_id": row.get("gcp_sku_id"),
                "gcp_od": row.get("gcp_od"),
                "hint": "1yr CUD equals OD rate — find and INSERT the Commit1Yr rate row into gcp_sku_rates."
            }
        }

    # I: NULL projection — diagnose cause (region or SKU missing)
    for row in data.get("I", []):
        key = row["aws_li_key"]
        gcp_region = row.get("gcp_region")
        gcp_sku_id = row.get("gcp_sku_id")
        if not gcp_region:
            candidates[key] = {
                "query": "I",
                "confidence": "HIGH",
                "candidate": {
                    "action": "set_region",
                    "table": "aws_li_catalog",
                    "gcp_region": "us-central1",
                    "reason": "NULL gcp_region prevents rate lookup — default to us-central1"
                }
            }
        elif not gcp_sku_id:
            candidates[key] = {
                "query": "I",
                "confidence": "HIGH",
                "candidate": {
                    "action": "needs_sku",
                    "reason": "gcp_sku_id is NULL — SKU was never resolved. Assign correct SKU."
                }
            }
        else:
            candidates[key] = {
                "query": "I",
                "confidence": "LOW",
                "candidate": None,
                "context": {
                    "gcp_service": row.get("gcp_service"),
                    "gcp_sku_id": gcp_sku_id,
                    "gcp_region": gcp_region,
                    "hint": "SKU and region both present but cost is NULL — rate missing for this SKU+region combination."
                }
            }

    conn.close()

    # ------------------------------------------------------------------ #
    # Write triage_candidates.json                                         #
    # ------------------------------------------------------------------ #

    with open(CANDIDATES_JSON, "w") as f:
        json.dump(candidates, f, indent=2, default=str)

    # ------------------------------------------------------------------ #
    # Write triage_suggestions.md                                          #
    # ------------------------------------------------------------------ #

    structural_rows = (
        data.get("B", []) + data.get("C", []) + data.get("D", []) +
        data.get("E", []) + data.get("G", []) + data.get("H", []) + data.get("I", [])
    )
    pricing_rows = data.get("A1", []) + data.get("A2", []) + data.get("F", [])

    with open(SUGGESTIONS_MD, "w") as f:
        f.write("# Triage Suggestions\n\n")
        f.write(f"Structural outliers: {data.get('total_structural', 0)}  |  "
                f"Pricing outliers: {data.get('total_pricing', 0)}\n\n")
        f.write("Return `outlier_fixes.json` — a JSON array:\n")
        f.write('```json\n[{"aws_li_key": "...", "decision": "confirm|override|veto",\n'
                '  "gcp_sku_id": "...", "gcp_sku_name": "...", "unit_multiplier": N,\n'
                '  "gcp_service": "...", "gcp_region": "...", "reason": "..."}]\n```\n\n')
        f.write("- `confirm`: apply the pre-computed candidate exactly\n")
        f.write("- `override`: apply your own values (include the fields you want changed)\n")
        f.write("- `veto`: leave row unchanged, document in reason (rate gap)\n\n")
        f.write("---\n\n")

        if structural_rows:
            f.write("## Structural Outliers (pre-computed candidates available)\n\n")
            f.write("HIGH confidence = ground truth from spec/catalog — confirm unless something is visibly wrong.\n")
            f.write("LOW confidence = attempted resolution, validate the candidate carefully.\n\n")

            for row in structural_rows:
                key = row["aws_li_key"]
                cand_info = candidates.get(key, {})
                conf = cand_info.get("confidence", "NONE")
                cand = cand_info.get("candidate")
                ctx  = cand_info.get("context", {})
                qid  = cand_info.get("query", "?")

                f.write(f"### `{key}` — Query {qid} — Confidence: `{conf}`\n\n")

                # Write available row fields as context
                for k, v in row.items():
                    if k != "aws_li_key" and v is not None:
                        f.write(f"- **{k}**: `{v}`\n")

                if cand:
                    f.write(f"\n**Candidate**: `{cand.get('action')}` — {cand.get('reason', '')}\n")
                    for ck, cv in cand.items():
                        if ck not in ("action", "reason") and cv is not None:
                            f.write(f"  - {ck}: `{cv}`\n")
                elif ctx:
                    f.write(f"\n**Context** (no candidate — reason LLM):\n")
                    for ck, cv in ctx.items():
                        f.write(f"  - {ck}: {cv}\n")
                else:
                    f.write("\n**No candidate** — requires LLM reasoning.\n")
                f.write("\n")

        if pricing_rows:
            f.write("---\n\n")
            f.write("## Pricing Outliers (context only — no candidate suggested)\n\n")
            f.write("Word-overlap catalog re-resolution is how Glacier 120x and S3 50x inflation happened.\n")
            f.write("Use the current SKU name, ratio, and projection_note to reason about the correct fix.\n\n")

            for row in pricing_rows:
                key = row["aws_li_key"]
                qid = _infer_pricing_query(row, data)
                f.write(f"### `{key}` — Query {qid}\n\n")
                for k, v in row.items():
                    if k != "aws_li_key" and v is not None:
                        f.write(f"- **{k}**: `{v}`\n")
                # Fetch projection_note for this row directly — it has diagnostic value
                f.write("\n")

    n_high = sum(1 for c in candidates.values() if c.get("confidence") == "HIGH" and c.get("candidate"))
    n_low  = sum(1 for c in candidates.values() if c.get("confidence") == "LOW")
    print(f"auto_triage: {len(structural_rows)} structural ({n_high} HIGH, {n_low} LOW), "
          f"{len(pricing_rows)} pricing (context-only)")


def _lookup_region(conn, aws_li_key):
    try:
        rows = conn.execute(
            "SELECT gcp_region FROM aws_li_catalog WHERE aws_li_key = ?", [aws_li_key]
        ).fetchone()
        return rows[0] if rows else "us-central1"
    except Exception:
        return "us-central1"


def _infer_pricing_query(row, data):
    key = row["aws_li_key"]
    for qid in ("A1", "A2", "F"):
        if any(r["aws_li_key"] == key for r in data.get(qid, [])):
            return qid
    return "?"


if __name__ == "__main__":
    main()
