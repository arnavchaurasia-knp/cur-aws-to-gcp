#!/usr/bin/env python3
"""
config_loader.py — Loads JSON config files from the skill's data/ directory.

All hardcoded tables (instance specs, GPU models, service aliases, etc.) live in
data/*.json. This module reads them so Python scripts never need to be edited
when new AWS/GCP services appear — only the JSON files need updating.

Usage:
    from config_loader import load_data_config
    specs = load_data_config("instance-specs")   # → parsed dict from data/instance-specs.json
"""
from __future__ import annotations
import json
import logging
import os

log = logging.getLogger(__name__)

# Resolved once at import time: the skill root is the parent of the scripts/ directory.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR   = os.environ.get("SKILL_DIR", os.path.dirname(_SCRIPTS_DIR))
_DATA_DIR    = os.path.join(_SKILL_DIR, "data")


def load_data_config(name: str) -> dict:
    """Load and return data/<name>.json. Returns {} and logs a warning on failure."""
    path = os.path.join(_DATA_DIR, f"{name}.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log.warning("config_loader: %s not found — using empty defaults", path)
        return {}
    except Exception as e:
        log.warning("config_loader: failed to load %s: %s — using empty defaults", path, e)
        return {}


def load_assumptions(job_dir: str | None = None) -> dict:
    """Assumption values for THIS job, from <job_dir>/assumptions.json.

    Falls back to the committed defaults in data/assumption-defaults.json when
    the snapshot is absent (local runs, older jobs). Never raises.
    """
    # Try job snapshot first
    if job_dir:
        snapshot_path = os.path.join(job_dir, "assumptions.json")
        try:
            with open(snapshot_path, encoding="utf-8") as f:
                data = json.load(f)
            values = data.get("values", {})
            if isinstance(values, dict) and values:
                return {str(k): float(v) for k, v in values.items()}
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass  # fall through to committed defaults

    # Fall back to committed defaults
    defaults_path = os.path.join(_DATA_DIR, "assumption-defaults.json")
    try:
        with open(defaults_path, encoding="utf-8") as f:
            data = json.load(f)
        values = data.get("values", {})
        if isinstance(values, dict):
            return {str(k): float(v) for k, v in values.items()}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
        pass
    return {}


def load_report_settings(job_dir: str | None = None) -> dict:
    """Report toggle values for THIS job, from <job_dir>/report-settings.json.

    Falls back to the committed defaults in data/report-settings-defaults.json
    when the snapshot is absent (local runs, older jobs). Never raises.
    Returns dict[str, str].
    """
    # Try job snapshot first
    if job_dir:
        snapshot_path = os.path.join(job_dir, "report-settings.json")
        try:
            with open(snapshot_path, encoding="utf-8") as f:
                data = json.load(f)
            values = data.get("values", {})
            if isinstance(values, dict) and values:
                return {str(k): str(v) for k, v in values.items()}
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass  # fall through to committed defaults

    # Fall back to committed defaults
    defaults_path = os.path.join(_DATA_DIR, "report-settings-defaults.json")
    try:
        with open(defaults_path, encoding="utf-8") as f:
            data = json.load(f)
        values = data.get("values", {})
        if isinstance(values, dict):
            return {str(k): str(v) for k, v in values.items()}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
        pass
    return {}
