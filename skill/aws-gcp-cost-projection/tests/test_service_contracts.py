"""
Tests for service_contracts.py (Phase J).

Verifies contract validation, default fallback behavior, canonical service
resolution for PDF product names, and duckdb bulk validation without pandas.
"""
import os
import sys
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from service_contracts import (
    validate_contract,
    _resolve_contract_key,
    validate_all_contracts,
    CONTRACTS,
)


def test_s3_row_does_not_fail_contract():
    # S3 rows do not have instance_type / vcpu / ram_gb.
    # They should resolve to _default contract and pass as long as region is present.
    row = {
        "product": "Amazon Simple Storage Service",
        "usage_type": "TimedStorage-ByteHrs",
        "region": "us-east-1",
    }
    key = _resolve_contract_key(row["product"], row)
    assert key == "_default"
    result = validate_contract(key, row)
    assert result.passed is True
    assert result.blocked_reason is None


def test_s3_row_with_gcp_region_alias_passes():
    row = {
        "product": "Amazon S3",
        "usage_type": "TimedStorage-ByteHrs",
        "gcp_region": "us-central1",
    }
    key = _resolve_contract_key(row["product"], row)
    assert key == "_default"
    result = validate_contract(key, row)
    assert result.passed is True


def test_pdf_rds_product_resolves_to_rds_contract():
    # PDF bills have product like "Relational Database Service for PostgreSQL"
    row = {
        "product": "Relational Database Service for PostgreSQL",
        "instance_type": "db.m5.large",
        "vcpu": 2,
        "ram_gb": 8,
        "region": "us-east-1",
        "engine": "postgres",
        "license_model": "general-public-license",
        "multi_az": "Single-AZ",
    }
    key = _resolve_contract_key(row["product"], row)
    assert key == "RDS"
    result = validate_contract(key, row)
    assert result.passed is True


def test_ec2_burstable_resolution():
    row = {"product": "Amazon EC2", "instance_type": "t3.medium"}
    assert _resolve_contract_key(row["product"], row) == "EC2_burstable"

    row_c5 = {"product": "Amazon EC2", "instance_type": "c5.xlarge"}
    assert _resolve_contract_key(row_c5["product"], row_c5) == "EC2"


def test_unknown_service_missing_region_fails_default_contract():
    row = {"product": "CustomService"}
    key = _resolve_contract_key(row["product"], row)
    assert key == "_default"
    result = validate_contract(key, row)
    assert result.passed is False
    assert "region" in result.unknown_required_fields


def test_validate_all_contracts_without_pandas(tmp_path):
    db_path = str(tmp_path / "projection.duckdb")
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE aws_li_catalog (
            rowid BIGINT,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            instance_type VARCHAR,
            vcpu INTEGER,
            ram_gb DOUBLE,
            architecture VARCHAR,
            region VARCHAR,
            gcp_region VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
        (1, 'Amazon Simple Storage Service', 'TimedStorage-ByteHrs', '', NULL, NULL, NULL, NULL, 'us-east-1', 'us-east1'),
        (2, 'Amazon Elastic Compute Cloud', 'BoxUsage:m5.large', 'RunInstances', 'm5.large', 2, 8.0, 'x86_64', 'us-east-1', 'us-east1'),
        (3, 'Amazon Elastic Compute Cloud', 'BoxUsage:c5.xlarge', 'RunInstances', NULL, NULL, NULL, NULL, 'us-east-1', 'us-east1')
    """)
    con.close()

    summary = validate_all_contracts(db_path)
    assert summary["total_rows"] == 3
    # Row 1 (S3): passes _default contract
    # Row 2 (EC2): passes EC2 contract
    # Row 3 (EC2 with NULL instance_type): fails EC2 contract
    assert summary["contract_failures"] == 1
    assert "EC2" in summary["details_by_service"]
    assert summary["details_by_service"]["EC2"]["failures"] == 1
    assert "_default" in summary["details_by_service"]
    assert summary["details_by_service"]["_default"]["failures"] == 0
