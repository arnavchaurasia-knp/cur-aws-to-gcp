"""
Unit tests for manifest generation robustness, field state annotation, and integrity checks (FIX 18, 19, 20).
"""
import os
import sys
import json
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import classify_mechanics as cm


def test_annotate_misc_field_state():
    sample_misc_row = {
        "aws_li_key": "k1",
        "mechanic_group": "misc",
        "product": "AWS Clean Rooms",
        "usage_type": "Query-Bytes",
        "operation": "RunQuery",
        "aws_amortized_cost": 25.0,
        "instance_type": None,
        "instance_vcpus": None,
        "instance_ram_gb": None,
        "avg_cpu_utilization": None,  # NOT_AVAILABLE_CUR_ONLY in field_state.py
    }
    fstate = cm.annotate_misc_field_state(sample_misc_row)
    assert fstate["product"] == "present_in_cur"
    assert fstate["usage_type"] == "present_in_cur"
    assert fstate["operation"] == "present_in_cur"
    assert fstate["instance_type"] == "not_in_bill"
    assert fstate["avg_cpu_utilization"] == "not_available"


def test_check_manifest_integrity_clean():
    manifest = {
        "misc": [{"aws_amortized_cost": 50.0}],
        "compute_breakdown": [{"aws_amortized_cost": 100.0}],
        "flat_hourly": [{"aws_amortized_cost": 20.0}],
    }
    llm_groups = {"misc", "compute_breakdown"}
    handled_groups = cm.DETERMINISTIC_GROUPS  # includes flat_hourly

    errors = cm.check_manifest_integrity(manifest, llm_groups, handled_groups)
    assert len(errors) == 0


def test_check_manifest_integrity_catches_unhandled_group():
    manifest = {
        "misc": [{"aws_amortized_cost": 50.0}],
        "some_orphan_unhandled_group": [{"aws_amortized_cost": 123.45}],
    }
    llm_groups = {"misc"}
    handled_groups = cm.DETERMINISTIC_GROUPS

    errors = cm.check_manifest_integrity(manifest, llm_groups, handled_groups)
    assert len(errors) == 1
    assert "some_orphan_unhandled_group" in errors[0]
    assert "$123.45" in errors[0]


def test_manifest_select_missing_columns_does_not_crash(tmp_path, monkeypatch):
    # Create a DuckDB table missing optional columns like deployment_option, database_engine, etc.
    db_path = str(tmp_path / "projection.duckdb")
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            pricing_unit VARCHAR,
            line_item_type VARCHAR,
            pricing_model VARCHAR,
            aws_amortized_cost DOUBLE,
            instance_type VARCHAR,
            instance_vcpus INTEGER,
            instance_ram_gb DOUBLE,
            instance_count INTEGER,
            aws_region VARCHAR,
            gcp_region VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
        ('k1', 'Amazon Clean Rooms', 'Analysis-TB', 'RunQuery', 'TB', 'Usage', 'OnDemand', 40.0, NULL, NULL, NULL, 1, 'us-east-1', 'us-east1'),
        ('k2', 'Amazon Elastic Compute Cloud', 'BoxUsage:m5.large', 'RunInstances', 'Hrs', 'Usage', 'OnDemand', 100.0, 'm5.large', 2, 8.0, 1, 'us-east-1', 'us-east1')
    """)
    con.close()

    # Run main() on this database with missing schema columns
    monkeypatch.setattr(sys, "argv", ["classify_mechanics.py", db_path])
    with pytest.raises(SystemExit) as excinfo:
        cm.main()
    assert excinfo.value.code == 0

    manifest_path = tmp_path / "phase2_manifest.json"
    assert manifest_path.exists()
    with open(manifest_path, encoding="utf-8") as f:
        manifest_data = json.load(f)

    # Verify manifest was written and contains entries
    assert "misc" in manifest_data or "compute_breakdown" in manifest_data
    if "misc" in manifest_data:
        misc_rows = manifest_data["misc"]["rows"]
        assert len(misc_rows) >= 1
        # Check that FIX 18 attached field_state to misc row
        assert "field_state" in misc_rows[0]
        # Check that missing columns were populated as None
        assert misc_rows[0].get("deployment_option") is None
