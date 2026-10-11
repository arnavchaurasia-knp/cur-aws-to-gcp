"""
Integration tests for misc guidance wiring into classify_mechanics and phase2_manifest.json (STEP 3).
"""
from __future__ import annotations

import json
import os
import sys
import duckdb
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL = os.path.dirname(_HERE)
_SCRIPTS = os.path.join(_SKILL, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import classify_mechanics
from config_loader import load_data_config


def test_misc_guidance_wiring_integration(tmp_path):
    job_dir = tmp_path
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    db_path = audit_dir / "projection.duckdb"

    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            aws_region VARCHAR,
            gcp_region VARCHAR,
            pricing_model VARCHAR,
            line_item_type VARCHAR,
            is_workload BOOLEAN,
            total_usage DOUBLE,
            aws_amortized_cost DOUBLE,
            projection_note VARCHAR,
            instance_type VARCHAR,
            instance_vcpus INTEGER,
            instance_ram_gb DOUBLE,
            instance_arch VARCHAR,
            instance_count DOUBLE,
            billing_days INTEGER,
            aws_effective_unit_rate DOUBLE,
            license_model VARCHAR,
            operating_system VARCHAR,
            database_engine VARCHAR,
            deployment_option VARCHAR,
            volume_type VARCHAR,
            pricing_unit VARCHAR,
            workload_class VARCHAR,
            billing_format VARCHAR,
            product_code VARCHAR,
            mechanic_group VARCHAR,
            mechanic_rule VARCHAR
        )
    """)

    sample_rows = [
        # 1. Cognito row -> lands in misc
        ("k_cog", "Amazon Cognito", "AuthUsage", "Authenticate", "us-east-1", None, "OnDemand", "Usage", True, 100.0, 10.0, None, None, None, None, None, None, None, None, None, None, None, None, None, "Requests", None, "cur", "AmazonCognito"),
        # 2. DynamoDB RCU row -> lands in misc (via dynamodb_throughput)
        ("k_ddb", "Amazon DynamoDB", "APS3-ReadCapacityUnit", "Read", "ap-south-1", None, "OnDemand", "Usage", True, 500.0, 50.0, None, None, None, None, None, None, None, None, None, None, None, None, None, "rcu", None, "cur", "AmazonDynamoDB"),
        # 3. Unknown third party row -> lands in misc (starts with AWS but unknown service)
        ("k_unk", "AWS Custom Novel Service", "CustomOps", "OperationX", "us-east-1", None, "OnDemand", "Usage", True, 10.0, 12.0, None, None, None, None, None, None, None, None, None, None, None, None, None, "Hrs", None, "cur", None),
        # 4. Data-gap row -> lands in misc
        ("k_gap", "Amazon Simple Storage Service", "APS3-Usage:NA", "", "ap-south-1", None, "OnDemand", "Usage", True, 20.0, 20.0, None, None, None, None, None, None, None, None, None, None, None, None, None, "", None, "cur", "AmazonS3"),
    ]

    con.executemany("""
        INSERT INTO aws_li_catalog (
            aws_li_key, product, usage_type, operation, aws_region, gcp_region, pricing_model,
            line_item_type, is_workload, total_usage, aws_amortized_cost, projection_note,
            instance_type, instance_vcpus, instance_ram_gb, instance_arch, instance_count,
            billing_days, aws_effective_unit_rate, license_model, operating_system,
            database_engine, deployment_option, volume_type, pricing_unit, workload_class,
            billing_format, product_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, sample_rows)
    con.close()

    # Run classify_mechanics.main() with db_path as argument
    orig_argv = sys.argv
    orig_env = os.environ.get("SKILL_DIR")
    try:
        sys.argv = ["classify_mechanics.py", str(db_path)]
        os.environ["SKILL_DIR"] = _SKILL
        try:
            classify_mechanics.main()
        except SystemExit as exc:
            assert exc.code == 0, f"classify_mechanics exited with non-zero code {exc.code}"
    finally:
        sys.argv = orig_argv
        if orig_env is not None:
            os.environ["SKILL_DIR"] = orig_env
        else:
            os.environ.pop("SKILL_DIR", None)

    # Verify phase2_manifest.json was written
    manifest_path = audit_dir / "phase2_manifest.json"
    assert manifest_path.exists(), f"phase2_manifest.json not written at {manifest_path}"

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert "misc" in manifest, "misc group missing from phase2_manifest.json"
    misc_rows = manifest["misc"]["rows"]
    misc_by_key = {r["aws_li_key"]: r for r in misc_rows}

    # Universal guardrails check
    univ = load_data_config("llm_guardrails").get("universal", [])
    assert len(univ) > 0

    for k, r in misc_by_key.items():
        assert "misc_annotation" in r, f"Row {k} missing misc_annotation in manifest"
        annot = r["misc_annotation"]
        assert "guardrails" in annot, f"Row {k} annotation missing guardrails"
        assert len(annot["guardrails"]) >= len(univ), f"Row {k} annotation missing universal guardrails"

    # Assert Cognito guidance
    cog_annot = misc_by_key["k_cog"]["misc_annotation"]
    assert "MAU" in cog_annot.get("guidance", ""), f"Cognito guidance missing MAU: {cog_annot}"
    assert cog_annot.get("confidence_max") == 0.45

    # Assert DynamoDB guidance
    ddb_annot = misc_by_key["k_ddb"]["misc_annotation"]
    assert "Firestore" in ddb_annot.get("guidance", ""), f"DynamoDB guidance missing Firestore: {ddb_annot}"

    # Assert Unknown row guidance
    unk_annot = misc_by_key["k_unk"]["misc_annotation"]
    assert unk_annot.get("confidence_max") == 0.6
    assert unk_annot.get("strategy") in ("map", "passthrough")
