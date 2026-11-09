#!/usr/bin/env python3
"""
test_cud_catalog_derivation.py — Tests for WORKORDER_cud_catalog_derivation.md.

Verifies:
- Phase 1: DEFAULT multiplier is 1.0/1.0 (no fabricated CUD discount).
- Phase 1: cud_eligible allowlist exists and non-eligible services never get a discount.
- Regression guard: real commit SKUs from catalog are preserved identically.
- Regression guard: non-eligible services have committed cost == on-demand cost.
- Fallback synthesis stamps source as 'doc-percentage-fallback'.
"""

import json
import os
import sys
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import apply_rates
import apply_static_mappings
import validate_fix
import apply_directives


def _setup_catalog_table(con):
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            pricing_unit VARCHAR,
            gcp_region VARCHAR,
            aws_region VARCHAR,
            total_usage DOUBLE,
            pricing_model VARCHAR,
            line_item_type VARCHAR,
            aws_amortized_cost DOUBLE,
            is_workload BOOLEAN DEFAULT true,
            billing_month VARCHAR DEFAULT '2026-07'
        )
    """)


def _setup_mappings_table(con):
    con.execute("""
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR PRIMARY KEY,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            gcp_sku_name VARCHAR,
            strategy VARCHAR,
            unit_multiplier DOUBLE,
            gcp_region VARCHAR,
            projection_note VARCHAR,
            mapping_confidence DOUBLE,
            component VARCHAR,
            passthrough_reason VARCHAR,
            decided_by VARCHAR,
            rate_region_basis VARCHAR,
            rate_region_used VARCHAR,
            license_status VARCHAR,
            review_flags VARCHAR
        )
    """)


def test_cud_pct_schema_and_defaults():
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cud_path = os.path.join(skill_dir, "data", "cud_pct.json")
    with open(cud_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. cud_eligible allowlist exists
    assert "cud_eligible" in data
    assert isinstance(data["cud_eligible"], list)
    expected_eligible = {
        "Compute Engine",
        "Compute Engine Memory Optimized",
        "Cloud SQL",
        "Cloud Spanner",
        "Cloud Bigtable",
        "Memorystore",
        "Cloud Memorystore",
        "Cloud Memorystore for Redis",
        "Cloud Memorystore for Memcached",
        "AlloyDB",
        "Cloud Run",
    }
    assert set(data["cud_eligible"]) == expected_eligible

    # 2. DEFAULT is 1.0 / 1.0 (no fabrication)
    assert "DEFAULT" in data
    assert data["DEFAULT"]["1yr_multiplier"] == 1.0
    assert data["DEFAULT"]["3yr_multiplier"] == 1.0

    # 3. Check apply_rates loader
    pcts = apply_rates.load_cud_pct()
    assert pcts.get("DEFAULT") == (1.0, 1.0)
    eligible = apply_rates.load_cud_eligible()
    assert eligible == expected_eligible

    # 4. Check apply_static_mappings fallback
    assert apply_static_mappings.CUD_PCT_FALLBACK.get("DEFAULT") == (1.0, 1.0)

    # 5. Check validate_fix
    assert validate_fix._CUD_DEFAULT == (1.0, 1.0)
    assert validate_fix._cud_factors("Cloud Storage") == (1.0, 1.0)
    assert validate_fix._cud_factors("Compute Engine") == (0.63, 0.45)

    # 6. Check apply_directives
    assert apply_directives.get_cud_mult("Cloud Storage", "cud_1yr") == 1.0
    assert apply_directives.get_cud_mult("Cloud Storage", "cud_3yr") == 1.0
    assert apply_directives.get_cud_mult("Compute Engine", "cud_1yr") == 0.63
    assert apply_directives.get_cud_mult("Compute Engine", "cud_3yr") == 0.45


def test_regression_guard_real_catalog_commit_rows_unchanged(tmp_path, monkeypatch):
    """REGRESSION GUARD: A bill whose services ALL have real commit SKUs in the catalog
    must use the exact catalog rates byte-for-byte, stamped source = 'catalog.duckdb'."""
    db_path = str(tmp_path / "projection.duckdb")
    con = duckdb.connect(db_path)
    _setup_catalog_table(con)
    _setup_mappings_table(con)

    con.execute("""
        INSERT INTO aws_li_catalog (aws_li_key, product, gcp_region, aws_region, total_usage, pricing_model, aws_amortized_cost)
        VALUES 
            ('k_ce_od', 'AmazonEC2', 'us-central1', 'us-east-1', 1000.0, 'OnDemand', 40.0),
            ('k_ce_c1', 'AmazonEC2', 'us-central1', 'us-east-1', 1000.0, 'Reserved', 20.0);

        INSERT INTO aws_li_to_gcp_li (aws_li_key, gcp_service, gcp_sku_id, gcp_sku_name, strategy, unit_multiplier, gcp_region, projection_note, mapping_confidence)
        VALUES 
            ('k_ce_od', 'Compute Engine', 'SKU-CE-CORE-OD', 'N2 Core', 'map', 1.0, 'us-central1', '', 1.0),
            ('k_ce_c1', 'Compute Engine', 'SKU-CE-CORE-C1', 'N2 Core Commit 1Yr', 'map', 1.0, 'us-central1', '', 1.0);
    """)
    con.close()

    monkeypatch.setattr(apply_rates, "DB_PATH", db_path)

    cat_path = str(tmp_path / "catalog.duckdb")
    cat_db = duckdb.connect(cat_path)
    cat_db.execute("""
        CREATE TABLE skus (
            sku_id TEXT, service_name TEXT, description TEXT, resource_family TEXT,
            resource_group TEXT, usage_type TEXT, usage_unit TEXT, service_regions TEXT[]
        )
    """)
    cat_db.execute("CREATE TABLE tiered_rates (sku_id TEXT, tier_start DOUBLE, rate_usd DOUBLE)")
    cat_db.execute("""
        INSERT INTO skus VALUES 
            ('SKU-CE-CORE-OD', 'Compute Engine', 'N2 Core', 'Compute', 'CPU', 'OnDemand', 'h', ['us-central1']),
            ('SKU-CE-CORE-C1', 'Compute Engine', 'N2 Core Commit 1Yr', 'Compute', 'CPU', 'Commit1Yr', 'h', ['us-central1']);
        INSERT INTO tiered_rates VALUES 
            ('SKU-CE-CORE-OD', 0.0, 0.031611),
            ('SKU-CE-CORE-C1', 0.0, 0.019915);
    """)
    cat_db.close()
    monkeypatch.setattr(apply_rates, "CATALOG_DB", cat_path)

    apply_rates.main()

    con = duckdb.connect(db_path)
    rates = con.execute("""
        SELECT pricing_type, rate_usd, source, audit_url
        FROM gcp_sku_rates
        WHERE gcp_sku_id = 'SKU-CE-CORE-C1' AND region = 'us-central1'
        ORDER BY pricing_type
    """).fetchall()

    # Verify real catalog commit rates are loaded and stamped as catalog.duckdb
    assert len(rates) == 1
    pt, rate, src, audit = rates[0]
    assert pt == "Commit1Yr"
    assert rate == 0.019915
    assert src == "catalog.duckdb"
    assert audit == "catalog.duckdb#SKU-CE-CORE-C1"

    od_rates = con.execute("""
        SELECT pricing_type, rate_usd, source
        FROM gcp_sku_rates
        WHERE gcp_sku_id = 'SKU-CE-CORE-OD' AND region = 'us-central1' AND pricing_type = 'OnDemand'
    """).fetchall()
    assert od_rates[0] == ("OnDemand", 0.031611, "catalog.duckdb")
    con.close()


def test_regression_guard_non_eligible_service_committed_equals_ondemand(tmp_path, monkeypatch):
    """REGRESSION GUARD: A non-eligible service (e.g. Cloud Storage) has NO CUD discount.
    Its committed cost must EQUAL on-demand (multiplier 1.0), never the old 0.75/0.60 fabrication."""
    db_path = str(tmp_path / "projection.duckdb")
    con = duckdb.connect(db_path)
    _setup_catalog_table(con)
    _setup_mappings_table(con)

    con.execute("""
        INSERT INTO aws_li_catalog (aws_li_key, product, gcp_region, aws_region, total_usage, pricing_model, aws_amortized_cost)
        VALUES ('k_gcs', 'AmazonS3', 'us-central1', 'us-east-1', 500.0, 'OnDemand', 15.0);

        INSERT INTO aws_li_to_gcp_li (aws_li_key, gcp_service, gcp_sku_id, gcp_sku_name, strategy, unit_multiplier, gcp_region, projection_note, mapping_confidence)
        VALUES ('k_gcs', 'Cloud Storage', 'SKU-GCS-STD', 'Standard Storage us-central1', 'map', 1.0, 'us-central1', '', 1.0);
    """)
    con.close()

    monkeypatch.setattr(apply_rates, "DB_PATH", db_path)

    cat_path = str(tmp_path / "catalog.duckdb")
    cat_db = duckdb.connect(cat_path)
    cat_db.execute("""
        CREATE TABLE skus (
            sku_id TEXT, service_name TEXT, description TEXT, resource_family TEXT,
            resource_group TEXT, usage_type TEXT, usage_unit TEXT, service_regions TEXT[]
        )
    """)
    cat_db.execute("CREATE TABLE tiered_rates (sku_id TEXT, tier_start DOUBLE, rate_usd DOUBLE)")
    cat_db.execute("""
        INSERT INTO skus VALUES ('SKU-GCS-STD', 'Cloud Storage', 'Standard Storage', 'Storage', 'Storage', 'OnDemand', 'GiBy.mo', ['us-central1']);
        INSERT INTO tiered_rates VALUES ('SKU-GCS-STD', 0.0, 0.020);
    """)
    cat_db.close()
    monkeypatch.setattr(apply_rates, "CATALOG_DB", cat_path)

    apply_rates.main()

    con = duckdb.connect(db_path)
    # Verify no Commit rows were synthesized for Cloud Storage
    cud_rates = con.execute("""
        SELECT pricing_type FROM gcp_sku_rates
        WHERE gcp_sku_id = 'SKU-GCS-STD' AND pricing_type IN ('Commit1Yr', 'Commit3Yr')
    """).fetchall()
    assert len(cud_rates) == 0

    # In projection view, committed cost equals on-demand (no fabricated discount)
    proj = con.execute("""
        SELECT gcp_projected_cost, gcp_cost_1yr_cud, gcp_cost_3yr_cud
        FROM gcp_projection WHERE aws_li_key = 'k_gcs'
    """).fetchone()
    con.close()

    assert proj[0] == pytest.approx(500.0 * 0.020)
    assert proj[1] == proj[0]  # 1yr committed == OnDemand
    assert proj[2] == proj[0]  # 3yr committed == OnDemand


def test_eligible_service_fallback_synthesis(tmp_path, monkeypatch):
    """An eligible service (e.g. Cloud SQL) without catalog commit rows synthesizes
    from cud_pct.json and stamps source as 'doc-percentage-fallback'."""
    db_path = str(tmp_path / "projection.duckdb")
    con = duckdb.connect(db_path)
    _setup_catalog_table(con)
    _setup_mappings_table(con)

    con.execute("""
        INSERT INTO aws_li_catalog (aws_li_key, product, gcp_region, aws_region, total_usage, pricing_model, aws_amortized_cost)
        VALUES ('k_sql', 'AmazonRDS', 'us-central1', 'us-east-1', 100.0, 'OnDemand', 80.0);

        INSERT INTO aws_li_to_gcp_li (aws_li_key, gcp_service, gcp_sku_id, gcp_sku_name, strategy, unit_multiplier, gcp_region, projection_note, mapping_confidence)
        VALUES ('k_sql', 'Cloud SQL', 'SKU-SQL-CPU', 'Cloud SQL CPU', 'map', 1.0, 'us-central1', '', 1.0);
    """)
    con.close()

    monkeypatch.setattr(apply_rates, "DB_PATH", db_path)

    cat_path = str(tmp_path / "catalog.duckdb")
    cat_db = duckdb.connect(cat_path)
    cat_db.execute("""
        CREATE TABLE skus (
            sku_id TEXT, service_name TEXT, description TEXT, resource_family TEXT,
            resource_group TEXT, usage_type TEXT, usage_unit TEXT, service_regions TEXT[]
        )
    """)
    cat_db.execute("CREATE TABLE tiered_rates (sku_id TEXT, tier_start DOUBLE, rate_usd DOUBLE)")
    cat_db.execute("""
        INSERT INTO skus VALUES ('SKU-SQL-CPU', 'Cloud SQL', 'Cloud SQL CPU', 'ApplicationServices', 'Database', 'OnDemand', 'h', ['us-central1']);
        INSERT INTO tiered_rates VALUES ('SKU-SQL-CPU', 0.0, 0.05);
    """)
    cat_db.close()
    monkeypatch.setattr(apply_rates, "CATALOG_DB", cat_path)

    apply_rates.main()

    con = duckdb.connect(db_path)
    rates = con.execute("""
        SELECT pricing_type, rate_usd, source, audit_url
        FROM gcp_sku_rates
        WHERE gcp_sku_id = 'SKU-SQL-CPU' AND region = 'us-central1'
        ORDER BY pricing_type
    """).fetchall()

    rate_map = {r[0]: (r[1], r[2], r[3]) for r in rates}
    assert "Commit1Yr" in rate_map
    assert "Commit3Yr" in rate_map
    assert rate_map["Commit1Yr"][0] == pytest.approx(0.05 * 0.75)
    assert rate_map["Commit1Yr"][1] == "doc-percentage-fallback"
    assert rate_map["Commit1Yr"][2] == "cud_pct.json"
    assert rate_map["Commit3Yr"][0] == pytest.approx(0.05 * 0.48)
    assert rate_map["Commit3Yr"][1] == "doc-percentage-fallback"

    proj = con.execute("""
        SELECT gcp_projected_cost, gcp_cost_1yr_cud, gcp_cost_3yr_cud
        FROM gcp_projection WHERE aws_li_key = 'k_sql'
    """).fetchone()
    con.close()

    assert proj[0] == pytest.approx(100.0 * 0.05)
    assert proj[1] == pytest.approx(100.0 * 0.05 * 0.75)
    assert proj[2] == pytest.approx(100.0 * 0.05 * 0.48)


def test_cud_coverage_checker_missing_services_reports_anomalies_and_partial(tmp_path):
    """Phase 3 test: check_cud_coverage with a catalog missing eligible commit SKUs
    raises anomalies and marks status as 'partial' (no silent fabrication)."""
    from check_cud_coverage import check_cud_coverage

    cat_path = str(tmp_path / "catalog.duckdb")
    cat_db = duckdb.connect(cat_path)
    cat_db.execute("""
        CREATE TABLE skus (
            sku_id TEXT, service_name TEXT, description TEXT, resource_family TEXT,
            resource_group TEXT, usage_type TEXT, usage_unit TEXT, service_regions TEXT[]
        )
    """)
    cat_db.execute("""
        -- Only Compute Engine has commit rows in this catalog
        INSERT INTO skus VALUES 
            ('SKU-1', 'Compute Engine', 'N2 Core', 'Compute', 'CPU', 'Commit1Yr', 'h', ['us-central1']);
    """)
    cat_db.close()

    result = check_cud_coverage(catalog_path=cat_path)

    assert result["source"] == "cud_coverage"
    assert result["state"] == "partial"
    assert result["complete"] is False
    assert result["fetched_count"] == 1  # Only Compute Engine
    assert result["expected_count"] >= 10
    assert result["coverage_pct"] < 50.0
    assert len(result["anomalies"]) >= 9
    assert any("Cloud SQL" in a for a in result["anomalies"])
    assert result["item_outcomes"]["Compute Engine"] == "OK"
    assert result["item_outcomes"]["Cloud SQL"] == "MISSING"


def test_cud_coverage_checker_all_complete(tmp_path):
    """Phase 3 test: check_cud_coverage with all eligible services covered
    reports complete == True and state == 'success'."""
    from check_cud_coverage import check_cud_coverage

    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cud_path = os.path.join(skill_dir, "data", "cud_pct.json")
    with open(cud_path, "r", encoding="utf-8") as f:
        eligible = json.load(f)["cud_eligible"]

    cat_path = str(tmp_path / "catalog.duckdb")
    cat_db = duckdb.connect(cat_path)
    cat_db.execute("""
        CREATE TABLE skus (
            sku_id TEXT, service_name TEXT, description TEXT, resource_family TEXT,
            resource_group TEXT, usage_type TEXT, usage_unit TEXT, service_regions TEXT[]
        )
    """)
    for i, svc in enumerate(eligible):
        cat_db.execute(f"""
            INSERT INTO skus VALUES 
                ('SKU-{i}', '{svc}', 'Commit SKU', 'Family', 'Group', 'Commit1Yr', 'h', ['us-central1']);
        """)
    cat_db.close()

    result = check_cud_coverage(catalog_path=cat_path, cud_pct_path=cud_path)

    assert result["source"] == "cud_coverage"
    assert result["state"] == "success"
    assert result["complete"] is True
    assert result["coverage_pct"] == 100.0
    assert len(result["anomalies"]) == 0
    assert all(status == "OK" for status in result["item_outcomes"].values())
