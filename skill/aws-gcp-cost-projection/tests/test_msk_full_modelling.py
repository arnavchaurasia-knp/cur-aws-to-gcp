"""Tests for MSK full modelling per WORKORDER §4.22.

Retargets MSK broker-hours and Connect workers to GCP's Managed Service for Apache Kafka
DCU (DCU = 0.6 * vCPU + 0.1 * RAM_GiB) with synthetic rate injection:
- Broker CPU+RAM: $0.09/DCU-h (KAFKA-DCU-CPURAM)
- Connect CPU+RAM: $0.12/DCU-h (KAFKA-DCU-CONNECT)
- Local Storage: $0.000232877/GiB-h (~$0.17/GiB-mo, KAFKA-LOCAL-STORAGE)
- Unknown instance type: self-hosted GCE passthrough fallback
"""

import pytest
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
sys.path.insert(0, _SCRIPTS)

import apply_static_mappings as asm


def _row(**kw):
    return {
        "aws_li_key": kw.get("aws_li_key", "k_msk"),
        "product": kw.get("product", "Amazon Managed Streaming for Apache Kafka"),
        "product_code": kw.get("product_code", "AmazonMSK"),
        "usage_type": kw.get("usage_type", ""),
        "operation": kw.get("operation", "RunBroker"),
        "unit": kw.get("unit", "Hrs"),
        "total_usage": kw.get("total_usage", 100.0),
        "aws_amortized_cost": kw.get("aws_amortized_cost", 50.0),
        "gcp_region": kw.get("gcp_region", "us-central1"),
    }


def test_msk_broker_hours_maps_to_kafka_dcu():
    """kafka.m5.large (2 vCPU, 8 GiB) -> 0.6*2 + 0.1*8 = 2.0 DCU at $0.09/DCU-h."""
    r = _row(
        usage_type="APS5-Kafka.m5.large",
        operation="RunBroker",
        unit="Hrs",
    )
    res = asm.map_msk([r])
    assert len(res) == 1
    out = res[0]
    assert out["strategy"] == "map"
    assert out["gcp_service"] == "Managed Service for Apache Kafka"
    assert out["gcp_sku_id"] == "KAFKA-DCU-CPURAM"
    assert out["component"] == "broker"
    assert pytest.approx(out["unit_multiplier"], rel=1e-5) == 2.0
    assert "DCU=2.00" in out["projection_note"]
    assert "$0.09/DCU-h" in out["projection_note"]


def test_msk_broker_xlarge_dcu():
    """kafka.m5.xlarge (4 vCPU, 16 GiB) -> 0.6*4 + 0.1*16 = 4.0 DCU."""
    r = _row(
        usage_type="APS5-Kafka.m5.xlarge",
        operation="RunBroker",
        unit="Hrs",
    )
    res = asm.map_msk([r])
    assert len(res) == 1
    out = res[0]
    assert out["strategy"] == "map"
    assert out["gcp_sku_id"] == "KAFKA-DCU-CPURAM"
    assert pytest.approx(out["unit_multiplier"], rel=1e-5) == 4.0


def test_msk_burstable_t3_small():
    """kafka.t3.small (2 vCPU, 2 GiB) -> 0.6*2 + 0.1*2 = 1.4 DCU."""
    r = _row(
        usage_type="APS5-Kafka.t3.small",
        operation="RunBroker",
        unit="Hrs",
    )
    res = asm.map_msk([r])
    assert len(res) == 1
    out = res[0]
    assert out["strategy"] == "map"
    assert out["gcp_sku_id"] == "KAFKA-DCU-CPURAM"
    assert pytest.approx(out["unit_multiplier"], rel=1e-5) == 1.4


def test_msk_connect_worker_mcu():
    """MSK Connect (Kafka.mcu.general, 1 vCPU, 4 GiB) -> 1.0 DCU at $0.12/DCU-h."""
    r = _row(
        usage_type="APS5-Kafka.mcu.general",
        operation="RunConnect",
        unit="Hrs",
    )
    res = asm.map_msk([r])
    assert len(res) == 1
    out = res[0]
    assert out["strategy"] == "map"
    assert out["gcp_service"] == "Managed Service for Apache Kafka"
    assert out["gcp_sku_id"] == "KAFKA-DCU-CONNECT"
    assert out["component"] == "connect"
    assert pytest.approx(out["unit_multiplier"], rel=1e-5) == 1.0
    assert "$0.12/DCU-h" in out["projection_note"]


def test_msk_storage_maps_to_local_storage():
    """MSK broker storage (GB-Mo) -> Managed Service for Apache Kafka Local Storage (mult=GB_TO_GIB)."""
    r = _row(
        usage_type="APS5-Kafka.Storage",
        operation="",
        unit="GB-Mo",
    )
    res = asm.map_msk([r])
    assert len(res) == 1
    out = res[0]
    assert out["strategy"] == "map"
    assert out["gcp_service"] == "Managed Service for Apache Kafka"
    assert out["gcp_sku_id"] == "KAFKA-LOCAL-STORAGE"
    assert out["component"] == "storage"
    assert pytest.approx(out["unit_multiplier"], rel=1e-5) == asm.GB_TO_GIB
    assert "$0.17/GiB-mo" in out["projection_note"]


def test_msk_unknown_instance_type_fallback():
    """Unknown Kafka instance type falls back to Compute Engine passthrough (self-hosted GCE proxy)."""
    r = _row(
        usage_type="APS5-Kafka.nonexistent.99xlarge",
        operation="RunBroker",
        unit="Hrs",
    )
    res = asm.map_msk([r])
    assert len(res) == 1
    out = res[0]
    assert out["strategy"] == "passthrough"
    assert out["gcp_service"] == "Compute Engine"
    assert out["gcp_sku_id"] is None
    assert "self-hosted GCE fallback" in out["projection_note"]


def test_msk_mapping_invariants():
    """Invariant: every mapped MSK row has a valid gcp_sku_id (never empty)."""
    rows = [
        _row(usage_type="APS5-Kafka.m5.large"),
        _row(usage_type="APS5-Kafka.mcu.general", operation="RunConnect"),
        _row(usage_type="APS5-Kafka.Storage", unit="GB-Mo"),
    ]
    res = asm.map_msk(rows)
    for r in res:
        if r["strategy"] == "map":
            assert r.get("gcp_sku_id"), f"Mapped row missing gcp_sku_id: {r}"
            assert r.get("gcp_service") == "Managed Service for Apache Kafka"

