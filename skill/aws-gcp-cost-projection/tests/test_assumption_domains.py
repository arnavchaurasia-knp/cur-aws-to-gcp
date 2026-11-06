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
