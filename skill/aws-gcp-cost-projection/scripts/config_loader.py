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


_LAST_REJECTIONS: dict[str, str] = {}


def get_assumption_rejections() -> dict[str, str]:
    """Return map of assumption group -> rejection reason from the most recent load_assumptions() call."""
    return dict(_LAST_REJECTIONS)


def load_assumptions(job_dir: str | None = None) -> dict:
    """Assumption values for THIS job, from <job_dir>/assumptions.json (or cwd/assumptions.json).

    Falls back to the committed defaults in data/assumption-defaults.json when
    the snapshot is absent (local runs, older jobs). Never raises.
    """
    # Fall back to committed defaults
    defaults_path = os.path.join(_DATA_DIR, "assumption-defaults.json")
    defaults = {}
    try:
        with open(defaults_path, encoding="utf-8") as f:
            data = json.load(f)
        dvalues = data.get("values", {})
        if isinstance(dvalues, dict):
            defaults = {str(k): float(v) for k, v in dvalues.items()}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
        pass

    res = dict(defaults)
    # Try job snapshot first (explicit job_dir or cwd if assumptions.json present)
    target_dir = job_dir
    if not target_dir:
        cwd = os.getcwd()
        if os.path.exists(os.path.join(cwd, "assumptions.json")):
            target_dir = cwd

    if target_dir:
        snapshot_path = os.path.join(target_dir, "assumptions.json")
        try:
            with open(snapshot_path, encoding="utf-8") as f:
                data = json.load(f)
            values = data.get("values", {})
            if isinstance(values, dict) and values:
                res.update({str(k): float(v) for k, v in values.items()})
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    # Ratio group read validation (Task 4):
    # A value edited directly in the database or snapshot must never reach a customer's projection
    # if its resulting ratio is not one GCP actually sells.
    if "aurora_acu_vcpu" in res and "aurora_acu_ram_gib" in res:
        vcpu = res["aurora_acu_vcpu"]
        ram = res["aurora_acu_ram_gib"]
        if vcpu > 0:
            ratio = ram / vcpu
            try:
                from candidate_pool import get_allowed_ram_per_vcpu_ratios
                allowed = get_allowed_ram_per_vcpu_ratios()
            except Exception:
                allowed = [4.0, 8.0, 16.0]

            is_valid = any(abs(ratio - r) < 1e-3 for r in allowed)
            if not is_valid:
                reason = (
                    f"Stored aurora_acu ratio 1:{ratio:.1f} ({vcpu} vCPU, {ram} GiB) "
                    f"is out of band (allowed GCP ratios: {allowed}); falling back to default"
                )
                _LAST_REJECTIONS["aurora_acu"] = reason
                try:
                    from diagnostics import diag, WARN
                    diag(WARN, "assumption_ratio_out_of_band", "config_loader",
                         reason,
                         job_dir=target_dir)
                except Exception:
                    pass
                if "aurora_acu_vcpu" in defaults:
                    res["aurora_acu_vcpu"] = defaults["aurora_acu_vcpu"]
                if "aurora_acu_ram_gib" in defaults:
                    res["aurora_acu_ram_gib"] = defaults["aurora_acu_ram_gib"]
            else:
                _LAST_REJECTIONS.pop("aurora_acu", None)

    return res


# Keys whose passthrough is driven by a numeric value sentinel rather than an
# explicit toggle: passthrough is active when the assumption value is <= 0
# (e.g. inferentia_chip_to_tpu defaults to 0.0 → map Inferentia to nothing →
# passthrough at AWS cost). Kept here as the single source of truth shared by
# load_assumption_passthroughs and is_passthrough_enabled so the deterministic
# passthrough_enforcer and the no_illegal_passthroughs gate agree.
_VALUE_SENTINEL_PASSTHROUGH_KEYS = (
    "inferentia_chip_to_tpu",
    "trainium_chip_to_tpu",
    "dynamodb_rcu_to_reads_sec",
    "dynamodb_wcu_to_writes_sec",
)


def load_assumption_passthroughs(job_dir: str | None = None) -> set[str]:
    """Return the set of assumption keys for which passthrough is enabled for THIS job.

    Defaults are loaded from data/assumption-defaults.json ('passthrough' object),
    then overlaid with <job_dir>/assumptions.json ('passthrough' object). In
    addition, value-sentinel keys (see _VALUE_SENTINEL_PASSTHROUGH_KEYS) are
    included whenever their assumption value is <= 0 — without this, a default
    like inferentia_chip_to_tpu=0.0 was silently NOT enabled, so the enforcer
    never stamped passthrough_reason='operator_assumption' and the row tripped
    the no_illegal_passthroughs gate, forcing an expensive LLM review-retry.
    Never raises. Returns set[str].
    """
    res = _load_explicit_passthrough_toggles(job_dir)

    # Value-sentinel keys: enable passthrough when the numeric assumption value
    # is <= 0 (e.g. inferentia_chip_to_tpu=0.0 → passthrough). Mirrors the rule
    # in is_passthrough_enabled; without it these never entered the set.
    try:
        values = load_assumptions(job_dir)
        for k in _VALUE_SENTINEL_PASSTHROUGH_KEYS:
            v = values.get(k)
            if v is not None and float(v) <= 0:
                res.add(k)
    except (ValueError, TypeError):
        pass

    return res


def _load_explicit_passthrough_toggles(job_dir: str | None = None) -> set[str]:
    """Return only the keys with an EXPLICIT passthrough toggle (no value sentinels).

    Reads the 'passthrough' object (and 'items[].passthrough_enabled') from the
    committed defaults and the per-job assumptions.json snapshot. Kept separate
    from the value-sentinel logic so is_passthrough_enabled can treat a
    caller-supplied `value` as authoritative.
    """
    res: set[str] = set()

    # Fall back to committed defaults
    defaults_path = os.path.join(_DATA_DIR, "assumption-defaults.json")
    try:
        with open(defaults_path, encoding="utf-8") as f:
            data = json.load(f)
        dpt = data.get("passthrough", {})
        if isinstance(dpt, dict):
            for k, v in dpt.items():
                if bool(v):
                    res.add(str(k))
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
        pass

    # Try job snapshot
    if job_dir:
        snapshot_path = os.path.join(job_dir, "assumptions.json")
        try:
            with open(snapshot_path, encoding="utf-8") as f:
                data = json.load(f)
            jpt = data.get("passthrough", {})
            if isinstance(jpt, dict):
                for k, v in jpt.items():
                    if bool(v):
                        res.add(str(k))
                    elif str(k) in res:
                        res.remove(str(k))
            # Also check items array if present
            items = data.get("items", [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        k = item.get("key")
                        if k and item.get("passthrough_enabled"):
                            res.add(str(k))
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
            pass

    return res


def is_passthrough_enabled(key: str, job_dir: str | None = None, value: float | None = None) -> bool:
    """Return whether passthrough is active for key, considering both explicit toggle and legacy value sentinels.

    Rule: effective_passthrough(key) = (explicit toggle) OR (value-sentinel key
    whose value <= 0). When the caller supplies `value` for a value-sentinel key
    it is authoritative for the sentinel decision (overrides the loaded default),
    but an explicit toggle can still force passthrough on.
    """
    if value is not None and key in _VALUE_SENTINEL_PASSTHROUGH_KEYS:
        if value <= 0:
            return True
        return key in _load_explicit_passthrough_toggles(job_dir)
    return key in load_assumption_passthroughs(job_dir)


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
