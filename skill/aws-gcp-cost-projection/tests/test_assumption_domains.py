#!/usr/bin/env python3
"""test_assumption_domains.py — Test coverage and invariants for assumption value domains."""

import json
import os
import sys

import pytest

# Ensure scripts is on sys.path
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_TESTS_DIR)
_SCRIPTS_DIR = os.path.join(_SKILL_DIR, "scripts")
_DATA_DIR = os.path.join(_SKILL_DIR, "data")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from assumption_domains import load_domains, domain_for, derive_aurora_values


@pytest.fixture
def defaults():
    defaults_path = os.path.join(_DATA_DIR, "assumption-defaults.json")
    with open(defaults_path, "r", encoding="utf-8") as f:
        return json.load(f)["values"]


@pytest.fixture
def ratio_registry():
    path = os.path.join(_DATA_DIR, "ratio_registry.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_all_defaults_have_domain_entry(defaults):
    """Every key in assumption-defaults.json must be registered in assumption_domains.json."""
    domains = load_domains()
    assert len(domains) >= 19
    for key in defaults:
        assert key in domains, f"Missing domain entry for default key '{key}'"


def test_refresh_tiers_and_sources_valid():
    """Every key must have a valid refresh_tier (A, B, C, D) and Tier A/B must resolve sources."""
    domains = load_domains()
    assert len(domains) >= 19
    valid_tiers = {"A", "B", "C", "D"}
    for key, dom in domains.items():
        tier = dom.get("refresh_tier")
        assert tier in valid_tiers, f"Assumption '{key}' missing valid refresh_tier (got {tier})"

        if tier == "A":
            # Must point to an in-repo source_artifact or source_url
            artifact = dom.get("source_artifact")
            url = dom.get("source_url")
            assert artifact or url, f"Tier A key '{key}' missing source_artifact and source_url"
            if artifact:
                abs_art = os.path.join(_SKILL_DIR, artifact)
                assert os.path.exists(abs_art), f"Tier A key '{key}' points to non-existent artifact {artifact}"
        elif tier == "B":
            # Must point to a valid http(s) source_url
            url = dom.get("source_url")
            assert url and url.startswith("http"), f"Tier B key '{key}' missing valid source_url (got {url})"
        elif tier == "C":
            # Bill calibrated
            assert dom.get("source_kind") == "bill_calibrated"
        elif tier == "D":
            # Tracked outcome must be recorded
            assert dom.get("tracked_outcome") in {"NO_SOURCE", "POLICY", "NEEDS_HUMAN"}


def test_aurora_acu_ram_gib_matches_ratio_registry(ratio_registry):
    """aurora_acu_ram_gib.values must strictly equal ratio_registry.allowed_ram_per_vcpu."""
    domain = domain_for("aurora_acu_ram_gib")
    assert domain.get("kind") == "enum"
    assert domain.get("values") == ratio_registry["allowed_ram_per_vcpu"]


def test_aurora_derivation_helper():
    """derive_aurora_values correctly computes RAM and vCPU lists including default 2.0."""
    ram_vals, vcpu_vals = derive_aurora_values()
    assert ram_vals == [4.0, 8.0, 16.0]
    assert 2.0 in vcpu_vals
    assert vcpu_vals == [0.5, 1.0, 2.0]


def test_defaults_satisfy_domain_constraints(defaults):
    """Every default must be a valid member or fall within [min, max] of its domain."""
    domains = load_domains()
    for key, default_val in defaults.items():
        dom = domains[key]
        kind = dom.get("kind")

        if kind == "enum":
            allowed_vals = dom.get("values", [])
            assert default_val in allowed_vals or any(abs(default_val - v) < 1e-9 for v in allowed_vals), (
                f"Default {default_val} for enum key '{key}' not in {allowed_vals}"
            )
        elif kind == "flag":
            allowed_vals = [v["value"] for v in dom.get("values", [])]
            assert default_val in allowed_vals or any(abs(default_val - v) < 1e-9 for v in allowed_vals), (
                f"Default {default_val} for flag key '{key}' not in {allowed_vals}"
            )
        elif kind in ("range", "scalar_published"):
            min_v = dom.get("min")
            max_v = dom.get("max")
            assert min_v is not None and max_v is not None, f"Missing bounds for '{key}'"
            assert min_v <= default_val <= max_v, (
                f"Default {default_val} for '{key}' outside bounds [{min_v}, {max_v}]"
            )
            if kind == "scalar_published":
                pub_val = dom.get("value")
                assert pub_val is not None
                assert abs(pub_val - default_val) < 1e-9, (
                    f"Published scalar '{key}' value {pub_val} differs from default {default_val}"
                )
        else:
            pytest.fail(f"Unknown kind '{kind}' for assumption '{key}'")


def test_cli_list_and_key():
    """Verify assumption_domains CLI options."""
    import subprocess
    cmd = [sys.executable, os.path.join(_SCRIPTS_DIR, "assumption_domains.py"), "--list"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert "Assumption Value Domains Registry" in res.stdout
    assert "aurora_acu_ram_gib" in res.stdout

    cmd_key = [sys.executable, os.path.join(_SCRIPTS_DIR, "assumption_domains.py"), "--key", "aurora_acu_ram_gib"]
    res_key = subprocess.run(cmd_key, capture_output=True, text=True, check=True)
    assert "Assumption: aurora_acu_ram_gib" in res_key.stdout


def test_refresh_domains_behavior(tmp_path):
    """Verify refresh_domains behaviors for derived, provider_published, and judgment."""
    from datetime import date, timedelta
    from refresh_assumption_domains import refresh_domains

    test_domains = {
        "_meta": {"version": 1},
        "domains": {
            "aurora_acu_ram_gib": {
                "kind": "enum",
                "values": [2.0],  # deliberately wrong/stale
                "source_kind": "derived_in_repo",
                "source": "scripts/gen_ratio_registry.py",
                "refreshable": True,
            },
            "lambda_mb_per_vcpu": {
                "kind": "scalar_published",
                "value": 1769.0,
                "min": 512.0,
                "max": 10240.0,
                "source_kind": "provider_published",
                "source": "https://example.com/lambda",
                "last_verified": "2024-01-01",  # > 180 days ago
                "needs_human_verification": False,
            },
            "dax_node_gib": {
                "kind": "range",
                "min": 1.0,
                "max": 512.0,
                "source_kind": "judgment",
                "source": "fallback",
                "needs_human_verification": False,
            }
        }
    }

    domains_file = tmp_path / "assumption_domains.json"
    with open(domains_file, "w", encoding="utf-8") as f:
        json.dump(test_domains, f)

    # 1. Test check-only: prints VERIFY line, does not modify file
    today = date(2026, 11, 6)
    data, report, stale_count = refresh_domains(
        domains_path=str(domains_file),
        registry_path=os.path.join(_DATA_DIR, "ratio_registry.json"),
        check_only=True,
        as_of_date=today
    )
    assert stale_count == 1
    assert any("VERIFY lambda_mb_per_vcpu" in line for line in report)
    with open(domains_file, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    # File must be untouched in check_only
    assert disk_data["domains"]["aurora_acu_ram_gib"]["values"] == [2.0]
    assert disk_data["domains"]["lambda_mb_per_vcpu"]["needs_human_verification"] is False

    # 2. Test full refresh: writes file, updates derived, flags stale published, leaves judgment untouched
    data, report, stale_count = refresh_domains(
        domains_path=str(domains_file),
        registry_path=os.path.join(_DATA_DIR, "ratio_registry.json"),
        check_only=False,
        as_of_date=today
    )
    assert stale_count == 1
    with open(domains_file, "r", encoding="utf-8") as f:
        updated = json.load(f)["domains"]

    # derived_in_repo updated
    assert updated["aurora_acu_ram_gib"]["values"] == [4.0, 8.0, 16.0]
    # provider_published flagged
    assert updated["lambda_mb_per_vcpu"]["needs_human_verification"] is True
    assert updated["lambda_mb_per_vcpu"]["value"] == 1769.0  # value untouched!
    # judgment untouched
    assert updated["dax_node_gib"]["needs_human_verification"] is False


def test_refresh_domains_rejection_gates(tmp_path, monkeypatch):
    """Derived values that are $0, out of range, or a >50% jump must be rejected."""
    import refresh_assumption_domains as rad
    from refresh_assumption_domains import refresh_domains

    test_domains = {
        "_meta": {
            "version": 1,
            "max_refresh_delta_pct": 50,
        },
        "domains": {
            "aurora_acu_ram_gib": {
                "kind": "enum",
                "values": [4.0, 8.0, 16.0],
                "min": 1.0,
                "max": 64.0,
                "source_kind": "derived_in_repo",
                "source": "scripts/gen_ratio_registry.py",
                "refreshable": True,
                "needs_human_verification": False,
            },
            "aurora_acu_vcpu": {
                "kind": "enum",
                "values": [0.5, 1.0, 2.0],
                "min": 0.1,
                "max": 16.0,
                "source_kind": "derived_in_repo",
                "source": "scripts/gen_ratio_registry.py",
                "refreshable": True,
                "needs_human_verification": False,
            },
        }
    }

    # Case A: derived values contain 0.0
    domains_file = tmp_path / "domains_zero.json"
    with open(domains_file, "w", encoding="utf-8") as f:
        json.dump(test_domains, f)

    monkeypatch.setattr(rad, "derive_aurora_values", lambda *args, **kwargs: ([0.0, 8.0], [0.5, 1.0, 2.0]))
    data, report, _ = refresh_domains(domains_path=str(domains_file), check_only=False)
    with open(domains_file, "r", encoding="utf-8") as f:
        saved = json.load(f)["domains"]
    assert saved["aurora_acu_ram_gib"]["values"] == [4.0, 8.0, 16.0], "Old value must be retained on $0 derived"
    assert saved["aurora_acu_ram_gib"]["needs_human_verification"] is True
    assert any("REJECT" in line and "aurora_acu_ram_gib" in line for line in report)

    # Case B: derived values out of range (e.g. 128.0 > max 64.0)
    domains_file_oor = tmp_path / "domains_oor.json"
    with open(domains_file_oor, "w", encoding="utf-8") as f:
        json.dump(test_domains, f)

    monkeypatch.setattr(rad, "derive_aurora_values", lambda *args, **kwargs: ([128.0], [0.5, 1.0, 2.0]))
    data, report, _ = refresh_domains(domains_path=str(domains_file_oor), check_only=False)
    with open(domains_file_oor, "r", encoding="utf-8") as f:
        saved = json.load(f)["domains"]
    assert saved["aurora_acu_ram_gib"]["values"] == [4.0, 8.0, 16.0], "Old value must be retained on out-of-range"
    assert saved["aurora_acu_ram_gib"]["needs_human_verification"] is True
    assert any("REJECT" in line and "aurora_acu_ram_gib" in line for line in report)

    # Case C: derived values jump > 50% (e.g. 32.0 when old max was 16.0 -> 100% jump)
    domains_file_jump = tmp_path / "domains_jump.json"
    with open(domains_file_jump, "w", encoding="utf-8") as f:
        json.dump(test_domains, f)

    monkeypatch.setattr(rad, "derive_aurora_values", lambda *args, **kwargs: ([4.0, 8.0, 32.0], [0.5, 1.0, 2.0]))
    data, report, _ = refresh_domains(domains_path=str(domains_file_jump), check_only=False)
    with open(domains_file_jump, "r", encoding="utf-8") as f:
        saved = json.load(f)["domains"]
    assert saved["aurora_acu_ram_gib"]["values"] == [4.0, 8.0, 16.0], "Old value must be retained on >50% jump"
    assert saved["aurora_acu_ram_gib"]["needs_human_verification"] is True
    assert any("REJECT" in line and "aurora_acu_ram_gib" in line for line in report)


def test_tier_a_derivations(defaults):
    """Tier A derivations must match pinned defaults and be offline-safe."""
    from assumption_domains import derive_node_ram_values, derive_lambda_mb_per_vcpu

    dax_val = derive_node_ram_values("dax")
    assert dax_val == defaults["dax_node_gib"]
    assert dax_val == 16.0

    memdb_val = derive_node_ram_values("memorydb")
    assert memdb_val == defaults["memorydb_node_gib"]
    assert memdb_val == 16.0

    lambda_val = derive_lambda_mb_per_vcpu()
    assert lambda_val == defaults["lambda_mb_per_vcpu"]
    assert lambda_val == 2500.0


def test_tier_b_live_fetch(tmp_path, monkeypatch):
    """Tier B live fetches must be fail-safe, offline-safe, and obey honesty gates."""
    import refresh_assumption_domains as rad
    from refresh_assumption_domains import refresh_domains

    test_domains = {
        "_meta": {"version": 1, "max_refresh_delta_pct": 50},
        "domains": {
            "glue_dpu_vcpu": {
                "kind": "scalar_published",
                "value": 4.0,
                "min": 1.0,
                "max": 32.0,
                "step": 1.0,
                "refresh_tier": "B",
                "source_kind": "provider_published",
                "source_url": "https://aws.amazon.com/glue/pricing/",
                "last_verified": "2026-11-06",
                "needs_human_verification": False,
            }
        }
    }

    # Case 1: Mocked fetch succeeds with pinned value
    f1 = tmp_path / "d1.json"
    with open(f1, "w", encoding="utf-8") as f:
        json.dump(test_domains, f)

    monkeypatch.setattr(rad, "fetch_provider_scalar", lambda key, url: (4.0, ""))
    data1, report1, stale1 = refresh_domains(domains_path=str(f1), check_only=False)
    assert stale1 == 0
    assert data1["domains"]["glue_dpu_vcpu"]["needs_human_verification"] is False
    assert data1["_item_outcomes"]["glue_dpu_vcpu"] == "SELF_FETCHED"

    # Case 2: Mocked fetch fails with network error (fail-safe)
    f2 = tmp_path / "d2.json"
    with open(f2, "w", encoding="utf-8") as f:
        json.dump(test_domains, f)

    monkeypatch.setattr(rad, "fetch_provider_scalar", lambda key, url: (None, "HTTP 500 Connection Timeout"))
    data2, report2, stale2 = refresh_domains(domains_path=str(f2), check_only=False)
    assert any("VERIFY glue_dpu_vcpu" in r for r in report2)
    assert data2["domains"]["glue_dpu_vcpu"]["value"] == 4.0  # untouched!
    assert data2["_item_outcomes"]["glue_dpu_vcpu"] == "VERIFY"

    # Case 3: Mocked fetch returns $0 -> rejected by honesty gate
    f3 = tmp_path / "d3.json"
    with open(f3, "w", encoding="utf-8") as f:
        json.dump(test_domains, f)

    monkeypatch.setattr(rad, "fetch_provider_scalar", lambda key, url: (0.0, ""))
    data3, report3, stale3 = refresh_domains(domains_path=str(f3), check_only=False)
    assert any("REJECT glue_dpu_vcpu" in r for r in report3)
    assert data3["domains"]["glue_dpu_vcpu"]["value"] == 4.0  # untouched!
    assert data3["_item_outcomes"]["glue_dpu_vcpu"] == "REJECTED"


def test_tier_c_bill_calibration(tmp_path):
    """Tier C bill calibration calculates realized discounts when available and falls back to defaults."""
    import duckdb
    from ingest import calibrate_assumptions_from_bill

    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE aws_li_catalog (
            line_item_type VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            pricing_model VARCHAR,
            public_on_demand_cost DOUBLE,
            aws_amortized_cost DOUBLE
        )
    """)

    # Populate with known discounts:
    # SP: public OD 100, amortized 60 -> discount = (100 - 60)/100 = 0.40 (40%)
    conn.execute("""
        INSERT INTO aws_li_catalog VALUES
        ('SavingsPlanCoveredUsage', 'USE1-SavingsPlan', 'RunInstances', 'OnDemand', 100.0, 60.0),
        -- 1yr RI: public OD 200, amortized 120 -> discount = (200 - 120)/200 = 0.40 (40%)
        ('DiscountedUsage', 'USE1-HeavyUsage', 'RunInstances', 'Reserved', 200.0, 120.0),
        -- Spot: public OD 50, amortized 15 -> discount = (50 - 15)/50 = 0.70 (70%)
        ('Usage', 'USE1-SpotUsage', 'RunInstances', 'Spot', 50.0, 15.0)
    """)

    assump_file = tmp_path / "assumptions.json"
    calibrated = calibrate_assumptions_from_bill(conn, assumptions_path=str(assump_file))

    assert calibrated["aws_sp_discount_pct"] == 0.40
    assert calibrated["aws_ri_1yr_discount_pct"] == 0.40
    assert calibrated["aws_spot_discount_pct"] == 0.70
    assert calibrated["aws_ri_3yr_discount_pct"] == 0.52  # default fallback

    # Check written assumptions.json
    with open(assump_file, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["values"]["aws_sp_discount_pct"] == 0.40
    assert saved["value_source"]["aws_sp_discount_pct"] == "bill_derived"
    assert saved["value_source"]["aws_ri_3yr_discount_pct"] == "committed_default"


def test_uniform_tracking_all_19_domains():
    """All 19 domains must be visited each run and emit explicit honest outcomes."""
    from refresh_assumption_domains import refresh_domains

    data, report, stale_count = refresh_domains(check_only=True)
    outcomes = data.get("_item_outcomes", {})
    assert len(outcomes) >= 19

    # Tier A
    assert outcomes["aurora_acu_ram_gib"] == "DERIVED"
    assert outcomes["aurora_acu_vcpu"] == "DERIVED"
    assert outcomes["dax_node_gib"] == "DERIVED"
    assert outcomes["memorydb_node_gib"] == "DERIVED"
    assert outcomes["lambda_mb_per_vcpu"] == "DERIVED"

    # Tier B (cached or live fetched)
    assert outcomes["glue_dpu_ram_gib"] in ("SELF_FETCHED", "VERIFY")
    assert outcomes["glue_dpu_vcpu"] in ("SELF_FETCHED", "VERIFY")

    # Tier C
    assert outcomes["aws_ri_1yr_discount_pct"] == "BILL_CALIBRATED"
    assert outcomes["aws_ri_3yr_discount_pct"] == "BILL_CALIBRATED"
    assert outcomes["aws_sp_discount_pct"] == "BILL_CALIBRATED"
    assert outcomes["aws_spot_discount_pct"] == "BILL_CALIBRATED"
    assert outcomes["nat_vms_per_gateway"] == "BILL_CALIBRATED"

    # Tier D
    assert outcomes["dynamodb_rcu_to_reads_sec"] == "NO_SOURCE"
    assert outcomes["dynamodb_wcu_to_writes_sec"] == "NO_SOURCE"
    assert outcomes["inferentia_chip_to_tpu"] == "NO_SOURCE"
    assert outcomes["trainium_chip_to_tpu"] == "NO_SOURCE"
    assert outcomes["rds_iops_included_in_storage"] == "POLICY"
    assert outcomes["fx_max_staleness_days"] == "POLICY"
    assert outcomes["looker_studio_pro_seat_usd"] == "NEEDS_HUMAN"



