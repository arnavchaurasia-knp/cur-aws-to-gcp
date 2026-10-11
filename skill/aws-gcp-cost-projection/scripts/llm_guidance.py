#!/usr/bin/env python3
"""
llm_guidance.py — Spec-driven LLM guidance builder for Phase 2 mappings / misc.

Assembles the full structured guidance dict for ONE row from:
  1. Universal guardrails (data/llm_guardrails.json)
  2. Dynamic cross-cutting detectors (Accelerator, Self-host on GCE, Data-gap)
  3. Per-charge-type spec guidance (data/service_specs.json via dispatch_result)
  4. Fallback unknown-service guidance (mirroring classify_mechanics._misc_reason)
"""
from __future__ import annotations

import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from config_loader import load_data_config
import classify_mechanics as _cm
import spec_dispatch as _sd

_GUARDRAILS_CFG = load_data_config("llm_guardrails")
UNIVERSAL_GUARDRAILS: list[str] = _GUARDRAILS_CFG.get("universal", [])

_SVC_CFG = load_data_config("service-classification")
_SELF_HOST_ON_GCE = _SVC_CFG.get("self_host_on_gce", [
    "OpenSearch", "Elasticsearch",
    "Managed Streaming for Apache Kafka", "MSK", "Kafka",
])
_SPECS_RAW = load_data_config("service_specs")
_SPECS = {k: v for k, v in _SPECS_RAW.items() if not k.startswith("_")}


def _get_spec_charge_type(service: str, charge_type_id: str) -> dict | None:
    svc_spec = _SPECS.get(service)
    if not svc_spec:
        return None
    for ct in svc_spec.get("charge_types", []):
        if ct.get("id") == charge_type_id:
            return ct
    return None


def build_guidance(row: dict, dispatch_result: dict | None) -> dict:
    """Return the structured guidance dict for ONE row.

    row: same keys spec_dispatch/classify_mechanics use (product, usage_type,
         operation, unit, instance_type, instance_vcpus, instance_ram_gb,
         instance_count, aws_amortized_cost, canonical, product_code, ...).
    dispatch_result: output of spec_dispatch.dispatch(row), or None.
    """
    product = row.get("product") or ""
    usage_type = row.get("usage_type") or ""
    operation = row.get("operation") or ""
    unit = row.get("unit") or ""
    canonical = row.get("canonical")
    cost = row.get("aws_amortized_cost")
    if cost is None:
        cost = 0.0

    guardrails = list(UNIVERSAL_GUARDRAILS)

    # 1. Dynamic detectors first (cross-cutting, mirror _misc_reason):
    # a. Accelerator
    if _cm._is_accelerator(row):
        acc_guidance = (
            "This is an AI accelerator (Inferentia/Trainium) or GPU instance. Do NOT map "
            "it to a general-purpose CPU VM (N2D) — that hides the architectural change. "
            "Set strategy='passthrough' (cost parity) and mapping_confidence=0.3, and note "
            "'requires manual review: GCP TPU/GPU or accelerator service — not a like-for-like "
            "CPU mapping' in mapping-notes.md."
        )
        return {
            "why": f"AWS accelerator/GPU instance ({row.get('instance_type') or product!r}) — no CPU-VM equivalent",
            "gcp_target": "Accelerator / specialized silicon",
            "service_hint": "Accelerator / specialized silicon",
            "strategy": "passthrough",
            "recommended_strategy": "passthrough",
            "confidence_max": 0.3,
            "manual_review": True,
            "guidance": acc_guidance,
            "mapping_guidance": acc_guidance,
            "guardrails": guardrails,
        }

    # b. Self-host-on-GCE
    for frag in _SELF_HOST_ON_GCE:
        if frag.lower() in product.lower() or frag.lower() in usage_type.lower():
            has_specs = bool(row.get("instance_vcpus"))
            sh_strategy = "break_down" if has_specs else "map"
            sh_guidance = (
                "Map to self-hosted Compute Engine using the EXTRACTED footprint above "
                "(instance_type/vcpus/ram × instance_count = the real node count from "
                "instance-hours). break_down into core+ram components exactly like an EC2 "
                "instance. instance_count IS the actual node count — do NOT invent a "
                "replication factor or disk size. Storage sub-lines map to Persistent Disk "
                "(block_storage); the instance line maps to GCE compute."
            )
            extracted_footprint = {
                "instance_type": row.get("instance_type"),
                "instance_vcpus": row.get("instance_vcpus"),
                "instance_ram_gb": row.get("instance_ram_gb"),
                "instance_count": row.get("instance_count"),
            }
            return {
                "why": f"no managed GCP equivalent for product={product!r}; self-host on GCE",
                "gcp_target": f"{product} → self-hosted on Compute Engine",
                "service_hint": f"{product} → self-hosted on Compute Engine",
                "strategy": sh_strategy,
                "recommended_strategy": sh_strategy,
                "confidence_max": 0.5,
                "extracted_footprint": extracted_footprint,
                "guidance": sh_guidance,
                "mapping_guidance": sh_guidance,
                "guardrails": guardrails,
            }

    # c. Data-gap
    _ut = usage_type.strip().lower()
    degenerate_ut = (not _ut) or _ut.endswith(":na")
    if canonical and not unit.strip() and not operation.strip() and degenerate_ut:
        gap_guidance = (
            f"Identity is known ({canonical}) but this is an aggregated/rolled-up bill line "
            f"with no unit, operation, or granular usage_type, so the charge TYPE "
            f"(instance-hours vs storage vs request) cannot be determined from the source "
            f"bill. Passthrough at cost parity, mapping_confidence=0.3, and note in "
            f"mapping-notes.md that the source bill lacked line-item detail for this "
            f"{canonical} charge."
        )
        return {
            "why": (f"service identified as '{canonical}' but the bill row has no "
                    f"charge-type detail (unit, operation blank; usage_type={usage_type!r})"),
            "gcp_target": canonical,
            "service_hint": canonical,
            "strategy": "passthrough",
            "recommended_strategy": "passthrough",
            "confidence_max": 0.3,
            "data_gap": True,
            "missing_fields": ["unit", "operation", "usage_type_detail"],
            "guidance": gap_guidance,
            "mapping_guidance": gap_guidance,
            "guardrails": guardrails,
        }

    # 2. Per-charge-type guidance:
    if dispatch_result is not None:
        svc_name = dispatch_result.get("service")
        ct_id = dispatch_result.get("charge_type")
        ct = _get_spec_charge_type(svc_name, ct_id)
        if ct and "llm_guidance" in ct:
            g = ct["llm_guidance"]
            res_guardrails = list(guardrails)
            for extra in g.get("extra_guardrails", []):
                if extra not in res_guardrails:
                    res_guardrails.append(extra)
            res = {
                "why": f"spec matched {svc_name}.{ct_id}",
                "gcp_target": g.get("gcp_target", ""),
                "service_hint": g.get("gcp_target", ""),
                "strategy": g.get("strategy", "map"),
                "recommended_strategy": g.get("strategy", "map"),
                "confidence_max": g.get("confidence_max", 0.5),
                "guidance": g.get("guidance", ""),
                "mapping_guidance": g.get("guidance", ""),
                "guardrails": res_guardrails,
            }
            if "formula" in g:
                res["formula"] = g["formula"]
            return res

    # 3. Fallback: generic unknown-service guidance from _misc_reason
    completeness = _cm._info_completeness(row)
    fb_strategy = "passthrough" if (float(cost) < 10.0 or completeness == "minimal") else "map"
    fb_guidance = (
        "Service is unrecognized. Map by spend share: if aws_amortized_cost < $10/mo "
        "set strategy='passthrough'. Otherwise find the closest GCP equivalent by product "
        "name and usage_type, document your reasoning in mapping-notes.md, and set "
        "mapping_confidence ≤ 0.6."
    )
    return {
        "why": f"unrecognized product={product!r} usage_type={usage_type!r} unit={unit!r}",
        "gcp_target": "(unknown)",
        "service_hint": None,
        "strategy": fb_strategy,
        "recommended_strategy": fb_strategy,
        "confidence_max": 0.6,
        "guidance": fb_guidance,
        "mapping_guidance": fb_guidance,
        "guardrails": guardrails,
    }


def main() -> int:
    sample_rows = [
        (
            "Accelerator",
            {"product": "Amazon Elastic Compute Cloud", "instance_type": "inf1.xlarge", "aws_amortized_cost": 50.0},
        ),
        (
            "Self-host GCE",
            {
                "product": "Amazon OpenSearch Service",
                "instance_type": "m5.large",
                "instance_vcpus": 2,
                "instance_ram_gb": 8,
                "instance_count": 1,
                "aws_amortized_cost": 100.0,
            },
        ),
        (
            "Data Gap",
            {
                "canonical": "s3",
                "product": "Amazon Simple Storage Service",
                "unit": "",
                "operation": "",
                "usage_type": "APS3-Usage:NA",
                "aws_amortized_cost": 10.0,
            },
        ),
        (
            "Cognito Spec",
            {
                "canonical": "cognito",
                "product": "Amazon Cognito",
                "usage_type": "AuthUsage",
                "unit": "Requests",
                "aws_amortized_cost": 25.0,
            },
        ),
        (
            "Unknown Service",
            {
                "product": "CustomThirdPartySvc",
                "usage_type": "CustomType",
                "unit": "Hrs",
                "aws_amortized_cost": 5.0,
            },
        ),
    ]

    for label, row in sample_rows:
        d = _sd.dispatch(row)
        g = build_guidance(row, d)
        print(f"=== Sample: {label} ===")
        print(json.dumps(g, indent=2))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
