"""
Layer 1 authoritative product-code resolution tests.

canonical_service() now keys on the AWS service code (= CUR
lineItem/ProductCode) first, via data/aws-product-codes.json — a closed,
authoritative set regenerated from the AWS Price List bulk index by
scripts/refresh_aws_product_codes.py. The free-text product NAME remains the
fallback for bills that carry no code (PDF invoices, Cost Explorer CSV).

These tests pin the three properties that make the code-first design safe:
  1. codes resolve deterministically to the existing canonical keys,
  2. the code wins over a missing/garbled free-text name,
  3. an unknown/absent code falls back to the name path unchanged.
"""
import json
import os

from aws_normalizer import canonical_from_code, canonical_service

_CODES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "aws-product-codes.json"
)


def _codes():
    return json.load(open(_CODES_PATH, encoding="utf-8"))


def test_product_codes_file_is_a_closed_authoritative_set():
    doc = _codes()
    codes = doc["codes"]
    # The AWS price-list index has >250 services; guard against a truncated or
    # empty regeneration silently shipping.
    assert len(codes) > 200, len(codes)
    assert doc["_meta"]["count"] == len(codes)
    # Every value is a non-empty canonical key.
    assert all(isinstance(v, str) and v for v in codes.values())


def test_codes_resolve_to_existing_canonical_keys():
    # Real CUR lineItem/ProductCode values -> the canonical keys downstream
    # service_map rules already match on.
    cases = {
        "AmazonEC2": "ec2",
        "AmazonS3": "s3",
        "AmazonRDS": "rds",
        "AWSLambda": "lambda",
        "AmazonECS": "ecs",
        "AmazonEKS": "eks",
        "AmazonECR": "ecr",
        "AWSDatabaseMigrationSvc": "dms",
        "AmazonMemoryDB": "memorydb",
        "AmazonGuardDuty": "guardduty",
        "AmazonSageMaker": "sagemaker",
        "AmazonRoute53": "route53",
    }
    for code, expected in cases.items():
        assert canonical_from_code(code) == expected, code


def test_code_lookup_is_case_insensitive():
    assert canonical_from_code("amazonec2") == "ec2"
    assert canonical_from_code("AMAZONEC2") == "ec2"
    assert canonical_from_code("  AmazonEC2  ") == "ec2"


def test_legacy_elasticsearch_code_maps_to_opensearch():
    # AmazonES is the pre-rename Elasticsearch Service code; it must land on the
    # same canonical key as OpenSearch so the existing opensearch rule fires on
    # old bills. This is the kind of legacy-rename case the authoritative code
    # list handles that a current-snapshot name resolver would miss.
    assert canonical_from_code("AmazonES") == "opensearch"


def test_unknown_code_returns_none():
    assert canonical_from_code("TotallyNotARealServiceCode") is None
    assert canonical_from_code("") is None
    assert canonical_from_code(None) is None


def test_code_wins_over_missing_or_garbled_name():
    # PDF/flat bills sometimes leave product as the bare code or a garbled
    # string; the authoritative code must still resolve the row.
    assert canonical_service("some garbled label", "AmazonS3") == "s3"
    assert canonical_service("", "AmazonEC2") == "ec2"
    assert canonical_service("AWSDatabaseMigrationSvc", "AWSDatabaseMigrationSvc") == "dms"


def test_name_fallback_unchanged_when_no_code():
    # No code -> the existing free-text path must behave exactly as before.
    assert canonical_service("Amazon Simple Email Service") == "ses"
    assert canonical_service("AmazonSES") == "ses"
    assert canonical_service("Amazon Elastic Compute Cloud") == "ec2"


def test_unknown_code_falls_back_to_name():
    # A code we don't recognize must not block the name path.
    assert canonical_service("Amazon Elastic Compute Cloud", "SomeFutureCode") == "ec2"
    assert canonical_service("Amazon Simple Storage Service", "") == "s3"
