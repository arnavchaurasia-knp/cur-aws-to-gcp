#!/usr/bin/env python3
"""
apply_static_mappings.py — Deterministic mappings for flat_hourly, object_storage,
and per_request groups. Zero LLM tokens spent.

Usage:
    python3 apply_static_mappings.py <projection.duckdb>

Writes three files:
    projection-audit/mappings/flat_hourly_mappings.json
    projection-audit/mappings/object_storage_mappings.json
    projection-audit/mappings/per_request_mappings.json
"""

import gzip, json, os, re, sys
import duckdb

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

# object_storage: S3 storage class → GCS storage class SKU family
S3_CLASS_MAP = {
    "standard":              ("Cloud Storage",  "Regional Storage",          1.0),
    "intelligent":           ("Cloud Storage",  "Regional Storage",          1.0),
    "standardia":            ("Cloud Storage",  "Nearline Storage",          1.0),
    "standard-ia":           ("Cloud Storage",  "Nearline Storage",          1.0),
    "onezone-ia":            ("Cloud Storage",  "Nearline Storage",          1.0),
    "glacier instant":       ("Cloud Storage",  "Coldline Storage",          1.0),
    "glacier flexible":      ("Cloud Storage",  "Coldline Storage",          1.0),
    "glacier deep archive":  ("Cloud Storage",  "Archive Storage",           1.0),
    "reducedredundancy":     ("Cloud Storage",  "Regional Storage",          1.0),
}
S3_CLASS_DEFAULT = ("Cloud Storage", "Regional Storage", 1.0)

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
PER_REQUEST_MAP = [
    (r"Lambda",                "Cloud Run",    "Cloud Run Requests",           1.0),
    (r"AWSLambda",             "Cloud Run",    "Cloud Run CPU Allocation Time", 1.0),
    (r"SQS|SimpleQueue",       "Pub/Sub",      "Message Delivery",             1.0),
    (r"SNS|SimpleNotification","Pub/Sub",      "Message Delivery",             1.0),
    (r"Kinesis",               "Pub/Sub",      "Message Delivery",             1.0),
    (r"ApiGateway|API Gateway","Cloud Endpoints", "API Gateway Requests",      1.0),
    (r"Rekognition",           "Cloud Vision", "Vision API Requests",          1.0),
    (r"Comprehend",            "Natural Language API", "NL API Requests",      1.0),
    (r"Translate",             "Cloud Translation", "Translation Characters",  1.0),
    (r"Polly",                 "Text-to-Speech", "TTS Characters",             1.0),
    (r"Transcribe",            "Speech-to-Text", "STT Audio",                  1.0),
]
PER_REQUEST_DEFAULT = ("Cloud Run", "Requests", 1.0)


DATA_DIR = os.path.join(os.environ.get("SKILL_DIR", ""), "data")
# Cache file: projection-audit/resolved_skus.json — built once per job, reused on retry
RESOLVED_SKUS_FILE = None  # set in main()


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


def resolve_sku(gcp_service, desc_pattern, gcp_region):
    """
    Return the SKU ID for (service, description_pattern, region).
    Results are cached in RESOLVED_SKUS_FILE so the catalog is scanned only once per
    unique (service, desc_pattern, region) tuple across the entire job.
    """
    global RESOLVED_SKUS_FILE
    cache = {}
    if RESOLVED_SKUS_FILE and os.path.exists(RESOLVED_SKUS_FILE):
        try:
            with open(RESOLVED_SKUS_FILE) as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    key = f"{gcp_service}|{desc_pattern}|{gcp_region or ''}"
    if key in cache:
        return cache[key]

    sku_id = lookup_sku_in_catalog(gcp_service, desc_pattern, gcp_region)
    cache[key] = sku_id

    if RESOLVED_SKUS_FILE:
        try:
            os.makedirs(os.path.dirname(RESOLVED_SKUS_FILE), exist_ok=True)
            with open(RESOLVED_SKUS_FILE, "w") as f:
                json.dump(cache, f, indent=2)
        except Exception:
            pass

    if sku_id:
        print(f"  resolved SKU: {gcp_service} / {desc_pattern!r} → {sku_id}")
    else:
        print(f"  WARNING: no SKU found for {gcp_service} / {desc_pattern!r} in region {gcp_region}")
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
        usage_type = (r.get("usage_type") or "").lower()
        service, sku_name, mult = S3_CLASS_DEFAULT
        for key, val in S3_CLASS_MAP.items():
            if key in usage_type:
                service, sku_name, mult = val
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


def map_per_request(rows):
    out = []
    for r in rows:
        match = _match(r.get("usage_type"), r.get("product"), PER_REQUEST_MAP)
        service, sku_name, mult = match if match else PER_REQUEST_DEFAULT
        out.append({
            "aws_li_key":       r["aws_li_key"],
            "gcp_service":      service,
            "gcp_sku_name":     sku_name,
            "component":        "requests",
            "strategy":         "map",
            "unit_multiplier":  mult,
            "gcp_region":       r.get("gcp_region"),
            "projection_note":  f"per_request lookup → {sku_name}",
            "mapping_confidence": 0.85,
        })
    return out


def main():
    global RESOLVED_SKUS_FILE

    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    # Cache resolved SKU IDs alongside the DB so retries reuse previous lookups
    RESOLVED_SKUS_FILE = os.path.join(os.path.dirname(db_path), "resolved_skus.json")
    print(f"SKU cache: {RESOLVED_SKUS_FILE}")

    con = duckdb.connect(db_path)

    rows = con.execute("""
        SELECT aws_li_key, mechanic_group, product, usage_type, pricing_unit AS unit,
               aws_amortized_cost, aws_region AS region, gcp_region, operation
        FROM aws_li_catalog
        WHERE mechanic_group IN ('flat_hourly', 'object_storage', 'per_request')
    """).fetchall()
    con.close()

    cols = ["aws_li_key", "mechanic_group", "product", "usage_type", "unit",
            "aws_amortized_cost", "region", "gcp_region", "operation"]
    by_group: dict[str, list] = {"flat_hourly": [], "object_storage": [], "per_request": []}
    for raw in rows:
        r = dict(zip(cols, raw))
        by_group[r["mechanic_group"]].append(r)

    out_dir = os.path.join(os.path.dirname(db_path), "mappings")
    os.makedirs(out_dir, exist_ok=True)

    handlers = {
        "flat_hourly":    map_flat_hourly,
        "object_storage": map_object_storage,
        "per_request":    map_per_request,
    }
    for group, handler in handlers.items():
        mappings = handler(by_group[group])
        path = os.path.join(out_dir, f"{group}_mappings.json")
        with open(path, "w") as f:
            json.dump(mappings, f, indent=2)
        print(f"{group}: {len(mappings)} rows → {path}")


if __name__ == "__main__":
    main()
