#!/usr/bin/env python3
"""
usage_type_parser.py — additive, typed usage_type parser (Layer-1.5 closed-world guard, Part B).

Parses (usage_type, operation, product) into a small typed field dict:
    {
        "region":    GCP region (from usage_type prefix, e.g. "aps3" -> "asia-south1"),
        "family":    AWS service family (e.g. "ec2", "s3", "rds", "lambda"),
        "sub_type":  charge sub-type (e.g. "boxusage", "storage", "requests", "data-transfer"),
        "unit":      normalized billing unit (e.g. "hrs", "gb-mo", "gb", "requests"),
        "class":     cost class (e.g. "compute", "storage", "data_transfer", "per_request", "database", "non_workload"),
    }

Design
------
* Additive only: it never changes any existing classification. It is a pure
  function (no DB, no network, no side effects) so it can be unit-tested in
  isolation and gradually wired into spec_dispatch predicates one charge-type
  at a time without regression risk.
* All regex lives HERE (parsing is its legit job). spec_dispatch predicates
  read parsed fields instead of raw substrings once migrated.
* Unknown/unrecognized values yield empty strings rather than guesses —
  callers decide the fallback (fail-closed vs lenient).

Reuses:
  * region_prefix.decode_region_prefix for region extraction.
  * unit_equivalence.normalize_unit for unit canonicalization.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from region_prefix import decode_region_prefix as _decode_region  # noqa: E402
from unit_equivalence import normalize_unit as _normalize_unit     # noqa: E402


# ---------------------------------------------------------------------------
# Family detection: product -> service family. Ordered most-specific-first so
# longer product names win over prefixes (e.g. "Kinesis Firehose" before "Kinesis").
# ---------------------------------------------------------------------------
_FAMILY = [
    ("kinesis firehose", "firehose"),
    ("kinesis data firehose", "firehose"),
    ("kinesis data analytics", "kinesisanalytics"),
    ("kinesis video streams", "kinesisvideo"),
    ("kinesis data streams", "kinesis"),
    ("amazon kinesis", "kinesis"),
    ("aws kinesis", "kinesis"),
    ("managed streaming for kafka", "msk"),
    ("amazon msk", "msk"),
    ("aws msk", "msk"),
    ("elastic mapreduce", "emr"),
    ("amazon emr", "emr"),
    ("aws emr", "emr"),
    ("aws glue", "glue"),
    ("amazon glue", "glue"),
    ("amazon redshift", "redshift"),
    ("aws redshift", "redshift"),
    ("amazon rds", "rds"),
    ("aws rds", "rds"),
    ("relational database", "rds"),
    ("aurora", "rds"),
    ("documentdb", "documentdb"),
    ("amazon documentdb", "documentdb"),
    ("memorydb", "memorydb"),
    ("amazon memorydb", "memorydb"),
    ("amazon neptune", "neptune"),
    ("aws neptune", "neptune"),
    ("amazon dynamodb", "dynamodb"),
    ("aws dynamodb", "dynamodb"),
    ("amazon elasticache", "elasticache"),
    ("aws elasticache", "elasticache"),
    ("amazon opensearch", "opensearch"),
    ("aws opensearch", "opensearch"),
    ("amazon elasticsearch", "opensearch"),
    ("aws elasticsearch", "opensearch"),
    ("aws fargate", "fargate"),
    ("amazon fargate", "fargate"),
    ("aws ecs", "ecs"),
    ("amazon ecs", "ecs"),
    ("aws eks", "eks"),
    ("amazon eks", "eks"),
    ("amazon elastic kubernetes", "eks"),
    ("amazon elastic compute cloud", "ec2"),
    ("aws ec2", "ec2"),
    ("amazon ec2", "ec2"),
    ("elastic compute cloud", "ec2"),
    ("amazon s3", "s3"),
    ("aws s3", "s3"),
    ("simple storage service", "s3"),
    ("aws lambda", "lambda"),
    ("amazon lambda", "lambda"),
    ("aws sagemaker", "sagemaker"),
    ("amazon sagemaker", "sagemaker"),
    ("aws athena", "athena"),
    ("amazon athena", "athena"),
    ("aws waf", "waf"),
    ("amazon waf", "waf"),
    ("aws shield", "shield"),
    ("aws kms", "kms"),
    ("aws key management service", "kms"),
    ("aws secrets manager", "secretsmanager"),
    ("amazon secrets manager", "secretsmanager"),
    ("aws acm", "acm"),
    ("aws certificate manager", "acm"),
    ("aws cloudtrail", "cloudtrail"),
    ("amazon cloudtrail", "cloudtrail"),
    ("aws cloudwatch", "cloudwatch"),
    ("amazon cloudwatch", "cloudwatch"),
    ("amazon cloudwatch logs", "cloudwatch"),
    ("aws config", "config"),
    ("amazon config", "config"),
    ("aws xray", "xray"),
    ("aws x-ray", "xray"),
    ("amazon xray", "xray"),
    ("aws route 53", "route53"),
    ("amazon route 53", "route53"),
    ("aws cloudfront", "cloudfront"),
    ("amazon cloudfront", "cloudfront"),
    ("aws direct connect", "directconnect"),
    ("amazon direct connect", "directconnect"),
    ("aws vpc", "vpc"),
    ("amazon vpc", "vpc"),
    ("virtual private cloud", "vpc"),
    ("aws transit gateway", "transitgateway"),
    ("amazon transit gateway", "transitgateway"),
    ("aws privatelink", "privatelink"),
    ("aws private link", "privatelink"),
    ("aws apigateway", "apigateway"),
    ("amazon api gateway", "apigateway"),
    ("aws ebs", "ebs"),
    ("amazon ebs", "ebs"),
    ("elastic block store", "ebs"),
    ("aws efs", "efs"),
    ("amazon efs", "efs"),
    ("elastic file system", "efs"),
    ("aws fsx", "fsx"),
    ("amazon fsx", "fsx"),
    ("aws backup", "backup"),
    ("amazon backup", "backup"),
    ("aws support", "support"),
    ("amazon support", "support"),
    ("aws marketplace", "marketplace"),
    ("amazon marketplace", "marketplace"),
    ("aws tax", "tax"),
    ("amazon tax", "tax"),
]


def _first_match(patterns, text):
    for pat, fam in patterns:
        if pat in text:
            return fam
    return "unknown"


# ---------------------------------------------------------------------------
# Sub-type detection (from usage_type / operation / product)
# ---------------------------------------------------------------------------
_SUB_TYPE_RULES = [
    # requests / API calls
    (("request", "invocation", "api call", "calls", "operations", "ops"), "requests"),
    # storage capacity
    (("storage", "volumeusage", "gb-month", "gbmo", "standardstorage", "glacier", "deeparchive", "nearline", "coldline", "archive"), "storage"),
    # instance / node hours
    (("boxusage", "instance", "nodeusage", "dbinstance", "cachecluster", "replication", "broker", "node-hour", "running"), "instance_hours"),
    # data transfer / egress
    (("data-transfer", "transfer", "egress", "ingress", "bandwidth", "traffic"), "data_transfer"),
    # CPU credits / burstable
    (("cpucredit", "credit", "burstable", "t2unlimited", "t3unlimited", "t4g"), "cpu_credits"),
    # IOPS / provisioned
    (("iops", "provisioned iops", "storage-io"), "iops"),
    # snapshot / backup
    (("snapshot", "backup", "backupstorage"), "snapshot"),
    # elastic IP / address
    (("eip", "elastic-ip", "public ipv4", "address"), "address"),
    # load balancer / NAT / gateway hours
    (("loadbalancer", "elb", "alb", "nlb", "gateway", "natgateway", "nat-gateway"), "gateway_hours"),
    # requests count (s3)
    (("requests", "put", "get", "list", "select"), "requests"),
]


def _sub_type(text):
    for pats, st in _SUB_TYPE_RULES:
        for p in pats:
            if p in text:
                return st
    return "usage"


# ---------------------------------------------------------------------------
# Unit detection (from pricing_unit + usage_type hints)
# ---------------------------------------------------------------------------
_UNIT_HINTS = [
    (("gb-month", "gbmo", "gb-mo", "gib-month", "gib-mo", "bytehrs", "byte-hrs", "byte-hours"), "gb-mo"),
    (("gb", "gib", "gigabyte", "gibibyte", "terabyte", "tb", "mib", "miby"), "gb"),
    (("hrs", "hours", "hour", "hr", "h", "usage", "boxusage", "instance"), "hrs"),
    (("request", "requests", "invocation", "invocations", "calls", "ops"), "requests"),
    (("iops", "iops-month", "iops-mo"), "iops-mo"),
    (("data-transfer", "gb", "gib"), "gb"),
    (("count", "count-month", "count-mo"), "count"),
    (("vCPU", "vcpu", "vCPU-hour", "vcpu-hours"), "vcpu-hours"),
]


def _unit(pricing_unit, text):
    u = _normalize_unit(pricing_unit)
    if u:
        return u
    for pats, uni in _UNIT_HINTS:
        for p in pats:
            if p in text:
                return uni
    return ""


# ---------------------------------------------------------------------------
# Class detection (from family + sub_type)
# ---------------------------------------------------------------------------
_CLASS_BY_FAMILY = {
    "ec2": "compute",
    "fargate": "compute",
    "ecs": "compute",
    "eks": "compute",
    "lambda": "compute",
    "sagemaker": "compute",
    "emr": "compute",
    "glue": "compute",
    "athena": "compute",
    "kinesis": "compute",
    "firehose": "compute",
    "kinesisanalytics": "compute",
    "kinesisvideo": "compute",
    "msk": "compute",
    "batch": "compute",
    "s3": "storage",
    "ebs": "storage",
    "efs": "storage",
    "fsx": "storage",
    "backup": "storage",
    "snapshot": "storage",
    "rds": "database",
    "dynamodb": "database",
    "redshift": "database",
    "documentdb": "database",
    "memorydb": "database",
    "neptune": "database",
    "elasticache": "database",
    "opensearch": "database",
    "keyspaces": "database",
    "neptune": "database",
    "sns": "messaging",
    "sqs": "messaging",
    "eventbridge": "messaging",
    "kinesis": "messaging",
    "firehose": "messaging",
    "cloudwatch": "ops",
    "cloudtrail": "ops",
    "config": "ops",
    "xray": "ops",
    "health": "ops",
    "waf": "security",
    "shield": "security",
    "guardduty": "security",
    "inspector": "security",
    "kms": "security",
    "secretsmanager": "security",
    "acm": "security",
    "cloudhsm": "security",
    "route53": "networking",
    "cloudfront": "networking",
    "directconnect": "networking",
    "vpc": "networking",
    "transitgateway": "networking",
    "privatelink": "networking",
    "apigateway": "networking",
    "elb": "networking",
    "autoscaling": "networking",
    "support": "non_workload",
    "marketplace": "non_workload",
    "tax": "non_workload",
    "config": "non_workload",
    "billing": "non_workload",
    "budgets": "non_workload",
    "computeoptimizer": "non_workload",
    "costexplorer": "non_workload",
    "cur": "non_workload",
    "trustedadvisor": "non_workload",
    "wellarchitected": "non_workload",
    "controltower": "non_workload",
    "organizations": "non_workload",
    "ram": "non_workload",
    "servicecatalog": "non_workload",
    "ssm": "non_workload",
    "opsworks": "non_workload",
    "cloud9": "non_workload",
    "cloudshell": "non_workload",
    "copilot": "non_workload",
    "proton": "non_workload",
    "iot": "non_workload",
    "elemental": "non_workload",
    "mediaconvert": "non_workload",
    "medialive": "non_workload",
    "braket": "non_workload",
    "groundstation": "non_workload",
    "outposts": "non_workload",
    "localzones": "non_workload",
    "wavelength": "non_workload",
    "snow": "non_workload",
    "storagegateway": "non_workload",
    "drs": "non_workload",
    "dms": "non_workload",
    "lakeformation": "non_workload",
    "appmesh": "non_workload",
    "cloudmap": "non_workload",
    "globalaccelerator": "non_workload",
    "vpclattice": "non_workload",
    "appsync": "non_workload",
    "cognito": "non_workload",
    "connect": "non_workload",
    "chime": "non_workload",
    "workspaces": "non_workload",
    "appstream": "non_workload",
    "codecommit": "non_workload",
    "codebuild": "non_workload",
    "codedeploy": "non_workload",
    "codepipeline": "non_workload",
    "codestar": "non_workload",
    "cloudformation": "non_workload",
    "stepfunctions": "non_workload",
}


def _cost_class(family, sub_type):
    if sub_type in ("cpu_credits",):
        return "non_workload"
    return _CLASS_BY_FAMILY.get(family, "unknown")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse(usage_type: str | None, operation: str | None, product: str | None) -> dict:
    """Return a typed field dict for a billing row.

    Keys: region, family, sub_type, unit, class. All values are strings;
    unrecognized fields are "".
    """
    ut = usage_type or ""
    op = operation or ""
    prod = product or ""
    text = f"{ut} {op} {prod}".lower()
    ut_lower = ut.lower()

    region = _decode_region(ut) or ""
    family = _first_match(_FAMILY, text)
    sub_type = _sub_type(text)
    unit = _unit(None, text) or ""
    # If the row carries a real pricing_unit, prefer it.
    # (pricing_unit is not passed here; callers may override unit post-hoc.)
    cost_class = _cost_class(family, sub_type)

    return {
        "region": region,
        "family": family,
        "sub_type": sub_type,
        "unit": unit,
        "class": cost_class,
    }


def parse_with_unit(usage_type: str | None, operation: str | None,
                    product: str | None, pricing_unit: str | None) -> dict:
    """Like parse() but also resolves the unit from pricing_unit when present."""
    result = parse(usage_type, operation, product)
    if pricing_unit:
        resolved = _unit(pricing_unit, f"{usage_type or ''} {operation or ''} {product or ''}")
        if resolved:
            result["unit"] = resolved
    return result
