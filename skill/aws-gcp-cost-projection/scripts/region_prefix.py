#!/usr/bin/env python3
"""
Shared AWS usage_type region-prefix decoder.

PDF/flat-CSV bills often omit a dedicated Region column; the region is instead
embedded as a prefix on usage_type (e.g. "APS3-EBS:VolumeUsage.gp3" → Mumbai,
"APS5-Lambda-GB-Second-ARM" → Delhi/Hyderabad). This used to be decoded inside
individual mapper functions (map_block_storage, the MSK mapper, family_mapper's
EC2 path) — anything NOT covered by one of those (Lambda/Cloud Run rows, S3
storage rows, ...) silently kept gcp_region='global', which then breaks
description-based SKU resolution downstream: most GCP catalog SKU descriptions
embed a literal region name ("Standard Storage Hong Kong"), so there's often no
'global' variant to match against at all — the row's rate lookup fails outright.

Call decode_region_prefix() once, universally, in ingest.py's Step 5 (region
assignment) so every row gets this applied regardless of which mapper it later
routes through, instead of requiring each mapper to remember to call it itself.
"""
import re

# Prefix meanings (confirmed against real multi-region flat-CSV/PDF bills):
#   APS1=ap-southeast-1(SG), APS2=ap-southeast-2(SYD), APS3=ap-south-1(MUM),
#   APS4=ap-southeast-4(MEL), APS5=ap-south-2(DEL), APN1=ap-northeast-1(TYO),
#   USE1=us-east-1, USE2=us-east-2, USW1=us-west-1, USW2=us-west-2,
#   EUW1=eu-west-1, EUW2=eu-west-2, EUC1=eu-central-1
UT_PREFIX_TO_GCP: dict[str, str] = {
    "aps1": "asia-southeast1",           # ap-southeast-1 → Singapore
    "aps2": "australia-southeast1",      # ap-southeast-2 → Sydney
    "aps3": "asia-south1",               # ap-south-1     → Mumbai
    "aps4": "australia-southeast2",      # ap-southeast-4 → Melbourne
    "aps5": "asia-south2",               # ap-south-2     → Delhi/Hyderabad
    "aps6": "asia-southeast2",           # ap-southeast-3 → Jakarta
    "apn1": "asia-northeast1",           # ap-northeast-1 → Tokyo
    "apn2": "asia-northeast3",            # ap-northeast-2 → Seoul (GCP's Seoul region is
                                          # asia-northeast3, not -2 — was swapped with apn3)
    "apn3": "asia-northeast2",            # ap-northeast-3 → Osaka (GCP's Osaka region is
                                          # asia-northeast2, not -3 — was swapped with apn2)
    "use1": "us-east4",                  # us-east-1      → N. Virginia
    "use2": "us-east4",                  # us-east-2      → Ohio
    "usw1": "us-west2",                  # us-west-1      → N. California (GCP's us-west2 is
                                          # Los Angeles — was swapped with usw2)
    "usw2": "us-west1",                  # us-west-2      → Oregon (GCP's us-west1 is
                                          # The Dalles, Oregon — was swapped with usw1)
    "usw3": "us-west2",                  # us-west-3      → Salt Lake City
    "usw4": "us-west1",                  # us-west-4      → Las Vegas
    "euw1": "europe-west1",              # eu-west-1      → Ireland
    "euw2": "europe-west2",              # eu-west-2      → London
    "euw3": "europe-west9",              # eu-west-3      → Paris
    "euw4": "europe-west4",              # eu-west-4      → Netherlands
    "eun1": "europe-north1",             # eu-north-1     → Stockholm
    "euc1": "europe-west3",              # eu-central-1   → Frankfurt
    "euc2": "europe-west4",              # eu-central-2   → Zurich (was incorrectly aliased onto
                                          # euc1's Frankfurt value — the two AWS regions became
                                          # indistinguishable downstream; kept in sync with
                                          # ingest.py's REGION_MAP, the source of truth)
    "cac1": "northamerica-northeast1",   # ca-central-1   → Montreal
    "can1": "northamerica-northeast1",   # ca-central-1 (CAN1) → Montreal
    "can2": "northamerica-northeast2",   # ca-west-1 (CAN2) → Calgary
    "sae1": "southamerica-east1",        # sa-east-1      → São Paulo
    "mec1": "me-central1",              # me-central-1   → UAE
    "mes1": "me-west1",                  # me-south-1     → Bahrain
    "ilc1": "me-central2",              # il-central-1 (Israel/Tel Aviv) → me-central2
    "waw1": "europe-central2",          # Warsaw (Poland) → europe-central2
    "afs1": "africa-south1",            # af-south-1 (Cape Town) → africa-south1
    "ape1": "asia-east2",               # ap-east-1 (Hong Kong) → asia-east2
    "eus1": "europe-west8",             # eu-south-1 (Milan) → europe-west8
    "eus2": "europe-southwest1",        # eu-south-2 (Spain) → europe-southwest1
    "mxc1": "northamerica-south1",      # mx-central-1 (Mexico) → northamerica-south1
    # Generic continental / country prefixes used in data transfer out
    "eu":   "europe-west1",             # Europe generic → europe-west1
    "us":   "us-east4",                 # US generic → us-east4
    "ap":   "asia-southeast1",          # Asia-Pacific generic → asia-southeast1
    "sa":   "southamerica-east1",       # South America generic → southamerica-east1
    "au":   "australia-southeast1",     # Australia generic → australia-southeast1
    "in":   "asia-south1",              # India generic → asia-south1
    "jp":   "asia-northeast1",          # Japan generic → asia-northeast1
    "za":   "africa-south1",            # South Africa generic → africa-south1
}
UT_PREFIX_RE = re.compile(r"(?:^|\b)([a-z]{2,4}\d{1,2})-", re.IGNORECASE)
UT_PAIR_RE = re.compile(r"(?:^|\b)([a-z]{2,4}\d{0,2})-([a-z]{2,4}\d{0,2})-", re.IGNORECASE)


def decode_region_prefix(usage_type, fallback=None):
    """Return the GCP region embedded in usage_type's prefix, or fallback if
    usage_type has no recognized prefix (e.g. "APS3-EBS:..." → "asia-south1")."""
    m = UT_PREFIX_RE.search(usage_type or "")
    if not m:
        return fallback
    return UT_PREFIX_TO_GCP.get(m.group(1).lower(), fallback)


def decode_region_pair(usage_type, product=None, fallback_from=None, fallback_to=None):
    """Return (from_region, to_region) GCP regions embedded in usage_type or product.

    For region-pair usage types like 'APS1-WAW1-AWS-Out-Bytes' or 'APS5-USE1-AWS-Out-Bytes',
    both tokens are decoded via UT_PREFIX_TO_GCP.
    If usage_type is empty (PDF/flat bills), searches product.
    If only the first token is a recognized region prefix (e.g. 'APS3-EBS:VolumeUsage'),
    returns (from_region, fallback_to).
    If no recognized prefix matches, returns (fallback_from, fallback_to).
    """
    for text in (usage_type, product):
        if not text:
            continue
        m2 = UT_PAIR_RE.search(text)
        if m2:
            t1 = m2.group(1).lower()
            t2 = m2.group(2).lower()
            if t1 in UT_PREFIX_TO_GCP:
                from_reg = UT_PREFIX_TO_GCP[t1]
                to_reg = UT_PREFIX_TO_GCP.get(t2, fallback_to)
                return (from_reg, to_reg)

        from_single = decode_region_prefix(text, fallback=None)
        if from_single:
            return (from_single, fallback_to)

    return (fallback_from, fallback_to)
