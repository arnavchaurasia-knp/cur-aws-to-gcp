#!/usr/bin/env python3
"""
test_assert_pricing_matrix.py — Tests for pricing matrix reconciliation and parity SKUs.
"""

import os
import sys
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from assert_pricing_matrix import load_active_matrix, assert_pricing_matrix, get_expected_strategy
from apply_static_mappings import map_per_request, resolve_sku


def test_load_active_matrix_filters_inactive():
    active = load_active_matrix()
    assert len(active) > 0
    statuses = {r.get("status") for r in active}
    assert statuses == {"active"}
    
    # Verify rejected rows are NOT active
    for r in active:
        assert r["charge_type"] != "Public IPv4"
        assert r["charge_type"] != "EKS cluster-hours"
        assert r["charge_type"] != "Lambda GB-seconds"
        assert r["charge_type"] != "NAT gateway-hours"


def test_expected_strategy_mapping():
    assert get_expected_strategy("SKU") == {"map", "break_down"}
    assert get_expected_strategy("FORMULA") == {"map", "break_down"}
    assert get_expected_strategy("PASS") == {"passthrough"}
    assert get_expected_strategy("IGNORE") == {"ignore"}


def test_parity_skus_resolve_to_pinned_ids():
    # Cloud KMS software keys
    s_kms_keys = resolve_sku("Cloud KMS", "^Active software symmetric key versions$", "global")
    assert s_kms_keys.sku_id == "E09C-32B3-9AC7"

    # Cloud KMS operations
    s_kms_ops = resolve_sku("Cloud KMS", "^Cryptographic operations with a software symmetric key$", "global")
    assert s_kms_ops.sku_id == "3BDA-77FB-678B"

    # Cloud DNS ManagedZone
    s_dns_zone = resolve_sku("Cloud DNS", "^ManagedZone$", "global")
    assert s_dns_zone.sku_id == "8C22-6FC3-D478"

    # Cloud DNS Queries
    s_dns_query = resolve_sku("Cloud DNS", "DNS Query", "global")
    assert s_dns_query.sku_id == "6DFF-5025-A128"

    # Secret Manager secret versions
    s_sec_vers = resolve_sku("Secret Manager", "Active secret version", "global")
    assert s_sec_vers.sku_id == "7756-ADEF-84F4"

    # Secret Manager access operations
    s_sec_ops = resolve_sku("Secret Manager", "Secret access operations", "global")
    assert s_sec_ops.sku_id == "EBA7-264F-2D2C"


def test_map_per_request_handles_secrets_manager():
    rows = [
        {
            "aws_li_key": "k_sec_vers",
            "product": "AWS Secrets Manager",
            "usage_type": "USE1-Secret-Months",
            "operation": "",
            "unit": "Secrets",
            "gcp_region": "us-east4",
        },
        {
            "aws_li_key": "k_sec_ops",
            "product": "AWS Secrets Manager",
            "usage_type": "USE1-API-Requests",
            "operation": "GetSecretValue",
            "unit": "Requests",
            "gcp_region": "us-east4",
        },
    ]
    out = map_per_request(rows)
    assert len(out) == 2
    
    # Version storage row
    v_row = out[0]
    assert v_row["strategy"] == "map"
    assert v_row["gcp_service"] == "Secret Manager"
    assert str(v_row["gcp_sku_id"]) == "7756-ADEF-84F4"
    assert v_row["component"] == "secrets"

    # Access operations row
    o_row = out[1]
    assert o_row["strategy"] == "map"
    assert o_row["gcp_service"] == "Secret Manager"
    assert str(o_row["gcp_sku_id"]) == "EBA7-264F-2D2C"
    assert o_row["component"] == "requests"


def test_assert_pricing_matrix_runs_and_detects_mismatch(tmp_path):
    db_file = str(tmp_path / "projection.duckdb")
    con = duckdb.connect(db_file)
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR,
            product VARCHAR,
            canonical_service VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE gcp_projection (
            aws_li_key VARCHAR,
            strategy VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            gcp_projected_cost DOUBLE
        );
    """)

    # Row 1: KMS key-months correctly mapped
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
        ('k1', 'AWS Key Management Service', 'kms', 'USE1-KMS-Keys', '', 100.0);
        INSERT INTO gcp_projection VALUES
        ('k1', 'map', 'Cloud KMS', 'E09C-32B3-9AC7', 6.0);
    """)

    # Row 2: KMS ops wrongly passthroughed (mismatch)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
        ('k2', 'AWS Key Management Service', 'kms', 'USE1-KMS-Requests', 'Decrypt', 50.0);
        INSERT INTO gcp_projection VALUES
        ('k2', 'passthrough', 'Cloud KMS', NULL, 50.0);
    """)
    con.close()

    res = assert_pricing_matrix(db_file)
    assert res["matched_rows"] == 2
    assert len(res["mismatches"]) == 1
    m = res["mismatches"][0]
    assert m["aws_li_key"] == "k2"
    assert m["canonical_service"] == "kms"
    assert m["actual_strategy"] == "passthrough"
    assert "map" in m["expected_strategies"]
