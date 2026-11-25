"""Shadow-mode (dark-launch) invariant for service_contracts (D-CON-1).

Industry pattern: a new validation gate is deployed in OBSERVE-ONLY mode first —
it records what it would flag (telemetry/annotations) but changes NOTHING about
behavior — then promoted to enforce only after the signal is measured. This test
locks that guarantee for service_contracts.validate_all_contracts: it may write
only the annotation columns (contract_issues, field_availability) and MUST leave
every mapping/dollar-bearing column byte-identical. If anyone later makes it route
or block, this test fails — enforcing that promotion is a deliberate, reviewed
change, never an accident. See WORKORDER_s6_validators.md.
"""

import os
import sys
import tempfile

import duckdb

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "scripts"))

import service_contracts as sc  # noqa: E402

# Columns that carry mapping / dollar meaning — observe mode must never touch these.
_PROTECTED_COLUMNS = [
    "strategy", "unit_multiplier", "gcp_service", "gcp_sku_name",
    "gcp_sku_id", "component", "aws_amortized_cost", "mapping_confidence",
]


def _make_catalog(path: str):
    con = duckdb.connect(path)
    con.execute(
        """
        CREATE TABLE aws_li_catalog (
            rowid BIGINT,
            product TEXT,
            usage_type TEXT,
            instance_type TEXT,
            strategy TEXT,
            unit_multiplier DOUBLE,
            gcp_service TEXT,
            gcp_sku_name TEXT,
            gcp_sku_id TEXT,
            component TEXT,
            aws_amortized_cost DOUBLE,
            mapping_confidence DOUBLE
        )
        """
    )
    rows = [
        (1, "Amazon Elastic Compute Cloud", "APS3-BoxUsage:m5.large", "m5.large",
         "map", 2.0, "Compute Engine", "N4D Core", "SKU-1", "core", 100.0, 0.95),
        (2, "Amazon Simple Storage Service", "APS3-TimedStorage-ByteHrs", None,
         "map", 0.93, "Cloud Storage", "Standard Storage", "SKU-2", "storage", 50.0, 0.9),
        (3, "Amazon RDS Service", "APS3-InstanceUsage:db.r5.large", None,
         "passthrough", 1.0, "Cloud SQL", None, None, "db_instance", 80.0, 0.5),
    ]
    con.executemany(
        "INSERT INTO aws_li_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    con.close()


def _snapshot(path: str):
    con = duckdb.connect(path, read_only=True)
    cols = ", ".join(_PROTECTED_COLUMNS)
    snap = con.execute(f"SELECT rowid, {cols} FROM aws_li_catalog ORDER BY rowid").fetchall()
    con.close()
    return snap


def test_validate_all_contracts_does_not_touch_mapping_or_dollars():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "projection.duckdb")
        _make_catalog(db)

        before = _snapshot(db)
        summary = sc.validate_all_contracts(db)   # shadow run
        after = _snapshot(db)

        # Observe mode: not one mapping/dollar column may change.
        assert before == after, (
            "service_contracts changed a mapping/dollar column — observe-mode "
            "invariant broken (it must annotate only, never route/block/reprice)"
        )
        # It is allowed to (and should) write the annotation columns + return a summary.
        assert isinstance(summary, dict)
        assert "total_rows" in summary and summary["total_rows"] == 3


def test_annotation_columns_are_written_not_mapping():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "projection.duckdb")
        _make_catalog(db)
        sc.validate_all_contracts(db)
        con = duckdb.connect(db, read_only=True)
        cols = {d[0] for d in con.execute("DESCRIBE aws_li_catalog").fetchall()}
        con.close()
        # the two observe/telemetry columns exist after the run
        assert "contract_issues" in cols
        assert "field_availability" in cols
