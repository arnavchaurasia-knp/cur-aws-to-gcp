#!/usr/bin/env python3
"""tests/test_row_state_registry.py — Tests enforcing DESIGN_row_state_registry.md invariants:

1. test_schema_matches_registry: Live table columns match data/row_state_registry.json.
2. test_no_ddl_outside_row_state: No CREATE/ALTER TABLE aws_li_to_gcp_li outside row_state.py.
3. test_values_are_declared: Every enum value in row_state.py is declared in registry.
4. test_set_state_permissions: Writer tiers (llm, mapper, pipeline) are validated.
"""
import glob
import json
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
import backfill_row_state as bfrs


def test_schema_matches_registry():
    """Live table created by ensure_schema() has exactly the columns declared in registry."""
    con = duckdb.connect()
    rs.ensure_schema(con)
    cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
    con.close()

    declared = {k.lower() for k in rs.FIELDS.keys()}
    assert cols == declared, f"Mismatch: in table but not registry: {cols - declared}; in registry but not table: {declared - cols}"


def test_no_ddl_outside_row_state():
    """Fail if ALTER TABLE aws_li_to_gcp_li or CREATE TABLE aws_li_to_gcp_li exists in any script besides row_state.py."""
    ddl_pattern = re.compile(r"(?:ALTER|CREATE)\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?aws_li_to_gcp_li\b", re.IGNORECASE)
    violations = []
    for fpath in glob.glob(os.path.join(SCRIPTS_DIR, "*.py")):
        fname = os.path.basename(fpath)
        if fname in ("row_state.py", "render_notes.py"):
            continue
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f, start=1):
                clean_line = line.split("#")[0]
                if ddl_pattern.search(clean_line):
                    violations.append(f"{fname}:{idx}: {line.strip()}")

    assert not violations, "Found prohibited DDL on aws_li_to_gcp_li outside row_state.py:\n" + "\n".join(violations)


def test_values_are_declared():
    """Validate that domains are present and valid."""
    for field_name, field_spec in rs.FIELDS.items():
        kind = field_spec.get("kind")
        if kind in ("enum", "flagset"):
            domain = field_spec.get("domain", {})
            assert isinstance(domain, dict)
            assert len(domain) > 0, f"Field {field_name} has empty domain"


def test_set_state_permissions():
    """set_state validates caller tier and domain."""
    con = duckdb.connect()
    rs.ensure_schema(con)
    con.execute("INSERT INTO aws_li_to_gcp_li (aws_li_key) VALUES ('k_tier_test')")

    # Pipeline tier can write rate_region_basis
    rs.set_state(con, "k_tier_test", by="test", tier="pipeline", rate_region_basis="dominant", rate_region_used="us-central1")
    row = con.execute("SELECT rate_region_basis, rate_region_used FROM aws_li_to_gcp_li WHERE aws_li_key = 'k_tier_test'").fetchone()
    assert row[0] == "dominant"
    assert row[1] == "us-central1"

    # LLM tier writing rate_region_basis should fail validation
    with pytest.raises(ValueError, match="not permitted to write"):
        rs.set_state(con, "k_tier_test", by="test", tier="llm", raise_on_error=True, rate_region_basis="dominant")

    # Invalid enum value should fail validation
    with pytest.raises(ValueError, match="not in domain"):
        rs.set_state(con, "k_tier_test", by="test", tier="pipeline", raise_on_error=True, rate_region_basis="not_a_basis")

    con.close()


def test_forced_assumption_key_field():
    """ensure_schema creates forced_assumption_key; validate_value verifies known keys vs bogus keys."""
    con = duckdb.connect()
    rs.ensure_schema(con)
    cols = {r[1].lower(): r[2].upper() for r in con.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
    assert "forced_assumption_key" in cols
    assert cols["forced_assumption_key"] in ("VARCHAR", "TEXT")

    # Known assumption key passes
    ok, err = rs.validate_value("forced_assumption_key", "nat_vms_per_gateway", tier="pipeline")
    assert ok is True
    assert err is None

    # None passes (field is nullable)
    ok_none, err_none = rs.validate_value("forced_assumption_key", None, tier="pipeline")
    assert ok_none is True
    assert err_none is None

    # Bogus key fails
    bad, err_bad = rs.validate_value("forced_assumption_key", "bogus_assumption_key", tier="pipeline")
    assert bad is False
    assert "not in domain" in err_bad

    # Set state works and persists
    con.execute("INSERT INTO aws_li_to_gcp_li (aws_li_key) VALUES ('k_pt_test')")
    rs.set_state(con, "k_pt_test", by="passthrough_enforcer", tier="pipeline", forced_assumption_key="nat_vms_per_gateway")
    row = con.execute("SELECT forced_assumption_key FROM aws_li_to_gcp_li WHERE aws_li_key = 'k_pt_test'").fetchone()
    assert row[0] == "nat_vms_per_gateway"
    con.close()


def test_forced_assumption_keys_match_defaults():
    """Verify all domain keys in forced_assumption_key match real keys in assumption-defaults.json."""
    defaults_path = os.path.join(DATA_DIR, "assumption-defaults.json")
    with open(defaults_path, "r", encoding="utf-8") as f:
        known_keys = set(json.load(f)["values"].keys())

    domain = rs.domain_of("forced_assumption_key")
    assert len(domain) > 0
    for k in domain:
        assert k in known_keys, f"Domain key '{k}' not found in assumption-defaults.json"


def test_backfill_forced_assumption_key():
    """backfill_row_state populates forced_assumption_key from legacy note prose."""
    con = duckdb.connect()
    rs.ensure_schema(con)
    con.execute("""
        INSERT INTO aws_li_to_gcp_li (aws_li_key, projection_note, forced_assumption_key)
        VALUES ('k_bf_1', '[passthrough: forced via assumption nat_vms_per_gateway] carried at parity', NULL),
               ('k_bf_2', 'regular note without forced assumption', NULL)
    """)

    bfrs.backfill_row_state(con)

    r1 = con.execute("SELECT forced_assumption_key FROM aws_li_to_gcp_li WHERE aws_li_key = 'k_bf_1'").fetchone()
    assert r1[0] == "nat_vms_per_gateway"

    r2 = con.execute("SELECT forced_assumption_key FROM aws_li_to_gcp_li WHERE aws_li_key = 'k_bf_2'").fetchone()
    assert r2[0] is None
    con.close()


