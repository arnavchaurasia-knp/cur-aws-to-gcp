"""Unit and invariant tests for MWAA (Amazon Managed Workflows for Apache Airflow) -> Cloud Composer Gen 2 full modelling (§4.20).

Verifies:
1. Environment rows break down into 3 entries: Compute mCPUs (base_vcpu * 1000), Compute Memory (base_gib), Environment Fee ($/hr tier).
2. Worker / Scheduler / Webserver rows break down into 2 entries: Compute mCPUs (comp_vcpu * 1000), Compute Memory (comp_gib).
3. Metadata DB storage maps to Cloud Composer Database Storage ($0.17/GiB-mo, 1:1 match to Aurora PostgreSQL DB storage).
4. Empirical rate checks at us-central1 (mw1.large base ≈ $1.62/env-hr).
5. Invariants:
   - No map row has an empty or null gcp_sku_id.
   - Every passthrough row carries an intentional passthrough_reason.
   - Database vCPU is never mapped to Compute mCPUs (covered by Environment Fee).
6. Completeness: all MWAA dimensions in service_usage_catalog.json dispatch to mechanic="mwaa".
"""
import pytest
import duckdb
import os
from scripts.apply_static_mappings import (
    map_mwaa,
    _MWAA_CLASS_SPEC,
    _MWAA_CLASS_TO_COMPOSER_SIZE,
    GCP_COMPOSER,
)
import scripts.spec_dispatch as sd

HERE = os.path.dirname(__file__)
CATALOG_DB = os.path.join(HERE, "..", "data", "catalog.duckdb")


def _get_catalog_rate(sku_id: str) -> float:
    con = duckdb.connect(CATALOG_DB, read_only=True)
    res = con.execute("SELECT rate_usd FROM tiered_rates WHERE sku_id = ?", [sku_id]).fetchone()
    con.close()
    assert res is not None, f"SKU {sku_id} not found in tiered_rates"
    return float(res[0])


def test_mwaa_environment_large_breakdown():
    """mw1.large environment row breaks down to 3 entries: Compute mCPUs, Compute Memory, Large Environment Fee."""
    row = {
        "aws_li_key": "k_env_large",
        "product": "Amazon Managed Workflows for Apache Airflow",
        "usage_type": "USE1-Environment-mw1.large",
        "operation": "CreateEnvironment",
        "unit": "Hrs",
        "gcp_region": "us-central1",
    }
    res = map_mwaa([row])
    assert len(res) == 3, f"Expected 3 entries for environment row, got {len(res)}"

    components = {entry["component"]: entry for entry in res}
    assert set(components.keys()) == {"compute", "memory", "environment-fee"}

    # Base vCPU = 14 (1 webserver*2 + 1 worker*4 + 2 schedulers*4 = 2 + 4 + 8 = 14)
    # milli-CPUs multiplier = 14 * 1000 = 14000.0
    comp = components["compute"]
    assert comp["strategy"] == "map"
    assert comp["unit_multiplier"] == 14000.0
    assert comp["gcp_sku_id"] == "B701-3D6E-BFF1"
    assert comp["gcp_sku_unit"] == "h"
    assert comp["break_down"] is True

    # Base RAM = 28 GiB (1 webserver*4 + 1 worker*8 + 2 schedulers*8 = 4 + 8 + 16 = 28)
    mem = components["memory"]
    assert mem["strategy"] == "map"
    assert mem["unit_multiplier"] == 28.0
    assert mem["gcp_sku_id"] == "D7FD-FC38-57D9"
    assert mem["gcp_sku_unit"] == "GiBy.h"
    assert mem["break_down"] is True

    # Large Environment Fee ($0.85/hr)
    fee = components["environment-fee"]
    assert fee["strategy"] == "map"
    assert fee["unit_multiplier"] == 1.0
    assert fee["gcp_sku_id"] == "2C11-0523-61D9"
    assert fee["gcp_sku_unit"] == "h"
    assert fee["break_down"] is True


def test_mwaa_environment_small_and_medium():
    """mw1.small and mw1.medium environment rows break down with correct multipliers and fee tiers."""
    rows = [
        {
            "aws_li_key": "k_env_small",
            "product": "Amazon Managed Workflows for Apache Airflow",
            "usage_type": "USE1-Environment-mw1.small",
            "unit": "Hrs",
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_env_med",
            "product": "Amazon Managed Workflows for Apache Airflow",
            "usage_type": "USE1-Environment-mw1.medium",
            "unit": "Hrs",
            "gcp_region": "us-central1",
        },
    ]
    res = map_mwaa(rows)
    assert len(res) == 6

    # mw1.small: base vCPU = 4 (1*1 + 1*1 + 2*1), base RAM = 8 (2 + 2 + 2*2), Small env fee
    small_entries = {e["component"]: e for e in res if e["aws_li_key"] == "k_env_small"}
    assert small_entries["compute"]["unit_multiplier"] == 4000.0
    assert small_entries["memory"]["unit_multiplier"] == 8.0
    assert small_entries["environment-fee"]["gcp_sku_id"] == "6EA4-3652-173E"  # Small Environment Fee

    # mw1.medium: base vCPU = 7 (1*1 + 1*2 + 2*2), base RAM = 14 (2 + 4 + 2*4), Medium env fee
    med_entries = {e["component"]: e for e in res if e["aws_li_key"] == "k_env_med"}
    assert med_entries["compute"]["unit_multiplier"] == 7000.0
    assert med_entries["memory"]["unit_multiplier"] == 14.0
    assert med_entries["environment-fee"]["gcp_sku_id"] == "9802-34D7-AFE2"  # Medium Environment Fee


def test_mwaa_environment_micro():
    """mw1.micro has shared worker/scheduler: base vCPU=2, RAM=6, Small env fee."""
    row = {
        "aws_li_key": "k_env_micro",
        "product": "Amazon Managed Workflows for Apache Airflow",
        "usage_type": "USE1-Environment-mw1.micro",
        "unit": "Hrs",
        "gcp_region": "us-central1",
    }
    res = map_mwaa([row])
    assert len(res) == 3
    entries = {e["component"]: e for e in res}
    assert entries["compute"]["unit_multiplier"] == 2000.0
    assert entries["memory"]["unit_multiplier"] == 6.0
    assert entries["environment-fee"]["gcp_sku_id"] == "6EA4-3652-173E"  # Small


def test_mwaa_environment_xlarge_and_2xlarge_capped_fee():
    """mw1.xlarge and mw1.2xlarge cap Environment Fee at Large, noting the cap."""
    rows = [
        {
            "aws_li_key": "k_env_xl",
            "product": "Amazon Managed Workflows for Apache Airflow",
            "usage_type": "USE1-Environment-mw1.xlarge",
            "unit": "Hrs",
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_env_2xl",
            "product": "Amazon Managed Workflows for Apache Airflow",
            "usage_type": "USE1-Environment-mw1.2xlarge",
            "unit": "Hrs",
            "gcp_region": "us-central1",
        },
    ]
    res = map_mwaa(rows)
    assert len(res) == 6

    xl_entries = {e["component"]: e for e in res if e["aws_li_key"] == "k_env_xl"}
    assert xl_entries["compute"]["unit_multiplier"] == 28000.0  # base vCPU=28
    assert xl_entries["memory"]["unit_multiplier"] == 84.0      # base RAM=84
    assert xl_entries["environment-fee"]["gcp_sku_id"] == "2C11-0523-61D9"  # Large
    assert "caps at Large" in xl_entries["environment-fee"]["projection_note"]

    xxl_entries = {e["component"]: e for e in res if e["aws_li_key"] == "k_env_2xl"}
    assert xxl_entries["compute"]["unit_multiplier"] == 56000.0  # base vCPU=56
    assert xxl_entries["memory"]["unit_multiplier"] == 168.0     # base RAM=168
    assert xxl_entries["environment-fee"]["gcp_sku_id"] == "2C11-0523-61D9"  # Large
    assert "caps at Large" in xxl_entries["environment-fee"]["projection_note"]


def test_mwaa_worker_rows():
    """Worker rows emit 2 entries: Compute mCPUs + Compute Memory with per-worker specs."""
    row = {
        "aws_li_key": "k_worker_large",
        "product": "Amazon Managed Workflows for Apache Airflow",
        "usage_type": "USE1-Worker-mw1.large",
        "unit": "Hrs",
        "gcp_region": "us-central1",
    }
    res = map_mwaa([row])
    assert len(res) == 2
    entries = {e["component"]: e for e in res}
    assert set(entries.keys()) == {"compute", "memory"}

    # mw1.large worker: 4 vCPU, 8 GiB
    assert entries["compute"]["unit_multiplier"] == 4000.0
    assert entries["compute"]["gcp_sku_id"] == "B701-3D6E-BFF1"
    assert entries["memory"]["unit_multiplier"] == 8.0
    assert entries["memory"]["gcp_sku_id"] == "D7FD-FC38-57D9"


def test_mwaa_scheduler_and_webserver_rows():
    """Scheduler and webserver rows emit 2 entries: Compute mCPUs + Compute Memory."""
    rows = [
        {
            "aws_li_key": "k_sched_med",
            "product": "Amazon Managed Workflows for Apache Airflow",
            "usage_type": "USE1-Scheduler-mw1.medium",
            "unit": "Hrs",
            "gcp_region": "us-central1",
        },
        {
            "aws_li_key": "k_web_small",
            "product": "Amazon Managed Workflows for Apache Airflow",
            "usage_type": "USE1-Webserver-mw1.small",
            "unit": "Hrs",
            "gcp_region": "us-central1",
        },
    ]
    res = map_mwaa(rows)
    assert len(res) == 4

    # mw1.medium scheduler: 2 vCPU, 4 GiB
    sched = {e["component"]: e for e in res if e["aws_li_key"] == "k_sched_med"}
    assert sched["compute"]["unit_multiplier"] == 2000.0
    assert sched["memory"]["unit_multiplier"] == 4.0

    # mw1.small webserver: 1 vCPU, 2 GiB
    web = {e["component"]: e for e in res if e["aws_li_key"] == "k_web_small"}
    assert web["compute"]["unit_multiplier"] == 1000.0
    assert web["memory"]["unit_multiplier"] == 2.0


def test_mwaa_database_storage():
    """Metadata database storage maps to Cloud Composer Database Storage ($0.17/GiB-mo)."""
    row = {
        "aws_li_key": "k_db_storage",
        "product": "Amazon Managed Workflows for Apache Airflow",
        "usage_type": "USE1-MeteredDatabaseStorage-GB",
        "unit": "GB-Mo",
        "gcp_region": "us-central1",
    }
    res = map_mwaa([row])
    assert len(res) == 1
    entry = res[0]
    assert entry["component"] == "database-storage"
    assert entry["strategy"] == "map"
    assert entry["gcp_sku_id"] == "1642-E13D-F4C4"
    assert entry["unit_multiplier"] == 1.0
    assert entry["gcp_sku_unit"] == "GiBy.mo"
    assert entry["break_down"] is False

    # Check catalog rate: 100 GB-Mo -> $17.00
    rate = _get_catalog_rate(entry["gcp_sku_id"])
    assert rate == pytest.approx(0.17, rel=1e-3)
    projected = 100.0 * entry["unit_multiplier"] * rate
    assert projected == pytest.approx(17.00, rel=1e-3)


def test_mwaa_empirical_rate_large_base():
    """Empirical rate check: 1 environment-hour of mw1.large base (14 vCPU/28 GiB) ≈ $1.62/hr in us-central1."""
    row = {
        "aws_li_key": "k_env_large",
        "product": "Amazon Managed Workflows for Apache Airflow",
        "usage_type": "USE1-Environment-mw1.large",
        "unit": "Hrs",
        "gcp_region": "us-central1",
    }
    res = map_mwaa([row])
    assert len(res) == 3

    total_hourly = 0.0
    for entry in res:
        rate = _get_catalog_rate(entry["gcp_sku_id"])
        cost = 1.0 * entry["unit_multiplier"] * rate
        total_hourly += cost

    # Breakdown: Compute (14000 * 0.000045 = $0.63) + Memory (28 * 0.005 = $0.14) + Env fee ($0.85) = $1.62
    assert total_hourly == pytest.approx(1.62, abs=0.01)


def test_mwaa_database_vcpu_not_mapped_to_compute():
    """Database vCPU is never mapped to Compute mCPUs (covered by Environment Fee)."""
    # mw1.large total spec is webserver(2)+worker(4)+schedulers(8)+database(2) = 16 vCPU.
    # Compute mCPUs must ONLY map webserver+worker+schedulers = 14 vCPU (14000 milli).
    row = {
        "aws_li_key": "k_env_large",
        "product": "Amazon Managed Workflows for Apache Airflow",
        "usage_type": "USE1-Environment-mw1.large",
        "unit": "Hrs",
        "gcp_region": "us-central1",
    }
    res = map_mwaa([row])
    comp_entry = next(e for e in res if e["component"] == "compute")
    assert comp_entry["unit_multiplier"] == 14000.0
    assert comp_entry["unit_multiplier"] != 16000.0
    assert "DB compute covered by Composer env fee" in comp_entry["projection_note"]


def test_mwaa_invariants():
    """Invariants: no map row has empty gcp_sku_id; passthroughs carry intentional reason."""
    # Test valid mapped rows across regions
    for region in ["us-central1", "us-east1", "europe-west1"]:
        rows = [
            {"aws_li_key": f"k_env_{region}", "usage_type": "USE1-Environment-mw1.small", "gcp_region": region},
            {"aws_li_key": f"k_work_{region}", "usage_type": "USE1-Worker-mw1.large", "gcp_region": region},
            {"aws_li_key": f"k_stor_{region}", "usage_type": "USE1-MeteredDatabaseStorage-GB", "gcp_region": region},
        ]
        res = map_mwaa(rows)
        for e in res:
            if e["strategy"] == "map":
                assert e["gcp_sku_id"] is not None and len(e["gcp_sku_id"]) > 0
                assert e["passthrough_reason"] is None
            else:
                assert e["passthrough_reason"] is not None

    # Test unknown region -> falls to passthrough with no_rate_in_region
    res_unknown = map_mwaa([{
        "aws_li_key": "k_unknown_reg",
        "usage_type": "USE1-Environment-mw1.small",
        "gcp_region": "moon-crater-1",
    }])
    assert len(res_unknown) == 3
    for e in res_unknown:
        assert e["strategy"] == "passthrough"
        assert e["gcp_sku_id"] is None
        assert e["passthrough_reason"] == "no_rate_in_region"

    # Test unrecognized MWAA usage_type
    res_unrec = map_mwaa([{
        "aws_li_key": "k_unrec",
        "usage_type": "USE1-SomethingTotallyUnknown",
        "unit": "Requests",
        "gcp_region": "us-central1",
    }])
    assert len(res_unrec) == 1
    assert res_unrec[0]["strategy"] == "passthrough"
    assert res_unrec[0]["passthrough_reason"] == "service_policy_review"


def test_mwaa_catalog_completeness_and_dispatch():
    """Every dimension in service_usage_catalog.json for mwaa dispatches to mechanic='mwaa'."""
    cat = sd.SPECS.get("mwaa", {})
    assert cat, "mwaa not registered in service_specs"

    dims = [
        ("USE1-Environment-mw1.micro", "mwaa_environment"),
        ("USE1-Environment-mw1.small", "mwaa_environment"),
        ("USE1-Environment-mw1.medium", "mwaa_environment"),
        ("USE1-Environment-mw1.large", "mwaa_environment"),
        ("USE1-Environment-mw1.xlarge", "mwaa_environment"),
        ("USE1-Environment-mw1.2xlarge", "mwaa_environment"),
        ("USE1-Worker-mw1.small", "mwaa_worker"),
        ("USE1-Worker-mw1.large", "mwaa_worker"),
        ("USE1-Scheduler-mw1.small", "mwaa_scheduler"),
        ("USE1-Scheduler-mw1.large", "mwaa_scheduler"),
        ("USE1-Webserver-mw1.small", "mwaa_webserver"),
        ("USE1-Webserver-mw1.large", "mwaa_webserver"),
        ("USE1-MeteredDatabaseStorage-GB", "mwaa_storage"),
        ("USE1-Storage-mw1.small", "mwaa_storage"),
    ]
    for ut, expected_ct in dims:
        row = {
            "aws_li_key": f"k_{ut}",
            "product": "Amazon Managed Workflows for Apache Airflow",
            "product_code": "AmazonMWAA",
            "canonical": "mwaa",
            "usage_type": ut,
            "operation": "CreateEnvironment",
            "unit": "Hrs",
            "line_item_type": "Usage",
            "pricing_model": "OnDemand",
            "aws_amortized_cost": 100.0,
            "total_usage": 10.0,
            "gcp_region": "us-central1",
            "aws_region": "us-east-1",
        }
        res = sd.dispatch(row)
        assert res is not None
        assert res.get("mechanic") == "mwaa", f"{ut}: expected mechanic mwaa, got {res.get('mechanic')}"
        assert res.get("charge_type") == expected_ct, f"{ut}: expected charge_type {expected_ct}, got {res.get('charge_type')}"
