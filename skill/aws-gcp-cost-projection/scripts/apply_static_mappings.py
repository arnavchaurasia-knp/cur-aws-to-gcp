#!/usr/bin/env python3
from __future__ import annotations
"""
apply_static_mappings.py — Deterministic mappings for flat_hourly, object_storage,
per_request, block_storage, and data_transfer groups. Zero LLM tokens spent.

Usage:
    python3 apply_static_mappings.py <projection.duckdb>

Writes one <group>_mappings.json per handled group into projection-audit/mappings/.
These groups map by fixed rules (volume type, storage class, transfer direction),
so they are resolved here rather than by the LLM — deterministic and variance-free.
"""

import gzip, json, os, re, sys
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from egress_rates import EGRESS_SKUS


class SKUMeta(str):
    """str subclass returned by resolve_sku().
    Existing callers that do `if sku:` or `entry["gcp_sku_id"] = sku` continue
    to work unchanged. New callers access .unit and .resource_group.
    """
    def __new__(cls, sku_id, unit=None, resource_group=None):
        obj = str.__new__(cls, sku_id or "")
        obj.unit = unit
        obj.resource_group = resource_group
        return obj

    def __bool__(self):
        return bool(str.__str__(self))

    @property
    def sku_id(self):
        return str.__str__(self) or None

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

# object_storage: S3 storage class → GCS storage class SKU family.
# SKU names must use current GCS catalog vocabulary ("Standard Storage <region>").
# The legacy name "Regional Storage" word-matched an Archive Storage SKU
# ($0.0015/GB vs Standard's $0.023/GB — a 15x underprojection on bill3).
# ---------------------------------------------------------------------------
# S3 → GCS routing
#
# Two-pass approach:
#   Pass 1: usage_type structural codes (authoritative, always present in CUR exports)
#   Pass 2: blob fallback for PDF/summary bills where usage_type is empty/generic
#   Pass 3 (in main): LLM for truly unknown rows
#
# Pass 1 — keyed on usage_type substrings (region prefix is stripped by lower()).
# None  = no GCP equivalent, passthrough at AWS cost.
# str   = GCS storage class name to map to.
# Order matters: more specific patterns must precede shorter ones they overlap with.
# ---------------------------------------------------------------------------
_S3_USAGE_TYPE_ROUTING = [
    # Intelligent-Tiering storage tiers (must precede bare tiered-storage catch-alls)
    ("timedstorage-int-ia-bytehrs",       "Nearline Storage"),   # IT infrequent access
    ("timedstorage-int-fa-bytehrs",       "Standard Storage"),   # IT frequent access
    # Glacier variants
    ("timedstorage-deeparchivebytehrs",   "Archive Storage"),    # Glacier Deep Archive
    ("timedstorage-gda-bytehrs",          "Archive Storage"),    # GDA alias
    ("timedstorage-glacierbytehrs",       "Coldline Storage"),   # Glacier Flexible
    ("timedstorage-gir-bytehrs",          "Coldline Storage"),   # Glacier Instant Retrieval
    # IA variants
    ("timedstorage-sia-bytehrs",          "Nearline Storage"),   # Standard-IA
    ("timedstorage-zia-bytehrs",          "Nearline Storage"),   # One Zone-IA
    # Standard / RRS (catch-all last)
    ("timedstorage-rrs-bytehrs",          "Standard Storage"),   # Reduced Redundancy
    ("timedstorage-bytehrs",              "Standard Storage"),   # Standard

    # Fees with no GCP pricing equivalent — passthrough at AWS cost
    ("monitoring-automation-int",         None),   # IT per-object monitoring fee
    ("int-tier1",                         None),   # IT request charges (unit incompatible)
    ("int-tier2",                         None),   # IT request charges
    ("int-ret",                           None),   # IT retrieval fee
    ("earlydelete",                       None),   # Early delete penalty
    ("obj-lambda",                        None),   # S3 Object Lambda (no GCP equivalent)
]

def _s3_route_usage_type(usage_type_lower: str):
    """Return GCS class name, None (passthrough), or sentinel 'unknown'."""
    for key, gcs_class in _S3_USAGE_TYPE_ROUTING:
        if key in usage_type_lower:
            return gcs_class  # str or None
    return "unknown"

# Pass 2 — blob fallback for PDF/summary bills where usage_type is unpopulated.
# Keyed on human-readable substrings that appear in operation/product/description.
_S3_BLOB_FALLBACK_MAP = {
    "glacier deep archive": "Archive Storage",
    "glacierdeeparchive":   "Archive Storage",
    "glacier instant":      "Coldline Storage",
    "glacierinstant":       "Coldline Storage",
    "glacier flexible":     "Coldline Storage",
    "glacierflexible":      "Coldline Storage",
    "archive instant":      "Coldline Storage",
    "standard-ia":          "Nearline Storage",
    "standardia":           "Nearline Storage",
    "onezone-ia":           "Nearline Storage",
    "intelligent":          "Standard Storage",   # IT frequent access is the dominant tier
    "standard":             "Standard Storage",
    # No-equivalent patterns in blob context
    "per 1,000 objects":    None,
    "per 1000 objects":     None,
    "monitoring and automation": None,
    "monitoringautomation": None,
    "early delete":         None,
    "earlydelete":          None,
}

def _s3_route_blob(blob: str):
    """Blob fallback: longest key first to avoid 'standard' matching 'standard-ia'."""
    for key in sorted(_S3_BLOB_FALLBACK_MAP, key=len, reverse=True):
        if key in blob:
            return _S3_BLOB_FALLBACK_MAP[key]
    return "unknown"

# flat_hourly: usage_type/product/operation fragment → (GCP service, SKU description pattern, unit_multiplier)
# SKU IDs are resolved at runtime from the bundled GCP catalog — no hardcoding.
# sku_desc_pattern: regex matched against sku["description"] in the catalog file.
FLAT_HOURLY_MAP = [
    # ALB maps to Regional External Application Load Balancer Forwarding Rule Minimum
    (r"LoadBalancerUsage.*application|ALB|Application LoadBalancer",
     "Networking", r"Regional External Application Load Balancer Forwarding Rule Minimum", 1.0),
    (r"LoadBalancerUsage.*network|NLB",
     "Cloud Load Balancing", r"Network Load Balancer Forwarding Rule Minimum", 1.0),
    (r"LoadBalancerUsage",
     "Cloud Load Balancing", r"Classic Load Balancer Forwarding Rule Minimum", 1.0),
    (r"NatGateway-Hours|NatGateway",
     "Cloud NAT", r"NAT Gateway Uptime", 1.0),
    (r"TransitGateway-Hours|TransitGateway|TGW",
     "Networking", r"Network Connectivity Center Spoke Hours VPC Network Spoke", 1.0),
    # VPC in-use public IPv4 (simplified CUR) or ElasticIP / EIP (raw CUR)
    (r"In-use public IPv4|public IPv4 address|ElasticIP|EIP",
     "Compute Engine", r"External IP Charge on a Standard VM", 1.0),
    (r"VPN",
     "Cloud VPN", r"Cloud VPN Tunnel", 1.0),
    (r"DirectConnect|DX|HostedConnection",
     "Cloud Interconnect", r"Dedicated Interconnect", 1.0),
    # Global Accelerator port-hours → Cloud CDN (nearest CDN equivalent).
    # GA premium data transfer → Premium Network Tier egress (handled by data_transfer).
    (r"GlobalAccelerator|Global Accelerator",
     "Cloud CDN", r"Cache Egress", 1.0),
]
FLAT_HOURLY_DEFAULT_SERVICE = "Compute Engine"
FLAT_HOURLY_DEFAULT_DESC    = r"Other Hourly Charge"

# per_request: product/usage_type → GCP service + SKU family + unit_multiplier
# ORDER MATTERS — first match wins. Lambda GB-Second must precede generic Lambda
# so compute-time rows get CPU Allocation Time, not invocation pricing.
PER_REQUEST_MAP = [
    (r"Lambda.*GB.Second|Lambda-GB-Second",
                               "Cloud Run",       "Cloud Run CPU Allocation Time", 1.0),
    (r"Lambda",                "Cloud Run",       "Cloud Run Requests",            1.0),
    (r"SQS|SimpleQueue",       "Pub/Sub",         "Message Delivery",              1.0),
    (r"SNS|SimpleNotification","Pub/Sub",         "Message Delivery",              1.0),
    (r"Kinesis",               "Pub/Sub",         "Message Delivery",              1.0),
    (r"ApiGateway|API Gateway","Cloud Endpoints", "API Gateway Requests",          1.0),
    (r"StepFunctions|Step Functions",
                               "Workflows",       "Workflow Steps",                1.0),
    (r"EventBridge|EventBus",  "Eventarc",        "Eventarc Events",               1.0),
    (r"CloudFront",            "Cloud CDN",       "Cache Egress",                  1.0),
    (r"WAF",                   "Cloud Armor",     "Cloud Armor Requests",          1.0),
    (r"Rekognition",           "Cloud Vision",    "Vision API Requests",           1.0),
    (r"Comprehend",            "Natural Language API", "NL API Requests",          1.0),
    (r"Translate",             "Cloud Translation",   "Translation Characters",    1.0),
    (r"Polly",                 "Text-to-Speech",  "TTS Characters",                1.0),
    (r"Transcribe",            "Speech-to-Text",  "STT Audio",                     1.0),
]

# block_storage: EBS volume_type → GCP Persistent Disk tier (desc_pattern searched
# in the Compute Engine catalog). RDS/managed-db storage rows that landed here map
# to Cloud SQL storage instead (branched on product in map_block_storage).
EBS_VOLUME_MAP = {
    "gp2": "Balanced PD Capacity",
    "gp3": "Balanced PD Capacity",
    "io1": "Extreme PD Capacity",
    "io2": "Extreme PD Capacity",
    "st1": "Storage PD Capacity",
    "sc1": "Storage PD Capacity",
    "standard": "Storage PD Capacity",
    "magnetic": "Storage PD Capacity",
}
EBS_DEFAULT_DESC = "Balanced PD Capacity"

# data_transfer: direction inferred from usage_type. Ingress is free on GCP → ignore.
# Inter-zone / inter-region / internet egress each map to their egress SKU family.
def _transfer_target(usage_type, operation):
    """Classify AWS data-transfer direction. Returns (strategy, direction, note).
    direction is a key into EGRESS_SKUS (or None for ingress). Order matters.

    Calibrated against real CUR usage types:
      - '...-In-Bytes'                          → ingress, free on GCP → ignore
      - '<REG>-<REG>-AWS-Out-Bytes'             → inter-region egress
      - '...Regional...' / intra-AZ / Bandwidth → inter-zone egress
      - other '...-Out-Bytes' to internet       → internet egress
    """
    ut = f"{usage_type or ''} {operation or ''}".lower()
    if re.search(r"in-bytes|datatransfer-in|\bin\b.*byte", ut):
        return ("ignore", None, "ingress is free on GCP")
    # Region-to-region code pair (e.g. "aps1-apn1-aws-out-bytes") → inter-region.
    if re.search(r"\b[a-z]{2,4}\d?-[a-z]{2,4}\d?-aws-out", ut) or "inter-region" in ut or "interregion" in ut:
        return ("map", "interregion", "inter-region egress")
    # 'regional'/intra-AZ/Bandwidth = same-region cross-zone traffic → inter-zone.
    if "regional" in ut or "intra" in ut or "bandwidth" in ut or re.search(r"az.*az", ut):
        return ("map", "interzone", "inter-zone (same-region) egress")
    return ("map", "internet", "internet egress")


SKILL_DIR = os.environ.get("SKILL_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR  = os.path.join(SKILL_DIR, "data")
# Global SKU cache shared across all jobs — built once by prefetch_skus.py, appended on miss
RESOLVED_SKUS_FILE = os.path.join(DATA_DIR, "resolved_skus.json")


def _load_services():
    """Return {displayName: serviceId} from the bundled services.json."""
    path = os.path.join(DATA_DIR, "services.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {s["displayName"]: s["serviceId"] for s in json.load(f)}


def _sku_meta(sku):
    """Extract (sku_id, unit, resource_group) from a catalog SKU dict."""
    unit = None
    pricing = sku.get("pricingInfo", [])
    if pricing:
        expr = pricing[0].get("pricingExpression", {})
        unit = expr.get("usageUnit")
    resource_group = sku.get("category", {}).get("resourceGroup")
    return sku["skuId"], unit, resource_group


def lookup_sku_in_catalog(gcp_service, desc_pattern, gcp_region):
    """
    Search the bundled GCP catalog for the first SKU whose description matches
    desc_pattern (regex) and whose serviceRegions include gcp_region (or is GLOBAL).
    Returns (sku_id, unit, resource_group) tuple, or (None, None, None).
    """
    services = _load_services()
    service_id = services.get(gcp_service)
    if not service_id:
        return None, None, None

    sku_file = os.path.join(DATA_DIR, "skus", f"{service_id}.json.gz")
    if not os.path.exists(sku_file):
        return None, None, None

    with gzip.open(sku_file, "rt") as f:
        skus = json.load(f)

    _CONTINENT = {
        "asia": ["asia-east1","asia-east2","asia-northeast1","asia-northeast2","asia-northeast3",
                 "asia-south1","asia-south2","asia-southeast1","asia-southeast2"],
        "europe": ["europe-west1","europe-west2","europe-west3","europe-west4","europe-west6",
                   "europe-north1","europe-central2","europe-southwest1"],
        "us":     ["us-central1","us-east1","us-east4","us-east5","us-south1","us-west1","us-west2","us-west3","us-west4"],
    }

    def _region_match(sku):
        geo = sku.get("geoTaxonomy", {})
        if geo.get("type") == "GLOBAL":
            return True
        regions = sku.get("serviceRegions", [])
        if gcp_region and gcp_region in regions:
            return True
        for rc in regions:
            if gcp_region in _CONTINENT.get(rc.lower(), []):
                return True
        return False

    # Qualifiers that make a SKU a more-specific or differently-priced variant.
    # When desc_pattern doesn't mention them, these variants must be excluded —
    # "Nearline Storage" must not resolve to "Autoclass Nearline Storage" or
    # "Nearline Storage Dual-region". The same rule applies to all GCP services.
    _NOISE_QUALIFIERS = ["autoclass", "early delete", "dual-region", "multi-region"]

    def _has_noise(sku_desc):
        desc_lower = sku_desc.lower()
        pattern_lower = desc_pattern.lower()
        return any(q in desc_lower and q not in pattern_lower for q in _NOISE_QUALIFIERS)

    candidates = [sku for sku in skus
                  if re.search(desc_pattern, sku.get("description", ""), re.IGNORECASE)
                  and _region_match(sku)]

    # Pass 1: non-Preemptible, non-noise (the ideal match)
    for sku in candidates:
        desc = sku.get("description", "").lower()
        if "preemptible" not in desc and not _has_noise(sku.get("description", "")):
            return _sku_meta(sku)
    # Pass 2: non-Preemptible but allow noise (e.g. if only Autoclass SKU exists)
    for sku in candidates:
        if "preemptible" not in sku.get("description", "").lower():
            return _sku_meta(sku)
    # Pass 3: accept Preemptible as last resort
    for sku in candidates:
        return _sku_meta(sku)

    return None, None, None


def _gcp_token():
    """Return (kind, value) auth token, or None."""
    import subprocess
    api_key = os.environ.get("GOOGLE_CLOUD_API_KEY") or os.environ.get("GCP_API_KEY")
    if api_key:
        return ("key", api_key)
    try:
        import urllib.request as _req
        tok = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        if tok:
            return ("bearer", tok)
    except Exception:
        pass
    return None


def _live_sku_fetch(gcp_service, desc_pattern, gcp_region):
    """Last-resort live fetch from GCP Cloud Billing Catalog API. Append result to cache."""
    import urllib.request, urllib.parse
    services_file = os.path.join(DATA_DIR, "services.json")
    if not os.path.exists(services_file):
        return None
    with open(services_file) as f:
        svc_map = {s["displayName"]: s["serviceId"] for s in json.load(f)}
    svc_id = svc_map.get(gcp_service)
    if not svc_id:
        return None

    token_info = _gcp_token()
    if not token_info:
        return None

    kind, value = token_info
    page_token = ""
    while True:
        url = f"https://cloudbilling.googleapis.com/v1/services/{svc_id}/skus?pageSize=5000"
        if page_token:
            url += f"&pageToken={urllib.parse.quote(page_token)}"
        if kind == "key":
            url += f"&key={value}"
            req = urllib.request.Request(url)
        else:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {value}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
        except Exception as e:
            print(f"  live fetch failed: {e}")
            return None

        for sku in data.get("skus", []):
            if not re.search(desc_pattern, sku.get("description", ""), re.IGNORECASE):
                continue
            geo = sku.get("geoTaxonomy", {})
            if geo.get("type") == "GLOBAL" or gcp_region in sku.get("serviceRegions", []):
                return sku["skuId"]

        page_token = data.get("nextPageToken", "")
        if not page_token:
            break
    return None


def resolve_sku(gcp_service, desc_pattern, gcp_region):
    """
    Return the GCP SKU ID for (service, desc_pattern, region).

    Resolution order:
      1. Global cache (data/resolved_skus.json) — no scan, instant
      2. Bundled catalog (data/skus/*.json.gz) — scan once, cache result
      3. Live GCP Billing API — only for genuinely new SKUs not in catalog
         (requires GOOGLE_CLOUD_API_KEY env var or gcloud auth)

    Cache is append-only: only new entries are written, existing entries never overwritten.
    """
    cache = {}
    if os.path.exists(RESOLVED_SKUS_FILE):
        try:
            with open(RESOLVED_SKUS_FILE) as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    key = f"{gcp_service}|{desc_pattern}|{gcp_region or ''}"
    if key in cache:
        v = cache[key]
        if isinstance(v, dict):
            return SKUMeta(v.get("sku_id"), v.get("unit"), v.get("resource_group"))
        # backward compat: old flat-string entry (plain sku_id string or None)
        return SKUMeta(v)

    # Not in cache: try bundled catalog
    sku_id, unit, resource_group = lookup_sku_in_catalog(gcp_service, desc_pattern, gcp_region)

    if sku_id is None:
        # Genuinely missing — trigger live fetch for this new SKU only
        print(f"  SKU not in bundled catalog, trying live API: {gcp_service} / {desc_pattern!r}")
        raw = _live_sku_fetch(gcp_service, desc_pattern, gcp_region)
        sku_id = raw
        unit = None
        resource_group = None

    if sku_id:
        print(f"  resolved SKU: {gcp_service} / {desc_pattern!r} → {sku_id}")
    else:
        print(f"  WARNING: no SKU found for {gcp_service} / {desc_pattern!r} in {gcp_region}")

    # Append-only write — store enriched dict with unit/resource_group
    cache[key] = {"sku_id": sku_id, "unit": unit, "resource_group": resource_group}
    try:
        os.makedirs(os.path.dirname(RESOLVED_SKUS_FILE), exist_ok=True)
        with open(RESOLVED_SKUS_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass

    return SKUMeta(sku_id, unit, resource_group)


def _match(usage_type, product, table, operation=None):
    combined = f"{usage_type or ''} {product or ''} {operation or ''}"
    for pattern, *values in table:
        if re.search(pattern, combined, re.IGNORECASE):
            return values
    return None


def map_object_storage(rows):
    """Map S3 storage rows to GCS.

    Three-pass routing (most reliable first):
      Pass 1: usage_type structural codes — authoritative AWS billing keys
      Pass 2: blob fallback — for PDF/summary bills with unpopulated usage_type
      Pass 3: LLM — truly unknown usage_type; injected into misc by main()

    Returns (mapped_rows, llm_rows). main() writes mapped_rows to the mappings
    file and injects llm_rows into the manifest misc group for Phase 2 LLM.
    """
    mapped = []
    llm_rows = []

    for r in rows:
        gcp_region = r.get("gcp_region")
        usage_type_lower = (r.get("usage_type") or "").lower()

        # ── Pass 1: structured usage_type code lookup ────────────────────────
        result = _s3_route_usage_type(usage_type_lower)

        if result != "unknown":
            if result is None:
                # Known fee type with no GCP equivalent
                mapped.append({
                    "aws_li_key":         r["aws_li_key"],
                    "gcp_service":        "Cloud Storage",
                    "gcp_sku_name":       None,
                    "component":          "storage",
                    "strategy":           "passthrough",
                    "unit_multiplier":    1.0,
                    "gcp_region":         gcp_region,
                    "projection_note":    (f"S3 fee with no GCS equivalent "
                                          f"(usage_type={r.get('usage_type')!r}) — "
                                          f"passthrough at cost parity"),
                    "mapping_confidence": 0.40,
                })
            else:
                # Known storage class — resolve SKU directly to avoid word-overlap errors
                sku_id = resolve_sku("Cloud Storage", result, gcp_region)
                entry = {
                    "aws_li_key":         r["aws_li_key"],
                    "gcp_service":        "Cloud Storage",
                    "gcp_sku_name":       result,
                    "component":          "storage",
                    "strategy":           "map",
                    "unit_multiplier":    1.0,
                    "gcp_region":         gcp_region,
                    "projection_note":    f"S3 usage_type routing → {result}",
                    "mapping_confidence": 0.95,
                }
                if sku_id:
                    entry["gcp_sku_id"] = sku_id
                    entry["gcp_sku_unit"] = sku_id.unit
                mapped.append(entry)
            continue

        # ── Pass 2: blob fallback for PDF/summary bills ──────────────────────
        blob = (f"{usage_type_lower} {(r.get('operation') or '').lower()} "
                f"{(r.get('product') or '').lower()}")
        result_blob = _s3_route_blob(blob)

        if result_blob != "unknown":
            if result_blob is None:
                mapped.append({
                    "aws_li_key":         r["aws_li_key"],
                    "gcp_service":        "Cloud Storage",
                    "gcp_sku_name":       None,
                    "component":          "storage",
                    "strategy":           "passthrough",
                    "unit_multiplier":    1.0,
                    "gcp_region":         gcp_region,
                    "projection_note":    "S3 fee with no GCS equivalent (blob match) — passthrough at cost parity",
                    "mapping_confidence": 0.40,
                })
            else:
                sku_id = resolve_sku("Cloud Storage", result_blob, gcp_region)
                entry = {
                    "aws_li_key":         r["aws_li_key"],
                    "gcp_service":        "Cloud Storage",
                    "gcp_sku_name":       result_blob,
                    "component":          "storage",
                    "strategy":           "map",
                    "unit_multiplier":    1.0,
                    "gcp_region":         gcp_region,
                    "projection_note":    f"S3 blob fallback → {result_blob}",
                    "mapping_confidence": 0.75,
                }
                if sku_id:
                    entry["gcp_sku_id"] = sku_id
                    entry["gcp_sku_unit"] = sku_id.unit
                mapped.append(entry)
            continue

        # ── Pass 3: truly unknown — send to LLM ─────────────────────────────
        llm_rows.append(r)

    return mapped, llm_rows


def map_flat_hourly(rows):
    out = []
    for r in rows:
        match = _match(r.get("usage_type"), r.get("product"), FLAT_HOURLY_MAP, r.get("operation"))
        if match:
            service, desc_pattern, mult = match
        else:
            service, desc_pattern, mult = FLAT_HOURLY_DEFAULT_SERVICE, FLAT_HOURLY_DEFAULT_DESC, 1.0

        gcp_region = r.get("gcp_region")
        sku_id = resolve_sku(service, desc_pattern, gcp_region)

        # Use the matched description pattern as a human-readable name (strip regex chars)
        sku_name = re.sub(r'[\\^$.*+?()[\]{}|]', '', desc_pattern).strip()

        entry = {
            "aws_li_key":       r["aws_li_key"],
            "gcp_service":      service,
            "gcp_sku_name":     sku_name,
            "component":        "hourly",
            "strategy":         "map",
            "unit_multiplier":  mult,
            "gcp_region":       gcp_region,
            "projection_note":  f"flat_hourly lookup → {sku_name}",
            "mapping_confidence": 0.90,
        }
        if sku_id:
            entry["gcp_sku_id"] = sku_id
            entry["gcp_sku_unit"] = sku_id.unit
        out.append(entry)
    return out


def map_per_request(rows):
    out = []
    for r in rows:
        product = (r.get("product") or "").lower()
        # S3 per-request charges: passthrough at AWS cost.
        # Rationale: S3 bills in "per 1,000 requests" units; GCP Cloud Storage bills
        # in "per 10,000 operations" units at a different per-operation rate. The
        # word-overlap SKU auto-resolver finds wrong catalog entries ($0.00005/count
        # vs actual $0.0000004/op), inflating S3 data-event rows up to 50x. Since
        # API/request costs are pricing-model-incompatible between platforms and are
        # typically a small fraction of total spend, passthrough gives a more honest
        # migration estimate than a misleading mapped value.
        if "s3" in product or "simple storage" in product:
            out.append({
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Cloud Storage",
                "gcp_sku_name":       None,
                "component":          "requests",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         r.get("gcp_region"),
                "projection_note":    ("S3 per-request charge — unit-pricing model is "
                                       "incompatible with Cloud Storage operations scale; "
                                       "passthrough at cost parity"),
                "mapping_confidence": 0.50,
            })
            continue
        else:
            match = _match(r.get("usage_type"), r.get("product"), PER_REQUEST_MAP)
            if match:
                service, sku_name, mult = match
                strategy, confidence = "map", 0.85
                note = f"per_request lookup → {sku_name}"
            else:
                # No known GCP equivalent — passthrough at cost parity rather than
                # silently mapping to Cloud Run (which would be wrong for most unmatched
                # services like GuardDuty, Security Hub, unrecognized ML services, etc.).
                service, sku_name, mult = None, None, 1.0
                strategy, confidence = "passthrough", 0.40
                note = (f"per_request: no GCP equivalent found for "
                        f"product={r.get('product')!r} — passthrough at cost parity")
        out.append({
            "aws_li_key":         r["aws_li_key"],
            "gcp_service":        service,
            "gcp_sku_name":       sku_name,
            "component":          "requests",
            "strategy":           strategy,
            "unit_multiplier":    mult,
            "gcp_region":         r.get("gcp_region"),
            "projection_note":    note,
            "mapping_confidence": confidence,
        })
    return out


def map_block_storage(rows):
    """EBS volumes → Persistent Disk; RDS/managed-db storage → Cloud SQL storage."""
    out = []
    for r in rows:
        product = (r.get("product") or "").lower()
        ut = (r.get("usage_type") or "").lower()
        op = (r.get("operation") or "").lower()
        vol = (r.get("volume_type") or "").lower().strip()
        gcp_region = r.get("gcp_region")
        # PDF/summary bills leave the product/volumetype column blank, so fall
        # back to inferring the volume type and snapshot flag from the free text
        # (usage_type + operation + product description).
        blob = f"{ut} {op} {product}"
        if not vol:
            mvol = re.search(r'\b(gp3|gp2|io2|io1|st1|sc1|standard|magnetic)\b', blob)
            if mvol:
                vol = mvol.group(1)
        is_snapshot = ("snapshot" in ut) or ("snapshot" in op) or ("snapshot" in blob)
        # gp3/io2 provisioned-IOPS and throughput (MiBps) fees have no separate
        # GCP charge — pd-balanced bundles performance with capacity. Pricing
        # them per-GB against a capacity SKU produced nonsense rows; drop them
        # with an explicit note instead.
        if re.search(r"iops-mo|provisioned iops|mibps|throughput", blob):
            is_managed_db = any(k in product for k in
                                ("rds", "relational", "aurora", "documentdb", "memorydb", "elasticache"))
            gp3_like = "gp3" in blob or "gp2" in blob
            
            if is_managed_db:
                out.append({
                    "aws_li_key":       r["aws_li_key"],
                    "gcp_service":      "Cloud SQL",
                    "gcp_sku_name":     None,
                    "component":        "storage",
                    "strategy":         "ignore",
                    "unit_multiplier":  1.0,
                    "gcp_region":       gcp_region,
                    "projection_note":  "RDS Provisioned IOPS/throughput fee — performance scaling is included in Cloud SQL storage tier",
                    "mapping_confidence": 0.90,
                })
            else:
                out.append({
                    "aws_li_key":       r["aws_li_key"],
                    "gcp_service":      "Compute Engine",
                    "gcp_sku_name":     "Extreme PD IOPS",
                    "component":        "storage",
                    "strategy":         "ignore" if gp3_like else "passthrough",
                    "unit_multiplier":  1.0,
                    "gcp_region":       gcp_region,
                    "projection_note":  ("EBS gp3 provisioned IOPS/throughput fee — included "
                                         "in GCP pd-balanced capacity price, no separate charge"
                                         if gp3_like else
                                         "EBS provisioned IOPS/throughput fee — maps to PD "
                                         "Extreme provisioned IOPS; passthrough at cost parity"),
                    "mapping_confidence": 0.85 if gp3_like else 0.60,
                })
            continue
        is_managed_db = any(k in product for k in
                            ("rds", "relational", "aurora", "documentdb", "memorydb", "elasticache"))

        # Aurora/RDS per-I/O request charges: billed as "N million I/O requests".
        # Cloud SQL includes I/O in the storage price — no separate per-I/O charge.
        # These rows MUST be ignored: total_usage is an I/O count (e.g. 22,477,180
        # IOs), but storage SKU rates are in $/GiBy.mo — multiplying them inflates
        # cost by >1,000,000x (22M IOs × $0.41/GiBy.mo = $9M from a $5 AWS row).
        if is_managed_db and re.search(r"i/o request|million i/o|million io|\bio request", blob, re.IGNORECASE):
            out.append({
                "aws_li_key":       r["aws_li_key"],
                "gcp_service":      "Cloud SQL",
                "gcp_sku_name":     None,
                "component":        "storage",
                "strategy":         "ignore",
                "unit_multiplier":  1.0,
                "gcp_region":       gcp_region,
                "projection_note":  "Aurora/RDS per-I/O request fee — Cloud SQL storage pricing includes I/O; no separate per-I/O charge on GCP",
                "mapping_confidence": 0.95,
            })
            continue

        if is_managed_db:
            service = "Cloud SQL"
            if "backup" in ut or "backup" in op:
                desc = "Backups"
            elif vol in ("st1", "sc1", "standard", "magnetic"):
                desc = "HDD storage"
            else:
                desc = "SSD storage"
            note = f"managed-db storage ({vol or 'default'}) → Cloud SQL {desc}"
        else:
            service = "Compute Engine"
            if is_snapshot:
                desc = "Storage PD Snapshot"
            else:
                desc = EBS_VOLUME_MAP.get(vol, EBS_DEFAULT_DESC)
            note = f"EBS {vol or 'volume'}{' snapshot' if is_snapshot else ''} → {desc}"

        sku_id = resolve_sku(service, desc, gcp_region)
        entry = {
            "aws_li_key":       r["aws_li_key"],
            "gcp_service":      service,
            "gcp_sku_name":     desc,
            "component":        "storage",
            "strategy":         "map",
            "unit_multiplier":  1.0,
            "gcp_region":       gcp_region,
            "projection_note":  note,
            "mapping_confidence": 0.90,
        }
        if sku_id:
            entry["gcp_sku_id"] = sku_id
            entry["gcp_sku_unit"] = sku_id.unit
        out.append(entry)
    return out


def map_data_transfer(rows):
    """Classify transfer direction deterministically and pin the canonical egress
    SKU + rate (no fuzzy catalog matching — see egress_rates.py). Ingress → ignore."""
    out = []
    for r in rows:
        ut = f"{r.get('usage_type') or ''} {r.get('operation') or ''}".lower()
        if "natgateway-bytes" in ut:
            service = "Cloud NAT"
            desc_pattern = "Networking Cloud Nat Data Processing"
            sku_id = resolve_sku(service, desc_pattern, r.get("gcp_region"))
            entry = {
                "aws_li_key":       r["aws_li_key"],
                "gcp_service":      service,
                "gcp_sku_name":     "Networking Cloud Nat Data Processing",
                "component":        "transfer",
                "strategy":         "map",
                "unit_multiplier":  1.0,
                "gcp_region":       r.get("gcp_region"),
                "projection_note":  "NAT Gateway processed bytes → Cloud NAT Data Processing",
                "mapping_confidence": 0.90,
            }
            if sku_id:
                entry["gcp_sku_id"] = sku_id
                entry["gcp_sku_unit"] = sku_id.unit
            out.append(entry)
            continue
            
        if "transitgateway-bytes" in ut or "tgw-bytes" in ut:
            out.append({
                "aws_li_key":       r["aws_li_key"],
                "gcp_service":      "VPC Network",
                "gcp_sku_name":     None,
                "component":        "transfer",
                "strategy":         "ignore",
                "unit_multiplier":  1.0,
                "gcp_region":       r.get("gcp_region"),
                "projection_note":  "Transit Gateway data processing fee — VPC network spoke traffic has no transit fee on GCP; only standard egress applies",
                "mapping_confidence": 0.90,
            })
            continue

        if "lcu" in ut or "loadbalancer-bytes" in ut:
            service = "Networking"
            desc_pattern = "Regional External Application Load Balancer Outbound Data Processing"
            sku_id = resolve_sku(service, desc_pattern, r.get("gcp_region"))
            entry = {
                "aws_li_key":       r["aws_li_key"],
                "gcp_service":      service,
                "gcp_sku_name":     "Regional External Application Load Balancer Outbound Data Processing",
                "component":        "transfer",
                "strategy":         "map",
                "unit_multiplier":  1.0,
                "gcp_region":       r.get("gcp_region"),
                "projection_note":  "Load Balancer Capacity Units (LCU) → Regional External ALB Outbound Data Processing",
                "mapping_confidence": 0.90,
            }
            if sku_id:
                entry["gcp_sku_id"] = sku_id
                entry["gcp_sku_unit"] = sku_id.unit
            out.append(entry)
            continue

        strategy, direction, note = _transfer_target(r.get("usage_type"), r.get("operation"))
        entry = {
            "aws_li_key":       r["aws_li_key"],
            "gcp_service":      "Compute Engine",
            "component":        "transfer",
            "strategy":         strategy,
            "unit_multiplier":  1.0,
            "gcp_region":       r.get("gcp_region"),
            "projection_note":  f"data_transfer: {note}",
            "mapping_confidence": 0.85,
        }
        if strategy == "map" and direction in EGRESS_SKUS:
            sku_id, sku_name, _rate = EGRESS_SKUS[direction]
            entry["gcp_sku_id"] = sku_id
            # EGRESS_SKUS stores plain strings (synthetic IDs), no .unit attribute
            entry["gcp_sku_unit"] = getattr(sku_id, "unit", None)
            entry["gcp_sku_name"] = sku_name
        else:
            entry["gcp_sku_name"] = None
        out.append(entry)
    return out


def map_non_workload(rows):
    out = []
    for r in rows:
        product = (r.get("product") or "").lower()
        desc = (r.get("operation") or "").lower()
        if "marketplace" in product or "marketplace" in desc:
            gcp_service = "AWS Marketplace (Passthrough)"
            note = "AWS Marketplace subscription — passthrough at cost parity; not core workload"
        elif "support" in product or "support" in desc:
            gcp_service = "AWS Support (Passthrough)"
            note = "AWS Support plan — passthrough at cost parity; not core workload"
        else:
            gcp_service = "AWS Non-Workload (Passthrough)"
            note = "AWS Non-workload item — passthrough at cost parity"
            
        out.append({
            "aws_li_key":         r["aws_li_key"],
            "gcp_service":        gcp_service,
            "gcp_sku_id":         None,
            "gcp_sku_name":       None,
            "component":          "passthrough",
            "strategy":           "passthrough",
            "unit_multiplier":    1.0,
            "gcp_region":         r.get("gcp_region"),
            "projection_note":    note,
            "mapping_confidence": 1.0,
        })
    return out


def map_guardduty(rows):
    """GuardDuty and Security Hub → Security Command Center passthrough.

    Both services are priced on incompatible models (GuardDuty: $/GB-analyzed;
    SCC Premium: $/asset/mo) so a rate-based mapping is not feasible. We carry
    the AWS cost as an honest passthrough with the correct GCP service label
    so the report says "Security Command Center" rather than "Unmapped".
    """
    out = []
    for r in rows:
        product = (r.get("product") or "").lower()
        if "security hub" in product or "securityhub" in product:
            note = ("AWS Security Hub → Security Command Center Standard; "
                    "pricing model differs (per-finding vs per-asset/mo); passthrough at cost parity")
        else:
            note = ("Amazon GuardDuty → Security Command Center Premium; "
                    "pricing model differs (per-GB-analyzed vs per-asset/mo); passthrough at cost parity")
        out.append({
            "aws_li_key":         r["aws_li_key"],
            "gcp_service":        "Security Command Center",
            "gcp_sku_id":         None,
            "gcp_sku_name":       None,
            "component":          "security",
            "strategy":           "passthrough",
            "unit_multiplier":    1.0,
            "gcp_region":         r.get("gcp_region"),
            "projection_note":    note,
            "mapping_confidence": 0.60,
        })
    return out


# ElastiCache node type → RAM in GiB.
# Memorystore for Redis bills in GiBy.h, so unit_multiplier = RAM_GiB converts
# ElastiCache node-hours into Memorystore GiBy.h at the catalog rate.
# Source: https://aws.amazon.com/elasticache/pricing/ (node specs column)
_ELASTICACHE_RAM_GIB = {
    # T family
    "cache.t2.micro": 0.555, "cache.t2.small": 1.55, "cache.t2.medium": 3.22,
    "cache.t3.micro": 0.555, "cache.t3.small": 1.42, "cache.t3.medium": 3.09,
    "cache.t4g.micro": 0.555, "cache.t4g.small": 1.42, "cache.t4g.medium": 3.09,
    # M5 / M6g
    "cache.m5.large": 6.38,  "cache.m5.xlarge": 12.93, "cache.m5.2xlarge": 26.04,
    "cache.m5.4xlarge": 52.82, "cache.m5.12xlarge": 157.12, "cache.m5.24xlarge": 314.32,
    "cache.m6g.large": 6.38, "cache.m6g.xlarge": 12.93, "cache.m6g.2xlarge": 26.04,
    "cache.m6g.4xlarge": 52.82, "cache.m6g.8xlarge": 103.68, "cache.m6g.12xlarge": 157.12,
    "cache.m6g.16xlarge": 209.55,
    "cache.m7g.large": 6.38, "cache.m7g.xlarge": 12.93, "cache.m7g.2xlarge": 26.04,
    "cache.m7g.4xlarge": 52.82, "cache.m7g.8xlarge": 103.68, "cache.m7g.12xlarge": 157.12,
    "cache.m7g.16xlarge": 209.55,
    # R5 / R6g / R7g
    "cache.r5.large": 13.07, "cache.r5.xlarge": 26.24, "cache.r5.2xlarge": 52.82,
    "cache.r5.4xlarge": 105.81, "cache.r5.12xlarge": 317.77, "cache.r5.24xlarge": 635.61,
    "cache.r6g.large": 13.07, "cache.r6g.xlarge": 26.24, "cache.r6g.2xlarge": 52.82,
    "cache.r6g.4xlarge": 105.81, "cache.r6g.8xlarge": 209.55, "cache.r6g.12xlarge": 317.77,
    "cache.r6g.16xlarge": 423.48,
    "cache.r7g.large": 13.07, "cache.r7g.xlarge": 26.24, "cache.r7g.2xlarge": 52.82,
    "cache.r7g.4xlarge": 105.81, "cache.r7g.8xlarge": 209.55, "cache.r7g.12xlarge": 317.77,
    "cache.r7g.16xlarge": 423.48,
}

# Regex to extract a bare node type from the operation text when instance_type is null.
# Matches patterns like "M5.large", "m6g.xlarge", "R5.large", "T4G Medium", "T3 Small", etc.
_EC_NODE_RE = re.compile(
    r"\b(t[234]g?)\s*(micro|small|medium)"        # T-family with optional space
    r"|\b(m[567]g?)\.(large|xlarge|[248]xlarge|12xlarge|16xlarge|24xlarge)"  # M-family
    r"|\b(r[567]g?)\.(large|xlarge|[248]xlarge|12xlarge|16xlarge|24xlarge)"  # R-family
    , re.IGNORECASE
)


def _elasticache_ram(r: dict) -> float | None:
    """Return RAM in GiB for an ElastiCache row, or None if unknown."""
    # instance_ram_gb is populated by ingest.py for rows that have instance_type
    ram = r.get("instance_ram_gb")
    if ram:
        return float(ram)
    itype = (r.get("instance_type") or "").lower().strip()
    if itype in _ELASTICACHE_RAM_GIB:
        return _ELASTICACHE_RAM_GIB[itype]
    # Fall back to parsing the operation text (PDF bills often lack instance_type)
    op = r.get("operation") or ""
    m = _EC_NODE_RE.search(op)
    if m:
        # Reconstruct a canonical cache.family.size key
        if m.group(1):   # T-family: "T4G Medium" → "cache.t4g.medium"
            key = f"cache.{m.group(1).lower()}.{m.group(2).lower()}"
        elif m.group(3): # M-family
            key = f"cache.{m.group(3).lower()}.{m.group(4).lower()}"
        else:            # R-family
            key = f"cache.{m.group(5).lower()}.{m.group(6).lower()}"
        return _ELASTICACHE_RAM_GIB.get(key)
    return None


def map_elasticache(rows: list) -> list:
    """ElastiCache for Redis/Memcached → Cloud Memorystore for Redis.

    Billing model: ElastiCache charges per node-hour; Memorystore charges per
    GiBy.h of capacity.  unit_multiplier = RAM_GiB converts node-hours into
    GiBy.h so the catalog rate applies directly.

    Tier: defaults to Basic M1 (single-instance).  Multi-AZ / cluster-mode
    topology is not reliably exposed by CUR, so we note the uncertainty and
    apply a 0.80 confidence ceiling.
    """
    out = []
    for r in rows:
        region = r.get("gcp_region")
        ram = _elasticache_ram(r)

        if ram is None:
            # Can't determine node size — passthrough with correct GCP label
            out.append({
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Cloud Memorystore for Redis",
                "gcp_sku_id":         None,
                "gcp_sku_name":       "Redis Capacity Basic M1",
                "component":          "cache",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         region,
                "projection_note":    ("ElastiCache → Memorystore for Redis; node RAM unknown "
                                       "(instance_type missing from CUR) — passthrough at cost parity"),
                "mapping_confidence": 0.50,
            })
            continue

        sku_name = "Redis Capacity Basic M1"
        sku_id = resolve_sku("Cloud Memorystore for Redis", sku_name, region)
        strategy = "map" if sku_id else "passthrough"
        note = (f"ElastiCache → Memorystore for Redis Basic; "
                f"node RAM={ram:.2f} GiB → unit_multiplier={ram:.2f} (GiBy.h); "
                f"assumes single-instance (Basic tier) — verify if Multi-AZ or Cluster mode "
                f"is in use (Standard HA tier costs ~2x)")
        out.append({
            "aws_li_key":         r["aws_li_key"],
            "gcp_service":        "Cloud Memorystore for Redis",
            "gcp_sku_id":         sku_id,
            "gcp_sku_name":       sku_name,
            "component":          "cache",
            "strategy":           strategy,
            "unit_multiplier":    ram,
            "gcp_region":         region,
            "projection_note":    note,
            "mapping_confidence": 0.80,
        })
    return out


# Redshift node-type → BigQuery Standard Edition slot-hour conversion table.
# Derived from BigQuery migration guide capacity recommendations.
_REDSHIFT_SLOT_MAP = {
    "dc2.large":    500,
    "dc2.8xlarge":  4000,
    "ds2.xlarge":   500,
    "ds2.8xlarge":  4000,
    "ra3.xlplus":   500,
    "ra3.4xlarge":  1500,
    "ra3.16xlarge": 6000,
}
_BQ_SLOT_SKU      = "Standard Edition Slot Hour"
_BQ_STORAGE_SKU   = "Active Storage"


def map_redshift(rows):
    """Redshift → BigQuery deterministic mapping.

    Node-hours: converted to BigQuery Standard Edition slot-hours using
    _REDSHIFT_SLOT_MAP (slots per node). RA3 ManagedStorage → BQ active storage.
    Serverless RPU → BQ slot-hours at 128 slots/RPU. Backups → passthrough.
    Unknown instance types → passthrough with a note.
    """
    out = []
    for r in rows:
        ut  = (r.get("usage_type") or "").lower()
        op  = (r.get("operation")  or "").lower()
        gcp_region = r.get("gcp_region")
        blob = f"{ut} {op} {(r.get('product') or '').lower()}"

        # Backup and snapshot rows → passthrough (no BQ equivalent charge)
        if "backup" in blob or "snapshot" in blob:
            out.append({
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "BigQuery",
                "gcp_sku_id":         None,
                "gcp_sku_name":       None,
                "component":          "backup",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    "Redshift backup/snapshot — no BigQuery equivalent charge; passthrough at cost parity",
                "mapping_confidence": 0.70,
            })
            continue

        # RA3 ManagedStorage → BigQuery active storage ($0.02/GB-mo)
        if "managedstorage" in ut or "managed storage" in blob:
            sku_id = resolve_sku("BigQuery", _BQ_STORAGE_SKU, gcp_region)
            entry = {
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "BigQuery",
                "gcp_sku_name":       _BQ_STORAGE_SKU,
                "component":          "storage",
                "strategy":           "map" if sku_id else "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    "Redshift RA3 ManagedStorage → BigQuery Active Storage",
                "mapping_confidence": 0.80,
            }
            if sku_id:
                entry["gcp_sku_id"] = sku_id
                entry["gcp_sku_unit"] = sku_id.unit
            out.append(entry)
            continue

        # Serverless RPU hours → BigQuery slot-hours (1 RPU ≈ 128 BQ slots)
        if "serverless" in blob or re.search(r"\brpu\b", ut):
            sku_id = resolve_sku("BigQuery", _BQ_SLOT_SKU, gcp_region)
            entry = {
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "BigQuery",
                "gcp_sku_name":       _BQ_SLOT_SKU,
                "component":          "compute",
                "strategy":           "map" if sku_id else "passthrough",
                "unit_multiplier":    128.0,
                "gcp_region":         gcp_region,
                "projection_note":    "Redshift Serverless RPU → BigQuery slot-hours (1 RPU ≈ 128 BQ Standard slots)",
                "mapping_confidence": 0.65,
            }
            if sku_id:
                entry["gcp_sku_id"] = sku_id
                entry["gcp_sku_unit"] = sku_id.unit
            out.append(entry)
            continue

        # Node-hour rows: match instance type in usage_type or operation
        slots = None
        matched_family = None
        for family, slot_count in _REDSHIFT_SLOT_MAP.items():
            if family in ut or family in op:
                slots = slot_count
                matched_family = family
                break

        if slots:
            sku_id = resolve_sku("BigQuery", _BQ_SLOT_SKU, gcp_region)
            entry = {
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "BigQuery",
                "gcp_sku_name":       _BQ_SLOT_SKU,
                "component":          "compute",
                "strategy":           "map" if sku_id else "passthrough",
                "unit_multiplier":    float(slots),
                "gcp_region":         gcp_region,
                "projection_note":    f"Redshift {matched_family} node-hour → BigQuery {slots} Standard slot-hours",
                "mapping_confidence": 0.70,
            }
            if sku_id:
                entry["gcp_sku_id"] = sku_id
                entry["gcp_sku_unit"] = sku_id.unit
            out.append(entry)
        else:
            # Unrecognized Redshift row → passthrough with note
            out.append({
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "BigQuery",
                "gcp_sku_id":         None,
                "gcp_sku_name":       None,
                "component":          "compute",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    f"Redshift row unrecognized (usage_type={r.get('usage_type')!r}) — passthrough at cost parity",
                "mapping_confidence": 0.40,
            })
    return out


def map_cloudwatch(rows):
    out = []
    for r in rows:
        ut = (r.get("usage_type") or "").lower()
        op = (r.get("operation") or "").lower()
        gcp_region = r.get("gcp_region")
        p_unit = (r.get("unit") or "").lower()

        # Only map log DATA VOLUME rows to Cloud Logging — those where the billing
        # unit is GB/GiB (ingested bytes). Every other CloudWatch row (API calls,
        # queries, metric periods, alarms, dashboard refreshes) is priced in a count
        # unit incompatible with Cloud Logging's $/GiBy.mo rate; multiplying call
        # counts by a GiBy rate inflates by 100x+. Passthrough at AWS cost parity
        # is the safe default until a proper per-unit pricer exists.
        is_log_volume = (
            ("logbytes" in ut or "datascanned" in ut or "logstorage" in ut
             or "logingest" in ut or "logingestion" in ut)
            or (("log" in ut or "log" in op) and p_unit in ("gb", "gib", "gb-mo", "giby.mo"))
        )

        if is_log_volume:
            sku_id = resolve_sku("Cloud Logging", "Log Storage cost", gcp_region)
            entry = {
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Cloud Logging",
                "gcp_sku_id":         sku_id,
                "gcp_sku_name":       "Log Storage cost",
                "component":          "logs",
                "strategy":           "map" if sku_id else "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    "CloudWatch Logs data volume → Cloud Logging ingestion ($0.50/GiB after free tier)",
                "mapping_confidence": 0.85,
            }
        else:
            # API calls, queries, metric periods, alarms, dashboards — count-based units
            # don't translate to Cloud Monitoring/Logging volume rates. Passthrough.
            entry = {
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Cloud Monitoring",
                "gcp_sku_id":         None,
                "gcp_sku_name":       None,
                "component":          "monitoring",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    "CloudWatch metric/API/query charge — unit-incompatible with Cloud Monitoring rates; passthrough at AWS cost parity",
                "mapping_confidence": 0.70,
            }
        out.append(entry)
    return out


def map_athena(rows):
    """Athena → BigQuery on-demand.

    Athena bills per-TB-scanned. BigQuery on-demand is also $/TB-analyzed
    (same unit, directly comparable). We map at unit_multiplier=1.0 — the
    total_usage (bytes or GB/TB scanned) maps to BigQuery Analysis pricing.
    If bytes_scanned is not in the usage unit, passthrough with note.
    """
    _BQ_ANALYSIS_SKU = "Analysis"
    out = []
    for r in rows:
        ut  = (r.get("usage_type") or "").lower()
        gcp_region = r.get("gcp_region")

        # Passthrough for non-data-scanned rows (CTAS, DDL, cancelled, limits)
        if re.search(r"data.?scanned|bytes.?scanned|tb.?scanned", ut):
            sku_id = resolve_sku("BigQuery", _BQ_ANALYSIS_SKU, gcp_region)
            # Athena bills in TB; BigQuery Analysis SKU is also /TB → multiplier=1.0
            # Unit conversion note: if CUR usage_type shows bytes, the projection
            # framework uses total_usage directly against the rate — verify unit matches.
            entry = {
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "BigQuery",
                "gcp_sku_name":       _BQ_ANALYSIS_SKU,
                "component":          "analysis",
                "strategy":           "map" if sku_id else "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    "Athena data-scanned → BigQuery Analysis ($6.25/TB on-demand; first 1 TB/mo free)",
                "mapping_confidence": 0.85,
            }
            if sku_id:
                entry["gcp_sku_id"] = sku_id
                entry["gcp_sku_unit"] = sku_id.unit
            out.append(entry)
        else:
            # Other Athena charges (DDL, cancelled queries, DML metadata) → passthrough
            out.append({
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "BigQuery",
                "gcp_sku_id":         None,
                "gcp_sku_name":       None,
                "component":          "analysis",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    f"Athena row (usage_type={r.get('usage_type')!r}) not data-scanned charge — passthrough at cost parity",
                "mapping_confidence": 0.50,
            })
    return out


def map_kinesis(rows):
    """Kinesis shard-hours → Pub/Sub throughput (hourly billing only).

    Kinesis Shard-Hours are the per-shard reservation fee. GCP Pub/Sub does not
    have a shard model — it's throughput-based. A shard supports 1 MB/s write and
    2 MB/s read. We map shard-hours → Pub/Sub message delivery as an approximation:
    1 shard × 1 hr ≈ 3.6 GB throughput capacity (1 MB/s × 3600 s). This is an
    over-estimate (many shards run < full utilization) — passthrough is also honest.
    Strategy: passthrough with Pub/Sub label, because unit models differ too much
    for an accurate rate-based projection without utilization data.
    """
    out = []
    for r in rows:
        ut  = (r.get("usage_type") or "").lower()
        gcp_region = r.get("gcp_region")

        if re.search(r"shard.?hr|shardhours|extended.?retention", ut):
            note = "Kinesis shard-hours → Pub/Sub (no shard model on GCP; throughput-based billing differs fundamentally); passthrough at cost parity"
        else:
            note = f"Kinesis hourly charge (usage_type={r.get('usage_type')!r}) → Pub/Sub passthrough"

        out.append({
            "aws_li_key":         r["aws_li_key"],
            "gcp_service":        "Pub/Sub",
            "gcp_sku_id":         None,
            "gcp_sku_name":       None,
            "component":          "messaging",
            "strategy":           "passthrough",
            "unit_multiplier":    1.0,
            "gcp_region":         gcp_region,
            "projection_note":    note,
            "mapping_confidence": 0.55,
        })
    return out


# EFS storage class → Filestore tier
_EFS_STORAGE_MAP = {
    # Standard (infrequent access) and Intelligent-Tiering → Filestore Basic HDD
    "standardia":      ("Filestore", "Filestore Basic HDD Capacity",       0.90),
    "standard-ia":     ("Filestore", "Filestore Basic HDD Capacity",       0.90),
    "ia":              ("Filestore", "Filestore Basic HDD Capacity",       0.90),
    # Standard (frequent access) → Filestore Basic SSD (closer performance profile)
    "standard":        ("Filestore", "Filestore Basic SSD Capacity",       1.0),
}
_EFS_DEFAULT_STORAGE = ("Filestore", "Filestore Basic SSD Capacity", 1.0)


def map_efs(rows):
    """EFS → Filestore mapping.

    Standard storage → Filestore Basic SSD ($0.20/GB-mo).
    Infrequent Access → Filestore Basic HDD ($0.10/GB-mo).
    Provisioned Throughput (MB/s-month) → passthrough (Filestore includes throughput
    in capacity price; no separate throughput charge exists to map to).
    Data access / I/O requests → passthrough (GCP has no per-access EFS equivalent).
    """
    out = []
    for r in rows:
        ut  = (r.get("usage_type") or "").lower()
        op  = (r.get("operation") or "").lower()
        gcp_region = r.get("gcp_region")
        blob = f"{ut} {op}"

        # Provisioned Throughput (MB/s-month) → passthrough
        if re.search(r"provisioned.?throughput|throughput.?capacity", blob):
            out.append({
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Filestore",
                "gcp_sku_id":         None,
                "gcp_sku_name":       None,
                "component":          "storage",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    "EFS Provisioned Throughput — Filestore includes throughput in capacity price; no separate charge to map",
                "mapping_confidence": 0.75,
            })
            continue

        # Data access requests / IO charges → passthrough
        if re.search(r"data.?access|io.?request|meteredthroughput", blob):
            out.append({
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Filestore",
                "gcp_sku_id":         None,
                "gcp_sku_name":       None,
                "component":          "storage",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    "EFS data-access / I/O request charge — no direct Filestore equivalent; passthrough at cost parity",
                "mapping_confidence": 0.60,
            })
            continue

        # Storage rows — detect access tier
        service, sku_name, mult = _EFS_DEFAULT_STORAGE
        for key, val in _EFS_STORAGE_MAP.items():
            if key in blob:
                service, sku_name, mult = val
                break

        sku_id = resolve_sku(service, sku_name, gcp_region)
        entry = {
            "aws_li_key":         r["aws_li_key"],
            "gcp_service":        service,
            "gcp_sku_name":       sku_name,
            "component":          "storage",
            "strategy":           "map" if sku_id else "passthrough",
            "unit_multiplier":    mult,
            "gcp_region":         gcp_region,
            "projection_note":    f"EFS storage → {sku_name}",
            "mapping_confidence": 0.75,
        }
        if sku_id:
            entry["gcp_sku_id"] = sku_id
            entry["gcp_sku_unit"] = sku_id.unit
        out.append(entry)
    return out


def map_fsx(rows):
    """FSx variants → Filestore or passthrough.

    FSx for Lustre → Filestore High Scale (closest performance tier).
    FSx for Windows → Filestore Enterprise (SMB-compatible).
    FSx for NetApp ONTAP, OpenZFS → passthrough (no direct GCP equivalent).
    Backup/snapshot rows → passthrough.
    """
    out = []
    for r in rows:
        product = (r.get("product") or "").lower()
        ut      = (r.get("usage_type") or "").lower()
        gcp_region = r.get("gcp_region")
        blob = f"{product} {ut}"

        # Backup rows → passthrough regardless of FSx type
        if "backup" in blob or "snapshot" in blob:
            out.append({
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Filestore",
                "gcp_sku_id":         None,
                "gcp_sku_name":       None,
                "component":          "storage",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    "FSx backup — no Filestore equivalent; passthrough at cost parity",
                "mapping_confidence": 0.70,
            })
            continue

        if "lustre" in blob:
            sku_name = "Filestore High Scale SSD Capacity"
            note = "FSx for Lustre → Filestore High Scale SSD (high-throughput parallel workloads)"
            confidence = 0.65
        elif "windows" in blob:
            sku_name = "Filestore Enterprise Capacity"
            note = "FSx for Windows → Filestore Enterprise (SMB-compatible; Kerberos/AD auth differs)"
            confidence = 0.65
        else:
            # NetApp ONTAP, OpenZFS — no direct GCP equivalent
            out.append({
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Filestore",
                "gcp_sku_id":         None,
                "gcp_sku_name":       None,
                "component":          "storage",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    f"FSx ({r.get('product')!r}) — no direct GCP equivalent; passthrough at cost parity",
                "mapping_confidence": 0.50,
            })
            continue

        sku_id = resolve_sku("Filestore", sku_name, gcp_region)
        entry = {
            "aws_li_key":         r["aws_li_key"],
            "gcp_service":        "Filestore",
            "gcp_sku_name":       sku_name,
            "component":          "storage",
            "strategy":           "map" if sku_id else "passthrough",
            "unit_multiplier":    1.0,
            "gcp_region":         gcp_region,
            "projection_note":    note,
            "mapping_confidence": confidence,
        }
        if sku_id:
            entry["gcp_sku_id"] = sku_id
            entry["gcp_sku_unit"] = sku_id.unit
        out.append(entry)
    return out


def map_xray(rows):
    """X-Ray → Cloud Trace.

    X-Ray: $5.00/million traces (first 100k free). Cloud Trace: $0.20/million spans.
    Unit models are compatible (count-based), though GCP spans are more granular than
    AWS traces. We map at unit_multiplier=1.0 and flag the rate difference — the GCP
    price is dramatically lower ($0.20 vs $5.00/M), so passthrough would overstate cost.
    """
    _TRACE_SKU = "Trace Ingestion"
    out = []
    for r in rows:
        gcp_region = r.get("gcp_region")
        sku_id = resolve_sku("Cloud Trace", _TRACE_SKU, gcp_region)
        entry = {
            "aws_li_key":         r["aws_li_key"],
            "gcp_service":        "Cloud Trace",
            "gcp_sku_name":       _TRACE_SKU,
            "component":          "tracing",
            "strategy":           "map" if sku_id else "passthrough",
            "unit_multiplier":    1.0,
            "gcp_region":         gcp_region,
            "projection_note":    "AWS X-Ray → Cloud Trace ($0.20/M spans vs $5.00/M traces on AWS — GCP significantly cheaper)",
            "mapping_confidence": 0.75,
        }
        if sku_id:
            entry["gcp_sku_id"] = sku_id
            entry["gcp_sku_unit"] = sku_id.unit
        out.append(entry)
    return out


# EMR instance type → vCPU count (management fee scales with vCPUs via Dataproc premium).
# EC2 instance-hour cost is captured separately by compute_breakdown; only the EMR
# management premium fee rows reach this mapper.
_EMR_VCPU_MAP = {
    "m5.xlarge": 4,    "m5.2xlarge": 8,    "m5.4xlarge": 16,  "m5.8xlarge": 32,
    "m5.12xlarge": 48, "m5.16xlarge": 64,  "m5.24xlarge": 96,
    "m5a.xlarge": 4,   "m5a.2xlarge": 8,   "m5a.4xlarge": 16, "m5a.8xlarge": 32,
    "m6i.xlarge": 4,   "m6i.2xlarge": 8,   "m6i.4xlarge": 16, "m6i.8xlarge": 32,
    "m6g.xlarge": 4,   "m6g.2xlarge": 8,   "m6g.4xlarge": 16, "m6g.8xlarge": 32,
    "m6a.xlarge": 4,   "m6a.2xlarge": 8,   "m6a.4xlarge": 16, "m6a.8xlarge": 32,
    "r5.xlarge": 4,    "r5.2xlarge": 8,    "r5.4xlarge": 16,  "r5.8xlarge": 32,
    "r5.12xlarge": 48, "r5.16xlarge": 64,  "r5.24xlarge": 96,
    "r6i.xlarge": 4,   "r6i.2xlarge": 8,   "r6i.4xlarge": 16, "r6i.8xlarge": 32,
    "r6g.xlarge": 4,   "r6g.2xlarge": 8,   "r6g.4xlarge": 16, "r6g.8xlarge": 32,
    "c5.xlarge": 4,    "c5.2xlarge": 8,    "c5.4xlarge": 16,  "c5.9xlarge": 36,  "c5.18xlarge": 72,
    "c6i.xlarge": 4,   "c6i.2xlarge": 8,   "c6i.4xlarge": 16, "c6i.8xlarge": 32,
    "c6g.xlarge": 4,   "c6g.2xlarge": 8,   "c6g.4xlarge": 16, "c6g.8xlarge": 32,
    "i3.xlarge": 4,    "i3.2xlarge": 8,    "i3.4xlarge": 16,  "i3.8xlarge": 32,
    "i3en.xlarge": 4,  "i3en.2xlarge": 8,  "i3en.3xlarge": 12,"i3en.6xlarge": 24,
    "i3en.12xlarge": 48, "i3en.24xlarge": 96,
    "p3.2xlarge": 8,   "p3.8xlarge": 32,   "p3.16xlarge": 64,
    "g4dn.xlarge": 4,  "g4dn.2xlarge": 8,  "g4dn.4xlarge": 16,"g4dn.8xlarge": 32,
}


def _emr_vcpus(usage_type, instance_vcpus):
    """Extract vCPU count for an EMR management fee row."""
    if instance_vcpus:
        try:
            return int(instance_vcpus)
        except (TypeError, ValueError):
            pass
    if not usage_type:
        return None
    # usage_type pattern: "m5.xlarge-EMR-CORE" or "USE2-m5.2xlarge-EMR-MASTER"
    m = re.search(r'([a-z][0-9][a-z0-9]*\.[a-z0-9]+)-EMR', usage_type, re.IGNORECASE)
    if m:
        return _EMR_VCPU_MAP.get(m.group(1).lower())
    return None


def map_emr(rows):
    """EMR management fee → Cloud Dataproc Premium.

    EMR charges a per-node-hour management fee on top of the underlying EC2 cost.
    The EC2 cost is captured separately by compute_breakdown. This mapper converts
    the EMR management premium to the Dataproc cluster service charge.

    Dataproc Premium: $0.01/vCPU-hr. We multiply the node-hours by vCPU count
    to get vCPU-hours, then apply the Dataproc Premium SKU rate.
    When the instance type is unknown, passthrough at cost parity with Dataproc label.
    """
    _DATAPROC_PREMIUM_SKU = "Dataproc Premium"
    out = []
    for r in rows:
        ut = r.get("usage_type") or ""
        gcp_region = r.get("gcp_region")
        blob = f"{ut} {r.get('operation') or ''} {r.get('product') or ''}".lower()

        # Spot/preemptible rows and storage-only rows → passthrough
        if re.search(r"spot|storage|backup", blob):
            out.append({
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Cloud Dataproc",
                "gcp_sku_id":         None,
                "gcp_sku_name":       None,
                "component":          "management",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    f"EMR spot/storage row — passthrough at cost parity (usage_type={ut!r})",
                "mapping_confidence": 0.55,
            })
            continue

        vcpus = _emr_vcpus(ut, r.get("instance_vcpus"))
        if vcpus:
            sku_id = resolve_sku("Cloud Dataproc", _DATAPROC_PREMIUM_SKU, gcp_region)
            entry = {
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Cloud Dataproc",
                "gcp_sku_name":       _DATAPROC_PREMIUM_SKU,
                "component":          "management",
                "strategy":           "map" if sku_id else "passthrough",
                "unit_multiplier":    float(vcpus),
                "gcp_region":         gcp_region,
                "projection_note":    f"EMR management fee → Dataproc Premium ({vcpus} vCPU × $0.01/hr per node-hour)",
                "mapping_confidence": 0.70,
            }
            if sku_id:
                entry["gcp_sku_id"] = sku_id
                entry["gcp_sku_unit"] = sku_id.unit
        else:
            entry = {
                "aws_li_key":         r["aws_li_key"],
                "gcp_service":        "Cloud Dataproc",
                "gcp_sku_id":         None,
                "gcp_sku_name":       None,
                "component":          "management",
                "strategy":           "passthrough",
                "unit_multiplier":    1.0,
                "gcp_region":         gcp_region,
                "projection_note":    f"EMR management fee — vCPU count unknown for usage_type={ut!r}; passthrough at cost parity",
                "mapping_confidence": 0.45,
            }
        out.append(entry)
    return out


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    print(f"SKU cache: {RESOLVED_SKUS_FILE}")
    con = duckdb.connect(db_path)

    rows = con.execute("""
        SELECT aws_li_key, mechanic_group, product, usage_type, pricing_unit AS unit,
               aws_amortized_cost, aws_region AS region, gcp_region, operation, volume_type,
               total_usage, instance_type, instance_ram_gb
        FROM aws_li_catalog
        WHERE mechanic_group IN
              ('flat_hourly', 'object_storage', 'per_request', 'block_storage', 'data_transfer',
               'non_workload', 'cloudwatch', 'guardduty', 'redshift', 'athena', 'kinesis', 'efs',
               'xray', 'fsx', 'emr', 'elasticache')
    """).fetchall()
    con.close()

    cols = ["aws_li_key", "mechanic_group", "product", "usage_type", "unit",
            "aws_amortized_cost", "region", "gcp_region", "operation", "volume_type",
            "total_usage", "instance_type", "instance_ram_gb"]
    by_group: dict[str, list] = {g: [] for g in
                                 ("flat_hourly", "object_storage", "per_request",
                                  "block_storage", "data_transfer", "non_workload", "cloudwatch",
                                  "guardduty", "redshift", "athena", "kinesis", "efs", "xray",
                                  "fsx", "emr", "elasticache")}
    for raw in rows:
        r = dict(zip(cols, raw))
        by_group[r["mechanic_group"]].append(r)

    out_dir = os.path.join(os.path.dirname(db_path), "mappings")
    os.makedirs(out_dir, exist_ok=True)

    handlers = {
        "flat_hourly":    map_flat_hourly,
        "object_storage": map_object_storage,
        "per_request":    map_per_request,
        "block_storage":  map_block_storage,
        "data_transfer":  map_data_transfer,
        "non_workload":   map_non_workload,
        "cloudwatch":     map_cloudwatch,
        "guardduty":      map_guardduty,
        "redshift":       map_redshift,
        "athena":         map_athena,
        "kinesis":        map_kinesis,
        "efs":            map_efs,
        "xray":           map_xray,
        "fsx":            map_fsx,
        "emr":            map_emr,
        "elasticache":    map_elasticache,
    }
    all_llm_rows: list[dict] = []

    for group, handler in handlers.items():
        try:
            result = handler(by_group[group])
        except Exception as e:
            # A mapper failure must never stop report generation. Log it and emit
            # an empty mapping file so downstream phases see the group as handled.
            print(f"WARNING: {group} mapper raised {type(e).__name__}: {e} — skipping group, rows will be passthrough", file=sys.stderr)
            result = []

        # Handlers that have LLM fallback return (mapped, llm_rows).
        # Legacy handlers return a plain list — treat as (result, []).
        if isinstance(result, tuple):
            mappings, llm_rows = result
            all_llm_rows.extend(llm_rows)
        else:
            mappings = result

        path = os.path.join(out_dir, f"{group}_mappings.json")
        with open(path, "w") as f:
            json.dump(mappings, f, indent=2)
        llm_note = f" (+{len(llm_rows)} → LLM)" if isinstance(result, tuple) and llm_rows else ""
        print(f"{group}: {len(mappings)} rows → {path}{llm_note}")

    # Inject unknown rows from static mappers into the manifest misc group so the
    # Phase 2 LLM handles them with full context instead of them being silently lost.
    if all_llm_rows:
        manifest_path = os.path.join(os.path.dirname(db_path), "phase2_manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path) as f:
                manifest = json.load(f)
            misc = manifest.setdefault("misc", {"row_count": 0, "rows": []})
            for r in all_llm_rows:
                misc["rows"].append({
                    "aws_li_key":          r["aws_li_key"],
                    "product":             r.get("product"),
                    "usage_type":          r.get("usage_type"),
                    "operation":           r.get("operation"),
                    "gcp_region":          r.get("gcp_region"),
                    "aws_amortized_cost":  r.get("aws_amortized_cost"),
                    "mechanic_group":      r.get("mechanic_group"),
                    "_injected_from_static": True,
                })
            misc["row_count"] = len(misc["rows"])
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)
            print(f"\nInjected {len(all_llm_rows)} unknown static-mapper row(s) into misc for LLM.")
        else:
            print(f"\nWARNING: {len(all_llm_rows)} unknown row(s) could not be injected "
                  f"— phase2_manifest.json not found at {manifest_path}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Never exit 1 — a partial mapping is always better than no report.
        print(f"FATAL in apply_static_mappings: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(0)
