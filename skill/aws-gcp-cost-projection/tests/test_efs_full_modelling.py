"""Tests for EFS (Elastic File System) full modelling per WORKORDER §4.9."""

import pytest
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
sys.path.insert(0, _SCRIPTS)

import apply_static_mappings as asm


def _row(**kw):
    r = {
        "aws_li_key": kw.get("aws_li_key", "k_efs"),
        "product": kw.get("product", "Amazon Elastic File System"),
        "usage_type": kw.get("usage_type", ""),
        "operation": kw.get("operation", ""),
        "unit": kw.get("unit", "GB-Mo"),
        "total_usage": kw.get("total_usage", 100.0),
        "aws_amortized_cost": kw.get("aws_amortized_cost", 30.0),
        "gcp_region": kw.get("gcp_region", "us-central1"),
    }
    return r


def test_efs_standard_regional_maps_to_filestore_regional():
    """Standard Regional multi-AZ storage maps to Filestore Regional (flat $0.45/GiB-mo, custom-perf OFF)."""
    rows = [
        _row(
            aws_li_key="k_efs_std_reg",
            usage_type="APS3-TimedStorage-ByteHrs",
            unit="GB-Mo",
            total_usage=500.0,
            aws_amortized_cost=150.0,
        )
    ]
    res = asm.map_efs(rows)
    assert len(res) == 1
    r = res[0]
    assert r["gcp_service"] == "Cloud Filestore"
    assert r["gcp_sku_name"] == asm.GCP_FILESTORE_REGIONAL
    assert r["strategy"] == "map"
    assert r["component"] == "storage"
    assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB
    assert "Regional" in r["projection_note"]
    assert "cloud.google.com/filestore/pricing" in r["projection_note"]
    # Invariant: No EFS row is ever priced as live Hyperdisk/PD
    assert "Hyperdisk" not in r["projection_note"]
    assert "Persistent Disk" not in r["projection_note"]


def test_efs_standard_onezone_maps_to_filestore_zonal():
    """Standard One-Zone storage maps to Filestore Zonal (flat $0.25/GiB-mo, custom-perf OFF)."""
    rows = [
        _row(
            aws_li_key="k_efs_std_oz1",
            usage_type="APS3-TimedStorage-Z-ByteHrs",
            unit="GB-Mo",
            total_usage=1000.0,
            aws_amortized_cost=160.0,
        ),
        _row(
            aws_li_key="k_efs_std_oz2",
            usage_type="APS3-OneZone-StorageUsage",
            unit="GB-Mo",
            total_usage=500.0,
            aws_amortized_cost=80.0,
        ),
    ]
    res = asm.map_efs(rows)
    assert len(res) == 2
    for r in res:
        assert r["gcp_service"] == "Cloud Filestore"
        assert r["gcp_sku_name"] == asm.GCP_FILESTORE_ZONAL
        assert r["strategy"] == "map"
        assert r["component"] == "storage"
        assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB
        assert "One-Zone" in r["projection_note"]
        assert "cloud.google.com/filestore/pricing" in r["projection_note"]


def test_efs_ia_passthrough_with_agent_volumes_cold_disclosure():
    """IA storage ($0.016/GB) must passthrough at AWS cost with mandatory disclosure naming Agent Volumes Cold ($0.02).
    
    Invariant: NEVER priced-mapped to Basic HDD (which would be a 10x overprojection trap).
    """
    rows = [
        _row(
            aws_li_key="k_efs_ia_1",
            usage_type="APS3-TimedStorage-IA-ByteHrs",
            unit="GB-Mo",
            total_usage=10000.0,
            aws_amortized_cost=160.0,
        ),
        _row(
            aws_li_key="k_efs_ia_2",
            usage_type="APS3-TimedStorage-Z-IA-ByteHrs",
            unit="GB-Mo",
            total_usage=5000.0,
            aws_amortized_cost=66.5,
        ),
    ]
    res = asm.map_efs(rows)
    assert len(res) == 2
    for r in res:
        assert r["gcp_service"] == "Cloud Filestore"
        assert r["strategy"] == "passthrough"
        assert r["unit_multiplier"] == 1.0
        assert r["component"] == "storage"
        # Invariant: Never priced-mapped to Basic HDD
        assert r.get("gcp_sku_name") != "Filestore Capacity Basic HDD"
        note = r["projection_note"]
        assert "Filestore Agent Volumes Cold/Frozen ~$0.02/GiB-mo" in note
        assert "10×" in note
        assert "cloud.google.com/filestore/pricing" in note


def test_efs_archive_passthrough_with_agent_volumes_cold_disclosure():
    """Archive storage ($0.008/GB) must passthrough at AWS cost with mandatory disclosure naming Agent Volumes Cold.
    
    Invariant: NEVER falls through to default Basic SSD or Basic HDD (which would be a 20x overprojection trap).
    """
    rows = [
        _row(
            aws_li_key="k_efs_arch",
            usage_type="APS3-TimedStorage-Archive-ByteHrs",
            unit="GB-Mo",
            total_usage=20000.0,
            aws_amortized_cost=160.0,
        )
    ]
    res = asm.map_efs(rows)
    assert len(res) == 1
    r = res[0]
    assert r["gcp_service"] == "Cloud Filestore"
    assert r["strategy"] == "passthrough"
    assert r["unit_multiplier"] == 1.0
    assert r["component"] == "storage"
    assert r.get("gcp_sku_name") != "Filestore Capacity Basic SSD"
    assert r.get("gcp_sku_name") != "Filestore Capacity Basic HDD"
    note = r["projection_note"]
    assert "Filestore Agent Volumes Cold/Frozen ~$0.02/GiB-mo" in note
    assert "20×" in note
    assert "cloud.google.com/filestore/pricing" in note


def test_efs_provisioned_and_elastic_throughput_bundled_ignore():
    """Provisioned and Elastic throughput map to strategy='ignore' ($0) as capacity-bundled."""
    rows = [
        _row(
            aws_li_key="k_efs_tp_prov1",
            usage_type="APS3-ProvisionedTP-MiBpsHrs",
            unit="MBps-Mo",
            total_usage=50.0,
            aws_amortized_cost=300.0,
        ),
        _row(
            aws_li_key="k_efs_tp_prov2",
            usage_type="APS3-ProvisionedThroughput-MBps",
            unit="MBps-Mo",
            total_usage=20.0,
            aws_amortized_cost=120.0,
        ),
        _row(
            aws_li_key="k_efs_tp_elast1",
            usage_type="APS3-ETDataAccess-Bytes",
            unit="GB",
            total_usage=15000.0,
            aws_amortized_cost=450.0,
        ),
        _row(
            aws_li_key="k_efs_tp_elast2",
            usage_type="APS3-MeteredIOBytes",
            unit="GB",
            total_usage=8000.0,
            aws_amortized_cost=240.0,
        ),
    ]
    res = asm.map_efs(rows)
    assert len(res) == 4
    for r in res:
        assert r["gcp_service"] == "Cloud Filestore"
        assert r["strategy"] == "ignore"
        assert r["unit_multiplier"] == 0.0
        assert r["component"] == "storage"
        assert "bundled into Filestore capacity" in r["projection_note"]
        assert "cloud.google.com/filestore/pricing" in r["projection_note"]


def test_efs_backup_maps_to_filestore_backup_usage():
    """EFS Backup storage maps deterministically to Filestore Backup Usage ($0.08/GiB-mo).
    
    Invariant: EFS backup is never a bare passthrough once the SKU resolves.
    """
    rows = [
        _row(
            aws_li_key="k_efs_bku1",
            usage_type="APS3-BackupUsage",
            unit="GB-Mo",
            total_usage=1000.0,
            aws_amortized_cost=50.0,
            gcp_region="us-central1",
        ),
        _row(
            aws_li_key="k_efs_bku2",
            usage_type="APS3-WarmBackupStorage",
            unit="GB-Mo",
            total_usage=500.0,
            aws_amortized_cost=25.0,
            gcp_region="us-central1",
        ),
    ]
    res = asm.map_efs(rows)
    assert len(res) == 2
    for r in res:
        assert r["gcp_service"] == "Cloud Filestore"
        assert r["gcp_sku_name"] == asm.GCP_FILESTORE_BACKUP
        assert r["strategy"] == "map"
        assert r["component"] == "backup"
        assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB
        assert "0.08" in r["projection_note"]
        assert "cloud.google.com/filestore/pricing" in r["projection_note"]


def test_efs_catalog_and_spec_dispatch_coverage():
    """Verify all 17 EFS catalog dimensions dispatch cleanly to their explicit charge types."""
    import json
    import spec_dispatch as sd

    cat_path = os.path.join(os.path.dirname(_HERE), "data", "service_usage_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    efs_entries = catalog.get("efs", [])
    assert len(efs_entries) >= 15

    for entry in efs_entries:
        row = {
            "aws_li_key": "k",
            "product": "Amazon Elastic File System",
            "product_code": "AmazonEFS",
            "usage_type": entry["usage_type"],
            "unit": entry.get("unit", "GB-Mo"),
            "operation": "",
            "aws_region": "us-east-1",
        }
        d = sd.dispatch(row)
        assert d is not None, f"Dispatch returned None for {entry['usage_type']}"
        assert d["charge_type"] == entry["expected_charge_type"], (
            f"Expected {entry['expected_charge_type']}, got {d['charge_type']} for {entry['usage_type']}"
        )
        assert d["mechanic"] == entry["expected_mechanic"], (
            f"Expected {entry['expected_mechanic']}, got {d['mechanic']} for {entry['usage_type']}"
        )

