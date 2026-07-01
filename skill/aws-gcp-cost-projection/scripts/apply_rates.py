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

# Published GCP CUD discount multipliers (rate_usd = od_rate * multiplier).
# "1yr" ~30% off OnDemand; "3yr" ~45% off OnDemand for Compute Engine.
CUD_PCT = {
    "Compute Engine": {"1yr": 0.70, "3yr": 0.55},   # ~30%/45% discount
    "Cloud SQL":      {"1yr": 0.75, "3yr": 0.60},   # ~25%/40% discount
    "Cloud Spanner":  {"1yr": 0.75, "3yr": 0.60},
    "Cloud Bigtable": {"1yr": 0.75, "3yr": 0.60},
    "Memorystore":    {"1yr": 0.80, "3yr": 0.65},
    "DEFAULT":        {"1yr": 0.75, "3yr": 0.60},   # conservative fallback
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


def synthesize_cud_rates(con, sku_ids: list) -> int:
    """For each OnDemand rate in gcp_sku_rates that has no Commit1Yr or Commit3Yr row,
    synthesize them using published GCP CUD discount percentages.
    Returns count of rows synthesized."""

    # Find (gcp_sku_id, region, gcp_service) pairs with OnDemand but missing CUD rows.
    missing = con.execute("""
        SELECT od.gcp_sku_id, od.region, od.gcp_service, od.gcp_sku_name,
               od.resource_family, od.resource_group, od.unit, od.rate_usd
        FROM gcp_sku_rates od
        WHERE od.pricing_type = 'OnDemand'
          AND od.gcp_sku_id = ANY(?)
          AND NOT EXISTS (
              SELECT 1 FROM gcp_sku_rates c
              WHERE c.gcp_sku_id = od.gcp_sku_id
                AND c.region = od.region
                AND c.pricing_type IN ('Commit1Yr', 'Commit3Yr')
          )
    """, [sku_ids]).fetchall()

    if not missing:
        return 0

    synthesized = 0
    rows_to_insert = []
    for (sku_id, region, gcp_service, sku_name, res_family, res_group, unit, od_rate) in missing:
        # Find the best matching CUD_PCT key by checking if the service name contains a known key.
        pct_key = "DEFAULT"
        for k in CUD_PCT:
            if k != "DEFAULT" and k.lower() in (gcp_service or "").lower():
                pct_key = k
                break

        pcts = CUD_PCT[pct_key]
        for commit_type, multiplier in [("Commit1Yr", pcts["1yr"]), ("Commit3Yr", pcts["3yr"])]:
            rows_to_insert.append((
                sku_id, gcp_service, sku_name, res_family, res_group,
                commit_type, region, unit,
                od_rate * multiplier, "apply_rates-cud-synth",
                "https://cloud.google.com/compute/docs/instances/signing-up-committed-use-discounts"
            ))
            synthesized += 1

    for row in rows_to_insert:
        con.execute("""
            INSERT OR REPLACE INTO gcp_sku_rates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, row)

    return synthesized


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

    all_sku_ids = [sku_id for sku_id, _ in skus_used]

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

        # For flat-rate SKUs (single tier) use the base rate directly.
        # For tiered SKUs (multiple tiers), using MAX usage across all LIs sharing this SKU
        # over-projects cost for lighter LIs. Instead we use MEDIAN usage as a better heuristic.
        # TODO(schema): For an exact fix, add aws_li_key TEXT (nullable) to gcp_sku_rates,
        #   insert one per-LI rate row (with aws_li_key set) for tiered SKUs, and update the
        #   gcp_projection view to prefer the specific row over the generic NULL row via:
        #     LEFT JOIN gcp_sku_rates r ON r.gcp_sku_id = m.gcp_sku_id
        #       AND (r.aws_li_key = c.aws_li_key OR r.aws_li_key IS NULL)
        #       AND r.region = ...
        #     ORDER BY r.aws_li_key NULLS LAST
        #   That requires a schema migration and is deferred.
        base_rate = parsed_tiers[0]["rate"]
        if len(parsed_tiers) > 1:
            # Use MEDIAN usage across all LIs for this SKU (heuristic, avoids MAX over-projection).
            median_qty = conn.execute(f"""
                SELECT MEDIAN(c.total_usage) FROM aws_li_catalog c
                JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key
                WHERE m.gcp_sku_id = '{sku_id}'
            """).fetchone()[0] or 0.0
            base_rate = blended_rate(parsed_tiers, median_qty)

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

    # Apply CUD synthesis: for every OnDemand SKU with no CUD rows yet, synthesize
    # Commit1Yr / Commit3Yr using published GCP discount percentages.
    n_synth = synthesize_cud_rates(conn, all_sku_ids)
    print(f"Synthesized {n_synth} CUD rate rows")

    # Legacy hard-coded CUD fallbacks kept for services not covered by the generic synthesizer
    # (these use tighter resource_group filters and service-specific docs URLs).
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
        FROM gcp_sku_rates od
        WHERE od.gcp_service = 'Cloud SQL' AND od.pricing_type = 'OnDemand'
          AND od.resource_group IN ('SQLGen2InstancesCPU','SQLInstancesCPU','SQLGen2InstancesRAM','SQLInstancesRAM','SQLGen2InstancesPD-SSD','SQLInstancesPD-SSD')
          AND NOT EXISTS (SELECT 1 FROM gcp_sku_rates r WHERE r.gcp_sku_id = od.gcp_sku_id AND r.region = od.region AND r.pricing_type = 'Commit1Yr');

        INSERT INTO gcp_sku_rates
        SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
               resource_group, 'Commit3Yr', region, unit,
               rate_usd * 0.48, 'doc-percentage', 'https://cloud.google.com/sql/cud'
        FROM gcp_sku_rates od
        WHERE od.gcp_service = 'Cloud SQL' AND od.pricing_type = 'OnDemand'
          AND od.resource_group IN ('SQLGen2InstancesCPU','SQLInstancesCPU','SQLGen2InstancesRAM','SQLInstancesRAM','SQLGen2InstancesPD-SSD','SQLInstancesPD-SSD')
          AND NOT EXISTS (SELECT 1 FROM gcp_sku_rates r WHERE r.gcp_sku_id = od.gcp_sku_id AND r.region = od.region AND r.pricing_type = 'Commit3Yr');

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
