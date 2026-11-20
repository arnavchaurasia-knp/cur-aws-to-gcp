#!/usr/bin/env python3
"""
tests/test_unit_table_exhaustive.py — Tests that unit_dimensions.json is exhaustive
across all catalog SKUs and all mapper expected units, and tests scale reconciliation.
Follows WORKORDER_unit_conversion_engine.md Phase P1 (P1.4, P1.5).
"""

import json
import os
import sys
import duckdb
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_HERE)
_SCRIPTS_DIR = os.path.join(_SKILL_DIR, "scripts")
_DATA_DIR = os.path.join(_SKILL_DIR, "data")
sys.path.insert(0, _SCRIPTS_DIR)

from unit_equivalence import (
    unit_dims,
    unit_scale,
    compatible,
    conversion_factor,
    normalize_unit,
)
from resolve_sku import infer_expected_unit


def test_every_catalog_unit_is_in_table():
    """P1.4: Every usage_unit in data/catalog.duckdb has an entry in unit_dimensions.json."""
    catalog_path = os.path.join(_DATA_DIR, "catalog.duckdb")
    con = duckdb.connect(catalog_path, read_only=True)
    rows = con.execute("SELECT DISTINCT usage_unit FROM skus WHERE usage_unit IS NOT NULL AND usage_unit != ''").fetchall()
    con.close()

    assert len(rows) > 0, "No catalog units found in catalog.duckdb"
    missing = []
    for (u,) in rows:
        dims = unit_dims(u)
        scale = unit_scale(u)
        if dims is None or scale is None:
            missing.append(u)

    assert not missing, f"P1.4 Failure: catalog units missing from unit_dimensions.json: {missing}"


def test_all_20_known_catalog_units_typed():
    """P1.4: The 20 distinct usageUnit values from GCP catalog are all typed."""
    known_20 = [
        "By", "GBy", "GBy.h", "GiBy", "GiBy.d", "GiBy.h", "GiBy.mo", "GiBy.s",
        "MiBy", "MiBy.h", "MiBy.s", "TiBy", "count", "d", "h", "min", "mo", "ms", "s", "yr"
    ]
    for u in known_20:
        dims = unit_dims(u)
        scale = unit_scale(u)
        assert dims is not None, f"Catalog unit '{u}' must have dimensions"
        assert scale is not None, f"Catalog unit '{u}' must have scale"
        assert scale > 0, f"Catalog unit '{u}' scale must be positive"


def test_reconcile_scales_gb_to_gib_exact():
    """P1.5: Assert conversion_factor('gb-mo','giby.mo') == conversion_factor('gb-hours','gib.h')
    and that both equal the exact 1/1.073741824.
    """
    exact_gb_to_gib = 1.0 / 1.073741824  # 0.9313225746154785

    factor_mo = conversion_factor("gb-mo", "giby.mo")
    factor_h = conversion_factor("gb-hours", "gib.h")

    assert factor_mo is not None, "conversion_factor('gb-mo', 'giby.mo') must not be None"
    assert factor_h is not None, "conversion_factor('gb-hours', 'gib.h') must not be None"

    assert abs(factor_mo - factor_h) < 1e-12, (
        f"Scale drift detected: factor_mo={factor_mo} vs factor_h={factor_h}"
    )
    assert abs(factor_mo - exact_gb_to_gib) < 1e-12, (
        f"Scale differs from exact 1/1.073741824: got {factor_mo}, expected {exact_gb_to_gib}"
    )


def test_missing_dimensions_added():
    """P1.2: Dimensions throughput, cpu, iops, object exist and are correctly assigned."""
    # Throughput
    assert unit_dims("gibps-mo") == {"throughput": 1, "time": 1}
    assert unit_dims("mibps-mo") == {"throughput": 1, "time": 1}
    factor_tp = conversion_factor("mibps-mo", "gibps-mo")
    assert factor_tp is not None
    assert abs(factor_tp - (1.0 / 1024.0)) < 1e-9

    # CPU
    assert unit_dims("vcpu") == {"cpu": 1}
    assert unit_dims("mcpu") == {"cpu": 1}
    assert unit_dims("vcpu-hours") == {"cpu": 1, "time": 1}
    assert abs(conversion_factor("mcpu", "vcpu") - 0.001) < 1e-9

    # IOPS
    assert unit_dims("iops") == {"iops": 1}
    assert unit_dims("iops-mo") == {"iops": 1, "time": 1}

    # Object
    assert unit_dims("objects") == {"object": 1}
    assert unit_dims("object") == {"object": 1}


def test_generated_header_present():
    """P1.3: data/unit_dimensions.json contains the GENERATED notice."""
    path = os.path.join(_DATA_DIR, "unit_dimensions.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    meta = data.get("_meta", {})
    notice = meta.get("notice", "")
    assert "GENERATED — do not edit" in notice

