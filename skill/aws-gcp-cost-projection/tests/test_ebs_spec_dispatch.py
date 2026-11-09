import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import spec_dispatch
from apply_static_mappings import map_block_storage


def test_ebs_dispatch_standard_snapshot():
    """EBS standard snapshot dispatches to ebs_snapshot_standard."""
    row = {
        "product": "Amazon Elastic Block Store",
        "product_code": "AmazonEC2",
        "usage_type": "APS1-EBS:SnapshotUsage",
        "operation": "CreateSnapshot",
    }
    res = spec_dispatch.dispatch(row)
    assert res is not None
    assert res["service"] == "ec2"
    assert res["charge_type"] == "ebs_snapshot_standard"
    assert res["mechanic"] == "block_storage"


def test_ebs_dispatch_archive_snapshot():
    """EBS snapshot archive dispatches to ebs_snapshot_archive."""
    row = {
        "product": "Amazon Elastic Block Store",
        "product_code": "AmazonEC2",
        "usage_type": "APS1-EBS:SnapshotArchiveStorage",
        "operation": "archive",
    }
    res = spec_dispatch.dispatch(row)
    assert res is not None
    assert res["service"] == "ec2"
    assert res["charge_type"] == "ebs_snapshot_archive"
    assert res["mechanic"] == "block_storage"


def test_ebs_dispatch_volume():
    """EBS volume dispatches to ebs_volume default child."""
    row = {
        "product": "Amazon Elastic Block Store",
        "product_code": "AmazonEC2",
        "usage_type": "APS1-EBS:VolumeUsage.gp3",
        "operation": "CreateVolume",
    }
    res = spec_dispatch.dispatch(row)
    assert res is not None
    assert res["service"] == "ec2"
    assert res["charge_type"] == "ebs_volume"
    assert res["mechanic"] == "block_storage"


def test_ebs_top_level_spec():
    """EBS top-level spec dispatches for canonical ebs."""
    row = {
        "product": "Amazon Elastic Block Store",
        "product_code": "AmazonEBS",
        "usage_type": "APS1-EBS:SnapshotUsage",
    }
    res = spec_dispatch.dispatch(row)
    assert res is not None
    assert res["service"] == "ebs"
    assert res["charge_type"] == "ebs_snapshot_standard"
    assert res["mechanic"] == "block_storage"


def test_map_block_storage_snapshot_tiering():
    """Verify map_block_storage differentiates standard vs archive snapshots."""
    rows = [
        {
            "aws_li_key": "snap_std",
            "product": "Amazon EC2",
            "usage_type": "APS1-EBS:SnapshotUsage",
            "aws_region": "ap-southeast-1",
            "gcp_region": "asia-southeast1",
            "operation": "CreateSnapshot",
            "pricing_unit": "GB-Mo",
            "total_usage": 100.0,
            "aws_amortized_cost": 5.0,
            "mechanic_rule": "spec:ec2:ebs_storage.ebs_snapshot_standard",
        },
        {
            "aws_li_key": "snap_arc",
            "product": "Amazon EC2",
            "usage_type": "APS1-EBS:SnapshotArchiveStorage",
            "aws_region": "ap-southeast-1",
            "gcp_region": "asia-southeast1",
            "operation": "archive",
            "pricing_unit": "GB-Mo",
            "total_usage": 100.0,
            "aws_amortized_cost": 1.25,
            "mechanic_rule": "spec:ec2:ebs_storage.ebs_snapshot_archive",
        },
    ]
    res = map_block_storage(rows)
    mapped = {r["aws_li_key"]: r for r in res}

    assert mapped["snap_std"]["gcp_sku_name"] == "Storage PD Snapshot"
    assert mapped["snap_std"]["gcp_sku_id"] == "6E69-DD68-8DCA"

    assert mapped["snap_arc"]["gcp_sku_name"] == "Regional Archive Snapshot Data Storage"
    assert mapped["snap_arc"]["gcp_sku_id"] == "9DB9-341A-E6D3"
