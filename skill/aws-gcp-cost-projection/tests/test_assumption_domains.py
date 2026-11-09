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


