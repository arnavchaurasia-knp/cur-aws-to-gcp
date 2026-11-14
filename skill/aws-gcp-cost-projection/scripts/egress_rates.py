#!/usr/bin/env python3
"""
egress_rates.py — canonical GCP network-egress rates by direction.

Data-transfer egress cannot be resolved by fuzzy catalog SKU-name matching: the
catalog holds a region-pair explosion of "Network Inter Region Data Transfer Out
from <A> to <B>" SKUs, so a generic name like "inter-zone egress" word-overlap-
matches a different one each run (observed 2x on one run, 8x on the next — both
wrong). Egress pricing is stable and published, so we pin it to these canonical
rates instead: deterministic (same input → same rate) and correct.

Ingress is free on GCP and is handled upstream as strategy='ignore'.

Rates are tier-0 $/GB (published GCP list, verified against the bundled catalog):
  - inter-zone (between zones, same region):        $0.01/GB
  - inter-region (cross-region egress):             $0.08/GB  (catalog-observed)
  - internet egress (to the public internet):       $0.12/GB  (first tier)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_loader import load_data_config as _cfg

_egress_cfg = _cfg("egress-rates").get("egress_skus", {})

# direction -> (canonical_sku_id, human_sku_name, rate_usd_per_gb)
EGRESS_SKUS = {
    direction: (v["sku_id"], v["sku_name"], v["rate_usd_per_gb"])
    for direction, v in _egress_cfg.items()
} if _egress_cfg else {
    "interzone":   ("GCP-EGRESS-INTERZONE",   "Network Inter Zone Egress",   0.01),
    "interregion": ("GCP-EGRESS-INTERREGION", "Network Inter Region Egress", 0.08),
    "internet":    ("GCP-EGRESS-INTERNET",    "Network Internet Egress",     0.12),
}

# Cloud CDN Cache Egress — tiered rates per destination bucket.
# AWS CloudFront destination codes (IN, EU, US, …) map to these buckets.
# Tiers are upper-bound GB thresholds; None = unbounded (last tier).
# Source: cloud.google.com/cdn/pricing — verify current rates before finalizing.
_cdn_cfg = {
    k: v for k, v in _cfg("egress-rates").get("cdn_egress", {}).items()
    if not k.startswith("_")
}
CDN_EGRESS_TIERS = _cdn_cfg if _cdn_cfg else {
    "apac": {
        "sku_id": "GCP-CDN-EGRESS-APAC", "sku_name": "Cloud CDN Cache Egress - APAC",
        "tiers": [
            {"up_to_gb": 10240,  "rate_usd_per_gb": 0.08},
            {"up_to_gb": 153600, "rate_usd_per_gb": 0.06},
            {"up_to_gb": None,   "rate_usd_per_gb": 0.05},
        ],
    },
    "americas": {
        "sku_id": "GCP-CDN-EGRESS-AMERICAS", "sku_name": "Cloud CDN Cache Egress - Americas",
        "tiers": [
            {"up_to_gb": 10240,  "rate_usd_per_gb": 0.08},
            {"up_to_gb": 153600, "rate_usd_per_gb": 0.06},
            {"up_to_gb": None,   "rate_usd_per_gb": 0.05},
        ],
    },
    "emea": {
        "sku_id": "GCP-CDN-EGRESS-EMEA", "sku_name": "Cloud CDN Cache Egress - EMEA",
        "tiers": [
            {"up_to_gb": 10240,  "rate_usd_per_gb": 0.08},
            {"up_to_gb": 153600, "rate_usd_per_gb": 0.06},
            {"up_to_gb": None,   "rate_usd_per_gb": 0.05},
        ],
    },
    "apac_au": {
        "sku_id": "GCP-CDN-EGRESS-APAC-AU", "sku_name": "Cloud CDN Cache Egress - Australia",
        "tiers": [
            {"up_to_gb": 10240,  "rate_usd_per_gb": 0.12},
            {"up_to_gb": 153600, "rate_usd_per_gb": 0.09},
            {"up_to_gb": None,   "rate_usd_per_gb": 0.07},
        ],
    },
    "china": {
        "sku_id": "GCP-CDN-EGRESS-CHINA", "sku_name": "Cloud CDN Cache Egress - China",
        "tiers": [
            {"up_to_gb": 512,  "rate_usd_per_gb": 0.20},
            {"up_to_gb": 2048, "rate_usd_per_gb": 0.18},
            {"up_to_gb": None, "rate_usd_per_gb": 0.17},
        ],
    },
}


# Catalog SKU ID for inter-zone traffic: $0.01/GiBy worldwide.
# Official rate: https://cloud.google.com/vpc/network-pricing
# Note: Google Cloud charges $0.01/GiB for egress between zones in the same region worldwide.
# Only traffic within the same zone using internal IP is free.
# The catalog SKU C1F1-02CA-F355 (published as $0.00) is misleading and must not be used.
_INTERZONE_SKU_PAID = "DE9E-AFBC-A15A"


def interzone_sku_for_region(gcp_region=None):
    """Return (sku_id, sku_name, rate_usd_per_gb) for inter-zone egress in gcp_region.

    Inter-zone egress between zones in the same region is billed at $0.01/GiB
    across all regions.
    Reference: https://cloud.google.com/vpc/network-pricing
    """
    return (_INTERZONE_SKU_PAID, "Network Inter Zone Data Transfer Out", 0.01)


def cdn_egress_rate(bucket, total_gb):
    """Return (sku_id, sku_name, rate_usd_per_gb) for the cheapest applicable tier.

    bucket      — CDN destination bucket key (apac, americas, emea, apac_au, china)
    total_gb    — total monthly GB for this destination (used for tier placement)
    """
    info = CDN_EGRESS_TIERS.get(bucket) or CDN_EGRESS_TIERS.get("apac")
    for tier in info["tiers"]:
        if tier["up_to_gb"] is None or total_gb <= tier["up_to_gb"]:
            return info["sku_id"], info["sku_name"], tier["rate_usd_per_gb"]
    last = info["tiers"][-1]
    return info["sku_id"], info["sku_name"], last["rate_usd_per_gb"]


# Internet Egress (Premium Tier) — tiered rates per destination continent and monthly volume.
# Source: cloud.google.com/vpc/network-pricing
_internet_cfg = {
    k: v for k, v in _cfg("egress-rates").get("internet_egress", {}).items()
    if not k.startswith("_")
}
INTERNET_EGRESS_TIERS = _internet_cfg

# Inter-Region Egress Matrix — $/GiB by continent pair (from_continent -> to_continent).
# Source: cloud.google.com/vpc/network-pricing
_interregion_cfg = {
    k: v for k, v in _cfg("egress-rates").get("interregion_matrix", {}).items()
    if not k.startswith("_")
}
INTERREGION_MATRIX = _interregion_cfg


def region_to_continent(region: str | None) -> str:
    """Map a GCP or normalized region string to its pricing continent.
    Reference: https://cloud.google.com/vpc/network-pricing
    """
    if not region:
        return "northamerica"
    r = str(region).strip().lower()
    if r.startswith("southamerica-"):
        return "southamerica"
    if r.startswith("us-") or r.startswith("northamerica-"):
        return "northamerica"
    if r.startswith("europe-"):
        return "europe"
    if r == "asia-southeast2":
        return "indonesia"
    if r == "asia-northeast3":
        return "korea"
    if r.startswith("australia-") or r.startswith("australasia-"):
        return "oceania"
    if r.startswith("asia-"):
        return "asia"
    if r == "me-central2":
        return "saudi"
    if r.startswith("me-"):
        return "middleeast"
    if r.startswith("africa-"):
        return "africa"
    return "northamerica"


def dest_bucket_for_internet(parts: dict) -> str:
    """Infer destination continent bucket for internet egress from row fields.
    Buckets: americas, europe, asia, australia_special, middleeast_africa, china.
    Reference: https://cloud.google.com/vpc/network-pricing
    """
    to_reg = parts.get("to_region")
    if to_reg:
        cont = region_to_continent(to_reg)
        if cont in ("oceania", "indonesia", "korea", "southamerica", "saudi"):
            return "australia_special"
        if cont in ("middleeast", "africa"):
            return "middleeast_africa"
        if cont == "china":
            return "china"
        if cont == "europe":
            return "europe"
        if cont == "asia":
            return "asia"
        return "americas"

    ut = str(parts.get("usage_type") or "").lower()
    op = str(parts.get("operation") or "").lower()
    prod = str(parts.get("product") or "").lower()
    blob = f"{ut} {op} {prod}"

    if any(k in blob for k in ("china", "-cn-", "(cn)")):
        return "china"
    if any(k in blob for k in ("australia", "sydney", "melbourne", "-au-", "(au)", "indonesia", "jakarta", "korea", "seoul", "saudi", "dammam", "southamerica", "sao paulo", "chile")):
        return "australia_special"
    if any(k in blob for k in ("middleeast", "middle-east", "dubai", "doha", "tel aviv", "africa", "johannesburg", "me-", "af-")):
        return "middleeast_africa"
    if any(k in blob for k in ("europe", "frankfurt", "london", "ireland", "paris", "amsterdam", "milan", "madrid", "-eu-", "(eu)", "eu-", "euc", "euw")):
        return "europe"
    if any(k in blob for k in ("asia", "tokyo", "singapore", "hong kong", "mumbai", "delhi", "osaka", "taiwan", "-ap-", "(ap)", "aps", "apn")):
        return "asia"
    return "americas"


def internet_egress_rate(bucket: str | None, total_gb: float) -> tuple[str, str, float]:
    """Return (sku_id, sku_name, rate_usd_per_gb) for Premium Tier internet egress.

    bucket   — Destination bucket: americas, europe, asia, australia_special, middleeast_africa, china
    total_gb — Total monthly volume in GB for tier placement (0-1 TB / 1-10 TB / >10 TB)
    Reference: https://cloud.google.com/vpc/network-pricing
    """
    b = bucket or "americas"
    info = INTERNET_EGRESS_TIERS.get(b) or INTERNET_EGRESS_TIERS.get("americas")
    if not info:
        return EGRESS_SKUS["internet"]

    for tier in info["tiers"]:
        if tier["up_to_gb"] is None or total_gb <= tier["up_to_gb"]:
            label = f"{info['sku_name']} (Tier <= {tier['up_to_gb']} GB)" if tier["up_to_gb"] else f"{info['sku_name']} (Tier > 10 TB)"
            return tier["sku_id"], label, tier["rate_usd_per_gb"]
    last = info["tiers"][-1]
    return last["sku_id"], f"{info['sku_name']} (Tier > 10 TB)", last["rate_usd_per_gb"]


def interregion_rate(from_region: str | None, to_region: str | None) -> tuple[str, str, float]:
    """Return (sku_id, sku_name, rate_usd_per_gb) for inter-region egress from continent pair matrix.
    Reference: https://cloud.google.com/vpc/network-pricing
    """
    from_c = region_to_continent(from_region)
    to_c = region_to_continent(to_region)

    matrix_row = INTERREGION_MATRIX.get(from_c) or INTERREGION_MATRIX.get("northamerica", {})
    rate = matrix_row.get(to_c)
    if rate is None:
        rate = 0.08  # Default published fallback

    sku_id = f"GCP-EGRESS-INTERREGION-{from_c[:4].upper()}-{to_c[:4].upper()}"
    sku_name = f"Network Inter-Region Egress ({from_c.title()} to {to_c.title()})"
    return (sku_id, sku_name, rate)
