#!/usr/bin/env python3
"""Tests for WO-4: Passthrough and is_workload resolution consolidation.

Asserts that workload_resolver, classify_mechanics, passthrough_enforcer,
and validate_fix all agree on a shared corpus of rows.
"""
import os
import sys
import duckdb
import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import workload_resolver as wr
import passthrough_reasons as pr
from classify_mechanics import classify_with_trace
from passthrough_enforcer import enforce_assumption_passthroughs
from validate_fix import passthrough_integrity


def test_workload_resolver_direct():
    """Verify workload_resolver functions against canonical rules."""
    # Workload types
    assert wr.is_workload("Usage", "Amazon Elastic Compute Cloud", "BoxUsage:t3.medium") is True
    assert wr.is_workload("DiscountedUsage", "Amazon EC2", "RunInstances") is True
    assert wr.is_workload("SavingsPlanCoveredUsage", "Amazon EC2", "RunInstances") is True

    # Non-workload line item types
    assert wr.is_workload("Tax", "Tax", "Tax") is False
    assert wr.is_workload("Credit", "Amazon EC2", "Credit") is False
    assert wr.is_workload("Refund", "Amazon S3", "Refund") is False
    assert wr.is_workload("RIFee", "Amazon EC2", "HeavyUsage") is False

    # Pattern matches
    assert wr.is_workload("Usage", "AWS Marketplace", "Product") is False
    assert wr.is_workload("Usage", "AWS Support (Business)", "Support") is False
    assert wr.is_workload(None, "Tax", "sales tax") is False

    # Word boundary checks
    assert wr.is_workload("Usage", "Amazon Comprehend", "syntax analysis") is True
    assert wr.is_workload("Usage", "Amazon Service", "taxonomy") is True


def test_is_row_workload_resolution():
    """is_row_workload respects explicit flag, string booleans, and derives when missing."""
    assert wr.is_row_workload({"is_workload": True}) is True
    assert wr.is_row_workload({"is_workload": False}) is False
    assert wr.is_row_workload({"is_workload": 1}) is True
    assert wr.is_row_workload({"is_workload": 0}) is False
    assert wr.is_row_workload({"is_workload": "true"}) is True
    assert wr.is_row_workload({"is_workload": "false"}) is False

    # When missing or None, derives correctly
    assert wr.is_row_workload({
        "line_item_type": "Usage",
        "product": "Amazon Elastic Compute Cloud",
        "usage_type": "BoxUsage:t3.medium",
        "aws_amortized_cost": 10.0,
    }) is True

    assert wr.is_row_workload({
        "line_item_type": "Tax",
        "product": "Tax",
        "usage_type": "Tax",
        "aws_amortized_cost": 5.0,
    }) is False


def test_passthrough_reasons_resolver():
    """Verify passthrough reason validation and resolution."""
    assert pr.is_valid("operator_assumption") is True
    assert pr.is_valid("unresolved_sku") is True
    assert pr.is_valid("totally_bogus_reason") is False

    assert pr.resolve_reason("operator_assumption") == "operator_assumption"
    assert pr.resolve_reason("invalid_reason", default="fallback") == "fallback"

    assert pr.is_intentional("operator_assumption") is True
    assert pr.is_intentional("no_gcp_equivalent") is True
    assert pr.is_intentional("unresolved_sku") is False

    assert pr.is_defect("unresolved_sku") is True
    assert pr.is_defect("echo_reset") is True
    assert pr.is_defect("operator_assumption") is False


def test_shared_corpus_classifier_agreement(tmp_path):
    """Assert classifier routes non-workload items identically based on unified resolution."""
    corpus = [
        {
            "aws_li_key": "k_ec2",
            "product": "Amazon Elastic Compute Cloud",
            "usage_type": "BoxUsage:t3.medium",
            "operation": "RunInstances",
            "unit": "Hrs",
            "line_item_type": "Usage",
            "pricing_model": "OnDemand",
            "aws_amortized_cost": 100.0,
            "instance_type": "t3.medium",
            "is_workload": True,
            "expected_workload": True,
            "expected_group": "compute_burstable",
        },
        {
            "aws_li_key": "k_market",
            "product": "AWS Marketplace",
            "usage_type": "Subscription",
            "operation": "Marketplace",
            "unit": "Mo",
            "line_item_type": "Usage",
            "pricing_model": "OnDemand",
            "aws_amortized_cost": 50.0,
            "is_workload": False,
            "expected_workload": False,
            "expected_group": "non_workload",
        },
        {
            "aws_li_key": "k_support",
            "product": "AWS Support (Business)",
            "usage_type": "Support",
            "operation": "Support",
            "unit": "Mo",
            "line_item_type": "Usage",
            "pricing_model": "OnDemand",
            "aws_amortized_cost": 75.0,
            "is_workload": False,
            "expected_workload": False,
            "expected_group": "non_workload",
        },
        {
            "aws_li_key": "k_tax",
            "product": "Tax",
            "usage_type": "Tax",
            "operation": "Tax",
            "unit": "",
            "line_item_type": "Tax",
            "pricing_model": "",
            "aws_amortized_cost": 15.0,
            "is_workload": False,
            "expected_workload": False,
            "expected_group": "non_workload",
        },
        {
            "aws_li_key": "k_credit",
            "product": "Amazon Elastic Compute Cloud",
            "usage_type": "Credit",
            "operation": "Credit",
            "unit": "",
            "line_item_type": "Credit",
            "pricing_model": "",
            "aws_amortized_cost": -20.0,
            "is_workload": False,
            "expected_workload": False,
            "expected_group": "negative_cost",
        },
        {
            "aws_li_key": "k_rifee",
            "product": "Amazon Elastic Compute Cloud",
            "usage_type": "HeavyUsage:m5.xlarge",
            "operation": "None",
            "unit": "Hrs",
            "line_item_type": "RIFee",
            "pricing_model": "Reserved",
            "aws_amortized_cost": 80.0,
            "is_workload": False,
            "_bill_has_discounted_usage": True,
            "expected_workload": False,
            "expected_group": "commitment_discount",
        },
    ]

    for item in corpus:
        # 1. Resolver agrees
        assert wr.is_row_workload(item) == item["expected_workload"], f"Resolver mismatch on {item['aws_li_key']}"

        # 2. Classifier routes as expected
        group, _ = classify_with_trace(item)
        assert group == item["expected_group"], f"Classifier routing mismatch on {item['aws_li_key']}: got {group}, expected {item['expected_group']}"


def test_passthrough_enforcer_and_validator_consistency(tmp_path):
    """Enforcer only touches workload items and sets valid intentional reason; validator verifies it."""
    db_file = str(tmp_path / "projection.duckdb")
    con = duckdb.connect(db_file)

    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            aws_amortized_cost DOUBLE,
            is_workload BOOLEAN,
            pricing_unit VARCHAR,
            product_code VARCHAR,
            instance_type VARCHAR,
            mechanic_group VARCHAR,
            mechanic_rule VARCHAR
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            strategy VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            gcp_sku_name VARCHAR,
            gcp_sku_unit VARCHAR,
            component VARCHAR,
            unit_multiplier DOUBLE,
            projection_note VARCHAR,
            passthrough_reason VARCHAR
        );
    """)

    # Row 1: Workload NAT gateway (governed by nat_vms_per_gateway)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'nat1', 'Amazon Virtual Private Cloud', 'NatGateway-Hours', 'NatGateway',
            100.0, TRUE, 'Hrs', 'AmazonVPC', NULL, 'flat_hourly', 'vpc_nat_hours'
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'nat1', 'map', 'Compute Engine', 'SKU-NAT', 'Nat Gateway', 'hour',
            'nat', 1.0, 'original note', NULL
        );
    """)

    # Row 2: Non-workload Marketplace row
    con.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'mkt1', 'AWS Marketplace', 'SaaS', 'Marketplace',
            50.0, FALSE, 'Mo', 'AWSMarketplace', NULL, 'non_workload', 'non_workload'
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'mkt1', 'passthrough', 'AWS Marketplace (Passthrough)', NULL, NULL, NULL,
            'passthrough', 1.0, 'marketplace note', 'service_policy_review'
        );
    """)

    # Configure assumptions with nat_vms_per_gateway forced passthrough
    assumptions_file = tmp_path / "assumptions.json"
    assumptions_file.write_text('{"passthrough": {"nat_vms_per_gateway": true}}', encoding="utf-8")

    summary = enforce_assumption_passthroughs(job_dir_or_db=str(tmp_path), conn=con)
    assert "nat_vms_per_gateway" in summary
    assert summary["nat_vms_per_gateway"]["rows"] == 1

    # Check updated row in duckdb
    row = con.execute("SELECT strategy, passthrough_reason, projection_note FROM aws_li_to_gcp_li WHERE aws_li_key = 'nat1'").fetchone()
    assert row[0] == "passthrough"
    assert row[1] == "operator_assumption"
    assert pr.is_intentional(row[1]) is True
    assert "[passthrough: forced via assumption nat_vms_per_gateway]" in row[2]

    # Validate integrity: 0 violations because both reasons are intentional
    violations = passthrough_integrity(con)
    assert len(violations) == 0

    con.close()
