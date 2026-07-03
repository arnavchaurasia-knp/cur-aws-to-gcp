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

# direction -> (canonical_sku_id, human_sku_name, rate_usd_per_gb)
EGRESS_SKUS = {
    "interzone":   ("GCP-EGRESS-INTERZONE",   "Network Inter Zone Egress",   0.01),
    "interregion": ("GCP-EGRESS-INTERREGION", "Network Inter Region Egress", 0.08),
    "internet":    ("GCP-EGRESS-INTERNET",    "Network Internet Egress",     0.12),
}
