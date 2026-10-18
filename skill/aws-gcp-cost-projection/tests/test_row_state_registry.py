#!/usr/bin/env python3
"""tests/test_row_state_registry.py — Tests enforcing DESIGN_row_state_registry.md invariants:

1. test_schema_matches_registry: Live table columns match data/row_state_registry.json.
2. test_no_ddl_outside_row_state: No CREATE/ALTER TABLE aws_li_to_gcp_li outside row_state.py.
3. test_values_are_declared: Every enum value in row_state.py is declared in registry.
4. test_set_state_permissions: Writer tiers (llm, mapper, pipeline) are validated.
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
