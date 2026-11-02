"""Tests for Assumption-Driven Mappings (Workorder R1-R7 and Impact Registry)."""

import json
import os
import subprocess
import sys
import pytest

SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import apply_static_mappings as asm


def test_impact_registry_ratchet():
    """Every assumption key in assumption-defaults.json must be in assumption_impact.json."""
    defaults_path = os.path.join(DATA_DIR, "assumption-defaults.json")
    impact_path = os.path.join(DATA_DIR, "assumption_impact.json")

    with open(defaults_path, "r", encoding="utf-8") as f:
        defaults = json.load(f).get("values", {})
    with open(impact_path, "r", encoding="utf-8") as f:
        impact = json.load(f).get("impacts", {})

    defaults_keys = set(defaults.keys())
    impact_keys = set(impact.keys())

    missing = defaults_keys - impact_keys
    assert not missing, f"Assumption keys in defaults missing from assumption_impact.json: {missing}"


def test_assumptions_impact_cli():
    """scripts/assumptions_impact.py CLI works with --list and --key."""
    script = os.path.join(SCRIPTS_DIR, "assumptions_impact.py")

    # 1. Test --list
    res = subprocess.run([sys.executable, script, "--list"], capture_output=True, text=True, check=True)
    assert "nat_vms_per_gateway" in res.stdout
    assert "dax_node_gib" in res.stdout
    assert "memorydb_node_gib" in res.stdout

    # 2. Test --key with --json
    res_key = subprocess.run([sys.executable, script, "--key", "nat_vms_per_gateway", "--json"],
                             capture_output=True, text=True, check=True)
    parsed = json.loads(res_key.stdout)
    assert "services" in parsed.get("nat_vms_per_gateway", {})


def test_r1_nat_gateway_assumption(monkeypatch):
    """R1: NAT Gateway hours scaled by nat_vms_per_gateway."""
    row = {
        "aws_li_key": "k_nat",
        "product": "Amazon Virtual Private Cloud",
        "usage_type": "APS3-NatGateway-Hours",
        "operation": None,
        "gcp_region": "asia-south1",
        "aws_amortized_cost": 100.0,
        "total_usage": 100.0,
    }

    # At default 32.0 (matches 32-VM cap parity 0.044 / 0.0014)
    monkeypatch.setitem(asm._ASSUMPTIONS, "nat_vms_per_gateway", 32.0)
    out = asm.map_flat_hourly([row])
    assert len(out) == 1
    assert abs(out[0]["unit_multiplier"] - (0.044 / 0.0014)) < 1e-4
    assert out[0]["is_assumption"] is True
    assert "nat_vms_per_gateway=32" in out[0]["assumption"]

    # Changed to 10.0 (multiplier scales proportionally)
    monkeypatch.setitem(asm._ASSUMPTIONS, "nat_vms_per_gateway", 10.0)
    out2 = asm.map_flat_hourly([row])
    assert len(out2) == 1
    assert abs(out2[0]["unit_multiplier"] - (10.0 / 32.0 * (0.044 / 0.0014))) < 1e-4
    assert "nat_vms_per_gateway=10" in out2[0]["assumption"]


def test_r2_dax_node_hours(monkeypatch):
    """R2: DAX node-hours scaled to Memorystore GiB via dax_node_gib."""
    row = {
        "aws_li_key": "k_dax",
        "product": "Amazon DynamoDB Accelerator (DAX)",
        "canonical": "dax",
        "usage_type": "APS3-NodeUsage:dax.r5.large",
        "operation": None,
        "gcp_region": "us-central1",
        "aws_amortized_cost": 50.0,
        "total_usage": 100.0,
        "deployment_option": None,
    }

    # Default 16.0 GiB
    monkeypatch.setitem(asm._ASSUMPTIONS, "dax_node_gib", 16.0)
    out = asm.map_elasticache([row])
    assert len(out) == 1
    assert out[0]["strategy"] == "map"
    assert out[0]["gcp_service"] == "Cloud Memorystore for Redis"
    assert out[0]["unit_multiplier"] == 16.0
    assert out[0]["is_assumption"] is True
    assert out[0]["assumption"] == "dax_node_gib=16"

    # Changed to 32.0 GiB
    monkeypatch.setitem(asm._ASSUMPTIONS, "dax_node_gib", 32.0)
    out2 = asm.map_elasticache([row])
    assert len(out2) == 1
    assert out2[0]["unit_multiplier"] == 32.0
    assert out2[0]["assumption"] == "dax_node_gib=32"


def test_r3_memorydb_node_hours(monkeypatch):
    """R3: MemoryDB node-hours scaled to Memorystore GiB via memorydb_node_gib."""
    row = {
        "aws_li_key": "k_memdb",
        "product": "Amazon MemoryDB",
        "canonical": "memorydb",
        "usage_type": "APS3-NodeUsage:db.r6g.large",
        "operation": None,
        "gcp_region": "us-central1",
        "aws_amortized_cost": 75.0,
        "total_usage": 100.0,
        "deployment_option": None,
    }

    # Default 16.0 GiB
    monkeypatch.setitem(asm._ASSUMPTIONS, "memorydb_node_gib", 16.0)
    out = asm.map_elasticache([row])
    assert len(out) == 1
    assert out[0]["strategy"] == "map"
    assert out[0]["gcp_service"] == "Cloud Memorystore for Redis"
    assert out[0]["unit_multiplier"] == 16.0
    assert out[0]["is_assumption"] is True
    assert out[0]["assumption"] == "memorydb_node_gib=16"

    # Changed to 8.0 GiB
    monkeypatch.setitem(asm._ASSUMPTIONS, "memorydb_node_gib", 8.0)
    out2 = asm.map_elasticache([row])
    assert len(out2) == 1
    assert out2[0]["unit_multiplier"] == 8.0
    assert out2[0]["assumption"] == "memorydb_node_gib=8"


def test_r4_inferentia_and_trainium(monkeypatch):
    """R4: Inferentia and Trainium mapped to Cloud TPU via declared ratio."""
    row_inf = {
        "aws_li_key": "k_inf",
        "instance_type": "inf1.xlarge",
        "gcp_region": "us-central1",
    }
    row_trn = {
        "aws_li_key": "k_trn",
        "instance_type": "trn1.2xlarge",
        "gcp_region": "us-central1",
    }

    # Default 1.0
    monkeypatch.setitem(asm._ASSUMPTIONS, "inferentia_chip_to_tpu", 1.0)
    monkeypatch.setitem(asm._ASSUMPTIONS, "trainium_chip_to_tpu", 1.0)

    out_inf = asm.map_tpu_row(row_inf, {}, "inf1.xlarge")
    assert len(out_inf) == 1
    assert out_inf[0]["gcp_service"] == "Cloud TPU"
    assert out_inf[0]["is_assumption"] is True
    assert out_inf[0]["assumption"] == "inferentia_chip_to_tpu=1"
    chips_inf = out_inf[0]["unit_multiplier"]

    # Changing inferentia_chip_to_tpu moves unit_multiplier
    monkeypatch.setitem(asm._ASSUMPTIONS, "inferentia_chip_to_tpu", 2.5)
    out_inf2 = asm.map_tpu_row(row_inf, {}, "inf1.xlarge")
    assert out_inf2[0]["unit_multiplier"] == pytest.approx(chips_inf * 2.5)
    assert out_inf2[0]["assumption"] == "inferentia_chip_to_tpu=2.5"

    # Trainium check
    out_trn = asm.map_tpu_row(row_trn, {}, "trn1.2xlarge")
    assert len(out_trn) == 1
    assert out_trn[0]["gcp_service"] == "Cloud TPU"
    assert out_trn[0]["is_assumption"] is True
    assert out_trn[0]["assumption"] == "trainium_chip_to_tpu=1"
    chips_trn = out_trn[0]["unit_multiplier"]

    monkeypatch.setitem(asm._ASSUMPTIONS, "trainium_chip_to_tpu", 0.5)
    out_trn2 = asm.map_tpu_row(row_trn, {}, "trn1.2xlarge")
    assert out_trn2[0]["unit_multiplier"] == pytest.approx(chips_trn * 0.5)
    assert out_trn2[0]["assumption"] == "trainium_chip_to_tpu=0.5"


def test_r5_dynamodb_capacity(monkeypatch):
    """R5: DynamoDB RCU and WCU mapped to Firestore Read/Write ops."""
    row_rcu = {
        "aws_li_key": "k_rcu",
        "product": "Amazon DynamoDB",
        "usage_type": "APS3-ReadCapacityUnit-Hrs",
        "operation": None,
        "gcp_region": "us-central1",
    }
    row_wcu = {
        "aws_li_key": "k_wcu",
        "product": "Amazon DynamoDB",
        "usage_type": "APS3-WriteCapacityUnit-Hrs",
        "operation": None,
        "gcp_region": "us-central1",
    }

    # Default 1.0 (1 read/s = 3600 reads/hr)
    monkeypatch.setitem(asm._ASSUMPTIONS, "dynamodb_rcu_to_reads_sec", 1.0)
    monkeypatch.setitem(asm._ASSUMPTIONS, "dynamodb_wcu_to_writes_sec", 1.0)

    out_rcu = asm.map_dynamodb_capacity([row_rcu])
    assert len(out_rcu) == 1
    assert out_rcu[0]["gcp_service"] == "Cloud Firestore"
    assert out_rcu[0]["gcp_sku_name"] == "Cloud Firestore Read Ops"
    assert out_rcu[0]["strategy"] == "map"
    assert out_rcu[0]["unit_multiplier"] == 3600.0
    assert out_rcu[0]["is_assumption"] is True
    assert out_rcu[0]["assumption"] == "dynamodb_rcu_to_reads_sec=1"

    out_wcu = asm.map_dynamodb_capacity([row_wcu])
    assert len(out_wcu) == 1
    assert out_wcu[0]["gcp_service"] == "Cloud Firestore"
    assert out_wcu[0]["gcp_sku_name"] == "Cloud Firestore Entity Writes"
    assert out_wcu[0]["strategy"] == "map"
    assert out_wcu[0]["unit_multiplier"] == 3600.0
    assert out_wcu[0]["is_assumption"] is True
    assert out_wcu[0]["assumption"] == "dynamodb_wcu_to_writes_sec=1"

    # Moving assumption moves unit_multiplier
    monkeypatch.setitem(asm._ASSUMPTIONS, "dynamodb_rcu_to_reads_sec", 0.5)
    out_rcu2 = asm.map_dynamodb_capacity([row_rcu])
    assert out_rcu2[0]["unit_multiplier"] == 1800.0
    assert out_rcu2[0]["assumption"] == "dynamodb_rcu_to_reads_sec=0.5"

    monkeypatch.setitem(asm._ASSUMPTIONS, "dynamodb_wcu_to_writes_sec", 2.0)
    out_wcu2 = asm.map_dynamodb_capacity([row_wcu])
    assert out_wcu2[0]["unit_multiplier"] == 7200.0
    assert out_wcu2[0]["assumption"] == "dynamodb_wcu_to_writes_sec=2"


def test_r6_glue_wiring(monkeypatch):
    """R6: Glue DPU-hours converted to Dataflow vCPU+RAM with assumption stamps."""
    row = {
        "aws_li_key": "k_glue",
        "product": "AWS Glue",
        "usage_type": "APS3-ETL-DPU-Hour",
        "operation": "ETL job",
        "total_usage": 10.0,
        "gcp_region": "us-central1",
    }
    monkeypatch.setattr(asm, "_GLUE_DPU_VCPU", 4.0)
    monkeypatch.setattr(asm, "_GLUE_DPU_RAM_GIB", 16.0)

    out = asm.map_glue([row])
    assert len(out) == 2
    for comp in out:
        assert comp["gcp_service"] == "Cloud Dataflow"
        assert comp["is_assumption"] is True
        assert comp["assumption"] == "glue_dpu_vcpu=4, glue_dpu_ram_gib=16"


def test_r7_rds_provisioned_iops(monkeypatch):
    """R7: RDS provisioned IOPS controlled by rds_iops_included_in_storage."""
    row = {
        "aws_li_key": "k_rds_iops",
        "product": "Amazon Relational Database Service",
        "canonical": "rds",
        "usage_type": "APS3-ProvisionedIOPS",
        "operation": "CreateDBInstance",
        "gcp_region": "us-central1",
        "volume_type": "io1",
    }

    # When 1.0 (included in storage -> ignore)
    monkeypatch.setitem(asm._ASSUMPTIONS, "rds_iops_included_in_storage", 1.0)
    out = asm.map_block_storage([row])
    assert len(out) == 1
    assert out[0]["strategy"] == "ignore"
    assert out[0]["is_assumption"] is True
    assert out[0]["assumption"] == "rds_iops_included_in_storage=1"

    # When 0.0 (not included in storage -> passthrough at AWS cost)
    monkeypatch.setitem(asm._ASSUMPTIONS, "rds_iops_included_in_storage", 0.0)
    out2 = asm.map_block_storage([row])
    assert len(out2) == 1
    assert out2[0]["strategy"] == "passthrough"
    assert out2[0]["is_assumption"] is True
    assert out2[0]["assumption"] == "rds_iops_included_in_storage=0"