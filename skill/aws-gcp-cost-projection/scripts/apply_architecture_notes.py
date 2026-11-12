#!/usr/bin/env python3
"""apply_architecture_notes.py — record modelling disclosures as pipeline_note.

Some services cannot be fully modelled from a CUR: the bill shows what ran, but
not the topology that determines the GCP shape. A reader needs to know which
rows rest on an assumption nobody could verify from the input.

These disclosures used to live in `calibrate_confidence.py`, which appended them
while also clamping `mapping_confidence`. When the confidence subsystem was
removed the notes went with it — 147 rows across $17,964 of AWS spend silently
lost their caveat, which made the report look MORE certain, not less. The notes
were never about confidence; they just travelled with it.

Usage:
    python3 apply_architecture_notes.py <projection.duckdb>

Idempotent: a note is appended only if that row does not already carry it.
"""
import json
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import duckdb

SKILL_DIR = os.environ.get("SKILL_DIR") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(SKILL_DIR, "data")


def load_disclosures():
    path = os.path.join(DATA_DIR, "architecture-notes.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("disclosures", [])
    except Exception as exc:
        print(f"apply_architecture_notes: could not read {path}: {exc}",
              file=sys.stderr)
        return []


def apply(con, disclosures):
    try:
        import row_state as _rs
        _rs.ensure_schema(con)
    except Exception:
        pass
    total = 0
    for d in disclosures:
        note = (d.get("note") or "").strip()
        pats = d.get("product_ilike_patterns") or []
        if not note or not pats:
            continue

        prod_clause = " OR ".join("c.product ILIKE ?" for _ in pats)
        # The disclosure text is merged idempotently into pipeline_note (a
        # structured, output-only column); render_notes.py composes the
        # customer-facing projection_note from it. The CASE handles de-duplication
        # internally, so re-running is safe without reading the note for logic.
        params = [note, note, note] + list(pats)

        gcp_clause = ""
        if d.get("gcp_service_ilike"):
            gcp_clause = " AND m.gcp_service ILIKE ?"
            params.append(d["gcp_service_ilike"])

        sql = f"""
            UPDATE aws_li_to_gcp_li AS m
            SET pipeline_note = CASE
                WHEN COALESCE(m.pipeline_note, '') = '' THEN ?
                WHEN COALESCE(m.pipeline_note, '') NOT LIKE '%' || ? || '%' THEN m.pipeline_note || ' · ' || ?
                ELSE m.pipeline_note
            END
            FROM aws_li_catalog AS c
            WHERE c.aws_li_key = m.aws_li_key
              AND ({prod_clause}){gcp_clause}
        """
        try:
            # DuckDB returns the affected row count from the DML itself;
            # there is no changes() scalar function.
            res = con.execute(sql, params).fetchone()
            n = int(res[0]) if res else 0
        except Exception as exc:
            print(f"apply_architecture_notes: {d.get('label')}: {exc}",
                  file=sys.stderr)
            continue
        if n:
            print(f"  {d.get('label')}: {n} row(s) annotated")
            total += n
    if total:
        try:
            import render_notes
            render_notes.render_notes_for_db(con)
        except Exception:
            pass
    return total


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        return 2
    db_path = sys.argv[1]
    if not os.path.exists(db_path):
        print(f"apply_architecture_notes: {db_path} not found", file=sys.stderr)
        return 0  # soft: never block the pipeline for a missing DB

    disclosures = load_disclosures()
    if not disclosures:
        print("apply_architecture_notes: no disclosures configured")
        return 0

    con = duckdb.connect(db_path)
    try:
        cols = {r[1] for r in con.execute(
            "PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
        if "pipeline_note" not in cols:
            # Legacy DB without the structured column; ensure it exists.
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import row_state as _rs
                _rs.ensure_schema(con)
            except Exception:
                return 0
        total = apply(con, disclosures)
        con.commit()
    finally:
        con.close()
    print(f"apply_architecture_notes: {total} row(s) annotated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
