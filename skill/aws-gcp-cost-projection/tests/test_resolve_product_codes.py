"""
Tier-3 product-code backfill tests (resolve_unknown_product_codes.py).

Covers the deterministic tiers (cache + canonical->code), the pydantic/closed-set
validation of the agy residue, and end-to-end stamping on a no-code bill. The
agy call itself is not exercised (no agy on a dev box, and tier-3 is designed to
no-op without it) — but the validation gate that guards its output is.
"""
import json
import os

import duckdb
import pytest

import resolve_unknown_product_codes as R

_CODES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "aws-product-codes.json")


def _codes_doc():
    return json.load(open(_CODES_PATH, encoding="utf-8"))


# ---------------------------------------------------------------------------
# Reverse map (canonical -> representative code)
# ---------------------------------------------------------------------------
def test_reverse_map_present_and_consistent():
    doc = _codes_doc()
    codes, rev = doc["codes"], doc["canonical_to_code"]
    assert rev, "canonical_to_code missing — regenerate aws-product-codes.json"
    # Every representative code is a real code, and it maps back to its canonical.
    for canonical, code in rev.items():
        assert code in codes, code
        assert codes[code] == canonical, (canonical, code)


def test_reverse_map_picks_clean_code():
    rev = _codes_doc()["canonical_to_code"]
    assert rev["s3"] == "AmazonS3"
    assert rev["ec2"] == "AmazonEC2"
    assert rev["rds"] == "AmazonRDS"


# ---------------------------------------------------------------------------
# Tiers 1-2 — deterministic resolution
# ---------------------------------------------------------------------------
def test_deterministic_cache_hit():
    _codes, rev, valid = R._load_codes()
    cache = {"some weird bill label": "AmazonEC2"}
    assert R.resolve_deterministic("Some Weird Bill Label", cache, rev, valid) == "AmazonEC2"


def test_deterministic_cache_ignores_invalid_code():
    _codes, rev, valid = R._load_codes()
    cache = {"x": "NotARealCode"}
    # falls through cache to canonical (which also fails for "x") -> None
    assert R.resolve_deterministic("x", cache, rev, valid) is None


def test_deterministic_canonical_hit():
    _codes, rev, valid = R._load_codes()
    assert R.resolve_deterministic("Amazon Simple Storage Service", {}, rev, valid) == "AmazonS3"
    assert R.resolve_deterministic("Amazon Elastic Compute Cloud", {}, rev, valid) == "AmazonEC2"


def test_deterministic_miss_returns_none():
    _codes, rev, valid = R._load_codes()
    assert R.resolve_deterministic("Totally Unknown Widget 9000", {}, rev, valid) is None
    assert R.resolve_deterministic("", {}, rev, valid) is None


# ---------------------------------------------------------------------------
# Tier 3 — output validation (the closed-enum guardrail)
# ---------------------------------------------------------------------------
def test_validate_keeps_only_known_codes():
    valid = {"AmazonEC2", "AmazonS3"}
    raw = '{"foo svc": "AmazonEC2", "bar svc": "NotReal", "baz svc": "unknown"}'
    assert R._validate_llm_output(raw, valid) == {"foo svc": "AmazonEC2"}


def test_validate_strips_code_fences():
    valid = {"AmazonS3"}
    raw = '```json\n{"thing": "AmazonS3"}\n```'
    assert R._validate_llm_output(raw, valid) == {"thing": "AmazonS3"}


def test_validate_recovers_embedded_object():
    valid = {"AmazonEC2"}
    raw = 'Sure! Here you go:\n{"compute": "AmazonEC2"}\nHope that helps.'
    assert R._validate_llm_output(raw, valid) == {"compute": "AmazonEC2"}


def test_validate_malformed_returns_empty():
    valid = {"AmazonEC2"}
    assert R._validate_llm_output("not json at all", valid) == {}
    assert R._validate_llm_output("", valid) == {}


def test_residue_noops_without_agy(monkeypatch):
    # With agy absent, tier-3 must return nothing (tiers 1-2 already ran).
    monkeypatch.setattr(R.shutil, "which", lambda _b: None)
    assert R.resolve_residue_via_llm(["Weird Service"], {"AmazonEC2"}) == {}


# ---------------------------------------------------------------------------
# End-to-end stamping on a no-code bill (deterministic tiers only)
# ---------------------------------------------------------------------------
def test_end_to_end_stamps_no_code_rows(tmp_path, monkeypatch):
    db = str(tmp_path / "projection.duckdb")
    con = duckdb.connect(db)
    con.execute(
        "CREATE TABLE aws_li_catalog ("
        "aws_li_key VARCHAR, product VARCHAR, product_code VARCHAR, "
        "canonical_service VARCHAR, aws_amortized_cost DOUBLE)"
    )
    con.executemany(
        "INSERT INTO aws_li_catalog VALUES (?,?,?,?,?)",
        [
            # no-code rows (PDF-style): name present, product_code empty
            ("k1", "Amazon Simple Storage Service", "", None, 23.0),
            ("k2", "Amazon Elastic Compute Cloud", "", None, 100.0),
            # already has a code — must be left untouched
            ("k3", "Amazon Relational Database Service", "AmazonRDS", "rds", 50.0),
            # unresolvable name — stays empty
            ("k4", "Totally Unknown Widget 9000", "", None, 5.0),
        ],
    )
    con.close()

    # Ensure tier-3 does not run (no agy) so we test tiers 1-2 + stamping only.
    monkeypatch.setattr(R.shutil, "which", lambda _b: None)
    monkeypatch.setattr(R.sys, "argv", ["resolve_unknown_product_codes.py", db])
    assert R.main() == 0

    con = duckdb.connect(db)
    got = dict(
        (k, (pc, cs))
        for k, pc, cs in con.execute(
            "SELECT aws_li_key, product_code, canonical_service FROM aws_li_catalog ORDER BY aws_li_key"
        ).fetchall()
    )
    con.close()

    assert got["k1"] == ("AmazonS3", "s3")
    assert got["k2"] == ("AmazonEC2", "ec2")
    assert got["k3"] == ("AmazonRDS", "rds")   # untouched
    assert got["k4"][0] in (None, "")          # unresolved stays empty
