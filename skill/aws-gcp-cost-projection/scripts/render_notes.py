#!/usr/bin/env python3
"""render_notes.py <projection.duckdb>

Regenerates projection_note for every row from the row's structured state
fields. This is the ONLY writer of projection_note in the pipeline.

The note is OUTPUT: it is composed from state, never parsed back. Running this
twice produces identical text, so there are no idempotency guards anywhere.

Always exits 0 — a note-rendering problem must never fail a customer-facing run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb
import row_state as rs

try:
    from passthrough_reasons import REASONS as PT_REASONS
except Exception:
    PT_REASONS = {}


def _fill(template, row):
    try:
        return template.format(**row)
    except Exception:
        return template


def compose(row):
    """Return the human-readable note for one row. Pure function of state."""
    parts = []
    for field in rs.NOTE_ORDER:
        val = row.get(field)
        if not val:
            continue
        if field == "passthrough_reason":
            desc = (PT_REASONS.get(val) or {}).get("description")
            if desc:
                parts.append(desc)
        elif field == "review_flags":
            for flag in rs.flags_of(val):
                t = (rs.REVIEW_FLAGS.get(flag) or {}).get("note_template")
                if t:
                    parts.append(_fill(t, row))
        elif field == "mapper_note":
            parts.append(str(val))
        else:
            domain = getattr(rs, field.upper(), {}) or {}
            t = (domain.get(val) or {}).get("note_template")
            if t:
                parts.append(_fill(t, row))
    # de-duplicate while preserving order
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return "; ".join(out)


def render_notes_for_db(con):
    """Regenerate projection_note on con from state fields."""
    cols = ["aws_li_key", "component", "strategy", "passthrough_reason",
            "rate_source", "rate_region_basis", "rate_region_used",
            "license_status", "review_flags", "mapper_note"]
    # Ensure schema first in case columns are missing
    rs.ensure_schema(con)

    cat_cols = set()
    try:
        cat_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
    except Exception:
        pass

    if "gcp_region" in cat_cols:
        reg_expr = "c.gcp_region"
        join_clause = "LEFT JOIN aws_li_catalog c USING(aws_li_key)"
    else:
        reg_expr = "NULL AS gcp_region"
        join_clause = ""

    rows = con.execute(
        "SELECT m.rowid, " + ", ".join("m." + c for c in cols) +
        ", " + reg_expr + " FROM aws_li_to_gcp_li m " + join_clause
    ).fetchall()
    updates = []
    for raw in rows:
        row_id = raw[0]
        row = dict(zip(cols + ["gcp_region"], raw[1:]))
        updates.append((compose(row), row_id))
    if updates:
        con.executemany(
            "UPDATE aws_li_to_gcp_li SET projection_note = ? WHERE rowid = ?",
            updates,
        )
    return len(updates)


def main():
    if len(sys.argv) < 2:
        sys.exit(0)
    con = duckdb.connect(sys.argv[1])
    try:
        n = render_notes_for_db(con)
        print("render_notes: regenerated %d note(s)" % n)
    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
        sys.exit(0)
    except Exception as e:
        print("render_notes: failed (%s) — continuing" % e)
        sys.exit(0)
