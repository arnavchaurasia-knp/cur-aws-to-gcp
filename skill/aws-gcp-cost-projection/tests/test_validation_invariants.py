import duckdb
import pytest
import os
import sys

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from validate_fix import (
    check_cardinality_conservation,
    check_egress_strategy_invariant,
)


def test_cardinality_conservation_no_table():
    con = duckdb.connect(":memory:")
    violations = check_cardinality_conservation(con)
    assert violations == []


def test_cardinality_conservation_passes():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE ingest_stats (
            job_id VARCHAR,
            raw_row_count BIGINT,
            distinct_dedup_tuples_in BIGINT,
            catalog_row_count BIGINT,
            raw_cost_sum DOUBLE,
            catalog_cost_sum DOUBLE,
            invariant_holds BOOLEAN,
            recorded_at TIMESTAMP
        );
        INSERT INTO ingest_stats VALUES
            ('job1', 100, 42, 42, 123.45, 123.45, TRUE, CURRENT_TIMESTAMP);
    """)
    violations = check_cardinality_conservation(con)
    assert violations == []


def test_cardinality_conservation_fails():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE ingest_stats (
            job_id VARCHAR,
            raw_row_count BIGINT,
            distinct_dedup_tuples_in BIGINT,
            catalog_row_count BIGINT,
            raw_cost_sum DOUBLE,
            catalog_cost_sum DOUBLE,
            invariant_holds BOOLEAN,
            recorded_at TIMESTAMP
        );
        INSERT INTO ingest_stats VALUES
            ('job2', 100, 45, 42, 123.45, 120.00, FALSE, CURRENT_TIMESTAMP);
    """)
    violations = check_cardinality_conservation(con)
    assert len(violations) == 1
    assert violations[0]["gate"] == "cardinality_conservation"
    assert violations[0]["distinct_dedup_tuples_in"] == 45
    assert violations[0]["catalog_row_count"] == 42


def _make_transfer_test_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            mechanic_group VARCHAR,
            is_workload BOOLEAN,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            component VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            strategy VARCHAR,
            rate_source VARCHAR
        );
    """)
    return con


def test_egress_strategy_invariant_passes_mapped_and_ignore():
    con = _make_transfer_test_db()
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('dt1', 'AWS Data Transfer', 'APS1-AWS-Out-Bytes', 'DataTransfer-Out', 'data_transfer', TRUE, 85.50),
            ('dt2', 'AWS Data Transfer', 'APS1-DataTransfer-In-Bytes', 'DataTransfer-In', 'data_transfer', TRUE, 0.00);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('dt1', 'transfer', 'Compute Engine', 'DE9E-AFBC-A15A', 'map', 'canonical-egress'),
            ('dt2', 'transfer', 'Compute Engine', NULL, 'ignore', 'ignored');
    """)
    violations = check_egress_strategy_invariant(con)
    assert violations == []


def test_egress_strategy_invariant_catches_needs_reprice_sku():
    con = _make_transfer_test_db()
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('dt1', 'AWS Data Transfer', 'APS1-AWS-Out-Bytes', 'DataTransfer-Out', 'data_transfer', TRUE, 85.50);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('dt1', 'transfer', 'Compute Engine', 'needs_reprice', 'map', 'needs_reprice');
    """)
    violations = check_egress_strategy_invariant(con)
    assert len(violations) == 1
    assert violations[0]["aws_li_key"] == "dt1"
    assert violations[0]["gcp_sku_id"] == "needs_reprice"


def test_egress_strategy_invariant_catches_passthrough_strategy():
    con = _make_transfer_test_db()
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('dt1', 'AWS Data Transfer', 'APS1-AWS-Out-Bytes', 'DataTransfer-Out', 'data_transfer', TRUE, 85.50);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('dt1', 'transfer', 'Compute Engine', NULL, 'passthrough', 'passthrough');
    """)
    violations = check_egress_strategy_invariant(con)
    assert len(violations) == 1
    assert violations[0]["aws_li_key"] == "dt1"
    assert violations[0]["strategy"] == "passthrough"


def test_egress_strategy_invariant_catches_null_strategy():
    con = _make_transfer_test_db()
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('dt1', 'AWS Data Transfer', 'APS1-AWS-Out-Bytes', 'DataTransfer-Out', 'data_transfer', TRUE, 85.50);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('dt1', 'transfer', 'Compute Engine', 'DE9E-AFBC-A15A', NULL, 'exact_sku');
    """)
    violations = check_egress_strategy_invariant(con)
    assert len(violations) == 1
    assert violations[0]["aws_li_key"] == "dt1"
    assert violations[0]["strategy"] is None


def test_egress_strategy_invariant_ignores_nat_and_cdn():
    con = _make_transfer_test_db()
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('nat1', 'AmazonEC2', 'APS1-NatGateway-Bytes', 'NatGateway', 'data_transfer', TRUE, 12.00),
            ('cf1', 'Amazon CloudFront', 'US-DataTransfer-Out-Bytes', 'CloudFront', 'data_transfer', TRUE, 5.00);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('nat1', 'nat', 'Cloud NAT', 'NAT-SKU', 'passthrough', 'passthrough'),
            ('cf1', 'cdn', 'Cloud CDN', 'CDN-SKU', 'passthrough', 'passthrough');
    """)
    violations = check_egress_strategy_invariant(con)
    assert violations == []
