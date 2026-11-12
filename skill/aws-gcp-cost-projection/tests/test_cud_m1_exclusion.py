"""Test CUD synthesis exclusion for Memorystore M1 capacity tier (BUG-2).

Per cloud.google.com/memorystore/docs/redis/cuds:
Memorystore for Redis M1 capacity tier (< 5 GB) is excluded from committed-use discounts.
Memcached "Custom Core M1" / "Custom RAM M1" must NOT be excluded.
Redis M2-M5 tiers must NOT be excluded.
"""

import os
import sys
import duckdb

HERE = os.path.dirname(__file__)
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from apply_rates import load_cud_pct, load_cud_eligible, load_cud_exclusions


def test_cud_pct_contains_m1_exclusion():
    exclusions = load_cud_exclusions()
    assert "Cloud Memorystore for Redis" in exclusions or "Memorystore" in exclusions
    re_pattern = exclusions.get("Cloud Memorystore for Redis") or exclusions.get("Memorystore")
    assert "M1" in re_pattern


def test_cud_m1_synthesis_exclusion():
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_name VARCHAR,
            resource_family VARCHAR,
            resource_group VARCHAR,
            pricing_type VARCHAR,
            region VARCHAR,
            unit VARCHAR,
            rate_usd DOUBLE,
            source VARCHAR,
            reference VARCHAR,
            PRIMARY KEY (gcp_sku_id, pricing_type, region)
        )
    """)

    # Populate sample OnDemand SKUs
    skus = [
        ("SKU-REDIS-M1", "Cloud Memorystore for Redis", "Redis Capacity Basic M1", "Storage", "Memory", "OnDemand", "us-central1", "GiBy-hour", 0.049),
        ("SKU-REDIS-STD-M1", "Cloud Memorystore for Redis", "Redis Capacity Standard M1", "Storage", "Memory", "OnDemand", "us-central1", "GiBy-hour", 0.049),
        ("SKU-REDIS-M5", "Cloud Memorystore for Redis", "Redis Capacity Basic M5", "Storage", "Memory", "OnDemand", "us-central1", "GiBy-hour", 0.049),
        ("SKU-MEMCACHED-CORE", "Cloud Memorystore for Memcached", "Custom Core M1", "Compute", "CPU", "OnDemand", "us-central1", "hours", 0.02),
        ("SKU-MEMCACHED-RAM", "Cloud Memorystore for Memcached", "Custom RAM M1", "Storage", "RAM", "OnDemand", "us-central1", "GiBy-hour", 0.003),
    ]
    for row in skus:
        conn.execute("INSERT INTO gcp_sku_rates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'catalog', 'ref')", list(row))

    # Also mapped_services table simulation
    conn.execute("CREATE TABLE aws_li_to_gcp_li (gcp_service VARCHAR, strategy VARCHAR)")
    conn.execute("INSERT INTO aws_li_to_gcp_li VALUES ('Cloud Memorystore for Redis', 'map')")
    conn.execute("INSERT INTO aws_li_to_gcp_li VALUES ('Cloud Memorystore for Memcached', 'map')")

    # Run CUD synthesis logic
    cud_pct = load_cud_pct()
    cud_eligible = load_cud_eligible()
    cud_exclusions = load_cud_exclusions()

    mapped_services = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT gcp_service FROM aws_li_to_gcp_li WHERE strategy IN ('map', 'break_down') AND gcp_service IS NOT NULL"
        ).fetchall()
    }

    CUD_SERVICE_ALIASES = {
        "Cloud Memorystore for Redis": {"Memorystore", "Cloud Memorystore", "Cloud Memorystore for Redis"},
        "Cloud Memorystore for Memcached": {"Memorystore", "Cloud Memorystore", "Cloud Memorystore for Memcached"},
        "Cloud Memorystore": {"Memorystore", "Cloud Memorystore", "Cloud Memorystore for Redis", "Cloud Memorystore for Memcached"},
        "Memorystore": {"Memorystore", "Cloud Memorystore", "Cloud Memorystore for Redis", "Cloud Memorystore for Memcached"},
    }

    rate_services = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT gcp_service FROM gcp_sku_rates WHERE pricing_type = 'OnDemand' AND gcp_service IS NOT NULL"
        ).fetchall()
    }

    for rate_svc in rate_services:
        aliases = CUD_SERVICE_ALIASES.get(rate_svc, {rate_svc})
        eligible_key = next((a for a in [rate_svc] + list(aliases) if a in cud_eligible), None)
        if not eligible_key:
            continue
        if not (bool(aliases & mapped_services)):
            continue

        mults = None
        for k in [rate_svc] + list(aliases):
            if k in cud_pct and k != "DEFAULT":
                mults = cud_pct[k]
                break
        if not mults:
            continue
        r1, r3 = mults

        excl = None
        for k in [rate_svc] + list(aliases):
            if k in cud_exclusions:
                excl = cud_exclusions[k]
                break

        excl_clause = "AND NOT regexp_matches(gcp_sku_name, ?)" if excl else ""

        for pricing_type, mult in [("Commit1Yr", r1), ("Commit3Yr", r3)]:
            params = [mult, rate_svc] + ([excl] if excl else [])
            conn.execute(f"""
                INSERT INTO gcp_sku_rates
                SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
                       resource_group, '{pricing_type}', region, unit,
                       rate_usd * ?, 'doc-percentage-fallback', 'cud_pct.json'
                FROM gcp_sku_rates
                WHERE gcp_service = ? AND pricing_type = 'OnDemand'
                  {excl_clause}
                ON CONFLICT DO NOTHING
            """, params)

    # Assertions
    # 1. Zero Commit1Yr or Commit3Yr rows for Redis M1
    redis_m1_cuds = conn.execute("""
        SELECT COUNT(*) FROM gcp_sku_rates
        WHERE gcp_sku_id IN ('SKU-REDIS-M1', 'SKU-REDIS-STD-M1')
          AND pricing_type IN ('Commit1Yr', 'Commit3Yr')
    """).fetchone()[0]
    assert redis_m1_cuds == 0, f"Expected 0 CUD rows for Redis M1, got {redis_m1_cuds}"

    # 2. Redis M5 DOES get CUD rows
    redis_m5_cuds = conn.execute("""
        SELECT COUNT(*) FROM gcp_sku_rates
        WHERE gcp_sku_id = 'SKU-REDIS-M5'
          AND pricing_type IN ('Commit1Yr', 'Commit3Yr')
    """).fetchone()[0]
    assert redis_m5_cuds == 2, f"Expected 2 CUD rows for Redis M5, got {redis_m5_cuds}"

    # 3. Memcached Custom Core M1 & Custom RAM M1 DO get CUD rows
    memcached_cuds = conn.execute("""
        SELECT COUNT(*) FROM gcp_sku_rates
        WHERE gcp_sku_id IN ('SKU-MEMCACHED-CORE', 'SKU-MEMCACHED-RAM')
          AND pricing_type IN ('Commit1Yr', 'Commit3Yr')
    """).fetchone()[0]
    assert memcached_cuds == 4, f"Expected 4 CUD rows for Memcached, got {memcached_cuds}"
