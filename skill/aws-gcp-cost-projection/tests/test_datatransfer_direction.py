import pytest
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from usage_type_parser import transfer_direction


def test_transfer_direction_ingress():
    """Ingress is free on GCP -> ignore."""
    parts = {
        "usage_type": "APS1-DataTransfer-In-Bytes",
        "operation": "DataTransfer-In",
        "product": "AWS Data Transfer",
        "from_region": "asia-south1",
        "to_region": None,
    }
    strat, direction, note = transfer_direction(parts)
    assert strat == "ignore"
    assert direction is None
    assert "ingress is free" in note


def test_transfer_direction_same_region_interzone():
    """Same region from_region == to_region -> interzone."""
    parts = {
        "usage_type": "APS1-Regional-Bytes",
        "operation": "RegionalBytes",
        "product": "AWS Data Transfer",
        "from_region": "asia-southeast1",
        "to_region": "asia-southeast1",
    }
    strat, direction, note = transfer_direction(parts)
    assert strat == "map"
    assert direction == "interzone"
    assert "inter-zone" in note


def test_transfer_direction_cross_region_waw():
    """Cross region from_region != to_region (e.g. WAW) -> interregion."""
    parts = {
        "usage_type": "APS1-WAW1-AWS-Out-Bytes",
        "operation": "AWS-Out-Bytes",
        "product": "AWS Data Transfer APS1-WAW1-AWS-Out-Bytes",
        "from_region": "asia-southeast1",
        "to_region": "europe-central2",
    }
    strat, direction, note = transfer_direction(parts)
    assert strat == "map"
    assert direction == "interregion"
    assert "inter-region" in note


def test_transfer_direction_internet():
    """Internet egress (to_region is None, no regional hints)."""
    parts = {
        "usage_type": "APS1-AWS-Out-Bytes",
        "operation": "AWS-Out-Bytes",
        "product": "AWS Data Transfer",
        "from_region": "asia-southeast1",
        "to_region": None,
    }
    strat, direction, note = transfer_direction(parts)
    assert strat == "map"
    assert direction == "internet"
    assert "internet egress" in note


def test_transfer_direction_peering():
    """VPC Peering -> interzone."""
    parts = {
        "usage_type": "APS1-VpcPeering-Out-Bytes",
        "operation": "VpcPeering",
        "product": "AWS Data Transfer",
        "from_region": "asia-southeast1",
        "to_region": None,
    }
    strat, direction, note = transfer_direction(parts)
    assert strat == "map"
    assert direction == "interzone"
    assert "VPC Peering" in note


def test_transfer_direction_corrupted_pdf_label():
    """Flat/PDF corrupted label ($116.51 case):
    operation says regional data transfer between EC2 AZs, but product has corrupted WAW1 label.
    """
    parts = {
        "usage_type": "",
        "operation": "$0.010 per GB - regional data transfer - in/out/between EC2 AZs",
        "product": "AWS Data Transfer APS3-WAW1-AWS-Out-Bytes",
        "from_region": "asia-south1",
        "to_region": "europe-central2",
    }
    strat, direction, note = transfer_direction(parts)
    assert strat == "map"
    assert direction == "interzone"
    assert "inter-zone" in note


def test_transfer_direction_bug1_corrupted_to_region_override():
    """BUG-1 (P0): APS3-USW2-AWS-Out-Bytes has to_region wrongly set to asia-south1 (from_region).
    Authoritative usage_type region pair must override and return interregion.
    """
    parts = {
        "usage_type": "APS3-USW2-AWS-Out-Bytes",
        "operation": "AWS-Out-Bytes",
        "product": "AWS Data Transfer",
        "from_region": "asia-south1",
        "to_region": "asia-south1",
    }
    strat, direction, note = transfer_direction(parts)
    assert strat == "map"
    assert direction == "interregion"
    assert "inter-region" in note

    # Same-region pair stays interzone
    parts_same = {
        "usage_type": "APS3-APS3-AWS-Out-Bytes",
        "operation": "AWS-Out-Bytes",
        "product": "AWS Data Transfer",
        "from_region": "asia-south1",
        "to_region": "asia-south1",
    }
    strat, direction, note = transfer_direction(parts_same)
    assert strat == "map"
    assert direction == "interzone"

