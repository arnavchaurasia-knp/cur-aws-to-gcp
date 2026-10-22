import os
import re
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from config_loader import load_data_config
import check_catalog_age
import apply_rates


def test_review_config_catalog_thresholds():
    cfg = load_data_config("review-config")
    assert "catalog_refresh_after_days" in cfg, "review-config.json must define catalog_refresh_after_days"
    assert "catalog_max_build_age_days" in cfg, "review-config.json must define catalog_max_build_age_days"
    assert cfg["catalog_refresh_after_days"] == 7
    assert cfg["catalog_max_build_age_days"] == 14


def test_dockerfile_arg_matches_config():
    cfg = load_data_config("review-config")
    expected_days = cfg["catalog_max_build_age_days"]

    # Locate Dockerfile in repo root
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    dockerfile_path = os.path.join(repo_root, "Dockerfile")
    assert os.path.exists(dockerfile_path), f"Dockerfile not found at {dockerfile_path}"

    with open(dockerfile_path, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r'^\s*ARG\s+CATALOG_MAX_AGE_DAYS\s*=\s*(\d+)', content, re.MULTILINE)
    assert match is not None, "Dockerfile must define ARG CATALOG_MAX_AGE_DAYS=<default>"
    dockerfile_val = int(match.group(1))

    assert dockerfile_val == expected_days, (
        f"Dockerfile ARG CATALOG_MAX_AGE_DAYS ({dockerfile_val}) drifted from "
        f"review-config.json catalog_max_build_age_days ({expected_days})"
    )


def test_check_catalog_age_uses_config():
    cfg = load_data_config("review-config")
    assert check_catalog_age.REFRESH_DAYS == cfg["catalog_refresh_after_days"]
    assert check_catalog_age.DEFAULT_MAX_BUILD_AGE_DAYS == cfg["catalog_max_build_age_days"]


def test_apply_rates_uses_config():
    cfg = load_data_config("review-config")
    assert apply_rates._CATALOG_MAX_BUILD_AGE_DAYS == cfg["catalog_max_build_age_days"]
