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

import json, os, re, sys
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

# flat_hourly: usage_type/product fragment → GCP service + SKU family
FLAT_HOURLY_MAP = [
    (r"LoadBalancerUsage.*application|ALB",  "Cloud Load Balancing", "Application Load Balancer Forwarding Rule", 1.0),
    (r"LoadBalancerUsage.*network|NLB",      "Cloud Load Balancing", "Network Load Balancer Forwarding Rule",     1.0),
    (r"LoadBalancerUsage",                   "Cloud Load Balancing", "Classic Load Balancer Forwarding Rule",     1.0),
    (r"NatGateway-Hours|NatGateway",         "Cloud NAT",            "NAT Gateway Uptime",                       1.0),
    (r"ElasticIP|EIP",                       "Compute Engine",       "Static IP Charge",                         1.0),
    (r"VPN",                                 "Cloud VPN",            "Cloud VPN Tunnel",                         1.0),
    (r"DirectConnect|DX",                    "Cloud Interconnect",   "Dedicated Interconnect",                   1.0),
]
FLAT_HOURLY_DEFAULT = ("Compute Engine", "Other Hourly Charge", 1.0)

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


def _match(usage_type, product, table):
    combined = f"{usage_type or ''} {product or ''}"
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
        match = _match(r.get("usage_type"), r.get("product"), FLAT_HOURLY_MAP)
        service, sku_name, mult = match if match else FLAT_HOURLY_DEFAULT
        out.append({
            "aws_li_key":       r["aws_li_key"],
            "gcp_service":      service,
            "gcp_sku_name":     sku_name,
            "component":        "hourly",
            "strategy":         "map",
            "unit_multiplier":  mult,
            "gcp_region":       r.get("gcp_region"),
            "projection_note":  f"flat_hourly lookup → {sku_name}",
            "mapping_confidence": 0.90,
        })
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
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    con = duckdb.connect(db_path)

    rows = con.execute("""
        SELECT aws_li_key, mechanic_group, product, usage_type, unit,
               aws_amortized_cost, region, gcp_region
        FROM aws_li_catalog
        WHERE mechanic_group IN ('flat_hourly', 'object_storage', 'per_request')
    """).fetchall()
    con.close()

    cols = ["aws_li_key", "mechanic_group", "product", "usage_type", "unit",
            "aws_amortized_cost", "region", "gcp_region"]
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
