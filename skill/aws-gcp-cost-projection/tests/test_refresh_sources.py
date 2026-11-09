#!/usr/bin/env python3
"""tests/test_refresh_sources.py — Verify declared sources and assert no untracked refreshers."""

import glob
import os
import re
import pytest

from scripts.refresh_sources import load_refresh_sources, load_refresh_source, resolve_expected_count

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_TESTS_DIR)
_REPO_ROOT = os.path.dirname(os.path.dirname(_SKILL_DIR))
_JOBS_DIR = os.path.join(_REPO_ROOT, "internal", "jobs")


def test_refresh_sources_schema():
    sources = load_refresh_sources()
    assert len(sources) >= 2, "Expected at least 2 sources registered"

    # gcp_catalog
    cat = load_refresh_source("gcp_catalog")
    assert cat, "gcp_catalog source missing"
    assert cat["expected_set"] == "data/services.json"
    assert cat["completeness_threshold"] == 100.0
    assert cat["stale_after_days"] > 0
    assert "catalog_manager.py refresh" in cat["refresher"]
    assert cat["lifecycle"]["max_attempts"] >= 1
    assert cat["lifecycle"]["treat_partial_as"] == "retry"

    # compute_availability
    comp = load_refresh_source("compute_availability")
    assert comp, "compute_availability source missing"
    assert comp["expected_set"] == "self:regions"
    assert comp["completeness_threshold"] == 90.0
    assert "verify_compute_availability.py" in comp["refresher"]
    assert comp["lifecycle"]["treat_partial_as"] == "accept"

    # expected counts resolve
    cat_exp = resolve_expected_count(cat, _SKILL_DIR)
    assert cat_exp == 109, f"Expected 109 canonical services in services.json, got {cat_exp}"


def test_no_untracked_refreshers():
    """Every refresh script invoked by Go refreshers/orchestrator must have a declared entry in refresh_sources.json."""
    sources = load_refresh_sources()
    declared_refreshers = {cfg.get("refresher", "").split()[0] for cfg in sources.values() if cfg.get("refresher")}

    # Scan internal/jobs/*.go for executed python refresh scripts
    go_files = glob.glob(os.path.join(_JOBS_DIR, "*.go"))
    assert go_files, "No go files found in internal/jobs"

    script_pattern = re.compile(r'["\']([a-zA-Z0-9_\-]+\.py)["\']')
    invoked_refresh_scripts = set()

    # Scripts that are maintenance/audit tools run in pipelines or tests, not scheduled data refreshers
    non_refresher_scripts = {
        "apply_directives.py",
        "ingest.py",
        "classify_mechanics.py",
        "apply_static_mappings.py",
        "apply_rates.py",
        "validate_fix.py",
        "incremental_rerate.py",
        "render_html_report.py",
        "check_stale_pins.py",
        "aws_normalizer.py",
    }

    for gf in go_files:
        with open(gf, "r", encoding="utf-8") as f:
            content = f.read()
        # Look for script execution patterns like exec.Command(..., "script.py") or filepath.Join(..., "scripts", "xyz.py")
        for match in script_pattern.findall(content):
            if "refresh" in match or "availability" in match or "catalog_manager" in match:
                if match not in non_refresher_scripts:
                    invoked_refresh_scripts.add(match)

    untracked = invoked_refresh_scripts - declared_refreshers
    assert not untracked, f"Found untracked refresher scripts in Go code without entries in refresh_sources.json: {untracked}"
