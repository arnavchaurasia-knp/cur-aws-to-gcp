#!/usr/bin/env python3
"""
tests/test_multiplier_golden.py — Baseline & regression safety harness for unit multipliers.
Follows WORKORDER_unit_conversion_engine.md Phase P0 (P0.1 and P0.2).

Ensures that any refactor to unit conversions or multipliers does not silently change
multipliers or projected dollars across all mechanic groups and benchmark corpus jobs.
"""
import json
import os
import sys
import duckdb
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_HERE)
_SCRIPTS_DIR = os.path.join(_SKILL_DIR, "scripts")
_GOLDEN_DIR = os.path.join(_HERE, "golden")
sys.path.insert(0, _SCRIPTS_DIR)
sys.path.insert(0, _HERE)

import apply_static_mappings as asm
from generate_goldens import get_representative_rows


def test_multiplier_golden_snapshot():
    """P0.1: Golden test asserting (strategy, unit_multiplier, gcp_sku_name) across all mechanics."""
    golden_path = os.path.join(_GOLDEN_DIR, "multiplier_golden.json")
    assert os.path.exists(golden_path), f"Golden file missing: {golden_path}. Run generate_goldens.py"
    
    with open(golden_path, "r", encoding="utf-8") as f:
        golden = json.load(f)

    rows = get_representative_rows()
    handlers = {
        "flat_hourly": asm.map_flat_hourly,
        "object_storage": asm.map_object_storage,
        "per_request": asm.map_per_request,
        "block_storage": asm.map_block_storage,
        "data_transfer": asm.map_data_transfer,
        "non_workload": asm.map_non_workload,
        "cloudwatch": asm.map_cloudwatch,
        "guardduty": asm.map_guardduty,
        "inspector": asm.map_inspector,
        "marketplace_thirdparty": asm.map_marketplace_thirdparty,
        "quicksight": asm.map_quicksight,
        "redshift": asm.map_redshift,
        "athena": asm.map_athena,
        "kinesis": asm.map_kinesis,
        "efs": asm.map_efs,
        "xray": asm.map_xray,
        "fsx": asm.map_fsx,
        "emr": asm.map_emr,
        "elasticache": asm.map_elasticache,
        "msk": asm.map_msk,
        "compute_windows": asm.map_compute_windows,
        "compute_arm": asm.map_compute_arm,
        "compute_burstable": asm.map_compute_burstable,
        "compute_breakdown": asm.map_compute_breakdown,
        "commitment_discount": asm.map_commitment_discount,
        "managed_db": asm.map_managed_db,
        "opensearch": asm.map_opensearch,
        "rds_extended_support": asm.map_rds_extended_support,
        "glue": asm.map_glue,
        "shield": asm.map_shield,
        "dynamodb_storage": asm.map_dynamodb_storage,
        "dynamodb_capacity": asm.map_dynamodb_capacity,
        "mwaa": asm.map_mwaa,
        "ssm_parameter_store": asm.map_ssm_parameter_store,
        "sagemaker": asm.map_sagemaker,
        "backup": asm.map_backup,
    }

    by_group = {g: [] for g in handlers}
    for r in rows:
        grp = r["mechanic_group"]
        if grp in by_group:
            by_group[grp].append(r)

    current_snapshot = {}
    for grp, grp_rows in by_group.items():
        if not grp_rows:
            continue
        res = handlers[grp](grp_rows)
        if isinstance(res, tuple):
            mapped_entries = res[0]
        else:
            mapped_entries = res

        for m in mapped_entries:
            key = m["aws_li_key"]
            current_snapshot[key] = {
                "gcp_service": m.get("gcp_service"),
                "gcp_sku_name": m.get("gcp_sku_name"),
                "component": m.get("component"),
                "strategy": m.get("strategy"),
                "unit_multiplier": m.get("unit_multiplier"),
                "passthrough_reason": m.get("passthrough_reason"),
            }

    # Compare against golden
    diffs = []
    for key, expected in golden.items():
        actual = current_snapshot.get(key)
        if not actual:
            diffs.append(f"Missing row {key}")
            continue
        for field in ["strategy", "unit_multiplier", "gcp_sku_name"]:
            exp_val = expected.get(field)
            act_val = actual.get(field)
            if exp_val != act_val:
                diffs.append(f"{key}.{field}: expected {exp_val!r}, got {act_val!r}")

    assert not diffs, (
        f"Multiplier golden mismatch in {len(diffs)} fields:\n" + "\n".join(diffs[:20]) +
        ("\n..." if len(diffs) > 20 else "") +
        "\nAny intended multiplier change must be documented per WORKORDER_unit_conversion_engine.md P0.4."
    )


def test_pipeline_golden_snapshot():
    """P0.2: Pipeline golden test asserting projected costs on benchmark corpus."""
    golden_path = os.path.join(_GOLDEN_DIR, "pipeline_golden.json")
    assert os.path.exists(golden_path), f"Golden file missing: {golden_path}. Run generate_goldens.py"

    with open(golden_path, "r", encoding="utf-8") as f:
        golden = json.load(f)

    corpus_dir = os.path.join(_HERE, "corpus")
    for corpus_name, expected_rows in golden.items():
        db_path = os.path.join(corpus_dir, corpus_name, "projection-audit", "projection.duckdb")
        if not os.path.exists(db_path):
            continue
        con = duckdb.connect(db_path, read_only=True)
        rows = con.execute("SELECT aws_li_key, gcp_projected_cost FROM gcp_projection ORDER BY aws_li_key").fetchall()
        con.close()
        actual_rows = {k: round(float(cost), 4) for k, cost in rows}

        diffs = []
        for key, exp_cost in expected_rows.items():
            act_cost = actual_rows.get(key)
            if act_cost is None or abs(act_cost - exp_cost) > 0.001:
                diffs.append(f"{corpus_name}.{key}: expected ${exp_cost:.4f}, got ${act_cost}")

        assert not diffs, f"Pipeline golden mismatch in {corpus_name}:\n" + "\n".join(diffs)
