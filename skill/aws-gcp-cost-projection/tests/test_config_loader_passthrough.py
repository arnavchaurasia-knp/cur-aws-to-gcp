import json
import os
import sys
import tempfile
import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from config_loader import load_assumption_passthroughs, is_passthrough_enabled


def test_config_loader_passthrough_absent(tmp_path):
    """When snapshot is absent and defaults have no passthrough, returns empty set."""
    pts = load_assumption_passthroughs(str(tmp_path))
    assert isinstance(pts, set)


def test_config_loader_passthrough_from_snapshot(tmp_path):
    """Snapshot passthrough map is honored."""
    snap = {
        "values": {"nat_vms_per_gateway": 36.0},
        "passthrough": {
            "nat_vms_per_gateway": True,
            "memorydb_node_gib": False,
        }
    }
    with open(tmp_path / "assumptions.json", "w", encoding="utf-8") as f:
        json.dump(snap, f)

    pts = load_assumption_passthroughs(str(tmp_path))
    assert "nat_vms_per_gateway" in pts
    assert "memorydb_node_gib" not in pts


def test_config_loader_passthrough_from_items_array(tmp_path):
    """Snapshot items array containing passthrough_enabled: true is honored."""
    snap = {
        "values": {"glue_dpu_vcpu": 4.0},
        "items": [
            {"key": "glue_dpu_vcpu", "value": 4.0, "passthrough_enabled": True},
            {"key": "glue_dpu_ram_gib", "value": 16.0, "passthrough_enabled": False},
        ]
    }
    with open(tmp_path / "assumptions.json", "w", encoding="utf-8") as f:
        json.dump(snap, f)

    pts = load_assumption_passthroughs(str(tmp_path))
    assert "glue_dpu_vcpu" in pts
    assert "glue_dpu_ram_gib" not in pts


def test_is_passthrough_enabled_legacy_sentinel(tmp_path):
    """Legacy sentinel <= 0 is true for DynamoDB and Inferentia/Trainium."""
    # Explicit toggle is False/absent, but value is 0.0
    assert is_passthrough_enabled("dynamodb_rcu_to_reads_sec", str(tmp_path), value=0.0) is True
    assert is_passthrough_enabled("dynamodb_rcu_to_reads_sec", str(tmp_path), value=1.0) is False
    assert is_passthrough_enabled("inferentia_chip_to_tpu", str(tmp_path), value=0.0) is True
    assert is_passthrough_enabled("inferentia_chip_to_tpu", str(tmp_path), value=0.19) is False

    # For non-sentinel keys, value <= 0 does NOT trigger passthrough
    assert is_passthrough_enabled("nat_vms_per_gateway", str(tmp_path), value=0.0) is False

    # Explicit toggle overrides
    snap = {
        "passthrough": {"nat_vms_per_gateway": True, "inferentia_chip_to_tpu": True}
    }
    with open(tmp_path / "assumptions.json", "w", encoding="utf-8") as f:
        json.dump(snap, f)

    assert is_passthrough_enabled("nat_vms_per_gateway", str(tmp_path), value=36.0) is True
    assert is_passthrough_enabled("inferentia_chip_to_tpu", str(tmp_path), value=0.5) is True
