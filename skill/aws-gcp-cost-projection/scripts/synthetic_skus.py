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
    from egress_rates import (
        EGRESS_SKUS,
        CDN_EGRESS_TIERS,
        INTERNET_EGRESS_TIERS,
        INTERREGION_MATRIX,
    )
    _EGRESS_IDS = {sku_id for sku_id, _, _ in EGRESS_SKUS.values()}
    _CDN_IDS = {v["sku_id"] for v in CDN_EGRESS_TIERS.values() if isinstance(v, dict) and "sku_id" in v}
    _INTERNET_IDS = {
        tier["sku_id"]
        for b_info in INTERNET_EGRESS_TIERS.values() if isinstance(b_info, dict)
        for tier in b_info.get("tiers", []) if isinstance(tier, dict) and "sku_id" in tier
    }
    _INTERREGION_IDS = {
        f"GCP-EGRESS-INTERREGION-{from_c[:4].upper()}-{to_c[:4].upper()}"
        for from_c, dest_map in INTERREGION_MATRIX.items() if isinstance(dest_map, dict)
        for to_c in dest_map.keys()
    }
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
    _INTERNET_IDS = set()
    _INTERREGION_IDS = set()

# Pinned SaaS / non-catalog services:
# - Looker Studio Pro: SaaS seat pricing outside Google Cloud Billing catalog.
# - Cloud TPU v5e: Pinned on-demand chip-hour rate (data/tpu-rates.json).
# - Managed Service for Apache Kafka: DCU and storage rates (§4.22).
# - NetApp Volumes & Parallelstore: synthetic storage rates (§4.23).
# - Oracle Database@Google Cloud: Base Database Service ECPU rates.
_PINNED_SYNTHETIC_IDS = {
    "GCP-LOOKER-STUDIO-PRO-USER",
    "TPU-V5E-ONDEMAND",
    # Managed Service for Apache Kafka (§4.22)
    "KAFKA-DCU-CPURAM",
    "KAFKA-DCU-CONNECT",
    "KAFKA-LOCAL-STORAGE",
    "KAFKA-LONGTERM-STORAGE",
    # NetApp Volumes & Parallelstore (§4.23)
    "NETAPP-STANDARD",
    "NETAPP-PREMIUM",
    "NETAPP-EXTREME",
    "NETAPP-AUTOTIER-COLD",
    "PARALLELSTORE-CAPACITY",
    # Oracle Database@Google Cloud
    "ORACLE-BASE-DB-SE-ECPU",
    "ORACLE-BASE-DB-EE-ECPU",
    "ORACLE-BASE-DB-BYOL-ECPU",
    "ORACLE-BASE-DB-HP-ECPU",
}

SYNTHETIC_SKU_IDS: set[str] = _EGRESS_IDS | _CDN_IDS | _PINNED_SYNTHETIC_IDS | _INTERNET_IDS | _INTERREGION_IDS


def is_synthetic_sku(sku_id: str | None) -> bool:
    """Return True if sku_id is a recognized synthetic SKU ID."""
    if not sku_id:
        return False
    if sku_id in SYNTHETIC_SKU_IDS:
        return True
    if (
        sku_id.startswith("GCP-EGRESS-")
        or sku_id.startswith("GCP-CDN-")
        or sku_id.startswith("KAFKA-")
        or sku_id.startswith("NETAPP-")
        or sku_id.startswith("PARALLELSTORE-")
        or sku_id.startswith("ORACLE-")
    ):
        return True
    return False
