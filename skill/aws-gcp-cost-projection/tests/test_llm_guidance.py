#!/usr/bin/env python3
"""
Unit & parity tests for scripts/llm_guidance.py.

Verifies:
  1. Universal guardrails present on EVERY output (across diverse row types).
  2. Every misc charge_type in service_specs.json has valid llm_guidance.
  3. Dynamic parity with classify_mechanics._misc_reason for accelerator, self-host, and data-gap.
  4. Confidence caps respected (e.g. cognito == 0.45).

Runnable as:
  pytest tests/test_llm_guidance.py -q
  python tests/test_llm_guidance.py
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import classify_mechanics as cm
from config_loader import load_data_config
import llm_guidance as lg
import spec_dispatch as sd


def test_universal_guardrails_present_on_every_output():
    cfg = load_data_config("llm_guardrails")
    universal = cfg.get("universal", [])
    assert len(universal) > 0, "llm_guardrails.json universal must not be empty"

    sample_rows = [
        # accelerator
        {"product": "Amazon EC2", "instance_type": "g4dn.xlarge", "aws_amortized_cost": 100.0},
        # self-host
        {"product": "Amazon OpenSearch Service", "instance_type": "r5.large", "instance_vcpus": 2, "instance_ram_gb": 16, "instance_count": 2, "aws_amortized_cost": 200.0},
        # data-gap
        {"canonical": "s3", "product": "Amazon S3", "unit": "", "operation": "", "usage_type": "APS3-Usage:NA", "aws_amortized_cost": 5.0},
        # cognito
        {"canonical": "cognito", "product": "Amazon Cognito", "usage_type": "AuthUsage", "unit": "Requests", "aws_amortized_cost": 25.0},
        # ses
        {"canonical": "ses", "product": "Amazon Simple Email Service", "usage_type": "Emails", "unit": "Count", "aws_amortized_cost": 30.0},
        # sagemaker
        {"canonical": "sagemaker", "product": "Amazon SageMaker", "usage_type": "Train", "unit": "Hrs", "aws_amortized_cost": 500.0},
        # dms
        {"canonical": "dms", "product": "AWS Database Migration Service", "usage_type": "Replication", "unit": "Hrs", "aws_amortized_cost": 75.0},
        # unknown
        {"product": "UnknownThirdPartySvc", "usage_type": "Custom", "unit": "Hrs", "aws_amortized_cost": 2.0},
    ]

    for row in sample_rows:
        dispatch_res = sd.dispatch(row)
        res = lg.build_guidance(row, dispatch_res)
        assert "guardrails" in res, f"Result for row {row} missing guardrails"
        for rule in universal:
            assert rule in res["guardrails"], f"Universal rule {rule!r} missing for row {row}"


def test_every_misc_charge_type_in_service_specs_has_llm_guidance():
    specs = load_data_config("service_specs")
    target_ct_ids = {
        "cognito.cognito_all",
        "ses.ses_all",
        "sagemaker.sagemaker_all",
        "dms.dms_all",
        "backup.backup_other",
        "cloudtrail.cloudtrail_other",
        "ssm.ssm_other",
        "vpc.vpc_other",
    }

    checked_count = 0
    for svc_name, svc_spec in specs.items():
        if svc_name.startswith("_"):
            continue
        for ct in svc_spec.get("charge_types", []):
            ct_key = f"{svc_name}.{ct.get('id')}"
            if ct.get("mechanic") == "misc" or ct_key in target_ct_ids:
                checked_count += 1
                assert "llm_guidance" in ct, f"Charge type {ct_key} missing llm_guidance"
                g = ct["llm_guidance"]
                assert bool(g.get("gcp_target")), f"{ct_key} llm_guidance.gcp_target must not be empty"
                assert bool(g.get("strategy")), f"{ct_key} llm_guidance.strategy must not be empty"
                assert g.get("confidence_max") is not None, f"{ct_key} llm_guidance.confidence_max must be set"
                assert isinstance(g.get("confidence_max"), (int, float)), f"{ct_key} confidence_max must be float/int"

    assert checked_count >= 8, f"Expected at least 8 misc charge_types checked, got {checked_count}"


def test_dynamic_parity_with_misc_reason():
    # 1. Accelerator row
    r_acc = {"product": "Amazon EC2", "instance_type": "inf1.xlarge", "aws_amortized_cost": 60.0}
    d_acc = sd.dispatch(r_acc)
    g_acc = lg.build_guidance(r_acc, d_acc)
    legacy_acc = json.loads(cm._misc_reason(r_acc))

    assert g_acc["strategy"] == legacy_acc["recommended_strategy"]
    assert g_acc.get("recommended_strategy") == legacy_acc["recommended_strategy"]
    assert g_acc.get("manual_review") == legacy_acc.get("manual_review")

    # 2. Self-host row
    r_sh = {
        "product": "Amazon OpenSearch Service",
        "instance_type": "r5.large",
        "instance_vcpus": 2,
        "instance_ram_gb": 16,
        "instance_count": 1,
        "aws_amortized_cost": 150.0,
    }
    d_sh = sd.dispatch(r_sh)
    g_sh = lg.build_guidance(r_sh, d_sh)
    legacy_sh = json.loads(cm._misc_reason(r_sh))

    assert g_sh["strategy"] == legacy_sh["recommended_strategy"]
    assert g_sh.get("recommended_strategy") == legacy_sh["recommended_strategy"]
    assert g_sh.get("extracted_footprint") == legacy_sh.get("extracted_footprint")

    # 3. Data-gap row
    r_gap = {
        "canonical": "s3",
        "product": "Amazon Simple Storage Service",
        "unit": "",
        "operation": "",
        "usage_type": "APS3-Usage:NA",
        "aws_amortized_cost": 20.0,
    }
    d_gap = sd.dispatch(r_gap)
    g_gap = lg.build_guidance(r_gap, d_gap)
    legacy_gap = json.loads(cm._misc_reason(r_gap))

    assert g_gap["strategy"] == legacy_gap["recommended_strategy"]
    assert g_gap.get("recommended_strategy") == legacy_gap["recommended_strategy"]
    assert g_gap.get("data_gap") == legacy_gap.get("data_gap")


def test_confidence_cap_respected():
    r_cognito = {
        "canonical": "cognito",
        "product": "Amazon Cognito",
        "usage_type": "AuthUsage",
        "unit": "Requests",
        "aws_amortized_cost": 10.0,
    }
    d = sd.dispatch(r_cognito)
    g = lg.build_guidance(r_cognito, d)
    assert g["confidence_max"] == 0.45


def test_tailored_branches_actually_fire():
    # 1. Cognito
    r_cog = {"canonical": "cognito", "product": "Amazon Cognito", "usage_type": "AuthUsage", "unit": "Requests", "aws_amortized_cost": 10.0}
    g_cog = lg.build_guidance(r_cog, sd.dispatch(r_cog))
    assert "MAU" in g_cog["guidance"]
    assert ("20" in g_cog["guidance"] or "20" in g_cog.get("formula", ""))
    assert g_cog["confidence_max"] == 0.45

    # 2. DynamoDB RCU
    r_ddb = {"canonical": "dynamodb", "product": "Amazon DynamoDB", "usage_type": "APS3-ReadCapacityUnit", "unit": "rcu", "aws_amortized_cost": 50.0}
    g_ddb = lg.build_guidance(r_ddb, sd.dispatch(r_ddb))
    assert "Firestore" in g_ddb["guidance"]
    assert "Bigtable" in g_ddb["guidance"]
    assert "Spanner" in g_ddb["guidance"]

    # 3. SES
    r_ses = {"canonical": "ses", "product": "Amazon Simple Email Service", "usage_type": "Emails", "unit": "Count", "aws_amortized_cost": 30.0}
    g_ses = lg.build_guidance(r_ses, sd.dispatch(r_ses))
    assert g_ses["strategy"] == "passthrough"
    assert "no GCP equivalent" in g_ses["gcp_target"] or "no gcp equivalent" in g_ses["gcp_target"].lower()

    # 4. Accelerator
    r_acc = {"product": "Amazon EC2", "instance_type": "inf2.xlarge", "aws_amortized_cost": 100.0}
    g_acc = lg.build_guidance(r_acc, sd.dispatch(r_acc))
    assert g_acc["strategy"] == "passthrough"
    assert "N2D" in g_acc["guidance"]
    assert "manual review" in g_acc["guidance"].lower()

    # 5. Self-host
    r_sh = {"product": "Amazon OpenSearch Service", "instance_type": "r5.large", "instance_vcpus": 2, "instance_ram_gb": 16, "instance_count": 1, "aws_amortized_cost": 150.0}
    g_sh = lg.build_guidance(r_sh, sd.dispatch(r_sh))
    assert "do NOT invent" in g_sh["guidance"] or "do not invent" in g_sh["guidance"].lower()

    # 6. Data-gap
    r_gap = {"canonical": "s3", "product": "Amazon Simple Storage Service", "unit": "", "operation": "", "usage_type": "APS3-Usage:NA", "aws_amortized_cost": 20.0}
    g_gap = lg.build_guidance(r_gap, sd.dispatch(r_gap))
    assert g_gap.get("data_gap") is True

    # 7. Universal guardrails on all
    for g in (g_cog, g_ddb, g_ses, g_acc, g_sh, g_gap):
        assert len(g.get("guardrails", [])) >= len(lg.UNIVERSAL_GUARDRAILS)


def test_cost_only_blank_row_guidance():
    # Blank row with cost > 0 and no canonical or detail (H-5)
    r_blank = {
        "product": "Unrecognized Item",
        "aws_amortized_cost": 250.0,
        "usage_type": "",
        "operation": "",
        "unit": "",
        "instance_type": None,
        "canonical": None,
    }
    g = lg.build_guidance(r_blank, None)
    assert g.get("data_gap") is True
    assert g.get("strategy") == "passthrough"
    assert g.get("confidence_max") <= 0.2
    assert "could not be classified" in g.get("why", "")


def test_accelerator_detector_usage_type():
    # inf2 carried only in usage_type, instance_type is None
    r_acc = {
        "product": "Amazon Elastic Compute Cloud",
        "instance_type": None,
        "operation": "RunInstances",
        "usage_type": "APS3-BoxUsage:inf2.48xlarge",
        "aws_amortized_cost": 500.0,
    }
    assert cm._is_accelerator(r_acc) is True
    g = lg.build_guidance(r_acc, sd.dispatch(r_acc))
    assert g["strategy"] == "passthrough"
    assert g["manual_review"] is True


def test_curated_misc_service_context():
    # 1. Seeded service by canonical identity
    r_cr = {
        "canonical": "cleanrooms",
        "product": "AWS Clean Rooms",
        "usage_type": "Query-Bytes",
        "aws_amortized_cost": 250.0,
    }
    g_cr = lg.build_guidance(r_cr, None)
    assert g_cr["gcp_target"] == "BigQuery analysis"
    assert g_cr["confidence_max"] == 0.5
    assert "Clean Rooms" in g_cr["guidance"]

    # 2. Seeded service by product name substring
    r_bedrock = {
        "product": "Amazon Bedrock",
        "usage_type": "Tokens",
        "aws_amortized_cost": 150.0,
    }
    g_bedrock = lg.build_guidance(r_bedrock, None)
    assert g_bedrock["gcp_target"] == "Vertex AI Model Garden"
    assert g_bedrock["confidence_max"] == 0.5

    # 3. Unseeded unknown service gets generic fallback
    r_unk = {
        "product": "CompletelyUnknownServiceXYZ",
        "usage_type": "CustomCalls",
        "aws_amortized_cost": 150.0,
    }
    g_unk = lg.build_guidance(r_unk, None)
    assert g_unk["gcp_target"] == "(unknown)"
    assert g_unk["confidence_max"] == 0.6


def main():
    tests = [
        ("Universal guardrails present on every output", test_universal_guardrails_present_on_every_output),
        ("Every misc charge_type in service_specs has llm_guidance", test_every_misc_charge_type_in_service_specs_has_llm_guidance),
        ("Dynamic parity with _misc_reason", test_dynamic_parity_with_misc_reason),
        ("Confidence cap respected for cognito", test_confidence_cap_respected),
        ("Tailored branches actually fire (ADD-2)", test_tailored_branches_actually_fire),
        ("Accelerator detector checks usage_type (ADD-3)", test_accelerator_detector_usage_type),
    ]

    print(f"{'Test Case':<60} {'Status'}")
    print("-" * 70)
    all_passed = True
    for name, fn in tests:
        try:
            fn()
            print(f"{name:<60} PASS")
        except Exception as e:
            print(f"{name:<60} FAIL: {e}")
            all_passed = False

    print("-" * 70)
    if all_passed:
        print("All tests passed.")
        return 0
    else:
        print("Some tests failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
