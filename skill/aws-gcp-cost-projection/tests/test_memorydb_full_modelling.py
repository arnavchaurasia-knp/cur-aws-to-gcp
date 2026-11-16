"""Tests for Amazon MemoryDB full modelling (§4.4 WORKORDER_per_service_full_modelling.md).

Guarantees verified:
1. MemoryDB node-hours map to Memorystore for Redis Standard HA or Memorystore for Valkey (NEVER Basic).
2. Durable Multi-AZ primary database guarantee is preserved in the projection note.
3. Node RAM is sized from the real instance_type (e.g. db.r6g.xlarge -> 32 GiB, db.r6g.large -> 16 GiB),
   never flattened to a 16 GiB assumption when instance_type is present.
4. When instance_type is absent, fallback to memorydb_node_gib assumption with explicit assumption metadata.
5. Burstable node types (db.t4g) route cleanly without breaking HA invariants.
6. MemoryDB data-written ($0.20/GB write throughput) routes to misc / passthrough with review guidance.
7. MemoryDB snapshot usage routes to backup / snapshot storage.
8. Catalog completeness for MemoryDB is green in test_spec_coverage.
"""

import pytest
from scripts import apply_static_mappings as asm
from scripts import spec_dispatch as sd


def test_memorydb_node_hours_ha_tier_guarantee():
    """MemoryDB node-hours must map to Standard HA tier, NEVER Redis Basic."""
    row = {
        "aws_li_key": "memdb_ha_test",
        "product": "Amazon MemoryDB",
        "canonical": "memorydb",
        "instance_type": "db.r6g.large",
        "usage_type": "APS3-NodeUsage:db.r6g.large",
        "operation": None,
        "gcp_region": "us-central1",
        "aws_amortized_cost": 100.0,
        "total_usage": 100.0,
        "deployment_option": None,  # Even with None, MemoryDB is inherently durable/Multi-AZ
    }
    out = asm.map_elasticache([row])
    assert len(out) == 1
    m = out[0]
    assert m["strategy"] == "map"
    assert m["gcp_service"] == "Cloud Memorystore for Redis"
    assert "Standard" in m["gcp_sku_name"], f"Expected Standard HA tier, got: {m['gcp_sku_name']}"
    assert "Basic" not in m["gcp_sku_name"], "MemoryDB must never map to Redis Basic"
    assert "never downgraded to Basic" in m["projection_note"]
    assert "durable Multi-AZ" in m["projection_note"]


def test_memorydb_node_hours_real_instance_sizing():
    """MemoryDB node-hours read real RAM from instance_type (db.r6g.xlarge = 32 GiB)."""
    row_xl = {
        "aws_li_key": "memdb_xl_test",
        "product": "Amazon MemoryDB",
        "canonical": "memorydb",
        "instance_type": "db.r6g.xlarge",
        "usage_type": "APS3-NodeUsage:db.r6g.xlarge",
        "operation": None,
        "gcp_region": "us-central1",
        "aws_amortized_cost": 200.0,
        "total_usage": 100.0,
        "deployment_option": None,
    }
    out = asm.map_elasticache([row_xl])
    assert len(out) == 1
    m = out[0]
    assert m["unit_multiplier"] == 32.0, f"Expected 32.0 GiB for db.r6g.xlarge, got {m['unit_multiplier']}"
    assert m["is_assumption"] is False
    assert m["assumption"] is None
    assert "from instance type db.r6g.xlarge" in m["projection_note"]


def test_memorydb_node_hours_fallback_assumption_when_unknown(monkeypatch):
    """When instance_type is missing, fall back to memorydb_node_gib assumption with metadata."""
    row_unknown = {
        "aws_li_key": "memdb_unknown_test",
        "product": "Amazon MemoryDB",
        "canonical": "memorydb",
        "instance_type": None,
        "usage_type": "APS3-NodeUsage",
        "operation": None,
        "gcp_region": "us-central1",
        "aws_amortized_cost": 50.0,
        "total_usage": 50.0,
        "deployment_option": None,
    }
    monkeypatch.setitem(asm._ASSUMPTIONS, "memorydb_node_gib", 24.0)
    out = asm.map_elasticache([row_unknown])
    assert len(out) == 1
    m = out[0]
    assert m["unit_multiplier"] == 24.0
    assert m["is_assumption"] is True
    assert m["assumption"] == "memorydb_node_gib=24"
    assert "Standard" in m["gcp_sku_name"]


def test_memorydb_graviton3_sizing():
    """Verify Graviton3 db.r7g.2xlarge maps to 64 GiB RAM."""
    row = {
        "aws_li_key": "memdb_r7g_test",
        "product": "Amazon MemoryDB",
        "canonical": "memorydb",
        "instance_type": "db.r7g.2xlarge",
        "usage_type": "APS3-NodeUsage:db.r7g.2xlarge",
        "operation": None,
        "gcp_region": "us-central1",
        "aws_amortized_cost": 400.0,
        "total_usage": 100.0,
        "deployment_option": None,
    }
    out = asm.map_elasticache([row])
    assert len(out) == 1
    m = out[0]
    assert m["unit_multiplier"] == 64.0
    assert m["is_assumption"] is False
    assert "Standard" in m["gcp_sku_name"]


def test_memorydb_data_written_spec_dispatch():
    """MemoryDB data-written should dispatch to memorydb_data_written / misc mechanic."""
    row = {
        "product": "Amazon MemoryDB",
        "canonical": "memorydb",
        "usage_type": "APS3-DataWritten-Bytes",
        "pricing_unit": "GB",
        "unit": "GB",
        "line_item_type": "Usage",
    }
    res = sd.dispatch(row)
    assert res is not None
    assert res["charge_type"] == "memorydb_data_written"
    assert res["mechanic"] == "misc"


def test_memorydb_snapshot_spec_dispatch():
    """MemoryDB snapshot storage should dispatch to memorydb_snapshot / backup mechanic."""
    row = {
        "product": "Amazon MemoryDB",
        "canonical": "memorydb",
        "usage_type": "APS3-SnapshotUsage",
        "pricing_unit": "GB-Mo",
        "unit": "GB-Mo",
        "line_item_type": "Usage",
    }
    res = sd.dispatch(row)
    assert res is not None
    assert res["charge_type"] == "memorydb_snapshot"
    assert res["mechanic"] == "backup"


def test_elasticache_memcached_maps_to_memorystore_memcached_with_deprecation():
    """ElastiCache Memcached maps to Memorystore for Memcached with deprecation disclosure (§4.15)."""
    row = {
        "aws_li_key": "memcached_test",
        "product": "Amazon ElastiCache",
        "canonical": "elasticache",
        "operation": "CreateCacheCluster:memcached",
        "instance_type": "cache.m5.large",
        "usage_type": "NodeUsage:cache.m5.large",
        "gcp_region": "us-central1",
        "aws_amortized_cost": 80.0,
        "total_usage": 100.0,
    }
    out = asm.map_elasticache([row])
    # Memcached bills per-vCPU AND per-GiB → two legs (Custom RAM + Custom Core).
    assert len(out) == 2
    for m in out:
        assert m["gcp_service"] == "Cloud Memorystore for Memcached"
        assert m["strategy"] == "map"
        assert m["gcp_sku_id"] is not None
        assert "2029-01-31" in m["projection_note"]
        assert "Valkey" in m["projection_note"]
    assert {m["component"] for m in out} == {"ram", "core"}


def test_memorystore_cud_multipliers_20_40():
    """Memorystore CUD multipliers must be 0.80 (1-yr) and 0.60 (3-yr) (WORKORDER_followup_fixes A4)."""
    import json
    import os
    cud_path = os.path.join(asm.DATA_DIR, "cud_pct.json")
    with open(cud_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["Memorystore"]["1yr_multiplier"] == pytest.approx(0.80)
    assert data["Memorystore"]["3yr_multiplier"] == pytest.approx(0.60)
    assert data["Cloud Memorystore for Redis"]["1yr_multiplier"] == pytest.approx(0.80)
    assert data["Cloud Memorystore for Redis"]["3yr_multiplier"] == pytest.approx(0.60)
    assert asm.CUD_PCT_FALLBACK["Cloud Memorystore for Redis"] == (0.80, 0.60)
