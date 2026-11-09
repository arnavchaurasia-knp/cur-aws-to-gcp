#!/usr/bin/env python3
"""refresh_sources.py — Reader and validator for data/refresh_sources.json."""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_HERE)
_DATA_DIR = os.path.join(_SKILL_DIR, "data")
_SOURCES_PATH = os.path.join(_DATA_DIR, "refresh_sources.json")


def load_refresh_sources(path: str = None) -> dict:
    """Load and parse refresh_sources.json. Returns dict of source_name -> config."""
    p = path or _SOURCES_PATH
    if not os.path.exists(p):
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def load_refresh_source(source_name: str, path: str = None) -> dict:
    """Load configuration for a single source, or {} if not found."""
    sources = load_refresh_sources(path)
    return sources.get(source_name, {})


def resolve_expected_count(source_cfg: dict, skill_dir: str = None) -> int:
    """Resolve expected item count from source's expected_set declaration."""
    base = skill_dir or _SKILL_DIR
    expected_set = source_cfg.get("expected_set", "")
    if not expected_set:
        return 0

    if expected_set == "data/services.json":
        svc_path = os.path.join(base, "data", "services.json")
        if os.path.exists(svc_path):
            with open(svc_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return len(data)
    elif expected_set == "self:regions":
        # Compute availability probes all known GCP regions (~43)
        # Default or fallback when no live probe has completed yet
        return 43
    elif expected_set == "data/assumption_domains.json":
        dom_path = os.path.join(base, "data", "assumption_domains.json")
        if os.path.exists(dom_path):
            with open(dom_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return len(data.get("domains", {}))

    return 0


def main():
    sources = load_refresh_sources()
    print(f"Loaded {len(sources)} refresh sources from {_SOURCES_PATH}:")
    for name, cfg in sorted(sources.items()):
        exp = resolve_expected_count(cfg)
        thresh = cfg.get("completeness_threshold", 100.0)
        refresher = cfg.get("refresher", "N/A")
        print(f"  - {name}: expected ~{exp}, threshold {thresh}%, refresher: {refresher}")


if __name__ == "__main__":
    main()

