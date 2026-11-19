"""Tests for FSx (all four file systems) full modelling per WORKORDER §4.10."""

import pytest
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
sys.path.insert(0, _SCRIPTS)

import apply_static_mappings as asm


def _row(**kw):
    return {
        "aws_li_key": kw.get("aws_li_key", "k_fsx"),
        "product": kw.get("product", "Amazon FSx"),
        "usage_type": kw.get("usage_type", ""),
        "operation": kw.get("operation", ""),
        "unit": kw.get("unit", "GB-Mo"),
        "total_usage": kw.get("total_usage", 100.0),
        "aws_amortized_cost": kw.get("aws_amortized_cost", 30.0),
        "gcp_region": kw.get("gcp_region", "us-central1"),
    }


def test_fsx_lustre_storage_maps_to_parallelstore():
    """FSx for Lustre storage maps to Parallelstore Capacity per WORKORDER §4.23 ($0.14/GiB-mo)."""
    rows = [
        _row(
            aws_li_key="k_lustre_ssd",
            product="Amazon FSx for Lustre",
            usage_type="APS3-FSx:Lustre-Storage",
            unit="GB-Mo",
            total_usage=1200.0,
            aws_amortized_cost=168.0,
        ),
        _row(
            aws_li_key="k_lustre_hdd",
            product="Amazon FSx for Lustre",
            usage_type="APS3-FSx:Lustre-HDD-Storage",
            unit="GB-Mo",
            total_usage=6000.0,
            aws_amortized_cost=150.0,
        ),
    ]
    res = asm.map_fsx(rows)
    assert len(res) == 2
    for r in res:
        assert r["gcp_service"] == "Parallelstore"
        assert r["gcp_sku_id"] == "PARALLELSTORE-CAPACITY"
        assert r["strategy"] == "map"
        assert r["component"] == "storage"
        assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB
        assert "$0.14/GiB-mo" in r["projection_note"]
        # Invariant: No FSx row is ever priced as live Hyperdisk/PD
        assert "Hyperdisk" not in str(r.get("projection_note"))
        assert "Persistent Disk" not in str(r.get("projection_note"))


def test_fsx_ontap_storage_maps_to_netapp_volumes_premium_default():
    """FSx for NetApp ONTAP SSD storage maps to NetApp Volumes Premium ($0.294/GiB-mo)."""
    rows = [
        _row(
            aws_li_key="k_ontap_ssd",
            product="Amazon FSx for NetApp ONTAP",
            usage_type="APS3-FSx:ONTAP-Storage",
            unit="GB-Mo",
            total_usage=1000.0,
            aws_amortized_cost=250.0,
        )
    ]
    res = asm.map_fsx(rows)
    assert len(res) == 1
    r = res[0]
    assert r["gcp_service"] == asm.GCP_NETAPP_VOLUMES
    assert r["gcp_sku_id"] == "NETAPP-PREMIUM"
    assert r["strategy"] == "map"
    assert r["component"] == "storage"
    assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB
    assert "$0.294/GiB-mo" in r["projection_note"]


def test_fsx_ontap_storage_subtiers_standard_and_extreme():
    """FSx for NetApp ONTAP HDD/capacity maps to Standard ($0.20), extreme maps to Extreme ($0.393)."""
    rows = [
        _row(
            aws_li_key="k_ontap_hdd",
            product="Amazon FSx for NetApp ONTAP",
            usage_type="APS3-FSx:ONTAP-HDD-Storage",
            unit="GB-Mo",
        ),
        _row(
            aws_li_key="k_ontap_ext",
            product="Amazon FSx for NetApp ONTAP",
            usage_type="APS3-FSx:ONTAP-Extreme-Storage",
            unit="GB-Mo",
        ),
    ]
    res = asm.map_fsx(rows)
    assert len(res) == 2
    assert res[0]["gcp_sku_id"] == "NETAPP-STANDARD"
    assert pytest.approx(res[0]["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB
    assert "$0.200/GiB-mo" in res[0]["projection_note"]

    assert res[1]["gcp_sku_id"] == "NETAPP-EXTREME"
    assert pytest.approx(res[1]["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB
    assert "$0.393/GiB-mo" in res[1]["projection_note"]


def test_fsx_windows_storage_maps_to_netapp_volumes_or_enterprise():
    """FSx for Windows File Server maps to Google Cloud NetApp Volumes (native SMB) or Filestore Enterprise."""
    rows = [
        _row(
            aws_li_key="k_win_ssd",
            product="Amazon FSx for Windows File Server",
            usage_type="APS3-FSx:Windows-SSD-Storage",
            unit="GB-Mo",
            total_usage=500.0,
            aws_amortized_cost=65.0,
        ),
        _row(
            aws_li_key="k_win_hdd",
            product="Amazon FSx for Windows File Server",
            usage_type="APS3-FSx:Windows-HDD-Storage",
            unit="GB-Mo",
            total_usage=2000.0,
            aws_amortized_cost=50.0,
        ),
    ]
    res = asm.map_fsx(rows)
    assert len(res) == 2
    for r in res:
        assert r["gcp_service"] in (asm.GCP_NETAPP_VOLUMES, "Cloud Filestore")
        assert r["strategy"] == "map"
        assert r["component"] == "storage"
        assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB


def test_fsx_openzfs_storage_maps_to_filestore_zonal_or_regional():
    """FSx for OpenZFS maps to Filestore Zonal (Single-AZ) or Regional (Multi-AZ)."""
    rows = [
        _row(
            aws_li_key="k_zfs_single",
            product="Amazon FSx for OpenZFS",
            usage_type="APS3-FSx:OpenZFS-Storage",
            unit="GB-Mo",
            total_usage=500.0,
            aws_amortized_cost=100.0,
        ),
        _row(
            aws_li_key="k_zfs_multi",
            product="Amazon FSx for OpenZFS",
            usage_type="APS3-FSx:OpenZFS-Storage-MultiAZ",
            unit="GB-Mo",
            total_usage=500.0,
            aws_amortized_cost=160.0,
        ),
    ]
    res = asm.map_fsx(rows)
    assert len(res) == 2
    assert res[0]["gcp_service"] == "Cloud Filestore"
    assert res[0]["gcp_sku_name"] == asm.GCP_FILESTORE_ZONAL
    assert res[0]["strategy"] == "map"

    assert res[1]["gcp_service"] == "Cloud Filestore"
    assert res[1]["gcp_sku_name"] == asm.GCP_FILESTORE_REGIONAL
    assert res[1]["strategy"] == "map"


def test_fsx_throughput_and_iops_bundled_ignore():
    """FSx throughput and SSD-IOPS map to strategy='ignore' ($0) as capacity-bundled."""
    rows = [
        _row(
            aws_li_key="k_tput_lustre",
            product="Amazon FSx for Lustre",
            usage_type="APS3-FSx:Throughput-MBps",
            unit="MBps-Mo",
            total_usage=100.0,
            aws_amortized_cost=200.0,
        ),
        _row(
            aws_li_key="k_tput_ontap",
            product="Amazon FSx for NetApp ONTAP",
            usage_type="APS3-FSx:ThroughputCapacity",
            unit="MBps-Mo",
            total_usage=64.0,
            aws_amortized_cost=150.0,
        ),
        _row(
            aws_li_key="k_iops_ssd",
            product="Amazon FSx",
            usage_type="APS3-FSx:SSD-IOPS",
            unit="IOPS-Mo",
            total_usage=5000.0,
            aws_amortized_cost=50.0,
        ),
        _row(
            aws_li_key="k_iops_meta",
            product="Amazon FSx for Lustre",
            usage_type="APS3-FSx:MetadataIOPS",
            unit="IOPS-Mo",
            total_usage=3000.0,
            aws_amortized_cost=30.0,
        ),
    ]
    res = asm.map_fsx(rows)
    assert len(res) == 4
    for r in res:
        assert r["strategy"] == "ignore"
        assert r["unit_multiplier"] == 0.0
        assert "bundled into" in r["projection_note"]


def test_fsx_capacity_pool_ontap():
    """FSx ONTAP capacity pool cold storage maps to NetApp Volumes cold capacity."""
    rows = [
        _row(
            aws_li_key="k_pool_gb",
            product="Amazon FSx for NetApp ONTAP",
            usage_type="APS3-FSx:CapacityPool-GB",
            unit="GB-Mo",
            total_usage=5000.0,
            aws_amortized_cost=125.0,
        )
    ]
    res = asm.map_fsx(rows)
    assert len(res) == 1
    r = res[0]
    assert r["gcp_service"] == asm.GCP_NETAPP_VOLUMES
    assert r["strategy"] in ("map", "passthrough")
    if r["strategy"] == "map":
        assert r.get("gcp_sku_id")
    assert "NetApp Volumes" in str(r.get("projection_note")) or "NetApp Volumes" in str(r.get("gcp_sku_name"))


def test_fsx_intelligent_tiering_passthrough():
    """FSx Intelligent-Tiering cold storage rows passthrough with Cloud Storage cold-tier disclosure."""
    rows = [
        _row(
            aws_li_key="k_it_cold",
            product="Amazon FSx for Lustre",
            usage_type="APS3-FSx:InfrequentAccess-GB",
            unit="GB-Mo",
            total_usage=10000.0,
            aws_amortized_cost=125.0,
        )
    ]
    res = asm.map_fsx(rows)
    assert len(res) == 1
    r = res[0]
    assert r["strategy"] == "passthrough"
    assert r["unit_multiplier"] == 1.0
    assert "Cloud Storage Coldline/Archive" in r["projection_note"]


def test_fsx_backup_maps_to_parent_backup_sku():
    """FSx backups map to parent file system backup SKU (never blanket passthrough)."""
    rows = [
        _row(
            aws_li_key="k_bku_ontap",
            product="Amazon FSx for NetApp ONTAP",
            usage_type="APS3-FSx:Backup-GB-Mo",
            unit="GB-Mo",
            total_usage=1000.0,
            aws_amortized_cost=50.0,
        ),
        _row(
            aws_li_key="k_bku_generic",
            product="Amazon FSx",
            usage_type="APS3-FSx:Backup-GB-Mo",
            unit="GB-Mo",
            total_usage=500.0,
            aws_amortized_cost=25.0,
        ),
    ]
    res = asm.map_fsx(rows)
    assert len(res) == 2
    for r in res:
        assert r["strategy"] == "map"
        assert r["component"] == "backup"
        assert "Backup" in r["gcp_sku_name"]
        assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB


def test_fsx_catalog_and_spec_dispatch_coverage():
    """Verify all 18 FSx catalog dimensions dispatch cleanly to their explicit charge types."""
    import json
    import spec_dispatch as sd

    cat_path = os.path.join(os.path.dirname(_HERE), "data", "service_usage_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    fsx_entries = catalog.get("fsx", [])
    assert len(fsx_entries) >= 15

    for entry in fsx_entries:
        row = {
            "aws_li_key": "k",
            "product": "Amazon FSx",
            "product_code": "AmazonFSx",
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


def test_fsx_mapping_invariants():
    """Invariant: every mapped FSx row has a valid gcp_sku_id (never empty)."""
    rows = [
        _row(usage_type="APS3-FSx:Lustre-Storage", product="Amazon FSx for Lustre"),
        _row(usage_type="APS3-FSx:ONTAP-Storage", product="Amazon FSx for NetApp ONTAP"),
        _row(usage_type="APS3-FSx:CapacityPool-GB", product="Amazon FSx for NetApp ONTAP"),
        _row(usage_type="APS3-FSx:OpenZFS-Storage", product="Amazon FSx for OpenZFS"),
        _row(usage_type="APS3-FSx:Backup-GB-Mo", product="Amazon FSx"),
    ]
    res = asm.map_fsx(rows)
    for r in res:
        if r["strategy"] == "map":
            assert r.get("gcp_sku_id"), f"Mapped row missing gcp_sku_id: {r}"

