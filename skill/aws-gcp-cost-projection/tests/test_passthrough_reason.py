#!/usr/bin/env python3
"""
tests/test_passthrough_reason.py — Tests for structured passthrough reason & decision provenance.
Follows the requirements in WORKORDER_passthrough_reason_provenance.md Step 8.
"""
import json
import os
import sys
import tempfile
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import passthrough_reasons as pr
import merge_mappings
from validate_fix import passthrough_integrity


def test_enum_loads_and_splits():
    """1. Every reason in data/passthrough_reasons.json has kind in {'intentional', 'defect'};
    INTENTIONAL_REASONS and DEFECT_REASONS are disjoint and together equal VALID_REASONS.
    """
    assert len(pr.VALID_REASONS) > 0
    for r, info in pr.REASONS.items():
        assert info.get("kind") in ("intentional", "defect")

    assert pr.INTENTIONAL_REASONS.isdisjoint(pr.DEFECT_REASONS)
    assert pr.INTENTIONAL_REASONS | pr.DEFECT_REASONS == pr.VALID_REASONS


def test_merge_defaults_llm_passthrough_to_unclassified():
    """2. Feed merge_mappings a mapping record with strategy='passthrough' and no
    passthrough_reason; assert the stored value is 'unclassified'.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "projection.duckdb")
        mappings_dir = os.path.join(tmp_dir, "mappings")
        os.makedirs(mappings_dir, exist_ok=True)

        con = duckdb.connect(db_path)
        con.execute("CREATE TABLE aws_li_catalog (aws_li_key TEXT PRIMARY KEY, product TEXT, mechanic_group TEXT)")
        con.execute("INSERT INTO aws_li_catalog VALUES ('k1', 'AmazonEC2', 'misc')")
        con.close()

        mapping_file = os.path.join(mappings_dir, "misc_mappings.json")
        with open(mapping_file, "w", encoding="utf-8") as f:
            json.dump([{
                "aws_li_key": "k1",
                "strategy": "passthrough",
                "gcp_service": "Compute Engine",
                "component": "instance",
                "unit_multiplier": 1.0,
            }], f)

        sys_argv_bak = sys.argv
        try:
            sys.argv = ["merge_mappings.py", db_path, mappings_dir]
            with pytest.raises(SystemExit) as excinfo:
                merge_mappings.main()
            assert excinfo.value.code == 0
        finally:
            sys.argv = sys_argv_bak

        con = duckdb.connect(db_path)
        row = con.execute("SELECT passthrough_reason, decided_by FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()
        con.close()

        assert row is not None
        assert row[0] == "unclassified"
        assert row[1] == "misc_mappings.json" or row[1] == "merge_mappings.py"


def test_merge_rejects_unknown_reason():
    """3. A record with passthrough_reason='totally_made_up' is stored as 'unclassified'."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "projection.duckdb")
        mappings_dir = os.path.join(tmp_dir, "mappings")
        os.makedirs(mappings_dir, exist_ok=True)

        con = duckdb.connect(db_path)
        con.execute("CREATE TABLE aws_li_catalog (aws_li_key TEXT PRIMARY KEY, product TEXT, mechanic_group TEXT)")
        con.execute("INSERT INTO aws_li_catalog VALUES ('k1', 'AmazonEC2', 'misc')")
        con.close()

        mapping_file = os.path.join(mappings_dir, "misc_mappings.json")
        with open(mapping_file, "w", encoding="utf-8") as f:
            json.dump([{
                "aws_li_key": "k1",
                "strategy": "passthrough",
                "passthrough_reason": "totally_made_up",
                "gcp_service": "Compute Engine",
                "component": "instance",
                "unit_multiplier": 1.0,
            }], f)

        sys_argv_bak = sys.argv
        try:
            sys.argv = ["merge_mappings.py", db_path, mappings_dir]
            with pytest.raises(SystemExit) as excinfo:
                merge_mappings.main()
            assert excinfo.value.code == 0
        finally:
            sys.argv = sys_argv_bak

        con = duckdb.connect(db_path)
        row = con.execute("SELECT passthrough_reason FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()
        con.close()

        assert row is not None
        assert row[0] == "unclassified"


def test_merge_nulls_reason_for_mapped_rows():
    """4. A record with strategy='map' and passthrough_reason='no_gcp_equivalent' is stored with
    passthrough_reason IS NULL.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "projection.duckdb")
        mappings_dir = os.path.join(tmp_dir, "mappings")
        os.makedirs(mappings_dir, exist_ok=True)

        con = duckdb.connect(db_path)
        con.execute("CREATE TABLE aws_li_catalog (aws_li_key TEXT PRIMARY KEY, product TEXT, mechanic_group TEXT)")
        con.execute("INSERT INTO aws_li_catalog VALUES ('k1', 'AmazonEC2', 'misc')")
        con.close()

        mapping_file = os.path.join(mappings_dir, "misc_mappings.json")
        with open(mapping_file, "w", encoding="utf-8") as f:
            json.dump([{
                "aws_li_key": "k1",
                "strategy": "map",
                "passthrough_reason": "no_gcp_equivalent",
                "gcp_service": "Compute Engine",
                "gcp_sku_name": "N2 Instance Core",
                "component": "instance",
                "unit_multiplier": 1.0,
            }], f)

        sys_argv_bak = sys.argv
        try:
            sys.argv = ["merge_mappings.py", db_path, mappings_dir]
            with pytest.raises(SystemExit) as excinfo:
                merge_mappings.main()
            assert excinfo.value.code == 0
        finally:
            sys.argv = sys_argv_bak

        con = duckdb.connect(db_path)
        row = con.execute("SELECT passthrough_reason FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()
        con.close()

        assert row is not None
        assert row[0] is None


def test_gate_flags_undeclared_passthrough():
    """5. Build a tiny DB with one passthrough row at aws_amortized_cost=100 and
    passthrough_reason IS NULL; assert passthrough_integrity(con) returns exactly one violation.
    """
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT,
            product TEXT,
            is_workload BOOLEAN,
            aws_amortized_cost DOUBLE
        )
    """)
    con.execute("""
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key TEXT,
            strategy TEXT,
            passthrough_reason TEXT
        )
    """)

    con.execute("""
        INSERT INTO aws_li_catalog VALUES ('k1', 'AmazonEC2', TRUE, 100.0)
    """)
    con.execute("""
        INSERT INTO aws_li_to_gcp_li VALUES ('k1', 'passthrough', NULL)
    """)

    violations = passthrough_integrity(con)
    assert len(violations) == 1
    assert violations[0]["gate"] == "passthrough_integrity"
    assert violations[0]["aws_li_key"] == "k1"
    assert "passthrough written with no passthrough_reason" in violations[0]["message"]
