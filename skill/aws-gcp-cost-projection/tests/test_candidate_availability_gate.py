import pytest
from scripts.apply_static_mappings import (
    map_elasticache,
    is_managed_service_available,
    GCP_MEMORYSTORE_VALKEY,
    GCP_MEMORYSTORE,
)

def test_candidate_availability_gate_valkey():
    """Verify that when Valkey (memorystore.googleapis.com) is unavailable in a region,
    the best-fit selector drops Valkey and falls back to Redis Basic."""
    # us-central1 is available in managed_service_availability.json
    assert is_managed_service_available("memorystore.googleapis.com", "us-central1") is True
    # made-up region is unavailable
    assert is_managed_service_available("memorystore.googleapis.com", "mars-north1") is False

    row_available = {
        "aws_li_key": "row-avail",
        "product": "Amazon ElastiCache",
        "instance_type": "cache.t4g.small",
        "deployment_option": "Single-AZ",
        "gcp_region": "us-central1",
    }
    mapped_avail = map_elasticache([row_available])
    assert len(mapped_avail) == 1
    assert mapped_avail[0]["gcp_service"] == GCP_MEMORYSTORE_VALKEY
    assert mapped_avail[0]["gcp_sku_name"] == "Shared Core Nano Node"

    row_unavail = {
        "aws_li_key": "row-unavail",
        "product": "Amazon ElastiCache",
        "instance_type": "cache.t4g.small",
        "deployment_option": "Single-AZ",
        "gcp_region": "mars-north1",
    }
    mapped_unavail = map_elasticache([row_unavail])
    assert len(mapped_unavail) == 1
    # Dropped Valkey due to region availability gate, falls back to Redis Basic
    assert mapped_unavail[0]["gcp_service"] == GCP_MEMORYSTORE
    assert "Redis Capacity Basic" in mapped_unavail[0]["gcp_sku_name"]
