#!/usr/bin/env python3
"""Tests for scripts/directive_resolver.py."""
import pytest
from scripts.directives import Directive
from scripts.directive_resolver import resolve, compute_specificity, extract_row_identity


def test_specificity_ordering_pin_beats_service():
    rows = [
        {"aws_li_key": "row_ec2_1", "product": "Amazon Elastic Compute Cloud", "usage_type": "BoxUsage:m5.large"},
        {"aws_li_key": "row_ec2_2", "product": "Amazon Elastic Compute Cloud", "usage_type": "BoxUsage:c5.xlarge"},
    ]

    # Broad directive: all EC2 -> passthrough
    d_broad = Directive(
        id="d_broad",
        author="user",
        selector={"service": "ec2"},
        action={"type": "set_strategy", "value": "passthrough"},
    )
    # Specific pin: row_ec2_1 -> map
    d_pinned = Directive(
        id="d_pinned",
        author="user",
        selector={"aws_li_keys": ["row_ec2_1"]},
        action={"type": "set_strategy", "value": "map"},
    )

    # Even if d_broad comes AFTER d_pinned in ledger, d_pinned has higher specificity
    res = resolve(rows, [d_pinned, d_broad])

    # row_ec2_1 got 'map' (pin wins over broad service)
    assert res["row_ec2_1"].strategy == "map"
    assert "d_pinned" in res["row_ec2_1"].applied_directives
    assert "d_broad" in res["row_ec2_1"].superseded_directives

    # row_ec2_2 only matched d_broad
    assert res["row_ec2_2"].strategy == "passthrough"
    assert "d_broad" in res["row_ec2_2"].applied_directives


def test_recency_tie_break():
    rows = [
        {"aws_li_key": "row_s3_1", "product": "Amazon Simple Storage Service", "usage_type": "TimedStorage-ByteHrs"}
    ]

    d_first = Directive(
        id="d1",
        author="user",
        selector={"service": "s3"},
        action={"type": "set_target_service", "value": "Cloud Storage"},
    )
    d_later = Directive(
        id="d2",
        author="user",
        selector={"service": "s3"},
        action={"type": "set_target_service", "value": "Filestore"},
    )

    # Equal specificity: d2 is later in ledger, so d2 wins
    res = resolve(rows, [d_first, d_later])
    assert res["row_s3_1"].target_service == "Filestore"
    assert "d2" in res["row_s3_1"].applied_directives
    assert "d1" in res["row_s3_1"].superseded_directives


def test_non_matching_is_noop():
    rows = [
        {"aws_li_key": "row_ec2_1", "product": "Amazon Elastic Compute Cloud", "usage_type": "BoxUsage:m5.large"}
    ]

    # Directive for S3
    d_s3 = Directive(
        id="d_s3",
        author="user",
        selector={"service": "s3"},
        action={"type": "set_strategy", "value": "ignore"},
    )

    res = resolve(rows, [d_s3])
    assert "row_ec2_1" not in res


def test_annotation_accumulation():
    rows = [
        {"aws_li_key": "row_msk_1", "product": "Amazon Managed Streaming for Apache Kafka", "usage_type": "Kafka.m5.large"}
    ]

    d1 = Directive(
        id="d1",
        author="user",
        selector={"service": "msk"},
        action={"type": "annotate", "value": "Broker sizing assumes 3 brokers"},
    )
    d2 = Directive(
        id="d2",
        author="user",
        selector={"service": "msk"},
        action={"type": "annotate", "value": "Customer confirmed self-managed GCE Kafka target"},
    )

    res = resolve(rows, [d1, d2])
    assert "row_msk_1" in res
    assert len(res["row_msk_1"].notes) == 2
    assert "d1" in res["row_msk_1"].applied_directives
    assert "d2" in res["row_msk_1"].applied_directives


def test_section0_derived_identity_selectors():
    """Verify {service:ec2, arch:arm64}, {vendor:intel}, and instance_family:['m7g']."""
    rows = [
        {"aws_li_key": "graviton_row", "product": "Amazon Elastic Compute Cloud", "usage_type": "BoxUsage:m7g.large"},
        {"aws_li_key": "intel_row", "product": "Amazon Elastic Compute Cloud", "usage_type": "BoxUsage:c7i.2xlarge"},
        {"aws_li_key": "amd_row", "product": "Amazon Elastic Compute Cloud", "usage_type": "BoxUsage:c6a.xlarge"},
    ]

    # 1. arch: arm64 hits only Graviton
    d_arm = Directive(
        id="d_arm",
        author="user",
        selector={"service": "ec2", "arch": "arm64"},
        action={"type": "set_target_family", "value": "C4A"},
    )
    res_arm = resolve(rows, [d_arm])
    assert "graviton_row" in res_arm
    assert res_arm["graviton_row"].target_family == "C4A"
    assert "intel_row" not in res_arm
    assert "amd_row" not in res_arm

    # 2. vendor: intel hits only Intel
    d_intel = Directive(
        id="d_intel",
        author="user",
        selector={"vendor": "intel"},
        action={"type": "set_target_family", "value": "C4"},
    )
    res_intel = resolve(rows, [d_intel])
    assert "intel_row" in res_intel
    assert res_intel["intel_row"].target_family == "C4"
    assert "graviton_row" not in res_intel
    assert "amd_row" not in res_intel

    # 3. instance_family: ["m7g"] hits exact family
    d_m7g = Directive(
        id="d_m7g",
        author="user",
        selector={"instance_family": ["m7g"]},
        action={"type": "set_target_family", "value": "N2D"},
    )
    res_m7g = resolve(rows, [d_m7g])
    assert "graviton_row" in res_m7g
    assert res_m7g["graviton_row"].target_family == "N2D"
    assert "intel_row" not in res_m7g
    assert "amd_row" not in res_m7g


def test_preference_verbs_resolution():
    """Verify set_target_shape, set_region, set_pricing_basis fields in EffectiveOverride."""
    rows = [
        {"aws_li_key": "row_ec2", "product": "Amazon Elastic Compute Cloud", "usage_type": "BoxUsage:m5.large"},
    ]

    d_shape = Directive(
        id="d_shape",
        author="user",
        selector={"aws_li_keys": ["row_ec2"]},
        action={"type": "set_target_shape", "value": {"vcpu": 4, "ram_gb": 16.0}},
    )
    d_reg = Directive(
        id="d_reg",
        author="user",
        selector={"aws_li_keys": ["row_ec2"]},
        action={"type": "set_region", "value": "europe-west1", "region_policy": "nearest_then_global"},
    )
    d_cud = Directive(
        id="d_cud",
        author="user",
        selector={"aws_li_keys": ["row_ec2"]},
        action={"type": "set_pricing_basis", "value": "cud_3yr"},
    )

    res = resolve(rows, [d_shape, d_reg, d_cud])
    eff = res["row_ec2"]
    assert eff.target_shape == {"vcpu": 4, "ram_gb": 16.0}
    assert eff.region == "europe-west1"
    assert eff.region_policy == "nearest_then_global"
    assert eff.pricing_basis == "cud_3yr"
    assert "d_shape" in eff.applied_directives
    assert "d_reg" in eff.applied_directives
    assert "d_cud" in eff.applied_directives
