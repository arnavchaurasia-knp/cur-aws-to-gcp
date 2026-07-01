#!/usr/bin/env python3
"""
prefetch_skus.py — Pre-fetch GCP SKU IDs and AWS instance pricing at job start.

Runs once before Phase 2 (as a pre_llm_script). Builds/appends to:
  projection-audit/resolved_skus.json  — {lookup_key: sku_id} for flat_hourly lookups
  projection-audit/aws_instance_prices.json — {instance_type: {od_hourly, 1yr_hourly}} for enrichment

GCP fetch: queries the Cloud Billing Catalog API for each relevant service × region.
  Auth: tries GOOGLE_CLOUD_API_KEY env var, then `gcloud auth print-access-token`.
  Falls back silently to bundled catalog if auth is unavailable.

AWS fetch: queries the public AWS Pricing API (no auth) for EC2 + RDS instance pricing
  in the job's AWS region. Appends any instance types not already in the static JSON.

Usage:
    python3 prefetch_skus.py <projection.duckdb>
"""

import gzip, json, os, re, subprocess, sys, urllib.request, urllib.parse
import duckdb

SKILL_DIR = os.environ.get("SKILL_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR  = os.path.join(SKILL_DIR, "data")

# Global persistent caches — shared across all jobs, appended to never overwritten
RESOLVED_SKUS_FILE = os.path.join(DATA_DIR, "resolved_skus.json")
AWS_PRICES_FILE    = os.path.join(DATA_DIR, "aws_instance_prices.json")

# GCP services we always need regardless of bill content
CORE_GCP_SERVICES = [
    "Compute Engine",
    "Cloud SQL",
    "Cloud Storage",
    "Networking",
    "Cloud Memorystore for Memcached",
    "Cloud Memorystore for Redis",
    "Cloud Memorystore",
    "Artifact Registry",
]

# AWS Pricing API base — publicly accessible, no credentials required
AWS_PRICING_BASE = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else {}


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _append_json(path, new_entries):
    """Append only the keys that are not yet in the file — never overwrites existing entries."""
    existing = _load_json(path, {})
    added = 0
    for k, v in new_entries.items():
        if k not in existing:
            existing[k] = v
            added += 1
    if added:
        _save_json(path, existing)
    return added


# ---------------------------------------------------------------------------
# GCP token
# ---------------------------------------------------------------------------

def _gcp_token():
    """Return a Bearer token. Tries API key env var, then gcloud."""
    api_key = os.environ.get("GOOGLE_CLOUD_API_KEY") or os.environ.get("GCP_API_KEY")
    if api_key:
        return ("key", api_key)
    try:
        tok = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        if tok:
            return ("bearer", tok)
    except Exception:
        pass
    return None


def _gcp_get(url, token_info):
    if token_info is None:
        return None
    kind, value = token_info
    if kind == "key":
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}key={value}"
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {value}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  GCP fetch failed: {e}")
        return None


# ---------------------------------------------------------------------------
# GCP SKU prefetch
# ---------------------------------------------------------------------------

def _services_map():
    path = os.path.join(DATA_DIR, "services.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {s["displayName"]: s["serviceId"] for s in json.load(f)}


def _bundled_sku_lookup(service_id, desc_pattern, gcp_region):
    """Search the bundled (possibly stale) catalog as fast fallback."""
    sku_file = os.path.join(DATA_DIR, "skus", f"{service_id}.json.gz")
    if not os.path.exists(sku_file):
        return None
    with gzip.open(sku_file, "rt") as f:
        skus = json.load(f)
    for sku in skus:
        if not re.search(desc_pattern, sku.get("description", ""), re.IGNORECASE):
            continue
        geo = sku.get("geoTaxonomy", {})
        if geo.get("type") == "GLOBAL" or gcp_region in sku.get("serviceRegions", []):
            return sku["skuId"]
    return None


def fetch_gcp_skus_for_region(gcp_region, services_needed, token_info):
    """
    For each service, page through the Cloud Billing Catalog API and collect
    all SKUs that serve gcp_region. Returns {desc_lower: sku_id}.
    Uses bundled catalog as fallback when API is unavailable.
    """
    svc_map = _services_map()
    new_entries = {}

    for svc_name in services_needed:
        svc_id = svc_map.get(svc_name)
        if not svc_id:
            print(f"  [GCP] service not in services.json: {svc_name}")
            continue

        skus = []
        if token_info:
            page_token = ""
            while True:
                url = f"https://cloudbilling.googleapis.com/v1/services/{svc_id}/skus?pageSize=5000"
                if page_token:
                    url += f"&pageToken={urllib.parse.quote(page_token)}"
                data = _gcp_get(url, token_info)
                if not data:
                    break
                skus.extend(data.get("skus", []))
                page_token = data.get("nextPageToken", "")
                if not page_token:
                    break
            print(f"  [GCP] {svc_name}: {len(skus)} SKUs fetched from API")
        else:
            # Use bundled catalog
            sku_file = os.path.join(DATA_DIR, "skus", f"{svc_id}.json.gz")
            if os.path.exists(sku_file):
                with gzip.open(sku_file, "rt") as f:
                    skus = json.load(f)
                print(f"  [GCP] {svc_name}: {len(skus)} SKUs from bundled catalog")

        for sku in skus:
            geo = sku.get("geoTaxonomy", {})
            in_region = (
                geo.get("type") == "GLOBAL"
                or gcp_region in sku.get("serviceRegions", [])
            )
            if not in_region:
                continue
            desc = sku.get("description", "")
            key = f"gcp|{svc_name}|{desc.lower()}|{gcp_region}"
            new_entries[key] = sku["skuId"]

    return new_entries


# ---------------------------------------------------------------------------
# AWS instance pricing prefetch
# ---------------------------------------------------------------------------

def _aws_region_url_code(aws_region):
    """Map AWS region to the pricing URL code."""
    mapping = {
        "ap-southeast-1": "ap-southeast-1",
        "us-east-1": "us-east-1",
        "us-east-2": "us-east-2",
        "us-west-1": "us-west-1",
        "us-west-2": "us-west-2",
        "eu-west-1": "eu-west-1",
        "eu-central-1": "eu-central-1",
        "ap-northeast-1": "ap-northeast-1",
        "ap-south-1": "ap-south-1",
    }
    return mapping.get(aws_region, aws_region)


def fetch_aws_instance_prices(aws_region, instance_types):
    """
    Fetch On-Demand hourly prices from the AWS Pricing API for the given instance types.
    The AWS EC2 pricing index is publicly accessible — no credentials needed.
    Appends to aws_instance_prices.json (only new instance types).
    """
    existing = _load_json(AWS_PRICES_FILE, {})
    needed = [it for it in instance_types if it and it not in existing]
    if not needed:
        print(f"  [AWS] all {len(instance_types)} instance types already cached")
        return 0

    region_code = _aws_region_url_code(aws_region)
    url = f"{AWS_PRICING_BASE}/AmazonEC2/current/{region_code}/index.json"
    print(f"  [AWS EC2] fetching pricing index for {region_code} ...")
    try:
        req = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"  [AWS EC2] fetch failed: {e}")
        return 0

    products = data.get("products", {})
    terms    = data.get("terms", {}).get("OnDemand", {})
    new_prices = {}

    for sku_hash, product in products.items():
        attrs = product.get("attributes", {})
        itype = attrs.get("instanceType", "")
        if itype not in needed:
            continue
        if attrs.get("operatingSystem", "") != "Linux":
            continue
        if attrs.get("tenancy", "Shared") != "Shared":
            continue
        od_terms = terms.get(sku_hash, {})
        for _, term_data in od_terms.items():
            for _, dim in term_data.get("priceDimensions", {}).items():
                price = float(dim.get("pricePerUnit", {}).get("USD", 0))
                if price > 0:
                    new_prices[itype] = {"od_hourly_usd": price, "region": region_code}
                    break

    added = _append_json(AWS_PRICES_FILE, new_prices)
    print(f"  [AWS EC2] {len(new_prices)} prices found, {added} new entries cached")
    return added


def fetch_aws_rds_prices(aws_region, db_instance_types):
    """Fetch RDS On-Demand prices from the AWS Pricing API."""
    existing = _load_json(AWS_PRICES_FILE, {})
    needed = [it for it in db_instance_types if it and f"rds:{it}" not in existing]
    if not needed:
        return 0

    region_code = _aws_region_url_code(aws_region)
    url = f"{AWS_PRICING_BASE}/AmazonRDS/current/{region_code}/index.json"
    print(f"  [AWS RDS] fetching pricing index for {region_code} ...")
    try:
        req = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"  [AWS RDS] fetch failed: {e}")
        return 0

    products = data.get("products", {})
    terms    = data.get("terms", {}).get("OnDemand", {})
    new_prices = {}

    for sku_hash, product in products.items():
        attrs = product.get("attributes", {})
        itype = attrs.get("instanceType", "")
        if itype not in needed:
            continue
        engine = attrs.get("databaseEngine", "")
        deploy = attrs.get("deploymentOption", "Single-AZ")
        od_terms = terms.get(sku_hash, {})
        for _, term_data in od_terms.items():
            for _, dim in term_data.get("priceDimensions", {}).items():
                price = float(dim.get("pricePerUnit", {}).get("USD", 0))
                if price > 0:
                    cache_key = f"rds:{itype}:{engine}:{deploy}"
                    new_prices[cache_key] = {"od_hourly_usd": price, "region": region_code}
                    break

    added = _append_json(AWS_PRICES_FILE, new_prices)
    print(f"  [AWS RDS] {len(new_prices)} prices found, {added} new entries cached")
    return added


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    if not os.path.exists(db_path):
        print(f"DB not found: {db_path} — skipping prefetch", file=sys.stderr)
        sys.exit(0)

    conn = duckdb.connect(db_path, read_only=True)

    # Determine GCP region + AWS region from catalog
    row = conn.execute("""
        SELECT gcp_region, aws_region, COUNT(*) AS n
        FROM aws_li_catalog
        WHERE is_workload AND gcp_region IS NOT NULL
        GROUP BY gcp_region, aws_region
        ORDER BY n DESC LIMIT 1
    """).fetchone()

    if not row:
        print("No workload rows in catalog — skipping prefetch")
        conn.close()
        sys.exit(0)

    gcp_region, aws_region, _ = row
    print(f"Prefetching SKUs for gcp_region={gcp_region}, aws_region={aws_region}")

    # Which GCP services does this bill actually need?
    svc_rows = conn.execute("""
        SELECT DISTINCT gcp_service FROM aws_li_to_gcp_li
        WHERE gcp_service IS NOT NULL
    """).fetchall()
    billed_services = {r[0] for r in svc_rows}

    # Always include core services; add any billed services not already there
    services_needed = list(set(CORE_GCP_SERVICES) | billed_services)

    # Instance types referenced in the bill (EC2)
    ec2_types = [r[0] for r in conn.execute("""
        SELECT DISTINCT instance_type FROM aws_li_catalog
        WHERE instance_type IS NOT NULL
          AND mechanic_group = 'compute_breakdown'
    """).fetchall()]

    # DB instance types referenced in the bill
    db_types_raw = [r[0] for r in conn.execute("""
        SELECT DISTINCT operation FROM aws_li_catalog
        WHERE mechanic_group = 'managed_db'
    """).fetchall()]
    db_types = []
    for op in db_types_raw:
        m = re.search(r'(db\.[a-z0-9]+\.[a-z0-9]+)', op or "")
        if m:
            db_types.append(m.group(1))

    conn.close()

    # ------------------------------------------------------------------
    # 1. GCP SKUs
    # ------------------------------------------------------------------
    print("\n[1/3] GCP SKU prefetch")

    # Check if this region is already fully cached — skip if so
    existing_cache = _load_json(RESOLVED_SKUS_FILE, {})
    region_key_sample = f"gcp|Compute Engine|"
    already_has_region = any(
        k.startswith(region_key_sample) and k.endswith(f"|{gcp_region}")
        for k in existing_cache
    )
    if already_has_region:
        print(f"  Region {gcp_region} already in cache — skipping GCP fetch")
        added_gcp = 0
    else:
        token_info = _gcp_token()
        if token_info:
            print(f"  Auth: {token_info[0]}")
        else:
            print("  Auth: none — using bundled catalog only")
        gcp_entries = fetch_gcp_skus_for_region(gcp_region, services_needed, token_info)
        added_gcp = _append_json(RESOLVED_SKUS_FILE, gcp_entries)
        print(f"  → {len(gcp_entries)} region-matched SKUs, {added_gcp} new entries added to cache")

    # ------------------------------------------------------------------
    # 2. AWS EC2 instance prices
    # ------------------------------------------------------------------
    print("\n[2/3] AWS EC2 instance price prefetch")
    if ec2_types:
        fetch_aws_instance_prices(aws_region, ec2_types)
    else:
        print("  No EC2 instance types in bill")

    # ------------------------------------------------------------------
    # 3. AWS RDS instance prices
    # ------------------------------------------------------------------
    print("\n[3/3] AWS RDS instance price prefetch")
    if db_types:
        fetch_aws_rds_prices(aws_region, db_types)
    else:
        print("  No RDS instance types in bill")

    print(f"\nPrefetch complete. Cache: {RESOLVED_SKUS_FILE}")


if __name__ == "__main__":
    main()
