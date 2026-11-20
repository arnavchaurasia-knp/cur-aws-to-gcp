"""Tests for EBS (Elastic Block Store) full modelling per WORKORDER §4.25.

EBS is GOLD-tier. Volume-type -> Persistent Disk / Hyperdisk target mapping,
provisioned IOPS/throughput as separate components, snapshots split standard vs
archive. All GCP target rates verified against the official GCP disk pricing page
(cloud.google.com/compute/disks-image-pricing, us-central1 x730) 2026-11-20:
  Balanced PD Capacity           $0.1000/GiB-mo   (gp2)
  Hyperdisk Balanced Capacity    $0.0800/GiB-mo   (gp3, io1/io2 standard tier)
  Hyperdisk Balanced IOPS        $0.00500/IOPS-mo (gp3 provisioned IOPS)
  Hyperdisk Balanced Throughput  $0.0400/MiBps-mo (gp3 provisioned throughput)
  Hyperdisk Extreme IOPS         $0.0320/IOPS-mo  (io1/io2 provisioned IOPS)
  Storage PD Capacity            $0.0400/GiB-mo   (sc1/st1/standard)
  Standard PD Snapshot           $0.0500/GiB-mo
  Regional Archive Snapshot      $0.0190/GiB-mo
AWS units verified at aws.amazon.com/ebs/pricing: gp3/io1/io2 provisioned IOPS
bill in provisioned-IOPS-month -> GCP IOPS-month, unit_multiplier=1.0.
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
sys.path.insert(0, _SCRIPTS)

import apply_static_mappings as asm  # noqa: E402


def _row(usage_type, unit="GB-Mo", total_usage=100.0, volume_type="", **kw):
    return {
        "aws_li_key": kw.get("aws_li_key", "k_" + usage_type),
        "product": "Amazon Elastic Block Store",
        "usage_type": usage_type,
        "operation": kw.get("operation", ""),
        "unit": unit,
        "volume_type": volume_type,
        "total_usage": total_usage,
        "aws_amortized_cost": kw.get("aws_amortized_cost", 50.0),
        "gcp_region": kw.get("gcp_region", "us-central1"),
    }


def _one(rows):
    res = asm.map_block_storage(rows)
    res = res[0] if isinstance(res, tuple) else res
    assert len(res) == 1
    return res[0]


# ── Volume-type capacity targets ─────────────────────────────────────────────

_CAPACITY_TARGETS = [
    ("gp2", asm.GCP_BALANCED_PD),
    ("gp3", asm.GCP_HYPERDISK_BALANCED),
    ("io1", asm.GCP_HYPERDISK_BALANCED),   # standard tier under Balanced's 160k-IOPS ceiling
    ("io2", asm.GCP_HYPERDISK_BALANCED),
    ("sc1", asm.GCP_STANDARD_PD),
    ("st1", asm.GCP_STANDARD_PD),
    ("standard", asm.GCP_STANDARD_PD),
]


@pytest.mark.parametrize("vol,expected", _CAPACITY_TARGETS)
def test_ebs_capacity_maps_to_expected_pd_family(vol, expected):
    r = _one([_row(f"APS3-EBS:VolumeUsage.{vol}", unit="GB-Mo", volume_type=vol)])
    assert r["gcp_service"] == asm.GCP_COMPUTE_ENGINE
    assert r["gcp_sku_name"] == expected, f"{vol} -> {r['gcp_sku_name']}, expected {expected}"
    assert r["strategy"] == "map"
    assert r["component"] == "storage"
    assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB


# ── Provisioned IOPS / throughput ────────────────────────────────────────────

def test_gp3_provisioned_iops_maps_to_hyperdisk_balanced_iops():
    r = _one([_row("APS3-EBS:VolumeP-IOPS.gp3", unit="IOPS-Mo", total_usage=3000, volume_type="gp3")])
    assert r["gcp_sku_name"] == "Hyperdisk Balanced IOPS"
    assert r["component"] == "iops"
    assert r["strategy"] == "map"
    assert r["unit_multiplier"] == 1.0  # IOPS-month on both sides


def test_gp3_provisioned_throughput_maps_to_hyperdisk_balanced_throughput():
    r = _one([_row("APS3-EBS:VolumeP-Throughput.gp3", unit="GiBps-Mo", total_usage=1.0, volume_type="gp3")])
    assert r["gcp_sku_name"] == "Hyperdisk Balanced Throughput"
    assert r["component"] == "throughput"
    assert r["unit_multiplier"] == 1024.0  # GiBps-month -> MiBps-month


@pytest.mark.parametrize("vol", ["io1", "io2"])
def test_io1_io2_provisioned_iops_maps_to_extreme_iops_not_throughput(vol):
    """Regression: an io1/io2 provisioned-IOPS row (provisioned = thousands of
    IOPS) must map to Hyperdisk Extreme IOPS at unit_multiplier=1.0, NOT get
    intercepted by the throughput-ceiling branch (which fires when
    provisioned*1024 > 2400 MiBps) and mislabelled 'Extreme PD Throughput' x1024.
    Fixed by guarding that branch with `not is_iops`."""
    r = _one([_row(f"APS3-EBS:VolumeP-IOPS.{vol}", unit="IOPS-Mo", total_usage=5000, volume_type=vol)])
    assert r["component"] == "iops", f"{vol} IOPS landed in component {r['component']!r}"
    assert "Throughput" not in str(r["gcp_sku_name"]), (
        f"{vol} provisioned IOPS mis-mapped to a throughput SKU: {r['gcp_sku_name']!r}"
    )
    assert "IOPS" in str(r["gcp_sku_name"])
    assert r["unit_multiplier"] == 1.0  # never x1024 for an IOPS count
    # catalog resolves the Extreme IOPS SKU in us-central1 -> strategy map
    assert r["strategy"] == "map"


# ── Snapshots: standard vs archive ───────────────────────────────────────────

def test_ebs_standard_snapshot_maps_to_regional_pd_snapshot():
    r = _one([_row("APS3-EBS:SnapshotUsage", unit="GB-Mo", operation="CreateSnapshot")])
    assert r["gcp_sku_name"] == "Storage PD Snapshot"
    assert r["strategy"] == "map"
    assert pytest.approx(r["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB


def test_ebs_archive_snapshot_maps_to_regional_archive_snapshot():
    r = _one([_row("APS3-EBS:SnapshotArchiveStorage", unit="GB-Mo", operation="archive")])
    assert r["gcp_sku_name"] == "Regional Archive Snapshot Data Storage"
    assert r["strategy"] == "map"


# ── Completeness gate ────────────────────────────────────────────────────────

def test_ebs_catalog_and_spec_dispatch_coverage():
    import json
    import spec_dispatch as sd

    cat_path = os.path.join(os.path.dirname(_HERE), "data", "service_usage_catalog.json")
    with open(cat_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    ebs_entries = catalog.get("ebs", [])
    assert len(ebs_entries) >= 15, f"expected EBS catalog populated, got {len(ebs_entries)}"

    for entry in ebs_entries:
        row = {
            "aws_li_key": "k",
            "product": "Amazon Elastic Block Store",
            "product_code": "AmazonEBS",
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
