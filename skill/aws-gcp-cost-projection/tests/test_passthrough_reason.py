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


def test_apply_static_mappings_undeclared_passthrough_guardrail():
    """6. Guardrail: Scans scripts/apply_static_mappings.py to assert that inline
    dicts with strategy='passthrough' declare an explicit passthrough_reason from
    data/passthrough_reasons.json, leaving only a strict, approved allowlist of
    genuinely ambiguous fallback rows undeclared (WORKORDER_passthrough_reason_declarations.md).
    """
    import ast

    mappings_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "apply_static_mappings.py")
    with open(mappings_path, "r", encoding="utf-8") as f:
        src = f.read()

    tree = ast.parse(src)

    # Approved allowlist of genuinely ambiguous fallback rows that lack detail/signal
    # Each entry is (function_name, note_substring)
    APPROVED_UNDECLARED_ALLOWLIST = [
        ("map_block_storage", "blank pricing_unit"),
        ("map_data_transfer", "usage_type blank"),
        ("map_quicksight", "not Author/Reader/Q"),
        ("map_redshift", "Redshift row unrecognized"),
        ("map_compute_arm", "usage_type not parseable"),
        ("map_db_row", "engine could not be determined"),
    ]

    undeclared = []
    declared_reasons = set()

    class GuardrailVisitor(ast.NodeVisitor):
        def __init__(self):
            self.current_func = "top_level"

        def visit_FunctionDef(self, node):
            prev = self.current_func
            self.current_func = node.name
            self.generic_visit(node)
            self.current_func = prev

        def visit_Dict(self, node):
            is_pt = False
            reason = None
            note = ""
            for k, v in zip(node.keys, node.values):
                if k and isinstance(k, ast.Constant):
                    if k.value == "strategy" and isinstance(v, ast.Constant) and v.value == "passthrough":
                        is_pt = True
                    elif k.value == "passthrough_reason" and isinstance(v, ast.Constant):
                        reason = v.value
                    elif k.value in ("projection_note", "note"):
                        if isinstance(v, ast.Constant):
                            note = str(v.value)
                        elif isinstance(v, ast.JoinedStr):
                            parts = [str(p.value) for p in v.values if isinstance(p, ast.Constant)]
                            note = "".join(parts)
            if is_pt:
                if reason:
                    declared_reasons.add(reason)
                else:
                    undeclared.append((self.current_func, note))
            self.generic_visit(node)

    v = GuardrailVisitor()
    v.visit(tree)

    # All declared reasons must be in the closed enum
    invalid_reasons = declared_reasons - pr.VALID_REASONS
    assert not invalid_reasons, f"Found invalid passthrough_reasons in apply_static_mappings.py: {invalid_reasons}"

    # Undeclared passthrough dicts must not exceed the approved allowlist size
    assert len(undeclared) <= len(APPROVED_UNDECLARED_ALLOWLIST), (
        f"Found {len(undeclared)} undeclared passthrough dicts (max allowed: {len(APPROVED_UNDECLARED_ALLOWLIST)}). "
        f"Undeclared entries: {undeclared}"
    )

    # Verify each undeclared entry matches an entry in the allowlist
    for func_name, note in undeclared:
        matched = any(
            func_name == al_func and al_substr in note
            for al_func, al_substr in APPROVED_UNDECLARED_ALLOWLIST
        )
        assert matched, (
            f"Undeclared passthrough in {func_name} with note '{note[:80]}' is not in the approved allowlist."
        )

