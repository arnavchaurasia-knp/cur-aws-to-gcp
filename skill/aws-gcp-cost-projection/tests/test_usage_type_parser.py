"""Unit tests for usage_type_parser.py — additive, pure parsing (no DB/network)."""
import sys
import os

_SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SKILL, "scripts"))

from usage_type_parser import parse, parse_with_unit  # noqa: E402


def test_region_extracted_from_usage_type_prefix():
    r = parse("APS3-EBS:VolumeUsage.gp3", "CreateVolume", "Amazon Elastic Block Store")
    assert r["region"] == "asia-south1", r


def test_region_empty_when_no_prefix():
    r = parse("BoxUsage:m5.large", "RunInstances", "Amazon EC2")
    assert r["region"] == "", r


def test_family_ec2():
    r = parse("BoxUsage:m5.large", "RunInstances", "Amazon EC2")
    assert r["family"] == "ec2", r


def test_family_s3():
    r = parse("TimedStorage-ByteHrs", "PutObject", "Amazon S3")
    assert r["family"] == "s3", r


def test_family_rds():
    r = parse("DBInstanceUsage", "CreateDBInstance", "Amazon RDS")
    assert r["family"] == "rds", r


def test_family_lambda():
    r = parse("Lambda-GB-Second", "Invoke", "AWS Lambda")
    assert r["family"] == "lambda", r


def test_sub_type_storage():
    r = parse("TimedStorage-ByteHrs", "PutObject", "Amazon S3")
    assert r["sub_type"] == "storage", r


def test_sub_type_instance_hours():
    r = parse("BoxUsage:m5.large", "RunInstances", "Amazon EC2")
    assert r["sub_type"] == "instance_hours", r


def test_sub_type_requests():
    r = parse("Requests", "Invoke", "AWS Lambda")
    assert r["sub_type"] == "requests", r


def test_sub_type_data_transfer():
    r = parse("DataTransfer-Out-Bytes", "GetObject", "Amazon S3")
    assert r["sub_type"] == "data_transfer", r


def test_class_compute_for_ec2():
    r = parse("BoxUsage:m5.large", "RunInstances", "Amazon EC2")
    assert r["class"] == "compute", r


def test_class_storage_for_s3():
    r = parse("TimedStorage-ByteHrs", "PutObject", "Amazon S3")
    assert r["class"] == "storage", r


def test_class_database_for_rds():
    r = parse("DBInstanceUsage", "CreateDBInstance", "Amazon RDS")
    assert r["class"] == "database", r


def test_class_networking_for_route53():
    r = parse("Query", "CreateHostedZone", "Amazon Route 53")
    assert r["class"] == "networking", r


def test_unit_derives_from_usage_type_hint():
    r = parse("BoxUsage:m5.large", "RunInstances", "Amazon EC2")
    assert r["unit"] == "hrs", r


def test_unit_derives_gbmo_for_storage():
    r = parse("TimedStorage-ByteHrs", "PutObject", "Amazon S3")
    assert r["unit"] == "gb-mo", r


def test_unit_derives_requests():
    r = parse("Requests", "Invoke", "AWS Lambda")
    assert r["unit"] == "requests", r


def test_pricing_unit_overrides_hint():
    r = parse_with_unit("BoxUsage:m5.large", "RunInstances", "Amazon EC2", "GB-Mo")
    assert r["unit"] == "gb-mo", r


def test_unknown_service_is_unknown():
    r = parse("SomethingWeird", "DoThing", "CustomInHouseServiceThatHasNoRule")
    assert r["family"] == "unknown", r
    assert r["class"] == "unknown", r


def test_empty_inputs_safe():
    r = parse(None, None, None)
    assert r == {"region": "", "family": "unknown", "sub_type": "usage", "unit": "", "class": "unknown"}, r


def test_longer_product_name_wins_over_prefix():
    # "Kinesis Firehose" must map to firehose, not kinesis.
    r = parse("PutRecord", "PutRecord", "Amazon Kinesis Firehose")
    assert r["family"] == "firehose", r
