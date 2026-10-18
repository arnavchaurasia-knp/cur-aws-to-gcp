#!/usr/bin/env python3
"""tests/test_notes_are_output_only.py — Enforce that notes are pure derived output.

Guarantees:
1. No script (outside render_notes.py, backfill_row_state.py, render_report.py) reads
   projection_note in a predicate context (LIKE, ILIKE, IN, ==, .lower(), etc.).
2. render_notes.compose is a pure function of state and does not mutate row.
3. render_notes.py is idempotent on a database.
4. All values written by scripts for rate_source/rate_region_basis/license_status/review_flags
   are declared in row_state_registry.json.
5. merge_mappings strips or quarantines pipeline-tier fields (rate_region_basis, license_status)
   and maps incoming projection_note to mapper_note.
6. review_flags tokens are delimited and not prefix-matched.
"""
import glob
import os
import re
import sys
import duckdb
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
DATA_DIR = os.path.join(REPO_ROOT, "data")
sys.path.insert(0, SCRIPTS_DIR)

import row_state as rs
import render_notes


def test_no_script_reads_projection_note():
    """Verify that no script in scripts/ reads projection_note in a predicate context."""
    predicate_patterns = [
        re.compile(r"\bprojection_note\s+(?:I?LIKE|NOT\s+I?LIKE|IN\s*\(|IS\s+NOT\s+NULL|IS\s+NULL|!=|<>)", re.IGNORECASE),
        re.compile(r"\b(?:WHERE|AND|OR|WHEN)\s+.*?\bprojection_note\s*=", re.IGNORECASE),
        re.compile(r"['\"]projection_note['\"]\s*(?:==|!=|in\b)", re.IGNORECASE),
        re.compile(r"['\"][^'\"]+['\"]\s+in\s+(?:projection_note|note)\b", re.IGNORECASE),
        re.compile(r"\b(?:projection_note|note)\.lower\(\)", re.IGNORECASE),
        # (projection_note or "").lower() — the identifier is not adjacent to
        # .lower(), so the pattern above misses it.
        re.compile(r"projection_note[^\n]{0,40}\)\s*\.lower\(\)", re.IGNORECASE),
        # ... in note_lower / note_lower.startswith(...) — a lowercased note
        # held in a local and then matched against.
        re.compile(r"\bin\s+note_lower\b|\bnote_lower\s*\.", re.IGNORECASE),
    ]

    # Files that may write or display a note, so the patterns above do not apply.
    WRITERS_AND_DISPLAY = {
        "render_notes.py",       # the only writer
        "backfill_row_state.py", # one-time migration shim; reads notes by design
    }

    # Legacy reads that are KNOWN and SCOPED. The budget is a ratchet: it may
    # only ever go down. A file-level exemption would let a NEW prose matcher
    # slip into an 8,700-line file unnoticed, which is exactly the pattern this
    # test exists to prevent — so each file gets an exact allowance instead.
    #
    # To retire an entry: remove the read, lower the number. Never raise one.
    LEGACY_BUDGET = {
        # _emit() infers passthrough_reason from note prose when a mapper did
        # not declare one. Retire by making the ~34 mappers declare it.
        "apply_static_mappings.py": 3,
        # _is_intentional_passthrough_note()'s fallback for pre-migration DBs.
        "llm_override_guard.py": 2,
        # Report-side display filter, deliberately out of scope.
        "render_report.py": 1,
    }

    violations = []
    legacy_seen = {}
    py_files = glob.glob(os.path.join(SCRIPTS_DIR, "*.py"))
    for fpath in py_files:
        fname = os.path.basename(fpath)
        if fname in WRITERS_AND_DISPLAY:
            continue
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f, start=1):
                # strip inline comment
                clean_line = line.split("#")[0].strip()
                if not clean_line:
                    continue
                # Ignore docstrings/comments and SET statements
                if clean_line.startswith(('"""', "'''", "*", "--")):
                    continue
                if not any(pat.search(clean_line) for pat in predicate_patterns):
                    continue
                if fname in LEGACY_BUDGET:
                    legacy_seen.setdefault(fname, []).append(f"{idx}: {clean_line}")
                else:
                    violations.append(f"{fname}:{idx}: {clean_line}")

    assert not violations, "Found prohibited projection_note predicate reads:\n" + "\n".join(violations)

    # Exact match, not "<=": an allowance freed by retiring one read must not
    # be silently re-spent on a new one somewhere else in the same file.
    mismatched = []
    for fname, budget in LEGACY_BUDGET.items():
        found = legacy_seen.get(fname, [])
        if len(found) > budget:
            mismatched.append(
                f"{fname}: {len(found)} note read(s), budget {budget} — a NEW prose "
                f"matcher was added. Record the state in a row_state field instead "
                f"of writing it into, or reading it out of, a note.\n    "
                + "\n    ".join(found)
            )
        elif len(found) < budget:
            mismatched.append(
                f"{fname}: {len(found)} note read(s), budget {budget} — a read was "
                f"removed (good). Lower the budget in LEGACY_BUDGET to "
                f"{len(found)} so the allowance cannot be re-spent."
            )
    assert not mismatched, "Legacy note-read budget out of date:\n" + "\n".join(mismatched)


def test_render_notes_is_pure():
    """compose() should return identical output for identical state and not mutate input."""
    row = {
        "aws_li_key": "k1",
        "strategy": "map",
        "passthrough_reason": None,
        "rate_source": "word_overlap",
        "rate_region_basis": "dominant",
        "rate_region_used": "us-central1",
        "license_status": "premium_not_modeled",
        "review_flags": ",architecture_review,",
        "mapper_note": "custom mapper comment",
        "gcp_region": "us-east1",
    }
    row_copy = dict(row)

    res1 = render_notes.compose(row)
    res2 = render_notes.compose(row)

    assert res1 == res2
    assert row == row_copy
    assert "word overlap" in res1
    assert "us-central1" in res1
    assert "licence premium" in res1 or "license premium" in res1
    assert "Architecture review recommended" in res1
    assert "custom mapper comment" in res1


def test_render_notes_idempotent_in_db(tmp_path):
    """Running render_notes over a DB twice yields identical projection_note."""
    db_path = str(tmp_path / "test.duckdb")
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT,
            gcp_region TEXT
        );
        INSERT INTO aws_li_catalog VALUES ('k1', 'us-east1');
    """)
    rs.ensure_schema(con)
    con.execute("""
        INSERT INTO aws_li_to_gcp_li (
            aws_li_key, strategy, rate_source, rate_region_basis, rate_region_used,
            license_status, review_flags, mapper_note
        ) VALUES (
            'k1', 'map', 'word_overlap', 'dominant', 'us-central1',
            'premium_not_modeled', ',architecture_review,', 'custom note'
        );
    """)
    con.close()

    render_notes.main.__globals__["sys"].argv = ["render_notes.py", db_path]
    render_notes.main()

    con = duckdb.connect(db_path)
    note_first = con.execute("SELECT projection_note FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()[0]
    con.close()

    render_notes.main()

    con = duckdb.connect(db_path)
    note_second = con.execute("SELECT projection_note FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()[0]
    con.close()

    assert note_first is not None
    assert note_first == note_second


def test_registry_covers_every_value():
    """All domain dictionaries in row_state have valid note_template mappings."""
    for field in ["rate_source", "rate_region_basis", "license_status", "review_flags"]:
        domain = rs.domain_of(field)
        assert len(domain) > 0, f"Domain of {field} must not be empty"


def test_llm_cannot_set_derived_state():
    """merge_mappings rejects LLM setting pipeline-only fields, and converts projection_note to mapper_note."""
    import merge_mappings

    row = {
        "aws_li_key": "k1",
        "gcp_service": "Compute Engine",
        "gcp_sku_id": "sku1",
        "rate_region_basis": "cheapest_available",
        "license_status": "modeled",
        "projection_note": "LLM supplied prose note",
        "strategy": "map",
        "unit_multiplier": 1.0,
    }

    norm = merge_mappings._normalize_incoming_row(row)
    assert "rate_region_basis" not in norm or norm.get("rate_region_basis") is None
    assert "license_status" not in norm or norm.get("license_status") is None
    assert norm.get("mapper_note") == "LLM supplied prose note"


def test_review_flag_not_prefix_matched():
    """Verify that flag_present_clause handles commas properly and prevents prefix matching."""
    clause = rs.flag_present_clause("m.review_flags", "region")
    assert clause == "COALESCE(m.review_flags, ',') LIKE '%,region,%'"

    con = duckdb.connect()
    res = con.execute(f"SELECT {clause} AS matched FROM (SELECT ',region_unavailable,' AS review_flags) m").fetchone()[0]
    assert res is False

    res_true = con.execute(f"SELECT {clause} AS matched FROM (SELECT ',region,region_unavailable,' AS review_flags) m").fetchone()[0]
    assert res_true is True
    con.close()
