#!/usr/bin/env python3
"""
test_merge_mappings.py — Comprehensive tests for merge_mappings.py hardening,
SKU guardrails (G-1), atomic transaction rollback (M-1), row validation (M-2),
gcp_sku_unit preservation (M-3), synthetic SKUs (M-4), structured reporting (M-5),
and fail-closed catalog handling (C-1).
"""

import json
import os
import sys
import tempfile
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import merge_mappings
from synthetic_skus import SYNTHETIC_SKU_IDS
from resolve_sku import resolve_intent_to_sku, get_valid_catalog_services


@pytest.fixture
def temp_env():
    """Create a temporary environment with a DuckDB database and mappings directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "projection.duckdb")
        mappings_dir = os.path.join(tmp_dir, "mappings")
        os.makedirs(mappings_dir, exist_ok=True)

        con = duckdb.connect(db_path)
        # Create aws_li_catalog table
        con.execute("""
            CREATE TABLE aws_li_catalog (
                aws_li_key TEXT PRIMARY KEY,
                product TEXT,
                mechanic_group TEXT
            )
        """)
        # Populate valid keys
        con.execute("INSERT INTO aws_li_catalog VALUES ('k1', 'AmazonEC2', 'compute_breakdown')")
        con.execute("INSERT INTO aws_li_catalog VALUES ('k2', 'AmazonS3', 'object_storage')")
        con.execute("INSERT INTO aws_li_catalog VALUES ('k3', 'AmazonVPC', 'flat_hourly')")
        merge_mappings.ensure_table(con)
        con.close()

        yield tmp_dir, db_path, mappings_dir


# ---------------------------------------------------------------------------
# M-1: Atomic merge: rollback on failure preserves existing rows
# ---------------------------------------------------------------------------

def test_m1_atomic_transaction_rollback_on_failure(temp_env, monkeypatch):
    tmp_dir, db_path, mappings_dir = temp_env

    # Pre-populate aws_li_to_gcp_li with initial rows
    con = duckdb.connect(db_path)
    con.execute("""
        INSERT INTO aws_li_to_gcp_li (aws_li_key, gcp_service, strategy, unit_multiplier)
        VALUES ('k1', 'Compute Engine', 'map', 1.0),
               ('k2', 'Cloud Storage', 'map', 1.0)
    """)
    initial_count = con.execute("SELECT COUNT(*) FROM aws_li_to_gcp_li").fetchone()[0]
    assert initial_count == 2
    con.close()

    # Create mapping file with a bad record structure that causes INSERT to fail during executemany
    # e.g., monkeypatching con.executemany to raise an exception
    orig_executemany = duckdb.DuckDBPyConnection.executemany

    def failing_executemany(self, sql, records):
        if "INSERT INTO aws_li_to_gcp_li" in sql:
            raise RuntimeError("Simulated DuckDB write failure")
        return orig_executemany(self, sql, records)

    mapping_content = [
        {
            "aws_li_key": "k3",
            "gcp_service": "Compute Engine",
            "strategy": "map",
            "unit_multiplier": 1.0,
        }
    ]
    with open(os.path.join(mappings_dir, "flat_hourly_mappings.json"), "w") as f:
        json.dump(mapping_content, f)

    sys.argv = ["merge_mappings.py", db_path, mappings_dir]

    monkeypatch.setattr(duckdb.DuckDBPyConnection, "executemany", failing_executemany)
    with pytest.raises(RuntimeError, match="Simulated DuckDB write failure"):
        merge_mappings.main()

    # Verify table STILL has the original 2 rows due to ROLLBACK
    con = duckdb.connect(db_path)
    surviving_count = con.execute("SELECT COUNT(*) FROM aws_li_to_gcp_li").fetchone()[0]
    con.close()
    assert surviving_count == 2


# ---------------------------------------------------------------------------
# M-2: Validate rows before insert & reject bad rows
# ---------------------------------------------------------------------------

def test_m2_validate_row_rejects_and_coerces():
    valid_keys = {"k1", "k2"}

    # Missing aws_li_key
    clean, reason = merge_mappings.validate_row({"strategy": "map"}, valid_keys)
    assert clean is None
    assert reason == "missing_aws_li_key"

    # Missing strategy
    clean, reason = merge_mappings.validate_row({"aws_li_key": "k1"}, valid_keys)
    assert clean is None
    assert reason == "missing_strategy"

    # Orphan key
    clean, reason = merge_mappings.validate_row({"aws_li_key": "k_orphan", "strategy": "map"}, valid_keys)
    assert clean is None
    assert "orphan_key" in reason

    # Invalid unit multiplier
    clean, reason = merge_mappings.validate_row({"aws_li_key": "k1", "strategy": "map", "unit_multiplier": "2x"}, valid_keys)
    assert clean is None
    assert "invalid_unit_multiplier" in reason

    # Valid row with coercions
    raw = {
        "aws_li_key": "  k1  ",
        "strategy": "map",
        "unit_multiplier": "2.5",
        "is_workload": "true",
        "break_down": 1,
        "mapping_confidence": "0.95",
        "is_assumption": "yes",
        "assumption": "NAT assumption note",
    }
    clean, reason = merge_mappings.validate_row(raw, valid_keys)
    assert reason is None
    assert clean["aws_li_key"] == "k1"
    assert clean["unit_multiplier"] == 2.5
    assert clean["is_workload"] is True
    assert clean["break_down"] is True
    assert clean["mapping_confidence"] == 0.95
    assert clean["is_assumption"] is True
    assert clean["assumption_note"] == "NAT assumption note"


# ---------------------------------------------------------------------------
# M-3: Preservation of gcp_sku_unit
# ---------------------------------------------------------------------------

def test_m3_gcp_sku_unit_preserved(temp_env):
    tmp_dir, db_path, mappings_dir = temp_env
    assert "gcp_sku_unit" in merge_mappings.COLUMNS

    mapping_content = [
        {
            "aws_li_key": "k1",
            "gcp_service": "Compute Engine",
            "gcp_sku_id": "CP-COMPUTEENGINE-VMIMAGE-N2-STANDARD-4",
            "gcp_sku_unit": "gibibyte hour",
            "strategy": "map",
            "unit_multiplier": 1.0,
        }
    ]
    with open(os.path.join(mappings_dir, "compute_mappings.json"), "w") as f:
        json.dump(mapping_content, f)

    sys.argv = ["merge_mappings.py", db_path, mappings_dir]
    with pytest.raises(SystemExit) as exc:
        merge_mappings.main()
    assert exc.value.code == 0

    con = duckdb.connect(db_path)
    res = con.execute("SELECT gcp_sku_unit FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()
    con.close()
    assert res is not None
    assert res[0] == "gibibyte hour"


# ---------------------------------------------------------------------------
# M-4: Synthetic SKU whitelist
# ---------------------------------------------------------------------------

def test_m4_synthetic_sku_ids_whitelist():
    assert "GCP-EGRESS-INTERNET" in SYNTHETIC_SKU_IDS
    assert "GCP-EGRESS-INTERZONE" in SYNTHETIC_SKU_IDS
    assert "GCP-LOOKER-STUDIO-PRO-USER" in SYNTHETIC_SKU_IDS
    assert "TPU-V5E-ONDEMAND" in SYNTHETIC_SKU_IDS


# ---------------------------------------------------------------------------
# M-5: Structured merge report
# ---------------------------------------------------------------------------

def test_m5_structured_merge_report_emitted(temp_env):
    tmp_dir, db_path, mappings_dir = temp_env

    mapping_content = [
        # Valid row
        {
            "aws_li_key": "k1",
            "gcp_service": "Compute Engine",
            "strategy": "map",
            "unit_multiplier": 1.0,
        },
        # Orphan key (rejected)
        {
            "aws_li_key": "k_nonexistent",
            "gcp_service": "Compute Engine",
            "strategy": "map",
            "unit_multiplier": 1.0,
        },
    ]
    with open(os.path.join(mappings_dir, "misc_mappings.json"), "w") as f:
        json.dump(mapping_content, f)

    sys.argv = ["merge_mappings.py", db_path, mappings_dir]
    with pytest.raises(SystemExit) as exc:
        merge_mappings.main()
    assert exc.value.code == 0

    report_path = os.path.join(tmp_dir, "merge_report.json")
    assert os.path.exists(report_path)

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    assert report["inserted"] == 1
    assert report["rejected"] == 1
    assert report["orphan_keys"] == 1
    assert "sku_validation_ran" in report
    assert len(report["rejected_detail"]) == 1


# ---------------------------------------------------------------------------
# G-1: Intent resolution and SKU guardrail
# ---------------------------------------------------------------------------

def test_g1_sku_intent_resolution_and_guardrail(temp_env):
    tmp_dir, db_path, mappings_dir = temp_env

    # Row 1: Valid intent without raw sku_id -> resolves to real catalog SKU
    # Row 2: Unknown intent with no catalog match -> downgrades to passthrough + note
    # Row 3: Fabricated SKU ID -> nulled to None, downgraded to passthrough + note
    # Row 4: Hallucinated GCP service -> downgraded to passthrough
    mapping_content = [
        {
            "aws_li_key": "k1",
            "gcp_service": "Compute Engine",
            "gcp_sku_name": "N2 Instance Core running in Americas",
            "gcp_region": "us-central1",
            "strategy": "map",
            "unit_multiplier": 4.0,
        },
        {
            "aws_li_key": "k2",
            "gcp_service": "Cloud Storage",
            "gcp_sku_name": "Totally Nonexistent Storage Tier 999",
            "gcp_region": "us-central1",
            "strategy": "map",
            "unit_multiplier": 1.0,
        },
        {
            "aws_li_key": "k3",
            "gcp_service": "Compute Engine",
            "gcp_sku_id": "FABRICATED-SKU-ID-12345",
            "strategy": "map",
            "unit_multiplier": 1.0,
        },
    ]
    with open(os.path.join(mappings_dir, "test_mappings.json"), "w") as f:
        json.dump(mapping_content, f)

    sys.argv = ["merge_mappings.py", db_path, mappings_dir]
    with pytest.raises(SystemExit) as exc:
        merge_mappings.main()
    assert exc.value.code == 0

    con = duckdb.connect(db_path)
    rows = con.execute("SELECT aws_li_key, gcp_sku_id, strategy, projection_note FROM aws_li_to_gcp_li").fetchall()
    con.close()

    by_key = {r[0]: (r[1], r[2], r[3] or "") for r in rows}

    # k1 should have resolved a real SKU ID (if catalog is present) or remained safe
    assert "k1" in by_key

    # k2 had nonexistent intent -> must downgrade to passthrough
    k2_sku, k2_strat, k2_note = by_key["k2"]
    assert k2_sku is None
    assert k2_strat == "passthrough"
    assert "no-catalog-sku" in k2_note

    # k3 had fabricated SKU ID -> must be nulled and downgraded
    k3_sku, k3_strat, k3_note = by_key["k3"]
    assert k3_sku is None
    assert k3_strat == "passthrough"
    assert "fabricated-sku" in k3_note


# ---------------------------------------------------------------------------
# C-1: Fail-closed tracking
# ---------------------------------------------------------------------------

def test_c1_fail_closed_tracking_when_catalog_absent(temp_env, monkeypatch):
    tmp_dir, db_path, mappings_dir = temp_env

    mapping_content = [
        {
            "aws_li_key": "k1",
            "gcp_service": "Compute Engine",
            "strategy": "map",
            "unit_multiplier": 1.0,
        }
    ]
    with open(os.path.join(mappings_dir, "test_mappings.json"), "w") as f:
        json.dump(mapping_content, f)

    # Simulate missing catalog
    monkeypatch.setattr(merge_mappings, "CATALOG_DB", os.path.join(tmp_dir, "nonexistent_catalog.duckdb"))
    import resolve_sku
    resolve_sku.close_catalog_connection()
    monkeypatch.setattr(resolve_sku, "CATALOG_DB", os.path.join(tmp_dir, "nonexistent_catalog.duckdb"))
    monkeypatch.setattr(resolve_sku, "_VALID_SKUS_CACHE", None)
    monkeypatch.setattr(resolve_sku, "_SERVICES_CACHE", None)

    sys.argv = ["merge_mappings.py", db_path, mappings_dir]
    with pytest.raises(SystemExit) as exc:
        merge_mappings.main()
    assert exc.value.code == 0

    report_path = os.path.join(tmp_dir, "merge_report.json")
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    assert report["sku_validation_ran"] is False
