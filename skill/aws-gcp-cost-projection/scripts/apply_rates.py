#!/usr/bin/env python3
import duckdb
import os
import json
import gzip

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
DATA_DIR = os.path.join(os.environ.get("SKILL_DIR", ""), "data")

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

def main():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        return

    conn = duckdb.connect(DB_PATH)
    
    # Get all required SKUs
    skus_used = conn.execute("""
        SELECT DISTINCT m.gcp_sku_id, m.gcp_service
        FROM aws_li_to_gcp_li m
        WHERE m.gcp_sku_id IS NOT NULL
    """).fetchall()

    if not skus_used:
        print("No SKUs to fill rates for.")
        return

    services_file = os.path.join(DATA_DIR, "services.json")
    services = {}
    if os.path.exists(services_file):
        with open(services_file, "r") as f:
            services_data = json.load(f)
            for s in services_data:
                services[s["displayName"]] = s["serviceId"]

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
    conn.execute("DELETE FROM gcp_sku_rates WHERE source = 'catalog-bundled'")

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
        
        # Insert OD row for all regions
        for r in regions:
            conn.execute("""
                INSERT INTO gcp_sku_rates VALUES 
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        """, (sku_id, gcp_service, sku_data.get("description", "") + f" ({c_type} alias)", resource_family, resource_group,
                              c_type, r, unit, c_rate, "catalog-bundled", f"data/skus/{service_id}.json.gz#alias"))
            
    # Apply CUD synthesis
    conn.execute("""
        INSERT INTO gcp_sku_rates
        SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
               resource_group, 'Commit1Yr', region, unit,
               rate_usd * 0.63, 'doc-percentage', 'https://cloud.google.com/compute/docs/instances/signing-up-committed-use-discounts'
        FROM gcp_sku_rates od
        WHERE od.gcp_service = 'Compute Engine' AND od.pricing_type = 'OnDemand'
          AND od.resource_group IN ('CPU', 'RAM', 'GPU')
          AND NOT EXISTS (SELECT 1 FROM gcp_sku_rates r WHERE r.gcp_sku_id = od.gcp_sku_id AND r.region = od.region AND r.pricing_type = 'Commit1Yr');
          
        INSERT INTO gcp_sku_rates
        SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
               resource_group, 'Commit3Yr', region, unit,
               rate_usd * 0.45, 'doc-percentage', 'https://cloud.google.com/compute/docs/instances/signing-up-committed-use-discounts'
        FROM gcp_sku_rates od
        WHERE od.gcp_service = 'Compute Engine' AND od.pricing_type = 'OnDemand'
          AND od.resource_group IN ('CPU', 'RAM', 'GPU')
          AND NOT EXISTS (SELECT 1 FROM gcp_sku_rates r WHERE r.gcp_sku_id = od.gcp_sku_id AND r.region = od.region AND r.pricing_type = 'Commit3Yr');

        INSERT INTO gcp_sku_rates
        SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
               resource_group, 'Commit1Yr', region, unit,
               rate_usd * 0.75, 'doc-percentage', 'https://cloud.google.com/sql/cud'
        FROM gcp_sku_rates
        WHERE gcp_service = 'Cloud SQL' AND pricing_type = 'OnDemand' 
          AND resource_group IN ('SQLGen2InstancesCPU','SQLInstancesCPU','SQLGen2InstancesRAM','SQLInstancesRAM','SQLGen2InstancesPD-SSD','SQLInstancesPD-SSD');
          
        INSERT INTO gcp_sku_rates
        SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
               resource_group, 'Commit3Yr', region, unit,
               rate_usd * 0.48, 'doc-percentage', 'https://cloud.google.com/sql/cud'
        FROM gcp_sku_rates
        WHERE gcp_service = 'Cloud SQL' AND pricing_type = 'OnDemand' 
          AND resource_group IN ('SQLGen2InstancesCPU','SQLInstancesCPU','SQLGen2InstancesRAM','SQLInstancesRAM','SQLGen2InstancesPD-SSD','SQLInstancesPD-SSD');
          
        INSERT INTO gcp_sku_rates
        SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
               resource_group, 'Commit1Yr', region, unit,
               rate_usd * 0.80, 'doc-percentage', 'https://cloud.google.com/alloydb/pricing'
        FROM gcp_sku_rates
        WHERE gcp_service = 'AlloyDB' AND pricing_type = 'OnDemand';
        
        INSERT INTO gcp_sku_rates
        SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
               resource_group, 'Commit3Yr', region, unit,
               rate_usd * 0.60, 'doc-percentage', 'https://cloud.google.com/alloydb/pricing'
        FROM gcp_sku_rates
        WHERE gcp_service = 'AlloyDB' AND pricing_type = 'OnDemand';
        
        INSERT INTO gcp_sku_rates
        SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
               resource_group, 'Commit1Yr', region, unit,
               rate_usd * 0.75, 'doc-percentage', 'https://cloud.google.com/memorystore/docs/redis/committed-use-discounts'
        FROM gcp_sku_rates
        WHERE gcp_service IN ('Cloud Memorystore', 'Cloud Memorystore for Redis', 'Cloud Memorystore for Memcached') AND pricing_type = 'OnDemand'
          AND resource_group IN ('RedisCapacityBasicM1','RedisCapacityBasicM2','MemcacheNode');
          
        INSERT INTO gcp_sku_rates
        SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
               resource_group, 'Commit3Yr', region, unit,
               rate_usd * 0.52, 'doc-percentage', 'https://cloud.google.com/memorystore/docs/redis/committed-use-discounts'
        FROM gcp_sku_rates
        WHERE gcp_service IN ('Cloud Memorystore', 'Cloud Memorystore for Redis', 'Cloud Memorystore for Memcached') AND pricing_type = 'OnDemand'
          AND resource_group IN ('RedisCapacityBasicM1','RedisCapacityBasicM2','MemcacheNode');
    """)

    print("Rate fill complete.")

if __name__ == "__main__":
    main()
