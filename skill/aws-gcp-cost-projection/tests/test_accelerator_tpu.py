#!/usr/bin/env python3
"""
Unit tests for AWS Inferentia/Trainium mapping to Cloud TPU v5e in family_mapper.py.

Cover:
1. inf2.48xlarge row -> one entry, gcp_service=="Cloud TPU", unit_multiplier==12,
   strategy=="map", mapping_confidence==0.3, note contains "chip-count parity".
2. trn1.32xlarge -> unit_multiplier==16.
3. No separate core/ram component is emitted for a TPU row.
4. An inf*/trn* instance NOT in accelerator-chips.json -> chips==1, note says assumed.
5. Missing/empty tpu-rates -> strategy falls back to "passthrough" (no crash).
6. dl1.24xlarge (Gaudi) -> NOT mapped to Cloud TPU (stays passthrough / no-equivalent).

Runnable as:
  pytest tests/test_accelerator_tpu.py -q
  python tests/test_accelerator_tpu.py
"""
from __future__ import annotations
import os
import sys
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from conftest import row
import duckdb
import family_mapper as fm
import apply_rates


def test_inf2_48xlarge_mapping():
    r = row(
        aws_li_key="k_inf2_48xl",
        instance_type="inf2.48xlarge",
        instance_vcpus=192,
        instance_ram_gb=768.0,
        gcp_region="us-central1",
    )
    parsed = fm.parse_instance("inf2.48xlarge")
    assert fm._accel_kind(parsed, "inf2.48xlarge") == "tpu_mappable"

    res = fm.map_tpu_row(r, parsed, "inf2.48xlarge")
    assert len(res) == 1, f"Expected exactly 1 component for TPU row, got {len(res)}"
    entry = res[0]
    assert entry["gcp_service"] == "Cloud TPU"
    assert entry["gcp_sku_id"] == "TPU-V5E-ONDEMAND"
    assert entry["component"] == "accelerator"
    assert entry["unit_multiplier"] == 12.0
    assert entry["strategy"] == "map"
    assert entry["mapping_confidence"] == 0.3
    assert "chip-count parity" in entry["projection_note"]
    assert "12 chips" in entry["projection_note"]
    assert "Inferentia2" in entry["projection_note"]


def test_trn1_32xlarge_mapping():
    r = row(
        aws_li_key="k_trn1_32xl",
        instance_type="trn1.32xlarge",
        instance_vcpus=128,
        instance_ram_gb=512.0,
        gcp_region="us-east5",
    )
    parsed = fm.parse_instance("trn1.32xlarge")
    assert fm._accel_kind(parsed, "trn1.32xlarge") == "tpu_mappable"

    res = fm.map_tpu_row(r, parsed, "trn1.32xlarge")
    assert len(res) == 1
    entry = res[0]
    assert entry["gcp_service"] == "Cloud TPU"
    assert entry["unit_multiplier"] == 16.0
    assert entry["strategy"] == "map"
    assert "Trainium1" in entry["projection_note"]


def test_no_separate_core_ram_components_emitted():
    r = row(
        aws_li_key="k_inf1_xl",
        instance_type="inf1.xlarge",
        instance_vcpus=4,
        instance_ram_gb=8.0,
    )
    parsed = fm.parse_instance("inf1.xlarge")
    res = fm.map_tpu_row(r, parsed, "inf1.xlarge")

    components = [e["component"] for e in res]
    assert components == ["accelerator"], f"Expected only accelerator component, got {components}"
    assert "core" not in components
    assert "ram" not in components


def test_unlisted_instance_assumes_chips_1():
    r = row(
        aws_li_key="k_inf_custom",
        instance_type="inf99.custom",
        instance_vcpus=8,
        instance_ram_gb=32.0,
    )
    parsed = fm.parse_instance("inf99.custom")
    assert fm._accel_kind(parsed, "inf99.custom") == "tpu_mappable"

    res = fm.map_tpu_row(r, parsed, "inf99.custom")
    assert len(res) == 1
    entry = res[0]
    assert entry["unit_multiplier"] == 1.0
    assert "chip count assumed = 1" in entry["projection_note"]


def test_missing_tpu_rates_falls_back_to_passthrough():
    r = row(
        aws_li_key="k_inf1_xl",
        instance_type="inf1.xlarge",
        instance_vcpus=4,
        instance_ram_gb=8.0,
        gcp_region="unknown-region-99",
    )
    parsed = fm.parse_instance("inf1.xlarge")

    with patch.dict(fm._TPU_RATES_CFG, {"by_region": {}, "default_usd_per_chip_hour": None}, clear=True):
        res = fm.map_tpu_row(r, parsed, "inf1.xlarge")
        assert len(res) == 1
        assert res[0]["strategy"] == "passthrough"


def test_dl1_gaudi_not_mapped_to_tpu():
    r = row(
        aws_li_key="k_dl1_24xl",
        instance_type="dl1.24xlarge",
        instance_vcpus=96,
        instance_ram_gb=768.0,
    )
    parsed = fm.parse_instance("dl1.24xlarge")
    # Must NOT be identified as tpu_mappable
    assert fm._accel_kind(parsed, "dl1.24xlarge") != "tpu_mappable"

    # Even if dl1 has accelerator 'Habana Gaudi' in entry or spec, it must never map to Cloud TPU
    r_accel = dict(r)
    with patch.dict(fm._EC2_TYPES, {"dl1.24xlarge": {"accelerator": "8x Habana Gaudi", "vcpus": 96, "ram_gb": 768.0}}):
        res = fm.map_gpu_row(r_accel, parsed)
        assert res is not None
        for entry in res:
            assert entry["gcp_service"] != "Cloud TPU"
            assert entry["strategy"] == "passthrough"


def test_compute_mapper_kind_dispatch():
    """Regression guard for the deleted `is_gpu` NameError: the dispatch decision
    must be defined and correct for CPU, GPU, and TPU-class rows. (Tests the
    extracted _compute_mapper_kind so main()'s loop can't regress silently.)"""
    cases = {
        "m5.xlarge": "gce",
        "c7g.4xlarge": "gce",
        "g5.xlarge": "gpu",
        "p4d.24xlarge": "gpu",
        "inf2.48xlarge": "tpu",
        "trn1.32xlarge": "tpu",
    }
    for itype, expected in cases.items():
        parsed = fm.parse_instance(itype)
        got = fm._compute_mapper_kind(parsed, itype)
        assert got == expected, f"{itype}: expected {expected}, got {got}"


def test_tpu_rate_injected_into_gcp_sku_rates():
    """F2 guard: inject_tpu_rates() must write the pinned TPU rate (per-region +
    global) so the projection view can resolve it instead of NULL->passthrough."""
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id VARCHAR, gcp_service VARCHAR, gcp_sku_name VARCHAR,
            resource_family VARCHAR, resource_group VARCHAR, pricing_type VARCHAR,
            region VARCHAR, unit VARCHAR, rate_usd DOUBLE, source VARCHAR, audit_url VARCHAR,
            PRIMARY KEY (gcp_sku_id, pricing_type, region)
        )
        """
    )
    apply_rates.inject_tpu_rates(con)
    rows = con.execute(
        "SELECT region, rate_usd, gcp_service, pricing_type, unit FROM gcp_sku_rates WHERE gcp_sku_id = ?",
        [apply_rates._TPU_SKU_ID],
    ).fetchall()
    con.close()
    assert rows, "inject_tpu_rates wrote no TPU rows"
    by_region = {r[0]: r[1] for r in rows}
    assert "global" in by_region, "missing 'global' fallback rate"
    assert "us-central1" in by_region
    assert by_region["us-central1"] == 1.20
    assert by_region["global"] == 1.20
    for _region, _rate, svc, ptype, unit in rows:
        assert svc == "Cloud TPU" and ptype == "OnDemand" and unit == "chip-hour"


def main():
    tests = [
        ("inf2.48xlarge maps to Cloud TPU with 12 chips", test_inf2_48xlarge_mapping),
        ("trn1.32xlarge maps to Cloud TPU with 16 chips", test_trn1_32xlarge_mapping),
        ("No core/ram components emitted for TPU row", test_no_separate_core_ram_components_emitted),
        ("Unlisted accelerator instance assumes chips=1", test_unlisted_instance_assumes_chips_1),
        ("Missing TPU rates falls back to passthrough", test_missing_tpu_rates_falls_back_to_passthrough),
        ("dl1 (Gaudi) not mapped to Cloud TPU", test_dl1_gaudi_not_mapped_to_tpu),
        ("compute mapper dispatch kind (CPU/GPU/TPU)", test_compute_mapper_kind_dispatch),
        ("TPU rate injected into gcp_sku_rates", test_tpu_rate_injected_into_gcp_sku_rates),
    ]

    print(f"{'Test Case':<60} {'Status'}")
    print("-" * 70)
    all_passed = True
    for name, fn in tests:
        try:
            fn()
            print(f"{name:<60} PASS")
        except Exception as e:
            print(f"{name:<60} FAIL: {e}")
            all_passed = False

    print("-" * 70)
    if all_passed:
        print("All tests passed.")
        return 0
    else:
        print("Some tests failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
