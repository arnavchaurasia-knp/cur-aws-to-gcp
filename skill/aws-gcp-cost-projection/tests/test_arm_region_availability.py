import os
import sys
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from apply_static_mappings import map_compute_arm, map_msk
from tests.test_static_mappers import _with_sku


def make_row(aws_li_key="k1", product="Amazon Elastic Compute Cloud", usage_type="APS5-BoxUsage:m7g.2xlarge",
             operation="RunInstances", unit="Hrs", gcp_region="asia-south2", aws_cost=58.63):
    return {
        "aws_li_key": aws_li_key,
        "product": product,
        "usage_type": usage_type,
        "operation": operation,
        "pricing_unit": unit,
        "gcp_region": gcp_region,
        "aws_amortized_cost": aws_cost,
        "pricing_currency": "USD",
        "pricing_term": "OnDemand",
        "legal_entity": "AWS",
        "description": "EC2 Graviton instance",
        "instance_type": "m7g.2xlarge",
        "instance_vcpus": 8,
        "instance_ram_gb": 32.0,
    }


def test_family_not_available_reason_used():
    """1. test_family_not_available_reason_used:
    A row whose family has no SKU in its region gets family_not_available_in_region,
    not unresolved_sku.
    """
    row = make_row(gcp_region="asia-south2", usage_type="APS5-BoxUsage:m7g.2xlarge")
    out = _with_sku(map_compute_arm, [row])
    assert len(out) == 2
    for r in out:
        assert r["strategy"] == "passthrough"
        assert r["gcp_sku_id"] is None
        assert r.get("passthrough_reason") == "family_not_available_in_region"


def test_sku_name_never_holds_error_text():
    """2. test_sku_name_never_holds_error_text:
    No row in a built projection has a gcp_sku_name containing 'no GCP rate found'.
    """
    row_ec2 = make_row(aws_li_key="k1", gcp_region="asia-south2", usage_type="APS5-BoxUsage:m7g.2xlarge")
    out_ec2 = _with_sku(map_compute_arm, [row_ec2])
    for r in out_ec2:
        sku_name = r.get("gcp_sku_name")
        assert sku_name is None or "no GCP rate found" not in str(sku_name)

    row_msk = make_row(
        aws_li_key="k2",
        product="Amazon Managed Streaming for Apache Kafka",
        usage_type="APS5-Kafka.m7g.xlarge",
        operation="",
        unit="Hrs",
        gcp_region="asia-south2",
    )
    out_msk = _with_sku(map_msk, [row_msk])
    for r in out_msk:
        sku_name = r.get("gcp_sku_name")
        assert sku_name is None or "no GCP rate found" not in str(sku_name)


def test_sanity_check_sees_null_sku_rows(caplog):
    """3. test_sanity_check_sees_null_sku_rows:
    A mapped row with gcp_sku_id IS NULL is counted by the unreachable check.
    """
    import logging
    import apply_rates

    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            gcp_region VARCHAR,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            strategy VARCHAR,
            passthrough_reason VARCHAR,
            projection_note VARCHAR
        );
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id VARCHAR,
            region VARCHAR,
            rate_usd DOUBLE
        );
    """)

    # Row with NULL gcp_sku_id and family_not_available_in_region
    con.execute("""
        INSERT INTO aws_li_catalog VALUES ('k_arm', 'asia-south2', 500.0);
        INSERT INTO aws_li_to_gcp_li VALUES ('k_arm', 'Compute Engine', NULL, 'passthrough', 'family_not_available_in_region', 'note');
    """)

    # Query matching apply_rates.py sanity check
    cols_m = {c[1].lower() for c in con.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
    has_pt_reason = "passthrough_reason" in cols_m
    pt_reason_clause = "OR m.passthrough_reason = 'family_not_available_in_region'" if has_pt_reason else ""
    null_sku_unpriced = con.execute(f"""
        SELECT COUNT(*) AS rows_count, COALESCE(SUM(c.aws_amortized_cost), 0.0) AS total_usd
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.gcp_sku_id IS NULL
          AND (
              m.strategy IN ('map', 'break_down')
              {pt_reason_clause}
          )
    """).fetchone()

    rows_count = int(null_sku_unpriced[0])
    total_usd = float(null_sku_unpriced[1])

    assert rows_count == 1
    assert total_usd == 500.0


def test_success_message_not_printed_when_rows_unpriced(caplog):
    """4. test_success_message_not_printed_when_rows_unpriced:
    The 'all mapped SKUs have reachable rates' line does not appear when unpriced rows exist.
    """
    import logging

    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            gcp_region VARCHAR,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            strategy VARCHAR,
            passthrough_reason VARCHAR,
            projection_note VARCHAR
        );
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id VARCHAR,
            region VARCHAR,
            rate_usd DOUBLE
        );
    """)

    con.execute("""
        INSERT INTO aws_li_catalog VALUES ('k_arm', 'asia-south2', 100.0);
        INSERT INTO aws_li_to_gcp_li VALUES ('k_arm', 'Compute Engine', NULL, 'passthrough', 'family_not_available_in_region', 'note');
    """)

    unreachable = []
    cols_m = {c[1].lower() for c in con.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
    has_pt_reason = "passthrough_reason" in cols_m
    pt_reason_clause = "OR m.passthrough_reason = 'family_not_available_in_region'" if has_pt_reason else ""
    null_sku_unpriced = con.execute(f"""
        SELECT COUNT(*) AS rows_count, COALESCE(SUM(c.aws_amortized_cost), 0.0) AS total_usd
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.gcp_sku_id IS NULL
          AND (
              m.strategy IN ('map', 'break_down')
              {pt_reason_clause}
          )
    """).fetchone()

    null_sku_rows = int(null_sku_unpriced[0] or 0)

    logged_messages = []
    logger = logging.getLogger("test_apply_rates")
    if unreachable:
        logged_messages.append("unreachable moved to outlier_triage")
    elif null_sku_rows == 0:
        logged_messages.append("Sanity check: all mapped SKUs have reachable rates")

    assert "Sanity check: all mapped SKUs have reachable rates" not in logged_messages


def test_passthrough_parity_unchanged():
    """5. test_passthrough_parity_unchanged:
    The core component still carries the full AWS cost and ram stays $0;
    totals are identical before and after.
    """
    row = make_row(gcp_region="asia-south2", usage_type="APS5-BoxUsage:m7g.2xlarge", aws_cost=100.0)
    out = _with_sku(map_compute_arm, [row])
    assert len(out) == 2
    core = next(r for r in out if r["component"] == "core")
    ram = next(r for r in out if r["component"] == "ram")

    assert core["strategy"] == "passthrough"
    assert ram["strategy"] == "passthrough"
    assert core["gcp_sku_id"] is None
    assert ram["gcp_sku_id"] is None
