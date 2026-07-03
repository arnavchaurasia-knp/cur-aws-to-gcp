#!/usr/bin/env python3
import duckdb
import os
import json
import gzip

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
DATA_DIR = os.path.join(os.environ.get("SKILL_DIR", ""), "data")

# CUD discount multipliers — the SINGLE source of truth, shared verbatim with
# validate_fix.py. Both read data/cud_pct.json so 1yr/3yr math is identical no
# matter which script sets a rate. Fallback mirrors validate_fix's fallback.
_CUD_PCT_FALLBACK = {
    "Compute Engine": (0.70, 0.55),
    "Cloud SQL": (0.75, 0.60),
    "Cloud Spanner": (0.75, 0.60),
    "Cloud Bigtable": (0.75, 0.60),
    "Memorystore": (0.80, 0.65),
    "Cloud Memorystore": (0.80, 0.65),
    "Cloud Memorystore for Redis": (0.80, 0.65),
    "Cloud Memorystore for Memcached": (0.80, 0.65),
    "AlloyDB": (0.75, 0.60),
    "Cloud Run": (0.83, 0.67),
    "DEFAULT": (0.75, 0.60),
}


def load_cud_pct():
    """Load CUD multipliers from data/cud_pct.json, falling back to the dict above."""
    json_path = os.path.join(DATA_DIR, "cud_pct.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                raw = json.load(f)
            result = {}
            for svc, vals in raw.items():
                if svc == "_meta":
                    continue
                if isinstance(vals, dict) and "1yr_multiplier" in vals and "3yr_multiplier" in vals:
                    result[svc] = (float(vals["1yr_multiplier"]), float(vals["3yr_multiplier"]))
            if result:
                return result
        except Exception as e:
            print(f"WARNING: could not load cud_pct.json ({e}); using fallback multipliers.")
    return dict(_CUD_PCT_FALLBACK)

CONTAINER_CODES = {
    "us": ["us-central1", "us-east1", "us-east4", "us-east5", "us-south1", "us-west1", "us-west2", "us-west3", "us-west4"],
    "eu": ["europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west6", "europe-west8", "europe-west9", "europe-west10", "europe-west12", "europe-north1", "europe-central2", "europe-southwest1"],
    "europe": ["europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west6", "europe-west8", "europe-west9", "europe-west10", "europe-west12", "europe-north1", "europe-central2", "europe-southwest1"],
    "asia": ["asia-east1", "asia-east2", "asia-northeast1", "asia-northeast2", "asia-northeast3", "asia-south1", "asia-south2", "asia-southeast1", "asia-southeast2"],
    "northamerica": ["northamerica-northeast1", "northamerica-northeast2", "northamerica-south1"],
    "southamerica": ["southamerica-east1", "southamerica-west1"],
    "australia": ["australia-southeast1", "australia-southeast2"],
    "me": ["me-central1", "me-central2", "me-west1"],
    "middleeast": ["me-central1", "me-central2", "me-west1"],
    "africa": ["africa-south1"]
}

def blended_rate(tiered_rates, total_qty):
    total_cost = 0.0
    for i, tier in enumerate(tiered_rates):
        tier_start = tier.get("startUsageAmount", 0)
        tier_end = tiered_rates[i+1].get("startUsageAmount") if i+1 < len(tiered_rates) else float("inf")
        tier_qty = max(0, min(total_qty, tier_end) - tier_start)
        total_cost += tier_qty * tier.get("rate", 0)
    return total_cost / total_qty if total_qty > 0 else tiered_rates[-1].get("rate", 0)

def extract_rate(unit_price):
    if not unit_price: return 0.0
    units = int(unit_price.get("units", 0))
    nanos = int(unit_price.get("nanos", 0))
    return units + (nanos / 1e9)

def _score_sku_match(description: str, gcp_sku_name: str) -> int:
    """Score how well a catalog SKU description matches the LLM-provided gcp_sku_name.
    Higher = better. Uses word-level intersection — no hardcoded rules needed."""
    desc_words = set(description.lower().split())
    name_words = set(gcp_sku_name.lower().split())
    return len(desc_words & name_words)


def _resolve_sku_for_row(gcp_service, gcp_sku_name, gcp_region, services, data_dir):
    """Return the best gcp_sku_id for a NULL-sku mapped row using catalog description search.

    Strategy:
    1. Load the service's SKU catalog file.
    2. For each OnDemand SKU, score its description against gcp_sku_name by word overlap.
    3. Prefer an exact-region match; fall back to any region with the best score.
    No hardcoded rules — the catalog is the contract.
    """
    service_id = services.get(gcp_service)
    if not service_id:
        return None
    sku_file = os.path.join(data_dir, "skus", f"{service_id}.json.gz")
    if not os.path.exists(sku_file):
        return None

    with gzip.open(sku_file, "rt") as f:
        all_skus = json.load(f)

    if not gcp_sku_name:
        return None

    best_exact_score, best_exact_id = -1, None
    best_any_score,   best_any_id   = -1, None

    for s in all_skus:
        if s.get("category", {}).get("usageType") != "OnDemand":
            continue
        desc = s.get("description", "")
        # Skip $0 SKUs that would otherwise win a word-overlap tie and produce a
        # phantom $0 projection on a billed row:
        #   - free-tier / promotional / trial SKUs
        #   - "intra zone" transfer (free on GCP) matching a billed egress line
        #     whose name says "inter zone"/"internet"/"egress" (one word apart,
        #     opposite meaning — billed egress must never resolve to free intra-zone)
        dl = desc.lower()
        if "free tier" in dl or "promotional" in dl or "trial" in dl:
            continue
        if "intra zone" in dl or "intra-zone" in dl or "intra region" in dl or "intra-region" in dl:
            continue
        score = _score_sku_match(desc, gcp_sku_name)
        if score <= 0:
            continue
        sku = s["skuId"]
        regions = s.get("serviceRegions", [])
        # Tie-break on lexically-smallest skuId so an equal-score tie always
        # resolves the same way regardless of catalog ordering — a resolved SKU
        # must never change run-to-run for the same input.
        if gcp_region and gcp_region in regions:
            if score > best_exact_score or (score == best_exact_score and (best_exact_id is None or sku < best_exact_id)):
                best_exact_score, best_exact_id = score, sku
        if score > best_any_score or (score == best_any_score and (best_any_id is None or sku < best_any_id)):
            best_any_score, best_any_id = score, sku

    return best_exact_id or best_any_id


def main():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        return

    conn = duckdb.connect(DB_PATH)

    services_file = os.path.join(DATA_DIR, "services.json")
    services = {}
    if os.path.exists(services_file):
        with open(services_file, "r") as f:
            services_data = json.load(f)
            for s in services_data:
                services[s["displayName"]] = s["serviceId"]

    # Auto-resolve NULL gcp_sku_id for mapped rows using catalog lookup rules.
    # This prevents Phase 5 from seeing NULL projected cost and wrongly setting passthrough.
    null_sku_rows = conn.execute("""
        SELECT m.aws_li_key, m.gcp_service, m.gcp_sku_name, c.gcp_region
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.gcp_sku_id IS NULL
          AND m.strategy IN ('map', 'break_down')
    """).fetchall()

    resolved = 0
    for aws_li_key, gcp_service, gcp_sku_name, gcp_region in null_sku_rows:
        sku_id = _resolve_sku_for_row(gcp_service, gcp_sku_name, gcp_region, services, DATA_DIR)
        if sku_id:
            conn.execute(
                "UPDATE aws_li_to_gcp_li SET gcp_sku_id = ? WHERE aws_li_key = ? AND gcp_sku_id IS NULL",
                (sku_id, aws_li_key),
            )
            resolved += 1
            print(f"  resolved SKU: {gcp_service} / {gcp_sku_name!r} -> {sku_id}")

    if resolved:
        print(f"Auto-resolved {resolved} NULL gcp_sku_id row(s) from catalog")

    # Get all required SKUs (including any just resolved above)
    skus_used = conn.execute("""
        SELECT DISTINCT m.gcp_sku_id, m.gcp_service
        FROM aws_li_to_gcp_li m
        WHERE m.gcp_sku_id IS NOT NULL
    """).fetchall()

    if not skus_used:
        print("No SKUs to fill rates for.")
        return

    # Clear existing rate table just in case
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gcp_sku_rates (
            gcp_sku_id      VARCHAR,
            gcp_service     VARCHAR,
            gcp_sku_name    VARCHAR,
            resource_family VARCHAR,
            resource_group  VARCHAR,
            pricing_type    VARCHAR,
            region          VARCHAR,
            unit            VARCHAR,
            rate_usd        DOUBLE,
            source          VARCHAR,
            audit_url       VARCHAR,
            PRIMARY KEY (gcp_sku_id, pricing_type, region)
        )
    """)
    conn.execute("DELETE FROM gcp_sku_rates")

    # Load SKUs
    for sku_id, gcp_service in skus_used:
        service_id = services.get(gcp_service)
        if not service_id:
            print(f"Service {gcp_service} not found in services.json")
            continue

        sku_file = os.path.join(DATA_DIR, "skus", f"{service_id}.json.gz")
        if not os.path.exists(sku_file):
            print(f"SKU file {sku_file} not found")
            continue

        sku_data = None
        with gzip.open(sku_file, "rt") as f:
            all_skus = json.load(f)
            for s in all_skus:
                if s["skuId"] == sku_id:
                    sku_data = s
                    break
        
        if not sku_data:
            print(f"SKU {sku_id} not found in {sku_file}")
            continue

        category = sku_data.get("category", {})
        resource_family = category.get("resourceFamily", "")
        resource_group = category.get("resourceGroup", "")
        usage_type = category.get("usageType", "OnDemand")

        # Determine regions
        regions = set()
        geo = sku_data.get("geoTaxonomy", {})
        if geo.get("type") == "GLOBAL":
            regions.add("global")
        
        for r in sku_data.get("serviceRegions", []):
            if r.lower() in CONTAINER_CODES:
                regions.update(CONTAINER_CODES[r.lower()])
            else:
                regions.add(r)
        
        # Determine rate
        pricing_info = sku_data.get("pricingInfo", [])
        if not pricing_info: continue
        
        pe = pricing_info[0].get("pricingExpression", {})
        unit = pe.get("usageUnit", "")
        tiered_rates = pe.get("tieredRates", [])
        
        parsed_tiers = []
        for t in tiered_rates:
            rate = extract_rate(t.get("unitPrice"))
            parsed_tiers.append({"startUsageAmount": t.get("startUsageAmount", 0), "rate": rate})
        
        parsed_tiers.sort(key=lambda x: x["startUsageAmount"])
        
        if not parsed_tiers: continue

        # Since multiple LIs might use this SKU with different total_usages, 
        # for flat-rate SKUs we just use the base rate. For tiered rates, we need to fetch the max usage.
        # However, to be safe, if there's only 1 tier, it's flat. 
        # If >1 tier, we just use the blended rate for the max usage across all LIs.
        base_rate = parsed_tiers[0]["rate"]
        if len(parsed_tiers) > 1:
            total_qty = conn.execute(f"""
                SELECT MAX(c.total_usage) FROM aws_li_catalog c
                JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key
                WHERE m.gcp_sku_id = '{sku_id}'
            """).fetchone()[0] or 0.0
            base_rate = blended_rate(parsed_tiers, total_qty)
        
        # Insert OD row for all regions. ON CONFLICT DO NOTHING: one sku_id can
        # expand to overlapping regions via CONTAINER_CODES, and the same key can
        # recur across catalog entries — a bare INSERT would raise and abort the
        # whole rate fill mid-way, leaving a partial table and NULL projections.
        for r in regions:
            conn.execute("""
                INSERT INTO gcp_sku_rates VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
            """, (sku_id, gcp_service, sku_data.get("description", ""), resource_family, resource_group,
                  usage_type, r, unit, base_rate, "catalog-bundled", f"data/skus/{service_id}.json.gz#{sku_id}"))

        # Compute Engine CUD aliasing (pattern 1)
        if gcp_service == "Compute Engine" and usage_type == "OnDemand" and resource_group in ["CPU", "RAM", "GPU"]:
            commit_rates = {"Commit1Yr": None, "Commit3Yr": None}
            desc = sku_data.get("description", "")
            for s in all_skus:
                if s.get("description") == desc and s.get("category", {}).get("usageType") in commit_rates:
                    pi = s.get("pricingInfo", [])
                    if pi:
                        rate = extract_rate(pi[0].get("pricingExpression", {}).get("tieredRates", [{}])[0].get("unitPrice"))
                        commit_rates[s["category"]["usageType"]] = rate

            for c_type, c_rate in commit_rates.items():
                if c_rate is not None:
                    for r in regions:
                        conn.execute("""
                            INSERT INTO gcp_sku_rates VALUES
                            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT DO NOTHING
                        """, (sku_id, gcp_service, sku_data.get("description", "") + f" ({c_type} alias)", resource_family, resource_group,
                              c_type, r, unit, c_rate, "catalog-bundled", f"data/skus/{service_id}.json.gz#alias"))
            
    # CUD synthesis. Discount MULTIPLIERS come from data/cud_pct.json — the SINGLE
    # source of truth shared with validate_fix.py, so both scripts apply identical
    # CUD math and the 1yr/3yr columns never vary by which path last touched them.
    # Only the per-service committable resource_group set lives here (that's rate-
    # table structure, not a discount rate). rg_list=None → apply CUD to all
    # OnDemand rows of the service (e.g. Memorystore node capacity).
    #
    # Service list must cover every service the validator's cud_coverage gate
    # expects — apply_rates wipes the rate table each run, so an uncovered
    # committable service silently falls back to OnDemand for 1yr/3yr and trips
    # the gate.
    cud_pct = load_cud_pct()
    _CUD_GROUPS = [
        ("Compute Engine", "('CPU','RAM','GPU')",
         "https://cloud.google.com/compute/docs/instances/signing-up-committed-use-discounts"),
        ("Cloud SQL", "('SQLGen2InstancesCPU','SQLInstancesCPU','SQLGen2InstancesRAM',"
                      "'SQLInstancesRAM','SQLGen2InstancesPD-SSD','SQLInstancesPD-SSD')",
         "https://cloud.google.com/sql/cud"),
        ("AlloyDB", None, "https://cloud.google.com/alloydb/pricing"),
        ("Cloud Memorystore for Memcached", None,
         "https://cloud.google.com/memorystore/docs/memcached/committed-use-discounts"),
        ("Cloud Memorystore for Redis", None,
         "https://cloud.google.com/memorystore/docs/redis/committed-use-discounts"),
        ("Cloud Memorystore", None, "https://cloud.google.com/memorystore/pricing"),
    ]
    _default_pct = cud_pct.get("DEFAULT", (0.75, 0.60))
    for svc, rg_list, url in _CUD_GROUPS:
        r1, r3 = cud_pct.get(svc, _default_pct)
        rg_clause = f"AND resource_group IN {rg_list}" if rg_list else ""
        conn.execute(f"""
            INSERT INTO gcp_sku_rates
            SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
                   resource_group, 'Commit1Yr', region, unit,
                   rate_usd * {r1}, 'doc-percentage', '{url}'
            FROM gcp_sku_rates
            WHERE gcp_service = '{svc}' AND pricing_type = 'OnDemand'
              {rg_clause}
            ON CONFLICT DO NOTHING
        """)
        conn.execute(f"""
            INSERT INTO gcp_sku_rates
            SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
                   resource_group, 'Commit3Yr', region, unit,
                   rate_usd * {r3}, 'doc-percentage', '{url}'
            FROM gcp_sku_rates
            WHERE gcp_service = '{svc}' AND pricing_type = 'OnDemand'
              {rg_clause}
            ON CONFLICT DO NOTHING
        """)

    # Global fallback: for every SKU that has regional rates but no 'global' row,
    # synthesize a 'global' row by averaging the regional rates. This makes the
    # gcp_projection VIEW resilient to NULL gcp_region in aws_li_catalog — the
    # COALESCE(regional, global) fallback in the VIEW will always find a rate.
    conn.execute("""
        INSERT INTO gcp_sku_rates
        SELECT r.gcp_sku_id, r.gcp_service, r.gcp_sku_name, r.resource_family,
               r.resource_group, r.pricing_type, 'global', r.unit,
               AVG(r.rate_usd), 'global-fallback', MIN(r.audit_url)
        FROM gcp_sku_rates r
        WHERE r.region != 'global'
        GROUP BY r.gcp_sku_id, r.gcp_service, r.gcp_sku_name, r.resource_family,
                 r.resource_group, r.pricing_type, r.unit
        HAVING NOT EXISTS (
            SELECT 1 FROM gcp_sku_rates g
            WHERE g.gcp_sku_id = r.gcp_sku_id
              AND g.pricing_type = r.pricing_type
              AND g.region = 'global'
        )
        ON CONFLICT DO NOTHING
    """)

    print("Rate fill complete.")

if __name__ == "__main__":
    main()
