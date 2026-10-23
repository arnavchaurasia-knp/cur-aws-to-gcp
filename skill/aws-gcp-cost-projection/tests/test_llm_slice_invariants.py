"""Tests for WORKORDER_llm_slice_invariants.md T4.

All tests use fixtures and do NOT invoke any LLM or external network APIs.
"""
import os
import sys
import duckdb
import pytest

_SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SKILL, "scripts"))

from validate_fix import check_llm_slice_invariants


def _setup_db(con):
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR,
            product VARCHAR,
            canonical_service VARCHAR,
            aws_amortized_cost DOUBLE,
            is_workload BOOLEAN
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            strategy VARCHAR,
            passthrough_reason VARCHAR,
            decided_by VARCHAR
        );
        CREATE TABLE gcp_projection (
            aws_li_key VARCHAR,
            gcp_projected_cost DOUBLE
        );
    """)


def test_hallucinated_target_flagged():
    """T4.1: A row with gcp_service outside its service's allowed set is flagged."""
    con = duckdb.connect(":memory:")
    _setup_db(con)

    # AWS Key Management Service (allowed target in service_map is 'Cloud KMS')
    # LLM assigns 'Vertex AI'
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'AWS Key Management Service', 'kms', 50.0, true);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k1', 'Vertex AI', 'SKU-TEST-1', 'map', NULL, 'misc_mappings.json');
        INSERT INTO gcp_projection VALUES
            ('k1', 50.0);
    """)

    violations, summary = check_llm_slice_invariants(con)
    assert summary["rows"] == 1
    assert any(
        v.get("invariant") == "allowed_target_service" and "Vertex AI" in v.get("error", "")
        for v in violations
    )


def test_map_without_sku_flagged():
    """T4.2: strategy='map' with a null or empty gcp_sku_id is flagged."""
    con = duckdb.connect(":memory:")
    _setup_db(con)

    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k2', 'Amazon CloudWatch', 'cloudwatch', 25.0, true);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k2', 'Cloud Monitoring + Logging', NULL, 'map', NULL, 'misc_mappings.json');
        INSERT INTO gcp_projection VALUES
            ('k2', 25.0);
    """)

    violations, summary = check_llm_slice_invariants(con)
    assert any(
        v.get("invariant") == "map_has_sku_id" for v in violations
    )


def test_passthrough_without_reason_flagged():
    """T4.3: Unexplained passthrough on LLM slice is flagged."""
    con = duckdb.connect(":memory:")
    _setup_db(con)

    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k3', 'AWS CloudTrail', 'cloudtrail', 15.0, true);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k3', 'Cloud Logging', NULL, 'passthrough', NULL, 'misc_mappings.json');
        INSERT INTO gcp_projection VALUES
            ('k3', 15.0);
    """)

    violations, summary = check_llm_slice_invariants(con)
    assert any(
        v.get("invariant") == "passthrough_reason_set" for v in violations
    )


def test_plausible_ratio_not_flagged():
    """T4.4: A row at 2x AWS cost passes; the 0.1-10.0 band does not fire on ordinary variation."""
    con = duckdb.connect(":memory:")
    _setup_db(con)

    # AWS Key Management Service -> Cloud KMS, ratio 2x ($100 vs $50)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k4', 'AWS Key Management Service', 'kms', 50.0, true);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k4', 'Cloud KMS', 'SKU-KMS-001', 'map', NULL, 'misc_mappings.json');
        INSERT INTO gcp_projection VALUES
            ('k4', 100.0);
    """)

    violations, summary = check_llm_slice_invariants(con)
    ratio_violations = [v for v in violations if v.get("invariant") == "cost_ratio_plausibility_band"]
    assert len(ratio_violations) == 0
    assert summary["rows"] == 1


def test_service_without_rule_is_skipped_and_counted():
    """T4.5: A row whose service has no service_map rule is skipped, and the skip is counted."""
    con = duckdb.connect(":memory:")
    _setup_db(con)

    # Some bespoke/fictional service not in service_map.json
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k5', 'CustomInHouseServiceThatHasNoRule', 'custom_service_xyz', 40.0, true);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k5', 'Compute Engine', 'SKU-GCE-01', 'map', NULL, 'misc_mappings.json');
        INSERT INTO gcp_projection VALUES
            ('k5', 40.0);
    """)

    violations, summary = check_llm_slice_invariants(con)
    target_violations = [v for v in violations if v.get("invariant") == "allowed_target_service"]
    assert len(target_violations) == 0
    assert summary["skipped_no_rule"] == 1
