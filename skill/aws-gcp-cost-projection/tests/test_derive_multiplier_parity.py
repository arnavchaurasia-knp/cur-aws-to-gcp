#!/usr/bin/env python3
"""
tests/test_derive_multiplier_parity.py — Proves parity between legacy hand-coded constants
and the derive_multiplier engine. Follows WORKORDER_unit_conversion_engine.md Phase P2 (P2.2).
"""

import math
import os
import sys
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.join(os.path.dirname(_HERE), "scripts")
sys.path.insert(0, _SCRIPTS_DIR)

from unit_equivalence import derive_multiplier, conversion_factor, GB_TO_GIB


def test_derive_multiplier_gb_to_gib_parity():
    """Engine returns exact 1 / 1.073741824 for decimal GB -> binary GiB."""
    exact = 1.0 / 1.073741824  # 0.9313225746154785
    assert derive_multiplier("gb-mo", "giby.mo") == pytest.approx(exact, rel=1e-12)
    assert derive_multiplier("gb-hours", "giby.h") == pytest.approx(exact, rel=1e-12)
    assert derive_multiplier("gb", "gib") == pytest.approx(exact, rel=1e-12)
    assert GB_TO_GIB == pytest.approx(exact, rel=1e-12)


def test_derive_multiplier_throughput_parity():
    """Engine returns 1024.0 for GiBps->MiBps and 1/1024.0 for MiBps->GiBps."""
    # 1024.0 (GiB/s to MiB/s)
    factor_down = derive_multiplier("gibps-mo", "mibps-mo")
    assert factor_down == pytest.approx(1024.0, rel=1e-12)

    # 1/1024.0 (MiB/s to GiB/s)
    factor_up = derive_multiplier("mibps-mo", "gibps-mo")
    assert factor_up == pytest.approx(1.0 / 1024.0, rel=1e-12)


def test_derive_multiplier_time_conversions():
    """Engine correctly converts between time units and storage-duration units."""
    # Month to Hour (730.0)
    assert derive_multiplier("mo", "h") == pytest.approx(730.0, rel=1e-12)
    assert derive_multiplier("giby.mo", "giby.h") == pytest.approx(730.0, rel=1e-12)

    # Hour to Month (1 / 730.0)
    assert derive_multiplier("h", "mo") == pytest.approx(1.0 / 730.0, rel=1e-12)
    assert derive_multiplier("giby.h", "giby.mo") == pytest.approx(1.0 / 730.0, rel=1e-12)

    # Day to Hour (24.0)
    assert derive_multiplier("d", "h") == pytest.approx(24.0, rel=1e-12)
    assert derive_multiplier("giby.d", "giby.h") == pytest.approx(24.0, rel=1e-12)

    # Second to Hour (1 / 3600.0)
    assert derive_multiplier("s", "h") == pytest.approx(1.0 / 3600.0, rel=1e-12)
    assert derive_multiplier("giby.s", "giby.h") == pytest.approx(1.0 / 3600.0, rel=1e-12)


def test_derive_multiplier_cpu_granularity():
    """Engine converts vCPU <-> mCPU (millicores)."""
    assert derive_multiplier("mcpu", "vcpu") == pytest.approx(0.001, rel=1e-12)
    assert derive_multiplier("vcpu", "mcpu") == pytest.approx(1000.0, rel=1e-12)
    assert derive_multiplier("mcpu-hours", "vcpu-hours") == pytest.approx(0.001, rel=1e-12)
    assert derive_multiplier("vcpu-hours", "mcpu-hours") == pytest.approx(1000.0, rel=1e-12)


def test_derive_multiplier_lambda_gb_seconds():
    """Engine converts Lambda-GB-Second to GB-Mo and GiBy.mo."""
    # 1 GB-Month = 730 * 3600 = 2,628,000 Lambda-GB-Seconds
    factor_sec_to_mo = derive_multiplier("lambda-gb-second", "gb-mo")
    assert factor_sec_to_mo == pytest.approx(1.0 / 2628000.0, rel=1e-12)

    factor_mo_to_sec = derive_multiplier("gb-mo", "lambda-gb-second")
    assert factor_mo_to_sec == pytest.approx(2628000.0, rel=1e-12)


def test_derive_multiplier_rejects_incompatible():
    """Engine returns None when units are dimensionally incompatible."""
    assert derive_multiplier("Hrs", "GiBy.h") is None
    assert derive_multiplier("count", "GiBy") is None
    assert derive_multiplier("iops", "gibps-mo") is None
    assert derive_multiplier("vcpu", "GiBy") is None
    assert derive_multiplier("gibps-mo", "GiBy.mo") is None
    assert derive_multiplier(None, "GiBy") is None
    assert derive_multiplier("Hrs", None) is None
    assert derive_multiplier("unknown_unit_xyz", "h") is None

