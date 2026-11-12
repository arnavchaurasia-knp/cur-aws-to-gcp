"""Completeness gate: every known billing dimension in service_usage_catalog.json
must dispatch to an explicit charge-type with its declared mechanic (WORKORDER_spec_partition_completeness §4).

Guarantees that known usage types never silently fall to spec_gap / misc catch-alls.
"""
import json
import os
import pytest
import scripts.spec_dispatch as sd

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
CATALOG_PATH = os.path.join(ROOT, "data", "service_usage_catalog.json")


def _load_catalog():
    if not os.path.exists(CATALOG_PATH):
        return {}
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _synthetic_row(service: str, entry: dict) -> dict:
    spec = sd.SPECS.get(service, {})
    product_codes = spec.get("product_codes", [])
    product_code = product_codes[0] if product_codes else service
    display = spec.get("display", service)

    return {
        "aws_li_key": "k_test",
        "product": display,
        "product_code": product_code,
        "canonical": service,
        "usage_type": entry["usage_type"],
        "operation": entry.get("operation", ""),
        "pricing_unit": entry.get("unit", ""),
        "unit": entry.get("unit", ""),
        "line_item_type": "Usage",
        "pricing_model": "OnDemand",
        "aws_amortized_cost": 100.0,
        "total_usage": 10.0,
        "gcp_region": "us-central1",
        "aws_region": "us-east-1",
        "_bill_has_discounted_usage": True,
    }


def test_service_usage_catalog_completeness():
    catalog = _load_catalog()
    assert len(catalog) > 0, "service_usage_catalog.json is empty or missing"

    failures = []
    for svc, entries in catalog.items():
        for entry in entries:
            ut = entry["usage_type"]
            row = _synthetic_row(svc, entry)
            res = sd.dispatch(row)
            assert res is not None, f"{svc}/{ut}: dispatch returned None"

            source_rule = res.get("source_rule") or ""
            charge_type = res.get("charge_type")
            mechanic = res.get("mechanic")

            if source_rule.startswith("spec_gap"):
                failures.append(
                    f"{svc}/{ut} falls to catch-all ({source_rule}) — add an explicit charge-type"
                )
                continue

            expected_ct = entry.get("expected_charge_type")
            if expected_ct and charge_type != expected_ct:
                failures.append(
                    f"{svc}/{ut}: charge_type={charge_type!r}, expected {expected_ct!r}"
                )

            expected_mech = entry.get("expected_mechanic")
            if expected_mech and mechanic != expected_mech:
                failures.append(
                    f"{svc}/{ut}: mechanic={mechanic!r}, expected {expected_mech!r}"
                )

    assert not failures, "Completeness gate violations:\n  " + "\n  ".join(failures)

