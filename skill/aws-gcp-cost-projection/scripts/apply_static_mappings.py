#!/usr/bin/env python3
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

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

# object_storage: S3 storage class → GCS storage class SKU family.
# SKU names must use current GCS catalog vocabulary ("Standard Storage <region>").
# The legacy name "Regional Storage" word-matched an Archive Storage SKU
# ($0.0015/GB vs Standard's $0.023/GB — a 15x underprojection on bill3).
S3_CLASS_MAP = {
    "standard":              ("Cloud Storage",  "Standard Storage",          1.0),
    "intelligent":           ("Cloud Storage",  "Standard Storage",          1.0),
    "standardia":            ("Cloud Storage",  "Nearline Storage",          1.0),
    "standard-ia":           ("Cloud Storage",  "Nearline Storage",          1.0),
    "onezone-ia":            ("Cloud Storage",  "Nearline Storage",          1.0),
    "archive instant":       ("Cloud Storage",  "Nearline Storage",          1.0),
    "glacier instant":       ("Cloud Storage",  "Coldline Storage",          1.0),
    "glacier flexible":      ("Cloud Storage",  "Coldline Storage",          1.0),
    "glacier deep archive":  ("Cloud Storage",  "Archive Storage",           1.0),
    "reducedredundancy":     ("Cloud Storage",  "Standard Storage",          1.0),
}
S3_CLASS_DEFAULT = ("Cloud Storage", "Standard Storage", 1.0)

# S3 fees with no GCS equivalent (Intelligent-Tiering per-object monitoring,
# early-delete penalties). Priced as storage they project 2-3x high; carry the
# AWS cost as an honest passthrough instead.
S3_NO_EQUIVALENT_RE = re.compile(
    r"per 1,?000 objects|monitoring and automation|early.?delete", re.IGNORECASE)

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
    (r"DirectConnect|DX",
     "Cloud Interconnect", r"Dedicated Interconnect", 1.0),
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


def lookup_sku_in_catalog(gcp_service, desc_pattern, gcp_region):
    """
    Search the bundled GCP catalog for the first SKU whose description matches
    desc_pattern (regex) and whose serviceRegions include gcp_region (or is GLOBAL).
    Returns skuId string or None.
    """
    services = _load_services()
    service_id = services.get(gcp_service)
    if not service_id:
        return None

    sku_file = os.path.join(DATA_DIR, "skus", f"{service_id}.json.gz")
    if not os.path.exists(sku_file):
        return None

    with gzip.open(sku_file, "rt") as f:
        skus = json.load(f)

    for sku in skus:
        desc = sku.get("description", "")
        if not re.search(desc_pattern, desc, re.IGNORECASE):
            continue
        geo = sku.get("geoTaxonomy", {})
        if geo.get("type") == "GLOBAL":
            return sku["skuId"]
        if gcp_region and gcp_region in sku.get("serviceRegions", []):
            return sku["skuId"]
        # Container region codes (e.g. "asia") also count
        _CONTINENT = {
            "asia": ["asia-east1","asia-east2","asia-northeast1","asia-northeast2","asia-northeast3",
                     "asia-south1","asia-south2","asia-southeast1","asia-southeast2"],
            "europe": ["europe-west1","europe-west2","europe-west3","europe-west4","europe-west6",
                       "europe-north1","europe-central2","europe-southwest1"],
            "us":     ["us-central1","us-east1","us-east4","us-east5","us-south1","us-west1","us-west2","us-west3","us-west4"],
        }
        for region_code in sku.get("serviceRegions", []):
            if gcp_region in _CONTINENT.get(region_code.lower(), []):
                return sku["skuId"]

    return None


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
        return cache[key]  # cache hit — no scan needed

    # Not in cache: try bundled catalog
    sku_id = lookup_sku_in_catalog(gcp_service, desc_pattern, gcp_region)

    if sku_id is None:
        # Genuinely missing — trigger live fetch for this new SKU only
        print(f"  SKU not in bundled catalog, trying live API: {gcp_service} / {desc_pattern!r}")
        sku_id = _live_sku_fetch(gcp_service, desc_pattern, gcp_region)

    if sku_id:
        print(f"  resolved SKU: {gcp_service} / {desc_pattern!r} → {sku_id}")
    else:
        print(f"  WARNING: no SKU found for {gcp_service} / {desc_pattern!r} in {gcp_region}")

    # Append-only write — never overwrite existing entries
    cache[key] = sku_id
    try:
        os.makedirs(os.path.dirname(RESOLVED_SKUS_FILE), exist_ok=True)
        with open(RESOLVED_SKUS_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass

    return sku_id


def _match(usage_type, product, table, operation=None):
    combined = f"{usage_type or ''} {product or ''} {operation or ''}"
    for pattern, *values in table:
        if re.search(pattern, combined, re.IGNORECASE):
            return values
    return None


def map_object_storage(rows):
    out = []
    for r in rows:
        # Detect the storage class from all available text — PDF/summary bills
        # leave usage_type blank but name the class in the operation/description
        # ("Glacier Deep Archive", "Standard-IA", ...). Without this the class
        # always fell back to Standard.
        blob = (f"{r.get('usage_type') or ''} {r.get('operation') or ''} "
                f"{r.get('product') or ''}").lower()
        if S3_NO_EQUIVALENT_RE.search(blob):
            out.append({
                "aws_li_key":       r["aws_li_key"],
                "gcp_service":      "Cloud Storage",
                "gcp_sku_name":     None,
                "component":        "storage",
                "strategy":         "passthrough",
                "unit_multiplier":  1.0,
                "gcp_region":       r.get("gcp_region"),
                "projection_note":  ("S3 fee with no GCS equivalent (per-object "
                                     "monitoring / early delete) — passthrough at cost parity"),
                "mapping_confidence": 0.40,
            })
            continue
        service, sku_name, mult = S3_CLASS_DEFAULT
        # Longest key first so "standard-ia" / "glacier deep archive" win over
        # the bare "standard" substring (which they contain).
        for key in sorted(S3_CLASS_MAP, key=len, reverse=True):
            if key in blob:
                service, sku_name, mult = S3_CLASS_MAP[key]
                break
        out.append({
            "aws_li_key":       r["aws_li_key"],
            "gcp_service":      service,
            "gcp_sku_name":     sku_name,
            "component":        "storage",
            "strategy":         "map",
            "unit_multiplier":  mult,
            "gcp_region":       r.get("gcp_region"),
            "projection_note":  f"S3 class lookup → {sku_name}",
            "mapping_confidence": 0.95,
        })
    return out


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
        out.append(entry)
    return out


def _s3_request_target(operation):
    """S3 request ops → Cloud Storage operations (NOT Cloud Run). PUT/COPY/POST/
    LIST are Class A; GET and everything else are Class B."""
    op = (operation or "").lower()
    if re.search(r"put|copy|post|list", op):
        return ("Cloud Storage", "Class A Operations", 1.0)
    return ("Cloud Storage", "Class B Operations", 1.0)


def map_per_request(rows):
    out = []
    for r in rows:
        product = (r.get("product") or "").lower()
        # S3 request charges map to Cloud Storage operations, not Cloud Run.
        if "s3" in product or "simple storage" in product:
            service, sku_name, mult = _s3_request_target(r.get("operation"))
            strategy, confidence = "map", 0.85
            note = f"per_request lookup → {sku_name}"
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


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    print(f"SKU cache: {RESOLVED_SKUS_FILE}")
    con = duckdb.connect(db_path)

    rows = con.execute("""
        SELECT aws_li_key, mechanic_group, product, usage_type, pricing_unit AS unit,
               aws_amortized_cost, aws_region AS region, gcp_region, operation, volume_type
        FROM aws_li_catalog
        WHERE mechanic_group IN
              ('flat_hourly', 'object_storage', 'per_request', 'block_storage', 'data_transfer', 'non_workload')
    """).fetchall()
    con.close()

    cols = ["aws_li_key", "mechanic_group", "product", "usage_type", "unit",
            "aws_amortized_cost", "region", "gcp_region", "operation", "volume_type"]
    by_group: dict[str, list] = {g: [] for g in
                                 ("flat_hourly", "object_storage", "per_request",
                                  "block_storage", "data_transfer", "non_workload")}
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
    }
    for group, handler in handlers.items():
        mappings = handler(by_group[group])
        path = os.path.join(out_dir, f"{group}_mappings.json")
        with open(path, "w") as f:
            json.dump(mappings, f, indent=2)
        print(f"{group}: {len(mappings)} rows → {path}")


if __name__ == "__main__":
    main()
