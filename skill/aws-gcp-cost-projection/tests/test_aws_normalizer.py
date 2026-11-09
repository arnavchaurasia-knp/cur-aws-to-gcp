"""
Layer 1 (aws_normalizer.py) and service_classifier.py regression tests.

Real AWS CUR bills commonly give the `product` field as the compact
concatenated form ("AmazonSES", "AmazonSNS", ...) rather than the spelled-out
name ("Amazon Simple Email Service"). Both canonical_service() and
service_classifier._norm() used to require a literal space after the
"Amazon"/"AWS" prefix before stripping it, which silently failed to
normalize every short-code compact-form product name — confirmed on a real
bill where "AmazonSES" (product field, verbatim) resolved to no canonical
service at all, so its $1,789 charge fell through the SES service_map rule
into the generic "no GCP equivalent found" bucket instead.
"""
import json
import os

from aws_normalizer import canonical_service
from service_classifier import _norm, _rule_matches

_SERVICE_MAP_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "service_map.json"
)


def _rules():
    return [r for r in json.load(open(_SERVICE_MAP_PATH)).get("rules", []) if r.get("match")]


def test_canonical_service_handles_compact_product_names():
    # These are real, unmodified AWS CUR product-field values.
    cases = {
        "AmazonSES": "ses",
        "AmazonSNS": "sns",
        "AmazonSQS": "sqs",
        "AmazonECS": "ecs",
        "AmazonEKS": "eks",
        "AmazonMSK": "msk",
        "AmazonKMS": "kms",
        "AmazonEBS": "ebs",
    }
    for product, expected in cases.items():
        assert canonical_service(product) == expected, product


def test_canonical_service_still_handles_spelled_out_names():
    assert canonical_service("Amazon Simple Email Service") == "ses"
    assert canonical_service("Amazon Elastic Compute Cloud") == "ec2"


def test_norm_strips_compact_prefix():
    assert _norm("AmazonSES") == "ses"
    assert _norm("AmazonMSK") == "msk"
    assert _norm("Amazon Simple Email Service") == "simple email service"


def test_ses_rule_matches_compact_product_name():
    rule = next(r for r in _rules() if "simple email" in r["match"])
    cs_product = canonical_service("AmazonSES")
    assert _rule_matches(rule, "AmazonSES", "APS3-Recipients-EC2", cs_product, {})


def test_sns_and_sqs_rules_match_compact_product_names():
    sns_rule = next(r for r in _rules() if "simple notification" in r["match"])
    sqs_rule = next(r for r in _rules() if "simple queue" in r["match"])
    assert _rule_matches(sns_rule, "AmazonSNS", "Requests-Tier1", canonical_service("AmazonSNS"), {})
    assert _rule_matches(sqs_rule, "AmazonSQS", "Requests-Tier1", canonical_service("AmazonSQS"), {})


def test_dms_product_name_recognized_as_native_aws_service():
    # Real AWS CUR product-field value (job 6a561187, $945.00 row): a native,
    # first-party AWS service ("AWS Database Migration Service") with no space
    # after the "AWS" prefix and no internal word separators at all
    # ("AWSDatabaseMigrationSvc" is one PascalCase-concatenated ProductCode).
    # canonical_service() previously returned None for it (no alias existed at
    # all, unlike SES/SNS/SQS which had aliases but failed normalization) —
    # which meant classify_mechanics.py's marketplace_thirdparty catch-all
    # rule (guarded by `canonical_service(product) is None`) misclassified a
    # genuine native AWS service as third-party marketplace spend, permanently
    # blocking it from the pre-existing, already-correct "database migration"
    # service_map rule (DMS -> Database Migration Service, flagged for $0
    # candidacy since GCP DMS is free for homogeneous migrations).
    assert canonical_service("AWSDatabaseMigrationSvc") == "dms"
    assert canonical_service("AWS Database Migration Service") == "dms"


def test_dms_rule_matches_and_is_not_marketplace_thirdparty():
    import re

    product = "AWSDatabaseMigrationSvc"
    cs_product = canonical_service(product)
    assert not (
        bool(product)
        and not re.match(r"^(amazon|aws)\b", product, re.IGNORECASE)
        and cs_product is None
    ), "DMS row would still be misclassified as marketplace_thirdparty"

    dms_rule = next(r for r in _rules() if r["match"] == "database migration")
    # Covers every DMS replication-instance size seen on job 6a561187 (r5.2xlarge
    # $945.00, r5.xlarge $262.35, r6i.xlarge $262.35) — the fix is keyed on the
    # product name, not the instance type, so all sizes must resolve alike.
    for usage_type in (
        "APS3-InstanceUsg:dms.r5.2xlarge",
        "APS3-InstanceUsg:dms.r5.xlarge",
        "APS3-InstanceUsg:dms.r6i.xlarge",
        "APS3-InstanceUsg:dms.t3.large",
        "APS3-InstanceUsg:dms.t3.medium",
    ):
        assert _rule_matches(dms_rule, product, usage_type, cs_product, {}), usage_type


def test_sagemaker_product_name_recognized_as_native_aws_service():
    # Real AWS CUR product-field value (job 6a561187, $676.17 row): another
    # native AWS service with no space after the "Amazon" prefix
    # ("AmazonSageMaker"), same missing-alias bug class as DMS — canonical_
    # service() returned None (no alias existed), so classify_mechanics.py's
    # marketplace_thirdparty catch-all misclassified it as third-party SaaS,
    # blocking the pre-existing, already-correct "sagemaker" service_map rule
    # (SageMaker -> Vertex AI, review).
    assert canonical_service("AmazonSageMaker") == "sagemaker"


def test_sagemaker_rule_matches_and_is_not_marketplace_thirdparty():
    import re

    product = "AmazonSageMaker"
    cs_product = canonical_service(product)
    assert not (
        bool(product)
        and not re.match(r"^(amazon|aws)\b", product, re.IGNORECASE)
        and cs_product is None
    ), "SageMaker row would still be misclassified as marketplace_thirdparty"

    sm_rule = next(r for r in _rules() if r["match"] == "sagemaker")
    assert _rule_matches(
        sm_rule, product, "APS3-MLflow:TrackingServerCompute-Small", cs_product, {}
    )


def test_rds_mysql_community_edition_rule_does_not_zero_generic_rds_rows():
    # Regression for job d01fb92d (row 7, $6,783.08): the service_map rule
    # "relational database service for mysql community edition" (mode=ignore,
    # meant ONLY for AWS's distinct "RDS for MySQL Community Edition" EOL-
    # surcharge product) was reachable via canonical_service-equivalence for
    # ANY plain "Amazon Relational Database Service" row — because
    # canonical_service()'s substring fallback found the shorter embedded
    # alias "relational database service" (-> "rds") inside the rule's own,
    # longer match text, collapsing a narrow rule onto the generic RDS key.
    # Since mechanic_group "managed_db" isn't in DETERMINISTIC_GROUPS, this
    # silently zeroed a genuine $6,783.08 Aurora Serverless v2 I/O-Optimized
    # compute charge, mislabeling it as a "MySQL 5.7 EOL surcharge" it had
    # nothing to do with. Fixed by giving the rule's own match text a
    # dedicated alias key distinct from the generic "rds" one.
    generic_rds_cs = canonical_service("Amazon Relational Database Service")
    rule_text_cs = canonical_service(
        "relational database service for mysql community edition"
    )
    assert generic_rds_cs == "rds"
    assert rule_text_cs != generic_rds_cs

    mysql_rule = next(
        r
        for r in _rules()
        if r["match"] == "relational database service for mysql community edition"
    )
    assert not _rule_matches(
        mysql_rule,
        "Amazon Relational Database Service",
        "APS3-Aurora:ServerlessV2IOOptimizedUsage",
        generic_rds_cs,
        {},
    ), "generic RDS/Aurora row must not match the MySQL-Community-Edition-only rule"

    # The genuine target product must still match correctly.
    specific_cs = canonical_service(
        "Amazon Relational Database Service for MySQL Community Edition"
    )
    assert _rule_matches(
        mysql_rule,
        "Amazon Relational Database Service for MySQL Community Edition",
        "APS3-ExtendedSupport:Yr1-Yr2:MySQL5.7",
        specific_cs,
        {},
    )


def test_unrecognized_products_uncapped_and_diagnostics(tmp_path, monkeypatch):
    import duckdb
    from aws_normalizer import main
    from diagnostics import read_all

    db_path = str(tmp_path / "projection.duckdb")
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR,
            product VARCHAR,
            product_code VARCHAR,
            aws_amortized_cost DOUBLE
        )
    """)
    for i in range(25):
        con.execute(
            "INSERT INTO aws_li_catalog VALUES (?, ?, NULL, ?)",
            [f"key_{i:02d}", f"UnrecognizedCustomService_{i:02d}", 10.0 + i],
        )
    con.close()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["aws_normalizer.py", db_path])

    try:
        main()
    except SystemExit as e:
        assert e.code == 0

    records = read_all(str(tmp_path))
    unrec_records = [r for r in records if r.get("code") == "unrecognized_product"]
    assert len(unrec_records) == 25, f"Expected 25 diagnostic records, got {len(unrec_records)}"

    logged_prods = {r.get("detail", {}).get("product") for r in unrec_records}
    for i in range(25):
        assert f"UnrecognizedCustomService_{i:02d}" in logged_prods


def test_service_classifier_id_first_rejects_substring_false_match():
    """S5-2: A row whose name contains a rule's substring but whose canonical differs is NOT force-targeted."""
    # Find an ec2 rule
    ec2_rule = next((r for r in _rules() if r.get("match") == "ec2"), None)
    if not ec2_rule:
        ec2_rule = {"match": "ec2", "gcp_service": "Compute Engine", "category": "compute", "mode": "keep", "reason": "test"}
    # Product contains 'ec2' but its canonical service is 'ecr' (Elastic Container Registry)
    assert not _rule_matches(ec2_rule, "Amazon EC2 Container Registry", "Eps-Bytes", "ecr", {})


def test_canonical_service_consumed_from_catalog(tmp_path, monkeypatch):
    """S5-4: classify_mechanics and service_classifier consume the stored canonical_service column."""
    import duckdb
    from classify_mechanics import main as cm_main

    db_path = str(tmp_path / "projection.duckdb")
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            pricing_unit VARCHAR,
            line_item_type VARCHAR,
            pricing_model VARCHAR,
            aws_amortized_cost DOUBLE,
            instance_type VARCHAR,
            instance_vcpus DOUBLE,
            instance_ram_gb DOUBLE,
            instance_count INTEGER,
            billing_format VARCHAR,
            product_code VARCHAR,
            canonical_service VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k_test_s5_4', 'Amazon Custom Product Name', 'BoxUsage:c5.large', 'RunInstances',
            'Hrs', 'Usage', 'OnDemand', 100.0, 'c5.large', 2.0, 4.0, 1,
            'cur', 'AmazonEC2', 'ec2'
        )
    """)
    con.close()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["classify_mechanics.py", db_path])
    try:
        cm_main()
    except SystemExit as e:
        assert e.code == 0

    con = duckdb.connect(db_path)
    res = con.execute("SELECT mechanic_group FROM aws_li_catalog WHERE aws_li_key = 'k_test_s5_4'").fetchone()
    con.close()
    assert res[0] == "compute_breakdown"
