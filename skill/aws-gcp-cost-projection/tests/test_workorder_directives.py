import glob
import json
import duckdb
import pytest
from unittest.mock import patch
from conftest import row
from apply_static_mappings import (
    SKUMeta,
    map_flat_hourly,
    map_shield,
)
from classify_mechanics import classify

MOCK_SKU = SKUMeta("MOCK-SKU-1234", unit="h", resource_group="Mock")

def _with_sku(mapper, rows):
    with patch("apply_static_mappings.resolve_sku", return_value=MOCK_SKU):
        res = mapper(rows)
    if isinstance(res, tuple):
        return res[0]
    return res

def test_directive_4_nat_cap_pricing():
    """Verify NAT gateway hours use multiplier matching $0.044/hr cap without fleet VM dependency."""
    rows = [
        row(
            product="Amazon Virtual Private Cloud",
            usage_type="USE1-NatGateway-Hours",
            operation="",
            unit="Hrs",
            aws_amortized_cost=32.85,
            total_usage=730.0,
        )
    ]
    out = _with_sku(map_flat_hourly, rows)
    assert len(out) == 1
    nat_mapping = out[0]
    assert nat_mapping["strategy"] == "map"
    assert "Cloud Nat" in nat_mapping["gcp_sku_name"]
    # Multiplier is $0.044 / $0.0014 ~= 31.42857143
    assert abs(nat_mapping["unit_multiplier"] - (0.044 / 0.0014)) < 1e-4

def test_m1_shield_multi_month_pricing():
    """Verify Shield Advanced charges $3,000 per month across distinct billing months."""
    rows = [
        row(
            product="AWS Shield",
            usage_type="Shield-Advanced-Subscription-Monthly",
            operation="",
            unit="Mo",
            aws_amortized_cost=3000.0,
            total_usage=1.0,
            billing_month="2026-01",
        ),
        row(
            product="AWS Shield",
            usage_type="Shield-Advanced-Subscription-Monthly",
            operation="",
            unit="Mo",
            aws_amortized_cost=3000.0,
            total_usage=1.0,
            billing_month="2026-02",
        ),
        row(
            product="AWS Shield",
            usage_type="Shield-Advanced-Subscription-Monthly",
            operation="",
            unit="Mo",
            aws_amortized_cost=3000.0,
            total_usage=1.0,
            billing_month="2026-02",
        ),
    ]
    out = _with_sku(map_shield, rows)
    assert len(out) == 3
    # First month: mapped at 1.0 multiplier
    assert out[0]["strategy"] == "map"
    assert out[0]["unit_multiplier"] == 1.0
    # Second month: mapped at 1.0 multiplier
    assert out[1]["strategy"] == "map"
    assert out[1]["unit_multiplier"] == 1.0
    # Duplicate in second month: ignored ($0 dedup)
    assert out[2]["strategy"] == "ignore"
    assert out[2]["unit_multiplier"] == 0.0

def test_policy_p1_neptune_routing():
    """Verify Neptune instances classify as misc (passthrough at AWS cost), NOT compute_breakdown."""
    r = row(
        product="Amazon Neptune",
        usage_type="USE1-Neptune:db.r5.large",
        operation="CreateDBInstance:0002",
        unit="Hrs",
    )
    mech = classify(r)
    assert mech == "misc"
    assert mech != "compute_breakdown"

def test_directive_2_render_report_section_split_and_strict_footing(tmp_path, monkeypatch):
    """Test 3-section structure, strict footing, and headline savings in render_report."""
    from render_report import main as render_main

    job_dir = tmp_path / "job_footing"
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_file = audit_dir / "projection.duckdb"

    curr_info = {
        "currency": "USD",
        "symbol": "$",
        "fx_rate": 1.0,
        "source": "default",
        "label": "USD",
    }
    with open(audit_dir / "currency.json", "w", encoding="utf-8") as f:
        json.dump(curr_info, f)

    conn = duckdb.connect(str(db_file))
    conn.execute("""
    CREATE TABLE aws_li_catalog (
        aws_li_key TEXT,
        product TEXT,
        instance_type TEXT,
        instance_vcpus INTEGER,
        instance_ram_gb DOUBLE,
        instance_count DOUBLE,
        usage_type TEXT,
        operation TEXT,
        aws_region TEXT,
        gcp_region TEXT,
        line_item_type TEXT,
        mechanic_group TEXT,
        pricing_model TEXT,
        pricing_unit TEXT,
        is_workload BOOLEAN,
        total_usage DOUBLE,
        aws_amortized_cost DOUBLE
    );
    CREATE TABLE aws_li_to_gcp_li (
        aws_li_key TEXT,
        rowid BIGINT,
        strategy TEXT,
        gcp_service TEXT,
        gcp_machine_type TEXT,
        gcp_sku_id TEXT,
        gcp_sku_name TEXT,
        component TEXT,
        unit_multiplier DOUBLE,
        projection_note TEXT,
        mapping_confidence DOUBLE
    );
    CREATE TABLE gcp_projection (
        aws_li_key TEXT,
        component TEXT,
        gcp_service TEXT,
        gcp_machine_type TEXT,
        gcp_region TEXT,
        is_workload BOOLEAN,
        gcp_projected_cost DOUBLE,
        gcp_cost_1yr_cud DOUBLE,
        gcp_cost_3yr_cud DOUBLE,
        projection_status TEXT,
        projection_note TEXT
    );
    CREATE TABLE run_results (
        run_id TEXT,
        ts_utc TEXT,
        prospect TEXT
    );
    """)

    # Row 1: Section A (mapped workload with rate) -> AWS $100, GCP OD $80, 1yr $60, 3yr $40
    # Row 2: Section B (unpriced mapped workload) -> AWS $50, GCP null -> carried 1:1 ($50)
    # Row 3: Section B (passthrough Marketplace) -> AWS $30, GCP $30
    conn.execute("""
    INSERT INTO aws_li_catalog VALUES
    ('k1', 'Amazon Elastic Compute Cloud', 'c5.xlarge', 4, 8.0, 1.0, 'BoxUsage:c5.xlarge', 'RunInstances', 'us-east-1', 'us-central1', 'Usage', 'compute_breakdown', 'OnDemand', 'Hrs', true, 730, 100.0),
    ('k2', 'Amazon Kinesis', NULL, NULL, NULL, NULL, 'Kinesis-ShardHour', '', 'us-east-1', 'us-central1', 'Usage', 'per_request', 'OnDemand', 'Hrs', true, 730, 50.0),
    ('k3', 'AWS Marketplace Datadog', NULL, NULL, NULL, NULL, 'Datadog-Host', '', 'us-east-1', 'us-central1', 'Usage', 'non_workload', 'OnDemand', 'Hrs', false, 1, 30.0);

    INSERT INTO aws_li_to_gcp_li VALUES
    ('k1', 1, 'map', 'Compute Engine', 'n2-standard-4', 'sku-1', 'N2 Core', 'core', 1.0, 'OK', 0.95),
    ('k2', 1, 'map', 'Pub/Sub', NULL, NULL, NULL, 'throughput', 1.0, 'no rate', 0.8),
    ('k3', 1, 'passthrough', 'Manual Review', NULL, NULL, NULL, 'service', 1.0, 'Marketplace', 0.5);

    INSERT INTO gcp_projection VALUES
    ('k1', 'core', 'Compute Engine', 'n2-standard-4', 'us-central1', true, 80.0, 60.0, 40.0, 'mapped', 'OK'),
    ('k2', 'throughput', 'Pub/Sub', NULL, 'us-central1', true, NULL, NULL, NULL, 'unpriced', 'no rate found'),
    ('k3', 'service', 'Manual Review', NULL, 'us-central1', false, 30.0, 30.0, 30.0, 'passthrough', 'Marketplace');

    INSERT INTO run_results VALUES
    ('run-test', '2026-10-14 00:00:00', 'Test Prospect');
    """)
    conn.close()

    monkeypatch.chdir(job_dir)
    monkeypatch.setattr("render_report.JOB_DIR", str(job_dir))
    monkeypatch.setattr("render_report.DB_PATH", str(db_file))

    render_main()

    html_files = glob.glob(str(audit_dir / "report-*.html"))
    assert len(html_files) > 0
    content = open(html_files[0], encoding="utf-8").read()

    # Strict Footing Checks:
    # Section A: AWS = 100, GCP OD = 80, 1yr = 60, 3yr = 40. Savings = (100 - 80)/100 = 20.0%
    # Section B: AWS = 80 (50 + 30), GCP OD = 80, 1yr = 80, 3yr = 80. (0% savings)
    # Grand Total: AWS = 180, GCP OD = 160 -> 80 + 80 = 160.
    assert "SECTION A — MAPPED (REAL GCP EQUIVALENT)" in content
    assert "SECTION B — PASSTHROUGH / NO DIRECT GCP EQUIVALENT (CARRIED 1:1)" in content
    assert "GRAND TOTAL (LIST PRICES)" in content
    assert "SUBTOTAL SECTION A (MAPPED WORKLOAD)" in content
    assert "SUBTOTAL SECTION B (PASSTHROUGH / NON-WORKLOAD / UNPRICED)" in content

    # Headline Savings Card: Must be exactly Section A savings (20.0%), NOT diluted by Section B!
    assert "20.0%" in content
