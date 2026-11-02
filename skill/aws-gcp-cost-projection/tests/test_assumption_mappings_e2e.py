"""tests/test_assumption_mappings_e2e.py — END-TO-END dollar checks for assumption-driven mappers.

Unlike tests/test_assumption_driven_mappings.py (which asserts the mapper's
unit_multiplier and assumption stamp), this file drives each new mapper through
the REAL chain: mapper -> resolve_sku() against the committed data/catalog.duckdb
-> the SKU's real tiered rate -> projected GCP dollars. It then asserts that
changing the assumption moves the real dollar figure.

Why this exists: the pinned corpus (tests/corpus/) hard-codes gcp_projected_cost
as aws_od_cost * 0.94 with fake sku ids, so it CANNOT catch a mapper or rate bug.
These tests exercise the actual mappers against the actual catalog instead.
"""

import os
import sys

import duckdb
import pytest

SKILL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")
CATALOG = os.path.join(SKILL_DIR, "data", "catalog.duckdb")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

os.environ.setdefault("SKILL_DIR", SKILL_DIR)

import apply_static_mappings as asm


def _rate(sku_id: str) -> float:
    """Real minimum-tier rate for a SKU from the committed catalog."""
    con = duckdb.connect(CATALOG, read_only=True)
    try:
        row = con.execute(
            "SELECT min(rate_usd) FROM tiered_rates WHERE sku_id = ?", [str(sku_id)]
        ).fetchone()
    finally:
        con.close()
    assert row and row[0] is not None, f"no catalog rate for resolved SKU {sku_id}"
    return float(row[0])


def _dollars(entry: dict, usage: float) -> float:
    """Reproduce apply_rates' core: usage * rate(sku) * unit_multiplier."""
    sku_id = entry.get("gcp_sku_id")
    assert sku_id, f"mapper did not resolve a real SKU: {entry}"
    return usage * _rate(sku_id) * entry["unit_multiplier"]


def test_nat_e2e_dollars_scale_with_vms():
    """R1: Cloud NAT dollars resolve from the real SKU and scale with nat_vms_per_gateway."""
    usage = 100.0  # gateway-hours
    row = {
        "aws_li_key": "nat_e2e", "product": "Amazon Virtual Private Cloud",
        "usage_type": "APS3-NatGateway-Hours", "operation": "NatGateway",
        "gcp_region": "asia-south1", "total_usage": usage,
    }

    asm._ASSUMPTIONS["nat_vms_per_gateway"] = 32.0
    e32 = asm.map_flat_hourly([row])[0]
    assert e32["strategy"] == "map"
    assert e32["is_assumption"] is True
    d32 = _dollars(e32, usage)
    # 32-VM default = AWS parity at ~$0.044/hr -> $4.40 for 100 hrs
    assert d32 == pytest.approx(4.40, abs=0.02)

    asm._ASSUMPTIONS["nat_vms_per_gateway"] = 10.0
    e10 = asm.map_flat_hourly([row])[0]
    d10 = _dollars(e10, usage)
    # Dollars scale linearly with the VM assumption
    assert d10 == pytest.approx(d32 * 10.0 / 32.0, rel=1e-4)

    asm._ASSUMPTIONS["nat_vms_per_gateway"] = 32.0  # restore


@pytest.mark.parametrize("key,product,canonical,ut", [
    ("dax_node_gib", "Amazon DynamoDB Accelerator (DAX)", "dax", "APS3-NodeUsage:dax.r5.large"),
    ("memorydb_node_gib", "Amazon MemoryDB", "memorydb", "APS3-NodeUsage:db.r6g.large"),
])
def test_cache_e2e_dollars_scale_with_node_gib(key, product, canonical, ut):
    """R2/R3: DAX & MemoryDB resolve a real Memorystore SKU; dollars scale with node GiB."""
    usage = 100.0  # node-hours
    row = {
        "aws_li_key": f"{canonical}_e2e", "product": product, "canonical": canonical,
        "usage_type": ut, "operation": None, "gcp_region": "us-central1",
        "total_usage": usage, "deployment_option": None,
    }

    asm._ASSUMPTIONS[key] = 16.0
    e16 = asm.map_elasticache([row])[0]
    assert e16["strategy"] == "map"
    assert e16["gcp_service"] == "Cloud Memorystore for Redis"
    assert e16["is_assumption"] is True
    d16 = _dollars(e16, usage)

    asm._ASSUMPTIONS[key] = 32.0
    e32 = asm.map_elasticache([row])[0]
    d32 = _dollars(e32, usage)
    assert d32 == pytest.approx(d16 * 2.0, rel=1e-6)  # double the GiB -> double the dollars

    asm._ASSUMPTIONS[key] = 16.0  # restore


def test_dynamodb_rcu_e2e_dollars_scale():
    """R5: DynamoDB RCU resolves a real Firestore SKU; dollars scale with the conversion."""
    usage = 1000.0  # RCU-hours
    row = {
        "aws_li_key": "ddb_rcu_e2e", "product": "Amazon DynamoDB",
        "usage_type": "APS3-ReadCapacityUnit-Hrs", "operation": None,
        "gcp_region": "us-central1",
    }

    asm._ASSUMPTIONS["dynamodb_rcu_to_reads_sec"] = 1.0
    e1 = asm.map_dynamodb_capacity([row])[0]
    assert e1["strategy"] == "map"
    assert e1["gcp_service"] == "Cloud Firestore"
    d1 = _dollars(e1, usage)

    asm._ASSUMPTIONS["dynamodb_rcu_to_reads_sec"] = 0.5
    e05 = asm.map_dynamodb_capacity([row])[0]
    d05 = _dollars(e05, usage)
    assert d05 == pytest.approx(d1 * 0.5, rel=1e-6)

    asm._ASSUMPTIONS["dynamodb_rcu_to_reads_sec"] = 1.0  # restore


def test_glue_e2e_dollars_from_real_dataflow_skus():
    """R6: Glue DPU-hours resolve real Dataflow vCPU+RAM SKUs and price to non-zero dollars."""
    usage = 10.0  # DPU-hours
    row = {
        "aws_li_key": "glue_e2e", "product": "AWS Glue",
        "usage_type": "APS3-ETL-DPU-Hour", "operation": "ETL job",
        "total_usage": usage, "gcp_region": "us-central1",
    }
    asm._GLUE_DPU_VCPU = 4.0
    asm._GLUE_DPU_RAM_GIB = 16.0

    comps = asm.map_glue([row])
    assert len(comps) == 2
    total = 0.0
    for c in comps:
        assert c["strategy"] == "map"
        assert c["gcp_service"] == "Cloud Dataflow"
        assert c["is_assumption"] is True
        total += _dollars(c, usage)
    assert total > 0.0  # both components resolved real rates and priced
