#!/usr/bin/env python3
"""
synthetic_skus.py — Single source of truth for synthetic GCP SKU IDs.

Synthetic SKU IDs are deterministic rate placeholders injected directly into
gcp_sku_rates during apply_rates.py (e.g. pinned egress rates, Looker Studio Pro,
TPU v5e). They are NOT present in the bundled catalog.duckdb skus table.

Both merge_mappings.py (for whitelist validation against phantom nulling) and
apply_rates.py (for rate gap reporting and injection) import from here.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from egress_rates import EGRESS_SKUS, CDN_EGRESS_TIERS
    _EGRESS_IDS = {sku_id for sku_id, _, _ in EGRESS_SKUS.values()}
    _CDN_IDS = {v["sku_id"] for v in CDN_EGRESS_TIERS.values() if isinstance(v, dict) and "sku_id" in v}
except Exception:
    _EGRESS_IDS = {
        "GCP-EGRESS-INTERZONE",
        "GCP-EGRESS-INTERREGION",
        "GCP-EGRESS-INTERNET",
    }
    _CDN_IDS = {
        "GCP-CDN-EGRESS-APAC",
        "GCP-CDN-EGRESS-AMERICAS",
        "GCP-CDN-EGRESS-EMEA",
        "GCP-CDN-EGRESS-APAC-AU",
        "GCP-CDN-EGRESS-CHINA",
    }

# Pinned SaaS / non-catalog services:
# - Looker Studio Pro: SaaS seat pricing outside Google Cloud Billing catalog.
# - Cloud TPU v5e: Pinned on-demand chip-hour rate (data/tpu-rates.json).
_PINNED_SYNTHETIC_IDS = {
    "GCP-LOOKER-STUDIO-PRO-USER",
    "TPU-V5E-ONDEMAND",
}

SYNTHETIC_SKU_IDS: set[str] = _EGRESS_IDS | _CDN_IDS | _PINNED_SYNTHETIC_IDS


def is_synthetic_sku(sku_id: str | None) -> bool:
    """Return True if sku_id is a recognized synthetic SKU ID."""
    return bool(sku_id and sku_id in SYNTHETIC_SKU_IDS)
