"""Tests for the Cloud NAT per-VM-hour model (P-15) and its bill-derived
concurrent-VM estimate.

Cloud NAT bills per VM-hour at $0.0014, capped at 32 VMs/gateway → $0.044/hr
flat (verified cloud.google.com/nat/pricing 2026-11-19). AWS bills a flat
per-gateway hour, so the GCP figure needs the VM count behind each gateway.
nat_vms_per_gateway is a BILL_CALIBRATED assumption: estimate concurrent VMs
from compute instance-hours, fall back to the committed default otherwise.
"""

import pytest

import scripts.apply_static_mappings as m


class TestEstimateVmsPerGateway:
    def test_sum_hours_over_period_gives_concurrent_vms(self):
        # 32 VMs running a full 730-h month = 23360 instance-hours, 1 gateway
        assert m._estimate_vms_per_gateway(23360.0, 1) == pytest.approx(32.0)

    def test_split_across_gateways(self):
        # 64 concurrent VMs over 2 gateways → 32 each
        assert m._estimate_vms_per_gateway(64 * 730.0, 2) == pytest.approx(32.0)

    def test_small_fleet_scales_down(self):
        # 10 VMs full month, 1 gateway → 10 (below the 32 cap)
        assert m._estimate_vms_per_gateway(10 * 730.0, 1) == pytest.approx(10.0)

    def test_floor_of_one(self):
        assert m._estimate_vms_per_gateway(1.0, 1) >= 1.0

    def test_no_compute_hours_returns_none(self):
        # Nothing to derive → caller falls back to committed default
        assert m._estimate_vms_per_gateway(0.0, 1) is None
        assert m._estimate_vms_per_gateway(None, 1) is None

    def test_not_a_distinct_type_count(self):
        # A 500-VM fleet of ONE instance type is 500 VMs, not 1 (the old proxy's bug)
        assert m._estimate_vms_per_gateway(500 * 730.0, 1) == pytest.approx(500.0)


class TestNatMultiplier:
    def _nat_row(self):
        return {
            "aws_li_key": "nat1",
            "product": "Amazon Elastic Compute Cloud",
            "usage_type": "USE1-NatGateway-Hours",
            "operation": "",
            "unit": "Hrs",
            "total_usage": 730.0,
            "gcp_region": "us-central1",
        }

    def test_committed_default_is_parity(self, monkeypatch):
        """With no job DB (no bill derivation), nat_vms = committed default 32 →
        multiplier = 0.044/0.0014 (AWS parity)."""
        monkeypatch.setitem(m._ASSUMPTIONS, "nat_vms_per_gateway", 32.0)
        out = m.map_flat_hourly([self._nat_row()])
        nat = [o for o in out if o.get("gcp_sku_name") == "Networking Cloud Nat Gateway Uptime"]
        assert len(nat) == 1
        assert nat[0]["unit_multiplier"] == pytest.approx(0.044 / 0.0014, rel=1e-4)
        assert "committed default" in nat[0]["projection_note"]

    def test_lower_assumption_scales_down(self, monkeypatch):
        monkeypatch.setitem(m._ASSUMPTIONS, "nat_vms_per_gateway", 10.0)
        out = m.map_flat_hourly([self._nat_row()])
        nat = [o for o in out if o.get("gcp_sku_name") == "Networking Cloud Nat Gateway Uptime"][0]
        assert nat["unit_multiplier"] == pytest.approx((10.0 / 32.0) * (0.044 / 0.0014), rel=1e-4)
