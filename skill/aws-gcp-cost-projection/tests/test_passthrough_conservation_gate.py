import json
import os
import sys
import duckdb
import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from validate_fix import check_passthrough_conservation


def _build_test_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            product_code VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            pricing_unit VARCHAR,
            instance_type VARCHAR,
            mechanic_group VARCHAR,
            mechanic_rule VARCHAR,
            is_workload BOOLEAN,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR PRIMARY KEY,
            gcp_service VARCHAR,
            component VARCHAR,
            strategy VARCHAR,
            unit_multiplier DOUBLE,
            projection_note VARCHAR,
            passthrough_reason VARCHAR
        );
    """)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('mdb1', 'Amazon MemoryDB', 'AmazonMemoryDB', 'APS1-NodeUsage:db.r6g.large', 'CreateDBNode', 'Hrs', 'db.r6g.large', 'flat_hourly', 'memorydb_node_hours', TRUE, 150.0);
    """)
    return con


def test_passthrough_conservation_gate_no_active_assumptions(tmp_path):
    con = _build_test_db()
    con.execute("""
        INSERT INTO aws_li_to_gcp_li VALUES
            ('mdb1', 'Cloud Memorystore for Redis', 'cache', 'map', 1.0, 'Memorystore Redis tier', NULL);
    """)
    violations = check_passthrough_conservation(con, str(tmp_path))
    assert violations == []


def test_passthrough_conservation_gate_passes_when_applied(tmp_path):
    con = _build_test_db()
    con.execute("""
        INSERT INTO aws_li_to_gcp_li VALUES
            ('mdb1', 'Cloud Memorystore for Redis', 'cache', 'passthrough', 1.0, '[passthrough: forced via assumption memorydb_node_gib] Memorystore Redis tier', 'operator_assumption');
    """)
    snap = {
        "passthrough": {"memorydb_node_gib": True}
    }
    with open(tmp_path / "assumptions.json", "w", encoding="utf-8") as f:
        json.dump(snap, f)

    violations = check_passthrough_conservation(con, str(tmp_path))
    assert violations == []


def test_passthrough_conservation_gate_fails_when_not_applied(tmp_path):
    con = _build_test_db()
    # Strategy is still 'map' even though passthrough is enabled
    con.execute("""
        INSERT INTO aws_li_to_gcp_li VALUES
            ('mdb1', 'Cloud Memorystore for Redis', 'cache', 'map', 1.0, 'Memorystore Redis tier', NULL);
    """)
    snap = {
        "passthrough": {"memorydb_node_gib": True}
    }
    with open(tmp_path / "assumptions.json", "w", encoding="utf-8") as f:
        json.dump(snap, f)

    violations = check_passthrough_conservation(con, str(tmp_path))
    assert len(violations) == 1
    assert violations[0]["gate"] == "passthrough_not_applied"
    assert violations[0]["key"] == "memorydb_node_gib"
    assert violations[0]["count"] == 1
