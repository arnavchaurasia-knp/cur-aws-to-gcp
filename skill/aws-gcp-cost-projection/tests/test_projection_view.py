import duckdb
import pytest
from projection_view import create_projection_view, compute_dominant_region


@pytest.fixture
def mock_db():
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT PRIMARY KEY,
            product TEXT,
            aws_region TEXT,
            gcp_region TEXT,
            line_item_type TEXT,
            pricing_model TEXT,
            is_workload BOOLEAN,
            total_usage DOUBLE,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key TEXT,
            strategy TEXT,
            gcp_service TEXT,
            gcp_sku_id TEXT,
            component TEXT,
            unit_multiplier DOUBLE,
            projection_note TEXT
        );
        CREATE TABLE gcp_sku_rates (
            gcp_sku_id TEXT,
            gcp_service TEXT,
            gcp_sku_name TEXT,
            resource_family TEXT,
            resource_group TEXT,
            pricing_type TEXT,
            region TEXT,
            unit TEXT,
            rate_usd DOUBLE,
            source TEXT,
            audit_url TEXT
        );
    """)
    return conn


def test_dominant_region_computation(mock_db):
    conn = mock_db
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'OnDemand', true, 100, 10),
            ('k2', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'OnDemand', true, 100, 10),
            ('k3', 'Amazon EC2', 'us-west-2', 'us-west1', 'Usage', 'OnDemand', true, 50, 5),
            ('k4', 'Amazon CloudFront', 'global', 'global', 'Usage', 'OnDemand', true, 10, 1);
    """)
    dom = compute_dominant_region(conn)
    assert dom == "us-east1"


def test_global_row_fallback_to_dominant_region(mock_db):
    conn = mock_db
    # Bill dominated by us-east1
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'OnDemand', true, 100, 10),
            ('k2', 'Amazon EC2', 'us-east-1', 'us-east1', 'Usage', 'OnDemand', true, 100, 10),
            ('k_global_no_glob_sku', 'Amazon Route 53', 'global', 'global', 'Usage', 'OnDemand', true, 100, 5),
            ('k_global_has_glob_sku', 'AWS Shield', 'global', 'global', 'Usage', 'OnDemand', true, 1, 100);
    """)
    conn.execute("""
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k1', 'map', 'Compute Engine', 'SKU-VM', 'core', 1, NULL),
            ('k2', 'map', 'Compute Engine', 'SKU-VM', 'core', 1, NULL),
            ('k_global_no_glob_sku', 'map', 'Cloud DNS', 'SKU-REG-DNS', 'query', 1, NULL),
            ('k_global_has_glob_sku', 'map', 'Cloud Armor', 'SKU-GLOB-ARMOR', 'rule', 1, NULL);
    """)
    # SKU-REG-DNS only has rate in us-east1 (no global rate)
    # SKU-GLOB-ARMOR has rate in global ($100) and us-east1 ($120)
    conn.execute("""
        INSERT INTO gcp_sku_rates VALUES
            ('SKU-VM', 'Compute Engine', 'VM', 'Compute', 'CPU', 'OnDemand', 'us-east1', 'h', 0.10, 'cat', 'url'),
            ('SKU-REG-DNS', 'Cloud DNS', 'DNS', 'Network', 'DNS', 'OnDemand', 'us-east1', 'query', 0.05, 'cat', 'url'),
            ('SKU-GLOB-ARMOR', 'Cloud Armor', 'Armor', 'Security', 'Armor', 'OnDemand', 'global', 'mo', 100.0, 'cat', 'url'),
            ('SKU-GLOB-ARMOR', 'Cloud Armor', 'Armor', 'Security', 'Armor', 'OnDemand', 'us-east1', 'mo', 120.0, 'cat', 'url');
    """)

    create_projection_view(conn)

    # 1. k_global_no_glob_sku should price at dominant region rate ($0.05 * 100 = 5.0)
    row_no_glob = conn.execute("""
        SELECT gcp_projected_cost, projection_note
        FROM gcp_projection
        WHERE aws_li_key = 'k_global_no_glob_sku'
    """).fetchone()
    assert row_no_glob[0] == pytest.approx(5.0)
    assert "global charge; priced at us-east1 rate" in row_no_glob[1]

    # 2. k_global_has_glob_sku should price at global rate ($100.0 * 1 = 100.0), NOT us-east1 rate
    row_glob = conn.execute("""
        SELECT gcp_projected_cost, projection_note
        FROM gcp_projection
        WHERE aws_li_key = 'k_global_has_glob_sku'
    """).fetchone()
    assert row_glob[0] == pytest.approx(100.0)
    assert row_glob[1] is None or "global charge; priced at" not in row_glob[1]


def test_non_committable_items_cud_fallback_to_ondemand(mock_db):
    conn = mock_db
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_storage', 'Amazon S3', 'us-east-1', 'us-east1', 'Usage', 'OnDemand', true, 1000, 20);
        INSERT INTO aws_li_to_gcp_li VALUES
            ('k_storage', 'map', 'Cloud Storage', 'SKU-GCS', 'storage', 1, NULL);
        INSERT INTO gcp_sku_rates VALUES
            ('SKU-GCS', 'Cloud Storage', 'Standard', 'Storage', 'GCS', 'OnDemand', 'us-east1', 'GB-mo', 0.020, 'cat', 'url');
    """)
    create_projection_view(conn)
    row = conn.execute("""
        SELECT gcp_projected_cost, gcp_cost_1yr_cud, gcp_cost_3yr_cud
        FROM gcp_projection
        WHERE aws_li_key = 'k_storage'
    """).fetchone()
    assert row[0] == pytest.approx(20.0)
    # P-12: Non-committable rows fall back to on-demand rate in CUD projections
    assert row[1] == pytest.approx(20.0)
    assert row[2] == pytest.approx(20.0)
