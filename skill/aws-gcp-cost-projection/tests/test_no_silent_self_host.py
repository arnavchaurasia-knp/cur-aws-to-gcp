import pytest
from scripts.apply_static_mappings import (
    map_elasticache,
    GCP_MEMORYSTORE,
    GCP_MEMORYSTORE_VALKEY,
)

def test_no_silent_self_host_for_elasticache():
    """Verify that map_elasticache never silently maps an ElastiCache row
    to Compute Engine (self-hosted) without explicit flag or opt-in."""
    test_rows = [
        {
            "aws_li_key": "row-t4g",
            "product": "Amazon ElastiCache",
            "instance_type": "cache.t4g.small",
            "deployment_option": "Single-AZ",
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "row-m5",
            "product": "Amazon ElastiCache",
            "instance_type": "cache.m5.large",
            "deployment_option": "Single-AZ",
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "row-ha",
            "product": "Amazon ElastiCache",
            "instance_type": "cache.r5.xlarge",
            "deployment_option": "Multi-AZ",
            "gcp_region": "us-central1",
        },
    ]

    mapped = map_elasticache(test_rows)
    for m in mapped:
        # None of the targets should be Compute Engine or self-hosted
        assert "compute engine" not in m["gcp_service"].lower(), f"Unexpected self-host target: {m}"
        assert "self-host" not in m["gcp_service"].lower(), f"Unexpected self-host target: {m}"
        assert m["gcp_service"] in (GCP_MEMORYSTORE, GCP_MEMORYSTORE_VALKEY)
