"""
test_display_quantity_granularity.py — Phase P3 validation for SKU billing-granularity (L2).

Verifies that:
1. GKE Autopilot mCPU SKU resolves with display_quantity = 1000.0 in catalog / resolve_sku.
2. derive_multiplier("hours", sku.unit, sku.display_quantity) yields 1000.0 without hand-coding 1000.0.
3. Compute Engine core resolves display_quantity = 1.0 and derives multiplier = 1.0.
4. GKE Autopilot Memory resolves display_quantity = 1.0 and derives multiplier = GB_TO_GIB.
"""

import os
import pytest
from scripts.apply_static_mappings import resolve_sku
from scripts.unit_equivalence import derive_multiplier, GB_TO_GIB


def test_autopilot_mcpu_display_quantity():
    # Autopilot Pod mCPU Requests in us-central1
    sku = resolve_sku("Kubernetes Engine", "Autopilot Pod mCPU Requests", "us-central1")
    assert sku is not None, "Autopilot Pod mCPU Requests must resolve in us-central1"
    assert hasattr(sku, "display_quantity"), "SKUMeta must have display_quantity attribute"
    assert sku.display_quantity == 1000.0, f"Expected display_quantity 1000.0, got {sku.display_quantity}"
    assert sku.unit == "h", f"Expected usageUnit 'h', got {sku.unit}"

    # Derive multiplier from physical unit ('hours' -> 'h') + granularity (1000.0)
    # No literal 1000.0 in the mechanic!
    derived_mult = derive_multiplier("hours", sku.unit, sku.display_quantity)
    assert derived_mult == 1000.0, f"Expected derived multiplier 1000.0, got {derived_mult}"


def test_compute_engine_display_quantity_unity():
    # Standard Compute Engine N2 core in us-central1
    sku = resolve_sku("Compute Engine", "N2 Instance Core running in Americas", "us-central1")
    if not sku:
        sku = resolve_sku("Compute Engine", "N2 Instance Core", "us-central1")
    assert sku is not None, "Compute Engine core SKU must resolve"
    assert sku.display_quantity == 1.0, f"Expected display_quantity 1.0 for GCE core, got {sku.display_quantity}"

    derived_mult = derive_multiplier("hours", sku.unit, sku.display_quantity)
    assert derived_mult == 1.0, f"Expected derived multiplier 1.0, got {derived_mult}"


def test_autopilot_memory_display_quantity_unity():
    # Autopilot Pod Memory Requests in us-central1
    sku = resolve_sku("Kubernetes Engine", "Autopilot Pod Memory Requests", "us-central1")
    assert sku is not None, "Autopilot Pod Memory Requests must resolve in us-central1"
    assert sku.display_quantity == 1.0, f"Expected display_quantity 1.0 for Autopilot memory, got {sku.display_quantity}"

    # GB-Hours to GiBy.h (or GBy.h)
    derived_mult = derive_multiplier("gb-hours", sku.unit, sku.display_quantity)
    assert derived_mult == pytest.approx(GB_TO_GIB, rel=1e-6), (
        f"Expected derived multiplier {GB_TO_GIB}, got {derived_mult}"
    )

