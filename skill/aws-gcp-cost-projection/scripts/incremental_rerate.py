#!/usr/bin/env python3
"""
incremental_rerate.py — Phase 5 post-LLM rate refresh.

Phase 4 already built the full gcp_sku_rates table. Phase 5's LLM only changes
gcp_sku_id or unit_multiplier on a handful of rows. This script:
  1. Finds SKU IDs now in aws_li_to_gcp_li that have NO entry in gcp_sku_rates.
  2. Loads and upserts rates only for those missing SKUs.
  3. Recreates the projection VIEW.

This replaces running the full apply_rates.py again in Phase 5 PostLLMScripts,
which dropped and rebuilt the entire rate table (loading all catalog gzip files)
even though only 1-5 SKUs changed.
"""

import duckdb
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from projection_view import create_projection_view
from apply_rates import (
    CONTAINER_CODES,
    blended_rate,
    extract_rate,
    load_cud_pct,
    flag_license_exposure,
)

JOB_DIR  = os.getcwd()
DB_PATH  = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
DATA_DIR = os.path.join(os.environ.get("SKILL_DIR", ""), "data")


def fill_missing_skus(conn, services):
    """Load rates for any gcp_sku_id in aws_li_to_gcp_li not already in gcp_sku_rates."""
    missing = conn.execute("""
        SELECT DISTINCT m.gcp_sku_id, m.gcp_service
        FROM aws_li_to_gcp_li m
        WHERE m.gcp_sku_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM gcp_sku_rates r
              WHERE r.gcp_sku_id = m.gcp_sku_id
          )
    """).fetchall()

    if not missing:
        print("  incremental_rerate: no new SKUs to load")
        return 0

    print(f"  incremental_rerate: loading {len(missing)} new SKU(s)")

    loaded = 0
    for sku_id, gcp_service in missing:
        service_id = services.get(gcp_service)
        if not service_id:
            print(f"    skip {sku_id}: service '{gcp_service}' not in services.json")
            continue

        sku_file = os.path.join(DATA_DIR, "skus", f"{service_id}.json.gz")
        if not os.path.exists(sku_file):
            print(f"    skip {sku_id}: catalog file missing for {gcp_service}")
            continue

        with gzip.open(sku_file, "rt") as f:
            all_skus = json.load(f)

        sku_data = next((s for s in all_skus if s["skuId"] == sku_id), None)
        if not sku_data:
            print(f"    skip {sku_id}: not found in {gcp_service} catalog")
            continue

        category       = sku_data.get("category", {})
        resource_family = category.get("resourceFamily", "")
        resource_group  = category.get("resourceGroup", "")
        usage_type      = category.get("usageType", "OnDemand")

        regions = set()
        geo = sku_data.get("geoTaxonomy", {})
        if geo.get("type") == "GLOBAL":
            regions.add("global")
        for r in sku_data.get("serviceRegions", []):
            if r.lower() in CONTAINER_CODES:
                regions.update(CONTAINER_CODES[r.lower()])
            else:
                regions.add(r)

        pricing_info = sku_data.get("pricingInfo", [])
        if not pricing_info:
            continue
        pe = pricing_info[0].get("pricingExpression", {})
        unit = pe.get("usageUnit", "")
        tiered_rates = pe.get("tieredRates", [])
        parsed_tiers = sorted(
            [{"startUsageAmount": t.get("startUsageAmount", 0),
              "rate": extract_rate(t.get("unitPrice"))} for t in tiered_rates],
            key=lambda x: x["startUsageAmount"]
        )
        if not parsed_tiers:
            continue

        base_rate = parsed_tiers[0]["rate"]
        if len(parsed_tiers) > 1:
            total_qty = conn.execute(f"""
                SELECT MAX(c.total_usage) FROM aws_li_catalog c
                JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key
                WHERE m.gcp_sku_id = '{sku_id}'
            """).fetchone()[0] or 0.0
            base_rate = blended_rate(parsed_tiers, total_qty)

        audit_url = f"data/skus/{service_id}.json.gz#{sku_id}"
        for r in regions:
            conn.execute("""
                INSERT INTO gcp_sku_rates VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT DO NOTHING
            """, (sku_id, gcp_service, sku_data.get("description",""),
                  resource_family, resource_group, usage_type,
                  r, unit, base_rate, "catalog-bundled", audit_url))

        # CUD aliases for Compute Engine CPU/RAM/GPU SKUs
        if gcp_service == "Compute Engine" and usage_type == "OnDemand" \
                and resource_group in ["CPU", "RAM", "GPU"]:
            desc = sku_data.get("description", "")
            for s in all_skus:
                ct = s.get("category", {}).get("usageType", "")
                if s.get("description") == desc and ct in ("Commit1Yr", "Commit3Yr"):
                    pi = s.get("pricingInfo", [])
                    if pi:
                        c_rate = extract_rate(
                            pi[0].get("pricingExpression", {})
                               .get("tieredRates", [{}])[0].get("unitPrice"))
                        for r in regions:
                            conn.execute("""
                                INSERT INTO gcp_sku_rates VALUES (?,?,?,?,?,?,?,?,?,?,?)
                                ON CONFLICT DO NOTHING
                            """, (sku_id, gcp_service, desc + f" ({ct} alias)",
                                  resource_family, resource_group, ct,
                                  r, unit, c_rate, "catalog-bundled",
                                  f"data/skus/{service_id}.json.gz#alias"))

        loaded += 1
        print(f"    loaded: {gcp_service} / {sku_id}")

    return loaded


def synthesize_cud_for_new_skus(conn):
    """Synthesize Commit1Yr/3yr rows for any newly-loaded SKUs that don't have them yet."""
    cud_pct = load_cud_pct()
    _default_pct = cud_pct.get("DEFAULT", (0.75, 0.60))

    _CUD_GROUPS = [
        ("Compute Engine",                    "('CPU','RAM','GPU')"),
        ("Cloud SQL",                         None),
        ("AlloyDB",                           None),
        ("Cloud Memorystore for Memcached",   None),
        ("Cloud Memorystore for Redis",       None),
        ("Cloud Memorystore",                 None),
    ]
    for svc, rg_list in _CUD_GROUPS:
        r1, r3 = cud_pct.get(svc, _default_pct)
        rg_clause = f"AND resource_group IN {rg_list}" if rg_list else ""
        for pricing_type, mult in [("Commit1Yr", r1), ("Commit3Yr", r3)]:
            conn.execute(f"""
                INSERT INTO gcp_sku_rates
                SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
                       resource_group, '{pricing_type}', region, unit,
                       rate_usd * {mult}, 'doc-percentage', audit_url
                FROM gcp_sku_rates
                WHERE gcp_service = '{svc}' AND pricing_type = 'OnDemand'
                  {rg_clause}
                  AND NOT EXISTS (
                      SELECT 1 FROM gcp_sku_rates r2
                      WHERE r2.gcp_sku_id = gcp_sku_rates.gcp_sku_id
                        AND r2.pricing_type = '{pricing_type}'
                        AND r2.region = gcp_sku_rates.region
                  )
                ON CONFLICT DO NOTHING
            """)


def main():
    if not os.path.exists(DB_PATH):
        print("Database not found — nothing to re-rate.")
        sys.exit(0)

    conn = duckdb.connect(DB_PATH)

    services_file = os.path.join(DATA_DIR, "services.json")
    services = {}
    if os.path.exists(services_file):
        with open(services_file) as f:
            for s in json.load(f):
                services[s["displayName"]] = s["serviceId"]

    loaded = fill_missing_skus(conn, services)
    if loaded:
        synthesize_cud_for_new_skus(conn)

    # Re-flag license exposure in case Phase 5 changed a row's service
    flag_license_exposure(conn)

    # Recreate projection VIEW so the Phase 5 gate sees fresh costs
    create_projection_view(conn)
    print(f"  incremental_rerate: done ({loaded} new SKU(s) loaded)")


if __name__ == "__main__":
    main()
