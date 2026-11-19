"""Tests for ElastiCache full modelling (§4.15).

Verifies:
1. Redis node Single-AZ → Cloud Memorystore for Redis Basic (unit_multiplier = RAM_GiB).
2. Redis node Multi-AZ → Cloud Memorystore for Redis Standard HA (unit_multiplier = RAM_GiB).
3. Burstable Redis node (non-HA) → Memorystore for Valkey (Shared Core Nano Node).
4. Memcached node (<= 4 GiB) → Cloud Memorystore for Memcached (Custom RAM M1) + deprecation disclosure.
5. Memcached node (> 4 GiB) → Cloud Memorystore for Memcached (Custom RAM M2) + deprecation disclosure.
6. Memcached unknown sizing → passthrough at cost parity with instance_sizing_unknown.
7. Serverless data-stored → Cloud Memorystore for Redis Capacity Basic M1.
8. Serverless ECPU → passthrough at AWS cost with no_gcp_equivalent.
9. Backup / snapshot storage (GB-Mo) → Cloud Memorystore for Redis Backups (multiplier 730.0).
10. Backup / snapshot storage (GB-Hours) → Cloud Memorystore for Redis Backups (multiplier 1.0).
11. MemoryDB node-hours → Memorystore for Redis Standard HA (preserves Multi-AZ guarantee).
12. Invariant: no mapped ElastiCache row ever has strategy="map" with an empty or None gcp_sku_id.
13. Invariant: all passthrough ElastiCache rows declare an intentional passthrough_reason.
14. Service usage catalog completeness: all 11 ElastiCache dimensions in catalog dispatch cleanly.
"""
from __future__ import annotations

import json
import os
import sys
import pytest
import duckdb

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SCRIPTS = os.path.join(_ROOT, "scripts")
sys.path.insert(0, _SCRIPTS)

from apply_static_mappings import map_elasticache
import spec_dispatch as sd


def _get_catalog_rate(sku_id: str) -> float:
    con = duckdb.connect(os.path.join(_ROOT, "data", "catalog.duckdb"), read_only=True)
    res = con.execute("SELECT rate_usd FROM tiered_rates WHERE sku_id = ?", [sku_id]).fetchone()
    con.close()
    assert res is not None, f"SKU {sku_id} not found in tiered_rates"
    return float(res[0])


def _row(usage_type: str = "NodeUsage:cache.m5.large",
         operation: str = "CreateCacheCluster:0002",
         product: str = "Amazon ElastiCache",
         unit: str = "Hrs",
         instance_type: str = "cache.m5.large",
         deployment_option: str = "Single-AZ",
         total_usage: float = 730.0,
         cost: float = 100.0,
         gcp_region: str = "us-central1") -> dict:
    return {
        "aws_li_key": "k_elasticache_test",
        "product": product,
        "product_code": "AmazonElastiCache",
        "canonical": "elasticache",
        "usage_type": usage_type,
        "operation": operation,
        "unit": unit,
        "pricing_unit": unit,
        "total_usage": total_usage,
        "usage_amount": total_usage,
        "aws_amortized_cost": cost,
        "gcp_region": gcp_region,
        "instance_type": instance_type,
        "deployment_option": deployment_option,
        "line_item_type": "Usage",
        "pricing_model": "OnDemand",
    }


def test_redis_single_az_maps_to_cluster_standard_small():
    r = _row(usage_type="NodeUsage:cache.m5.large", instance_type="cache.m5.large", deployment_option="Single-AZ")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Cloud Memorystore for Redis"
    assert res["gcp_sku_name"] == "Redis Cluster Node Standard Small"
    assert res["gcp_sku_id"] == "02F6-0CB2-BDE1"
    assert res["unit_multiplier"] == 1.0
    assert "1:1 node mapping" in res["projection_note"]
    assert "corrects legacy per-GiBy.h 2–3× over-projection" in res["projection_note"]


def test_redis_multi_az_maps_to_cluster_no_ha_multiplier():
    r = _row(usage_type="USE1-NodeUsage:cache.m5.large", instance_type="cache.m5.large", deployment_option="Multi-AZ")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Cloud Memorystore for Redis"
    assert res["gcp_sku_name"] == "Redis Cluster Node Standard Small"
    assert res["gcp_sku_id"] == "02F6-0CB2-BDE1"
    assert res["unit_multiplier"] == 1.0
    assert "replicas/Multi-AZ are separate CUR NodeUsage rows — no HA multiplier" in res["projection_note"]


def test_redis_cluster_ladder_node_default_13gib():
    """13 GiB node maps to Redis Cluster Node Default ($0.1923/hr in us-central1)."""
    r = _row(usage_type="NodeUsage:cache.m5.xlarge", instance_type="cache.m5.xlarge", deployment_option="Single-AZ")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Cloud Memorystore for Redis"
    assert res["gcp_sku_name"] == "Redis Cluster Node Default"
    assert res["gcp_sku_id"] == "8513-DCBC-92D7"
    assert res["unit_multiplier"] == 1.0
    rate = _get_catalog_rate(res["gcp_sku_id"])
    assert rate == pytest.approx(0.1923, abs=0.001)


def test_redis_cluster_ladder_standard_large_26gib():
    """26 GiB node maps to Redis Cluster Node Standard Large ($0.5698/hr in us-central1)."""
    r = _row(usage_type="NodeUsage:cache.m5.2xlarge", instance_type="cache.m5.2xlarge", deployment_option="Single-AZ")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Cloud Memorystore for Redis"
    assert res["gcp_sku_name"] == "Redis Cluster Node Standard Large"
    assert res["gcp_sku_id"] == "87AC-74F6-A953"
    assert res["unit_multiplier"] == 1.0
    rate = _get_catalog_rate(res["gcp_sku_id"])
    assert rate == pytest.approx(0.5698, abs=0.001)


def test_redis_cluster_ladder_highmem_2xlarge_ceiling_200gib():
    """200 GiB node maps to 116 GB Redis Cluster Node Highmem 2xlarge with ceiling note."""
    r = _row(usage_type="NodeUsage:cache.r5.24xlarge", instance_type="cache.r5.24xlarge", deployment_option="Single-AZ")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Cloud Memorystore for Redis"
    assert res["gcp_sku_name"] == "Redis Cluster Node Highmem 2xlarge"
    assert res["gcp_sku_id"] == "BDF7-A6EC-FB85"
    assert res["unit_multiplier"] == 1.0
    assert "ceiling interim" in res["projection_note"]
    rate = _get_catalog_rate(res["gcp_sku_id"])
    assert rate == pytest.approx(1.6274, abs=0.001)


def test_redis_cluster_no_legacy_capacity_on_plain_redis():
    """Invariant: plain Redis node rows must never target legacy Redis Capacity M* SKUs."""
    for itype in ["cache.m5.large", "cache.m5.xlarge", "cache.m5.2xlarge", "cache.m5.4xlarge", "cache.r5.24xlarge"]:
        r = _row(usage_type=f"NodeUsage:{itype}", instance_type=itype, deployment_option="Single-AZ")
        out = map_elasticache([r])
        assert len(out) == 1
        res = out[0]
        assert "Redis Capacity" not in res["gcp_sku_name"], f"Plain Redis node {itype} mapped to legacy capacity: {res}"
        assert "Redis Cluster Node" in res["gcp_sku_name"], f"Expected Redis Cluster Node, got {res}"


def test_redis_burstable_maps_to_valkey_shared_core():
    r = _row(usage_type="NodeUsage:cache.t4g.medium", instance_type="cache.t4g.medium", deployment_option="Single-AZ")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Memorystore for Valkey"
    assert res["gcp_sku_name"] == "Shared Core Nano Node"
    assert res["gcp_sku_id"] is not None
    assert res["unit_multiplier"] == 1.0
    assert "Memorystore for Valkey" in res["projection_note"]


def test_memcached_small_node_maps_to_custom_ram_and_core_m1():
    # Memcached bills BOTH per-GiB (Custom RAM) AND per-vCPU (Custom Core) — both legs must be emitted.
    r = _row(usage_type="USE1-NodeUsage:cache.t4g.micro",
             operation="CreateCacheCluster:memcached",
             instance_type="cache.t4g.micro",
             deployment_option="Single-AZ")
    out = map_elasticache([r])
    assert len(out) == 2
    by_comp = {e["component"]: e for e in out}
    ram, core = by_comp["ram"], by_comp["core"]
    assert ram["strategy"] == "map" and ram["gcp_service"] == "Cloud Memorystore for Memcached"
    assert ram["gcp_sku_name"] == "Custom RAM M1" and ram["gcp_sku_id"] is not None
    assert abs(ram["unit_multiplier"] - 0.555) < 0.05
    assert core["strategy"] == "map" and core["gcp_sku_name"] == "Custom Core M1"
    assert core["gcp_sku_id"] is not None and core["unit_multiplier"] == 2.0  # t4g.micro = 2 vCPU
    assert "Memcached shuts down 2029-01-31" in ram["projection_note"]
    assert "Valkey" in ram["projection_note"]


def test_memcached_large_node_maps_to_custom_ram_and_core_m2():
    r = _row(usage_type="NodeUsage:cache.m5.large",
             operation="CreateCacheCluster:memcached",
             instance_type="cache.m5.large",
             deployment_option="Single-AZ")
    out = map_elasticache([r])
    assert len(out) == 2
    by_comp = {e["component"]: e for e in out}
    ram, core = by_comp["ram"], by_comp["core"]
    assert ram["gcp_sku_name"] == "Custom RAM M2" and ram["gcp_sku_id"] is not None
    assert abs(ram["unit_multiplier"] - 6.38) < 0.1
    assert core["gcp_sku_name"] == "Custom Core M2" and core["unit_multiplier"] == 2.0  # m5.large = 2 vCPU
    assert "Memcached shuts down 2029-01-31" in ram["projection_note"]


def test_memcached_unknown_sizing_falls_to_passthrough():
    r = _row(usage_type="NodeUsage:unknown",
             operation="CreateCacheCluster:memcached",
             instance_type="",
             deployment_option="Single-AZ")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "passthrough"
    assert res["gcp_service"] == "Cloud Memorystore for Memcached"
    assert res["gcp_sku_id"] is None
    assert res["passthrough_reason"] == "unresolved_sku"
    assert "Memcached shuts down 2029-01-31" in res["projection_note"]


def test_serverless_data_stored_maps_to_redis_basic_m1():
    r = _row(usage_type="ElastiCache:ServerlessUsage",
             operation="ServerlessUsage:DataStored",
             unit="GB-Mo",
             instance_type="")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Cloud Memorystore for Redis"
    assert res["gcp_sku_name"] == "Redis Capacity Basic M1"
    assert res["gcp_sku_id"] is not None
    assert res["unit_multiplier"] == 1.0
    assert "Serverless data stored" in res["projection_note"]


def test_serverless_ecpu_falls_to_passthrough():
    r = _row(usage_type="ElastiCache:ServerlessUsage",
             operation="ServerlessUsage:ECPU",
             unit="Count",
             instance_type="")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "passthrough"
    assert res["gcp_service"] == "Cloud Memorystore for Redis"
    assert res["gcp_sku_id"] is None
    assert res["passthrough_reason"] == "no_gcp_equivalent"
    assert "Serverless ECPU" in res["projection_note"]


def test_backup_storage_monthly_maps_to_redis_backups():
    r = _row(usage_type="StorageUsage:BackupUsage",
             operation="CreateSnapshot",
             unit="GB-Mo",
             instance_type="")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Cloud Memorystore for Redis"
    assert res["gcp_sku_name"] == "Memorystore for Redis Cluster: Backups"
    assert res["gcp_sku_id"] is not None
    assert res["unit_multiplier"] == 730.0
    assert "$0.00011111/GB-h" in res["projection_note"]


def test_backup_storage_hourly_maps_to_redis_backups():
    r = _row(usage_type="USE1-StorageUsage:BackupUsage",
             operation="CreateSnapshot",
             unit="GB-Hours",
             instance_type="")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_sku_id"] is not None
    assert res["unit_multiplier"] == 1.0


def test_memorydb_node_hours_preserves_standard_ha():
    r = _row(product="Amazon MemoryDB",
             usage_type="NodeUsage:db.r6g.large",
             operation="CreateCluster",
             instance_type="db.r6g.large",
             deployment_option="")
    out = map_elasticache([r])
    assert len(out) == 1
    res = out[0]
    assert res["strategy"] == "map"
    assert res["gcp_service"] == "Cloud Memorystore for Redis"
    assert "Standard" in res["gcp_sku_name"]
    assert res["gcp_sku_id"] is not None
    assert "durable Multi-AZ primary database guarantee preserved" in res["projection_note"]


def test_elasticache_mapping_invariants():
    test_cases = [
        _row(usage_type="NodeUsage:cache.m5.large", instance_type="cache.m5.large"),
        _row(usage_type="USE1-NodeUsage:cache.m5.large", instance_type="cache.m5.large", deployment_option="Multi-AZ"),
        _row(usage_type="USE1-NodeUsage:cache.t4g.micro", operation="CreateCacheCluster:memcached", instance_type="cache.t4g.micro"),
        _row(usage_type="NodeUsage:unknown", operation="CreateCacheCluster:memcached", instance_type=""),
        _row(usage_type="ElastiCache:ServerlessUsage", operation="ServerlessUsage:DataStored", unit="GB-Mo", instance_type=""),
        _row(usage_type="ElastiCache:ServerlessUsage", operation="ServerlessUsage:ECPU", unit="Count", instance_type=""),
        _row(usage_type="StorageUsage:BackupUsage", operation="CreateSnapshot", unit="GB-Mo", instance_type=""),
    ]
    results = map_elasticache(test_cases)
    for res in results:
        strat = res.get("strategy")
        sku_id = res.get("gcp_sku_id")
        if strat == "map":
            assert sku_id is not None and len(str(sku_id).strip()) > 0, f"Map row has empty sku_id: {res}"
        elif strat == "passthrough":
            assert sku_id is None, f"Passthrough row has non-None sku_id: {res}"
            assert res.get("passthrough_reason"), f"Passthrough row missing passthrough_reason: {res}"


def test_service_usage_catalog_elasticache_completeness_and_dispatch():
    cat_path = os.path.join(_ROOT, "data", "service_usage_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    assert "elasticache" in catalog, "elasticache missing from service_usage_catalog.json"
    entries = catalog["elasticache"]
    assert len(entries) >= 11, f"Expected >= 11 elasticache catalog entries, got {len(entries)}"

    for entry in entries:
        row = {
            "canonical": "elasticache",
            "product": "Amazon ElastiCache",
            "product_code": "AmazonElastiCache",
            "usage_type": entry["usage_type"],
            "operation": entry.get("operation", ""),
            "pricing_unit": entry.get("unit", "Hrs"),
            "line_item_type": "Usage",
            "pricing_model": "OnDemand",
        }
        res = sd.dispatch(row)
        expected_ct = entry.get("expected_charge_type")
        expected_mech = entry.get("expected_mechanic")
        if expected_ct:
            assert res.get("charge_type") == expected_ct, (
                f"Mismatch for {entry['usage_type']}: expected {expected_ct}, got {res.get('charge_type')}"
            )
        if expected_mech:
            assert res.get("mechanic") == expected_mech, (
                f"Mismatch mechanic for {entry['usage_type']}: expected {expected_mech}, got {res.get('mechanic')}"
            )
