"""Tests for AWS Redshift full modelling (§4.8 of WORKORDER_per_service_full_modelling.md).

Core invariants verified:
1. Spectrum (per-TB scanned) -> BigQuery on-demand analysis ($/TiB; x0.9095 unit conversion), DET.
2. RA3 Managed Storage (RMS) -> BigQuery Active Logical Storage (~$0.02/GiB-mo), DET.
3. Provisioned node-hours (RA3, DC2, DS2) -> BigQuery review/passthrough with candidate disclosure
   (Editions slots for steady utilization vs on-demand $/TiB for bursty); NEVER a hardcoded slot multiplier.
4. Serverless (RPU-hours) -> BigQuery review (Editions autoscaling / on-demand analysis).
5. Concurrency Scaling -> free tier to ignore ($0); overage to review.
6. Backup / snapshot storage -> BigQuery storage (table snapshots).
7. All catalog billing dimensions dispatch cleanly (zero spec gaps).
"""
import pytest
import scripts.apply_static_mappings as asm
import scripts.spec_dispatch as sd


def test_redshift_spectrum_maps_to_bigquery_analysis():
    """Spectrum (per-TB scanned) must map to BigQuery on-demand analysis ($6.25/TiB)."""
    rows = [
        {
            "aws_li_key": "k_spectrum_tb",
            "product": "Amazon Redshift",
            "usage_type": "APS3-Spectrum",
            "unit": "TB",
            "total_usage": 15.0,
            "aws_amortized_cost": 75.0,
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_spectrum_scanned",
            "product": "Amazon Redshift",
            "usage_type": "APS3-ScannedBytes",
            "unit": "Bytes",
            "total_usage": 10995116277760.0,  # 10 TiB in bytes
            "aws_amortized_cost": 50.0,
            "gcp_region": "us-central1",
        },
    ]

    res = asm.map_redshift(rows)
    assert len(res) == 2

    # TB row: converts to binary TiB (×0.9095)
    r1 = res[0]
    assert r1["gcp_service"] == "BigQuery"
    assert r1["gcp_sku_name"] == "Analysis"
    assert r1["component"] == "analysis"
    assert r1["strategy"] == "map"
    assert r1["unit_multiplier"] == pytest.approx(0.9095, rel=1e-3)
    assert "Analysis" in (r1.get("gcp_sku_name") or "")
    assert "cloud.google.com/bigquery/pricing" in r1["projection_note"]

    # Bytes row
    r2 = res[1]
    assert r2["gcp_service"] == "BigQuery"
    assert r2["gcp_sku_name"] == "Analysis"
    assert r2["strategy"] == "map"
    assert r2["unit_multiplier"] == pytest.approx(1.0 / (1024.0 ** 4), rel=1e-3)


def test_redshift_rms_storage_maps_to_active_logical_storage():
    """RMS Managed Storage must map to BigQuery Active Logical Storage."""
    rows = [
        {
            "aws_li_key": "k_rms_1",
            "product": "Amazon Redshift",
            "usage_type": "APS3-RMS:ra3.4xlarge",
            "unit": "GB-Mo",
            "total_usage": 12000.0,
            "aws_amortized_cost": 288.0,
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_rms_2",
            "product": "Amazon Redshift",
            "usage_type": "APS3-ManagedStorage",
            "unit": "GB-Mo",
            "total_usage": 8000.0,
            "aws_amortized_cost": 192.0,
            "gcp_region": "us-central1",
        },
    ]

    res = asm.map_redshift(rows)
    assert len(res) == 2

    for entry in res:
        assert entry["gcp_service"] == "BigQuery"
        assert entry["gcp_sku_name"] == "Active Logical Storage"
        assert entry["component"] == "storage"
        assert entry["strategy"] == "map"
        assert entry["unit_multiplier"] == 1.0
        assert "Active Logical Storage" in entry["projection_note"]
        assert "cloud.google.com/bigquery/pricing" in entry["projection_note"]


def test_redshift_node_hours_routes_to_bigquery_review_without_slot_multiplier():
    """Redshift node-hours must route to BigQuery review disclosing candidates, never hardcoded slots."""
    rows = [
        {
            "aws_li_key": "k_node_ra3",
            "product": "Amazon Redshift",
            "usage_type": "APS3-Node:ra3.4xlarge",
            "unit": "Hrs",
            "total_usage": 720.0,
            "aws_amortized_cost": 2347.20,
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_node_dc2",
            "product": "Amazon Redshift",
            "usage_type": "APS3-Node:dc2.large",
            "unit": "Hrs",
            "total_usage": 720.0,
            "aws_amortized_cost": 180.0,
            "gcp_region": "us-central1",
        },
    ]

    res = asm.map_redshift(rows)
    assert len(res) == 2

    for entry in res:
        assert entry["gcp_service"] in ("BigQuery", "BigQuery Reservation API")
        assert entry["strategy"] == "passthrough"
        assert entry["component"] == "compute"
        assert entry["unit_multiplier"] == 1.0
        assert entry["mapping_confidence"] == 0.50
        note = entry["projection_note"]
        # Invariant: Discloses both candidate sizing models (Editions slots vs on-demand $/TiB)
        assert "Editions committed slots" in note
        assert "on-demand query pricing" in note
        assert "Utilization not in CUR" in note
        assert "cloud.google.com/bigquery/pricing" in note


def test_redshift_serverless_rpu_routes_to_review():
    """Redshift Serverless RPU-hours must route to BigQuery review."""
    rows = [
        {
            "aws_li_key": "k_serverless_rpu",
            "product": "Amazon Redshift",
            "usage_type": "APS3-ServerlessUsage",
            "unit": "RPU-Hrs",
            "total_usage": 150.0,
            "aws_amortized_cost": 56.25,
            "gcp_region": "us-central1",
        }
    ]

    res = asm.map_redshift(rows)
    assert len(res) == 1
    r = res[0]
    assert r["gcp_service"] == "BigQuery"
    assert r["strategy"] == "passthrough"
    assert r["component"] == "compute"
    assert "Editions autoscaling or on-demand analysis" in r["projection_note"]


def test_redshift_concurrency_scaling():
    """Concurrency Scaling: free tier -> ignore ($0); overage -> review."""
    rows = [
        {
            "aws_li_key": "k_cs_free",
            "product": "Amazon Redshift",
            "usage_type": "APS3-CS:ra3.4xlarge",
            "unit": "Seconds",
            "total_usage": 3600.0,
            "aws_amortized_cost": 0.0,
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_cs_billed",
            "product": "Amazon Redshift",
            "usage_type": "APS3-CS:ra3.4xlarge",
            "unit": "Seconds",
            "total_usage": 7200.0,
            "aws_amortized_cost": 45.0,
            "gcp_region": "us-central1",
        },
    ]

    res = asm.map_redshift(rows)
    assert len(res) == 2

    # Free tier -> ignore ($0)
    r_free = res[0]
    assert r_free["gcp_service"] == "BigQuery"
    assert r_free["strategy"] == "ignore"
    assert r_free["unit_multiplier"] == 0.0
    assert "free tier" in r_free["projection_note"].lower()

    # Billed overage -> review/passthrough
    r_billed = res[1]
    assert r_billed["gcp_service"] == "BigQuery"
    assert r_billed["strategy"] == "passthrough"
    assert r_billed["unit_multiplier"] == 1.0
    assert "Editions autoscaling" in r_billed["projection_note"]


def test_redshift_backup_routes_to_passthrough():
    """Redshift backup/snapshot storage routes to review/passthrough."""
    rows = [
        {
            "aws_li_key": "k_backup",
            "product": "Amazon Redshift",
            "usage_type": "APS3-BackupUsage",
            "unit": "GB-Mo",
            "total_usage": 250.0,
            "aws_amortized_cost": 6.25,
            "gcp_region": "us-central1",
        }
    ]

    res = asm.map_redshift(rows)
    assert len(res) == 1
    r = res[0]
    assert r["gcp_service"] == "BigQuery"
    assert r["component"] == "backup"
    assert r["strategy"] == "passthrough"
    assert "cloud.google.com/bigquery/pricing" in r["projection_note"]


def test_redshift_spec_dispatch_coverage():
    """Verify all 13 Redshift billing dimensions dispatch cleanly in spec_dispatch."""
    test_cases = [
        ("APS3-Node:ra3.4xlarge", "Hrs", "redshift_node", "redshift"),
        ("APS3-Node:ra3.xlplus", "Hrs", "redshift_node", "redshift"),
        ("APS3-Node:ra3.16xlarge", "Hrs", "redshift_node", "redshift"),
        ("APS3-Node:dc2.large", "Hrs", "redshift_node", "redshift"),
        ("APS3-Node:dc2.8xlarge", "Hrs", "redshift_node", "redshift"),
        ("APS3-RMS:ra3.4xlarge", "GB-Mo", "redshift_storage", "redshift"),
        ("APS3-ManagedStorage", "GB-Mo", "redshift_storage", "redshift"),
        ("APS3-ServerlessUsage", "RPU-Hrs", "redshift_serverless", "redshift"),
        ("APS3-Spectrum", "TB", "redshift_spectrum", "redshift"),
        ("APS3-ScannedBytes", "Bytes", "redshift_spectrum", "redshift"),
        ("APS3-CS:ra3.4xlarge", "Seconds", "redshift_concurrency_scaling", "redshift"),
        ("APS3-BackupUsage", "GB-Mo", "redshift_backup", "redshift"),
        ("APS3-DataTransfer-Out-Bytes", "GB", "redshift_data_transfer", "data_transfer"),
    ]

    for ut, unit, exp_ct, exp_mech in test_cases:
        row = {
            "product": "Amazon Redshift",
            "product_code": "AmazonRedshift",
            "canonical": "redshift",
            "usage_type": ut,
            "unit": unit,
        }
        d = sd.dispatch(row)
        assert d is not None, f"Dispatch returned None for {ut}"
        assert d["charge_type"] == exp_ct, f"{ut}: charge_type={d['charge_type']!r}, expected {exp_ct!r}"
        assert d["mechanic"] == exp_mech, f"{ut}: mechanic={d['mechanic']!r}, expected {exp_mech!r}"

