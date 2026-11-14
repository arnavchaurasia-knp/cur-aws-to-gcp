"""Tests for AWS Lambda full modelling (§4.7 of WORKORDER_per_service_full_modelling.md).

Core invariants verified:
1. Provisioned Concurrency -> Cloud Run min-instances (idle/instance-based billing),
   NEVER mapped to regular request compute.
2. Ephemeral storage (/tmp > 512 MB) -> IGNORE ($0), folded into memory allocation,
   NEVER passed through at AWS cost.
3. Standard compute (x86/Arm) -> Cloud Run Functions request-based memory + CPU components.
4. Requests / Invocations -> Cloud Run Functions Invocations.
5. All catalog billing dimensions dispatch to explicit charge-types (zero spec gaps).
"""
import pytest
import scripts.apply_static_mappings as asm
import scripts.spec_dispatch as sd


def test_lambda_provisioned_concurrency_maps_to_min_instances():
    """Provisioned Concurrency must map to Min-Instance Memory & CPU, never request compute."""
    rows = [
        {
            "aws_li_key": "k_lambda_pc_1",
            "product": "AWS Lambda",
            "usage_type": "APS3-Lambda-Provisioned-Concurrency",
            "unit": "Lambda-GB-Second",
            "total_usage": 36000.0,
            "aws_amortized_cost": 50.0,
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_lambda_pc_2",
            "product": "AWS Lambda",
            "usage_type": "APS3-Lambda-GB-Second-Provisioned",
            "unit": "Lambda-GB-Second",
            "total_usage": 18000.0,
            "aws_amortized_cost": 25.0,
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_lambda_pc_3",
            "product": "AWS Lambda",
            "usage_type": "APS3-ProvisionedConcurrency",
            "unit": "Hrs",
            "total_usage": 100.0,
            "aws_amortized_cost": 15.0,
            "gcp_region": "us-central1",
        },
    ]

    res = asm.map_lambda(rows)
    # Each row produces 2 components: memory and cpu
    assert len(res) == 6

    for entry in res:
        assert entry["gcp_service"] == "Cloud Run Functions"
        assert entry["strategy"] == "map"
        assert entry.get("break_down") is True
        # Must resolve to Min-Instance, not standard request-based SKU
        sku_name = entry.get("gcp_sku_name") or ""
        assert "Min-Instance" in sku_name, f"Expected Min-Instance SKU, got {sku_name}"
        assert "min-instances" in entry["projection_note"].lower()
        assert "cloud.google.com/run/docs/configuring/min-instances" in entry["projection_note"]

    # Verify memory and cpu components for first row
    r1_components = [e for e in res if e["aws_li_key"] == "k_lambda_pc_1"]
    comp_types = {e["component"] for e in r1_components}
    assert comp_types == {"memory", "cpu"}

    mem_entry = next(e for e in r1_components if e["component"] == "memory")
    cpu_entry = next(e for e in r1_components if e["component"] == "cpu")

    assert mem_entry["unit_multiplier"] == 1.0
    assert cpu_entry["unit_multiplier"] == pytest.approx(1.0 / (asm._LAMBDA_MB_PER_VCPU / 1024.0), rel=1e-3)


def test_lambda_ephemeral_storage_maps_to_ignore():
    """Ephemeral storage (/tmp > 512 MB) must map to IGNORE ($0), never passthrough."""
    rows = [
        {
            "aws_li_key": "k_lambda_eph_1",
            "product": "AWS Lambda",
            "usage_type": "APS3-Lambda-Storage-Duration",
            "unit": "GB-Mo",
            "total_usage": 500.0,
            "aws_amortized_cost": 10.0,
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_lambda_eph_2",
            "product": "AWS Lambda",
            "usage_type": "APS3-Lambda-GB-Second-Storage",
            "unit": "Lambda-GB-Second",
            "total_usage": 10000.0,
            "aws_amortized_cost": 2.5,
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_lambda_eph_3",
            "product": "AWS Lambda",
            "usage_type": "APS3-Lambda-Storage-ByteHours",
            "unit": "GB-Mo",
            "total_usage": 12000.0,
            "aws_amortized_cost": 8.0,
            "gcp_region": "us-central1",
        },
    ]

    res = asm.map_lambda(rows)
    assert len(res) == 3

    for entry in res:
        assert entry["gcp_service"] == "Cloud Run Functions"
        assert entry["strategy"] == "ignore", f"Expected strategy='ignore', got {entry['strategy']}"
        assert entry["unit_multiplier"] == 0.0
        assert entry["component"] == "storage"
        assert entry["gcp_sku_id"] is None
        assert entry["mapping_confidence"] == 0.95
        assert "cloud.google.com/run/docs/configuring/services/memory-limits" in entry["projection_note"]
        assert "folds ephemeral /tmp into the memory allocation" in entry["projection_note"]


def test_lambda_standard_compute_maps_to_request_based():
    """Standard execution GB-seconds (x86 & Arm) must map to Request-based Memory & CPU."""
    rows = [
        {
            "aws_li_key": "k_lambda_x86",
            "product": "AWS Lambda",
            "usage_type": "APS3-Lambda-GB-Second",
            "unit": "Lambda-GB-Second",
            "total_usage": 50000.0,
            "aws_amortized_cost": 80.0,
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_lambda_arm",
            "product": "AWS Lambda",
            "usage_type": "APS3-Lambda-GB-Second-ARM",
            "unit": "Lambda-GB-Second",
            "total_usage": 30000.0,
            "aws_amortized_cost": 40.0,
            "gcp_region": "us-central1",
        },
    ]

    res = asm.map_lambda(rows)
    assert len(res) == 4

    for entry in res:
        assert entry["gcp_service"] == "Cloud Run Functions"
        assert entry["strategy"] == "map"
        sku_name = entry.get("gcp_sku_name") or ""
        assert "Request-based billing" in sku_name
        assert "Min-Instance" not in sku_name, f"Standard compute must not use Min-Instance SKU: {sku_name}"


def test_lambda_invocations_maps_to_crf_invocations():
    """Lambda invocations / requests must map to Cloud Run Functions Invocations."""
    rows = [
        {
            "aws_li_key": "k_lambda_req",
            "product": "AWS Lambda",
            "usage_type": "APS3-Request",
            "unit": "Requests",
            "total_usage": 10000000.0,
            "aws_amortized_cost": 2.0,
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_lambda_inv",
            "product": "AWS Lambda",
            "usage_type": "APS3-Invocations",
            "unit": "Count",
            "total_usage": 5000000.0,
            "aws_amortized_cost": 1.0,
            "gcp_region": "us-central1",
        },
    ]

    res = asm.map_lambda(rows)
    assert len(res) == 2

    for entry in res:
        assert entry["gcp_service"] == "Cloud Run Functions"
        assert entry["strategy"] == "map"
        assert entry["component"] == "requests"
        assert "Invocations" in (entry.get("gcp_sku_name") or "")


def test_lambda_spec_dispatch_coverage():
    """Verify all Lambda billing dimensions dispatch cleanly in spec_dispatch."""
    test_cases = [
        ("APS3-Lambda-GB-Second", "Lambda-GB-Second", "lambda_metered", "per_request"),
        ("APS3-Lambda-GB-Second-ARM", "Lambda-GB-Second", "lambda_metered", "per_request"),
        ("APS3-Request", "Requests", "lambda_metered", "per_request"),
        ("APS3-Invocations", "Count", "lambda_metered", "per_request"),
        ("APS3-Lambda-Provisioned-Concurrency", "Lambda-GB-Second", "lambda_provisioned_concurrency", "per_request"),
        ("APS3-Lambda-GB-Second-Provisioned", "Lambda-GB-Second", "lambda_provisioned_concurrency", "per_request"),
        ("APS3-ProvisionedConcurrency", "Hrs", "lambda_provisioned_concurrency", "per_request"),
        ("APS3-Lambda-Storage-Duration", "GB-Mo", "lambda_ephemeral_storage", "per_request"),
        ("APS3-Lambda-GB-Second-Storage", "Lambda-GB-Second", "lambda_ephemeral_storage", "per_request"),
        ("APS3-Lambda-Storage-ByteHours", "GB-Mo", "lambda_ephemeral_storage", "per_request"),
        ("APS3-DataTransfer-Out-Bytes", "GB", "lambda_data_transfer", "data_transfer"),
        ("APS3-Lambda-SnapStart-Restoration", "Count", "lambda_other", "misc"),
    ]

    for ut, unit, exp_ct, exp_mech in test_cases:
        row = {
            "product": "AWS Lambda",
            "product_code": "AWSLambda",
            "canonical": "lambda",
            "usage_type": ut,
            "unit": unit,
        }
        d = sd.dispatch(row)
        assert d is not None, f"Dispatch returned None for {ut}"
        assert d["charge_type"] == exp_ct, f"{ut}: charge_type={d['charge_type']!r}, expected {exp_ct!r}"
        assert d["mechanic"] == exp_mech, f"{ut}: mechanic={d['mechanic']!r}, expected {exp_mech!r}"
