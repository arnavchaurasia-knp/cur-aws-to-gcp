#!/usr/bin/env python3
"""
tests/test_equivalences.py — Test equivalence crosswalk registry, loader, review, and promotion.

Required tests from WORKORDER_equivalence_crosswalk_review.md:
1. test_loader_rejects_malformed — missing scope, unknown degree, inexact without comment or cap each raise.
2. test_lookup_is_scoped — a claim scoped to storage_time does not match a retrieval lookup.
3. test_unknown_is_not_allowed — lookup() for an unlisted pair returns None, and helper treats None as failure.
4. test_disjoint_is_a_real_answer — glacier_flexible -> standard disjoint claim is found and reported as explicitly not equivalent.
5. test_conflicting_claims_rejected — two claims for the same (attribute, aws, gcp, scope) with different degrees fail the load.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import pytest

from scripts.equivalences import (
    Claim,
    EquivalenceValidationError,
    UnknownEquivalenceError,
    all_claims,
    confidence_cap,
    is_provisional,
    load_equivalences,
    lookup,
    require_claim,
)
from scripts.equivalence_review import run_review
from scripts.promote_equivalence import promote_claim


def test_loader_rejects_malformed(tmp_path):
    """Missing scope, unknown degree, inexact without comment or cap each raise."""
    base_meta = {
        "_meta": {
            "format": "crosswalk",
            "degrees": ["equal", "equivalent", "wider", "narrower", "inexact", "unmatched", "disjoint"]
        }
    }

    # 1. Missing scope
    bad_scope = dict(base_meta)
    bad_scope["test_attr"] = [{
        "aws": "foo", "gcp": "bar", "degree": "equal", "scope": [], "comment": "test"
    }]
    p1 = tmp_path / "bad_scope.json"
    p1.write_text(json.dumps(bad_scope), encoding="utf-8")
    with pytest.raises(EquivalenceValidationError, match="scope is required and non-empty"):
        load_equivalences(str(p1), reload=True)

    # 2. Unknown degree
    bad_degree = dict(base_meta)
    bad_degree["test_attr"] = [{
        "aws": "foo", "gcp": "bar", "degree": "super_similar", "scope": ["storage_time"]
    }]
    p2 = tmp_path / "bad_degree.json"
    p2.write_text(json.dumps(bad_degree), encoding="utf-8")
    with pytest.raises(EquivalenceValidationError, match="Unknown degree 'super_similar'"):
        load_equivalences(str(p2), reload=True)

    # 3. Inexact without comment
    no_comment = dict(base_meta)
    no_comment["test_attr"] = [{
        "aws": "foo", "gcp": "bar", "degree": "inexact", "scope": ["storage_time"],
        "confidence_cap": 0.8
    }]
    p3 = tmp_path / "no_comment.json"
    p3.write_text(json.dumps(no_comment), encoding="utf-8")
    with pytest.raises(EquivalenceValidationError, match="requires non-empty comment"):
        load_equivalences(str(p3), reload=True)

    # 4. Inexact without confidence_cap
    no_cap = dict(base_meta)
    no_cap["test_attr"] = [{
        "aws": "foo", "gcp": "bar", "degree": "inexact", "scope": ["storage_time"],
        "comment": "Caveat stated"
    }]
    p4 = tmp_path / "no_cap.json"
    p4.write_text(json.dumps(no_cap), encoding="utf-8")
    with pytest.raises(EquivalenceValidationError, match="requires confidence_cap"):
        load_equivalences(str(p4), reload=True)


def test_lookup_is_scoped():
    """A claim scoped to storage_time does not match a retrieval lookup."""
    # Seed data has glacier_flexible -> coldline with scope ["storage_time"]
    claim_storage = lookup("storage_class", "glacier_flexible", "coldline", "storage_time")
    assert claim_storage is not None
    assert claim_storage.degree == "inexact"
    assert "storage_time" in claim_storage.scope

    # Same pair, different scope -> must return None
    claim_retrieval = lookup("storage_class", "glacier_flexible", "coldline", "retrieval")
    assert claim_retrieval is None


def test_unknown_is_not_allowed():
    """lookup() for an unlisted pair returns None, and the helper that callers use treats None as failure."""
    # lookup() returns None for unlisted pair
    unlisted = lookup("storage_class", "non_existent_aws_tier", "coldline", "storage_time")
    assert unlisted is None

    # require_claim() treats None as failure and raises UnknownEquivalenceError
    with pytest.raises(UnknownEquivalenceError, match="No equivalence claim for storage_class"):
        require_claim("storage_class", "non_existent_aws_tier", "coldline", "storage_time")


def test_disjoint_is_a_real_answer():
    """The glacier_flexible -> standard disjoint claim is found and reported as explicitly not equivalent."""
    claim = lookup("storage_class", "glacier_flexible", "standard", "storage_time")
    assert claim is not None
    assert claim.degree == "disjoint"
    assert "120x" in (claim.comment or "")
    assert claim.decided_by == "human"


def test_conflicting_claims_rejected(tmp_path):
    """Two claims for the same (attribute, aws, gcp, scope) with different degrees fail the load."""
    doc = {
        "_meta": {
            "format": "crosswalk",
            "degrees": ["equal", "equivalent", "wider", "narrower", "inexact", "unmatched", "disjoint"]
        },
        "storage_class": [
            {
                "aws": "glacier_flexible",
                "gcp": "coldline",
                "degree": "inexact",
                "scope": ["storage_time"],
                "comment": "Initial caveat",
                "confidence_cap": 0.75,
                "decided_by": "human",
                "decided_on": "2026-03-11"
            },
            {
                "aws": "glacier_flexible",
                "gcp": "coldline",
                "degree": "equal",  # CONFLICT!
                "scope": ["storage_time"],
                "comment": "Contradictory claim",
                "decided_by": "human",
                "decided_on": "2026-03-11"
            }
        ]
    }
    p = tmp_path / "conflict.json"
    p.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(EquivalenceValidationError, match="Conflicting claims for"):
        load_equivalences(str(p), reload=True)


def test_is_provisional_and_confidence_cap():
    """Check is_provisional and confidence_cap helpers."""
    human_claim = Claim(
        attribute="storage_class",
        aws="standard",
        gcp="standard",
        degree="equal",
        scope=["storage_time"],
        decided_by="human",
    )
    assert not is_provisional(human_claim)
    assert confidence_cap(human_claim) is None

    llm_claim = Claim(
        attribute="acu",
        aws="acu",
        gcp="vcpu_ram",
        degree="inexact",
        scope=["compute"],
        comment="ACU footprint heuristic",
        confidence_cap=0.6,
        decided_by="llm",
    )
    assert is_provisional(llm_claim)
    assert confidence_cap(llm_claim) == 0.6


def test_promotion_tool_dry_run_and_apply(tmp_path):
    """Test promote_equivalence.py dry-run and apply logic."""
    doc = {
        "_meta": {
            "format": "crosswalk",
            "degrees": ["equal", "equivalent", "wider", "narrower", "inexact", "unmatched", "disjoint"]
        },
        "test_attr": [
            {
                "aws": "src_tier",
                "gcp": "dest_tier",
                "degree": "equivalent",
                "scope": ["general"],
                "comment": "Model proposal",
                "decided_by": "llm",
                "decided_on": "2026-01-01"
            }
        ]
    }
    fpath = str(tmp_path / "test_equivalences.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(doc, f)

    # 1. Missing reviewer or comment fails
    rc_no_rev = promote_claim(
        attribute="test_attr",
        aws="src_tier",
        gcp="dest_tier",
        reviewer="",
        comment="Looks good",
        filepath=fpath,
    )
    assert rc_no_rev != 0

    # 2. Dry run does not modify file
    rc_dry = promote_claim(
        attribute="test_attr",
        aws="src_tier",
        gcp="dest_tier",
        reviewer="alice",
        comment="Human verified spec parity",
        apply=False,
        filepath=fpath,
    )
    assert rc_dry == 0
    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["test_attr"][0]["decided_by"] == "llm"

    # 3. Apply commits changes atomically
    rc_apply = promote_claim(
        attribute="test_attr",
        aws="src_tier",
        gcp="dest_tier",
        reviewer="alice",
        comment="Human verified spec parity",
        apply=True,
        filepath=fpath,
    )
    assert rc_apply == 0
    with open(fpath, "r", encoding="utf-8") as f:
        data_after = json.load(f)
    promoted = data_after["test_attr"][0]
    assert promoted["decided_by"] == "human"
    assert "Reviewed by alice" in promoted["comment"]


def test_equivalence_review_report(tmp_path, capsys):
    """Test equivalence_review.py on empty and populated databases."""
    db_file = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE equivalence_usage (
            job_id TEXT NOT NULL,
            attribute TEXT NOT NULL,
            aws_value TEXT NOT NULL,
            gcp_value TEXT NOT NULL,
            scope TEXT NOT NULL,
            rows_used INTEGER NOT NULL,
            usd_exposed REAL NOT NULL,
            recorded_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    # 1. Empty database
    rc_empty = run_review(db_path=db_file)
    assert rc_empty == 0
    captured = capsys.readouterr().out
    assert "Database is empty" in captured
    assert "REVIEWED CLAIMS: 8" in captured

    # 2. Populated database with unknown pair and reviewed pair
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO equivalence_usage VALUES
        ('job-1', 'storage_class', 'standard', 'standard', 'storage_time', 10, 500.0, '2026-03-12T00:00:00Z'),
        ('job-2', 'mystery_family', 'weird_aws', 'some_gcp', 'default', 2, 8500.0, '2026-03-12T00:00:00Z')
    """)
    conn.commit()
    conn.close()

    rc_pop = run_review(db_path=db_file)
    assert rc_pop == 0
    out_pop = capsys.readouterr().out
    assert "UNKNOWN PAIRS HIT (AUTHORING BACKLOG)" in out_pop
    assert "weird_aws -> some_gcp" in out_pop
    assert "UNKNOWN PAIRS HIT: 1" in out_pop
