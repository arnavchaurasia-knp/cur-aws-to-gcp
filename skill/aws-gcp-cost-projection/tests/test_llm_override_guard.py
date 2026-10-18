import duckdb
import pytest
from scripts.llm_override_guard import validate_override

def test_sku_id_format():
    ok, err = validate_override(None, "k1", "core", {"gcp_sku_id": "invalid-sku"})
    assert not ok
    assert "GCP SKU ID format" in err

    ok, err = validate_override(None, "k1", "core", {"gcp_sku_id": "AAAA-BBBB-CCCC", "unit_multiplier": 1.0})
    assert ok


def test_unit_multiplier_sanity():
    ok, err = validate_override(None, "k1", "core", {"unit_multiplier": "abc"})
    assert not ok
    assert "not a number" in err

    ok, err = validate_override(None, "k1", "core", {"unit_multiplier": 0})
    assert not ok
    assert "must be > 0" in err

    ok, err = validate_override(None, "k1", "core", {"unit_multiplier": 9999})
    assert not ok
    assert "exceeds the sanity limit" in err


def test_g2_intentional_passthrough_protection():
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            strategy VARCHAR,
            projection_note VARCHAR
        )
    """)
    conn.execute("""
        INSERT INTO aws_li_to_gcp_li VALUES
        ('k1', 'passthrough', 'no GCP equivalent — carried at AWS cost'),
        ('k2', 'passthrough', 'no GCS equivalent — penalty charge'),
        ('k3', 'passthrough', 'standard unmapped row')
    """)

    # Attempting to map an intentional passthrough should be rejected
    ok, err = validate_override(conn, "k1", "core", {"strategy": "map", "gcp_sku_id": "AAAA-BBBB-CCCC"})
    assert not ok
    assert "intentional passthrough" in err

    # Setting a SKU ID without strategy='map' also implies mapping
    ok, err = validate_override(conn, "k2", "core", {"gcp_sku_id": "AAAA-BBBB-CCCC"})
    assert not ok
    assert "intentional passthrough" in err

    # Non-intentional passthrough is allowed (if rates check passes or rates table doesn't exist)
    ok, err = validate_override(conn, "k3", "core", {"strategy": "map", "gcp_sku_id": "AAAA-BBBB-CCCC"})
    assert ok


def test_g1_catalog_rate_existence_guard():
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR,
            gcp_region VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            gcp_region VARCHAR,
            strategy VARCHAR,
            projection_note VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id VARCHAR,
            region VARCHAR,
            pricing_type VARCHAR,
            rate_usd DOUBLE
        )
    """)

    conn.execute("INSERT INTO aws_li_catalog VALUES ('k_us', 'us-east1')")
    conn.execute("INSERT INTO aws_li_to_gcp_li VALUES ('k_us', 'us-east1', 'needs_reprice', 'reprice needed')")

    conn.execute("""
        INSERT INTO gcp_sku_rates VALUES
        ('1111-2222-3333', 'us-east1', 'OnDemand', 0.05),
        ('4444-5555-6666', 'global', 'OnDemand', 0.10),
        ('7777-8888-9999', 'europe-west1', 'OnDemand', 0.20),
        ('AAAA-BBBB-CCCC', 'us-east1', 'Preemptible', 0.01)
    """)

    # 1. Matching region
    ok, err = validate_override(conn, "k_us", "core", {"strategy": "map", "gcp_sku_id": "1111-2222-3333"})
    assert ok

    # 2. Global fallback
    ok, err = validate_override(conn, "k_us", "core", {"strategy": "map", "gcp_sku_id": "4444-5555-6666"})
    assert ok

    # 3. Only in non-matching region and not dominant region
    ok, err = validate_override(conn, "k_us", "core", {"strategy": "map", "gcp_sku_id": "7777-8888-9999"})
    assert not ok
    assert "no OnDemand rate in gcp_sku_rates" in err

    # 4. Exists in region but only Preemptible (not OnDemand)
    ok, err = validate_override(conn, "k_us", "core", {"strategy": "map", "gcp_sku_id": "AAAA-BBBB-CCCC"})
    assert not ok
    assert "no OnDemand rate in gcp_sku_rates" in err

    # 5. SKU does not exist in gcp_sku_rates at all
    ok, err = validate_override(conn, "k_us", "core", {"strategy": "map", "gcp_sku_id": "0000-0000-0000"})
    assert not ok
    assert "no OnDemand rate in gcp_sku_rates" in err
