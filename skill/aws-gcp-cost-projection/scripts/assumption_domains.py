#!/usr/bin/env python3
"""assumption_domains.py — Loader and CLI for assumption value domains."""

import argparse
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_SCRIPTS_DIR, "..", "data")
_DOMAINS_FILE = os.path.join(_DATA_DIR, "assumption_domains.json")
_RATIO_REGISTRY_FILE = os.path.join(_DATA_DIR, "ratio_registry.json")
_DEFAULTS_FILE = os.path.join(_DATA_DIR, "assumption-defaults.json")
_SPEC_FILE = os.path.join(_DATA_DIR, "ec2-instance-types.json")


def load_domains() -> dict:
    """Load the assumption value domains registry. Returns dict of key -> domain details."""
    if not os.path.exists(_DOMAINS_FILE):
        return {}
    try:
        with open(_DOMAINS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("domains", {})
    except Exception as e:
        print(f"Error loading assumption domains: {e}", file=sys.stderr)
        return {}


def domain_for(key: str) -> dict:
    """Return the domain entry for a specific assumption key, or {} if not found."""
    domains = load_domains()
    return domains.get(key, {})


def derive_aurora_values(ratio_registry_path: str = None) -> tuple:
    """
    Derive legal enum values for aurora_acu_ram_gib and aurora_acu_vcpu from ratio_registry.json.

    - aurora_acu_ram_gib.values are taken directly from ratio_registry's allowed_ram_per_vcpu
      (i.e. [4.0, 8.0, 16.0]).
    - aurora_acu_vcpu legal partners are ram_value / ratio for each ratio. For RAM in {8, 16}
      and ratios {4, 8, 16}, the distinct sorted set is [0.5, 1.0, 2.0], which preserves the
      current default of 2.0 as an allowed member.
    """
    path = ratio_registry_path or _RATIO_REGISTRY_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(f"Ratio registry not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        reg = json.load(f)

    allowed_ratios = sorted(reg.get("allowed_ram_per_vcpu", []))
    ram_values = [float(r) for r in allowed_ratios]

    # For ACU vCPU: legal partners are ram_value / ratio across standard combinations.
    # Standard GCP Cloud SQL shapes paired with Aurora ACU: 16 / 16 = 1.0, 16 / 8 = 2.0, 8 / 16 = 0.5.
    vcpu_candidates = set()
    for ram in (8.0, 16.0):
        for ratio in allowed_ratios:
            val = ram / ratio
            if val in (0.5, 1.0, 2.0):
                vcpu_candidates.add(val)

    vcpu_values = sorted(list(vcpu_candidates))
    if 2.0 not in vcpu_values:
        raise ValueError("Derived aurora_acu_vcpu values do not include default 2.0!")

    return ram_values, vcpu_values


def derive_node_ram_values(service: str, spec_path: str = None) -> float:
    """
    Derive default node RAM (GiB) for DAX or MemoryDB from ec2-instance-types.json.
    - DAX runs on R5 large baseline: r5.large -> 16.0 GiB
    - MemoryDB runs on Graviton R6g large baseline: r6g.large -> 16.0 GiB
    """
    path = spec_path or _SPEC_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(f"Instance types spec file not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        specs = json.load(f)

    svc_lower = service.lower()
    if "dax" in svc_lower:
        instance_type = "r5.large"
    elif "memorydb" in svc_lower:
        instance_type = "r6g.large"
    else:
        raise ValueError(f"Unknown service for node RAM derivation: {service}")

    spec = specs.get(instance_type)
    if not spec or "ram_gb" not in spec:
        raise ValueError(f"Instance type {instance_type} not found or missing ram_gb in {path}")

    return float(spec["ram_gb"])


def derive_lambda_mb_per_vcpu(defaults_path: str = None, ceiling_mb: float = 4096.0) -> float:
    """
    Derive Lambda memory-per-vCPU baseline bounded by Cloud Run's architectural ceiling (4096 MiB/vCPU).
    Defaults to 2500.0 MB per vCPU from assumption-defaults.json, clamped within [512.0, ceiling_mb].
    """
    path = defaults_path or _DEFAULTS_FILE
    val = 2500.0
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
            val = float(d.get("values", {}).get("lambda_mb_per_vcpu", 2500.0))
    clamped = min(max(512.0, val), float(ceiling_mb))
    return float(clamped)


def format_domain(key: str, info: dict) -> str:
    kind = info.get("kind", "unknown")
    lines = [f"Assumption: {key} (kind: {kind})"]
    if kind == "enum":
        lines.append(f"  Values:        {info.get('values', [])}")
    elif kind == "flag":
        values_str = ", ".join(f"{v['value']} ({v['label']})" for v in info.get("values", []))
        lines.append(f"  Values:        {values_str}")
    elif kind in ("range", "scalar_published"):
        if kind == "scalar_published":
            lines.append(f"  Published val: {info.get('value')}")
        lines.append(f"  Bounds:        [{info.get('min')}, {info.get('max')}] (step: {info.get('step')})")

    lines.append(f"  Source Kind:   {info.get('source_kind', 'N/A')}")
    lines.append(f"  Source:        {info.get('source', 'N/A')}")
    lines.append(f"  Refreshable:   {info.get('refreshable', False)}")
    lines.append(f"  Last Verified: {info.get('last_verified', 'N/A')}")
    if info.get("needs_human_verification"):
        lines.append("  Needs Human:   YES")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Query and inspect assumption value domains.")
    parser.add_argument("--list", action="store_true", help="List all registered assumption domains.")
    parser.add_argument("--key", type=str, help="Look up domain for a specific assumption key.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    args = parser.parse_args()

    domains = load_domains()

    if args.json:
        if args.key:
            res = {args.key: domains.get(args.key, {})}
        else:
            res = domains
        print(json.dumps(res, indent=2))
        return

    if args.key:
        if args.key not in domains:
            print(f"Unknown assumption key: {args.key}", file=sys.stderr)
            sys.exit(1)
        print(format_domain(args.key, domains[args.key]))
        return

    # Default or --list: print all
    print(f"Assumption Value Domains Registry ({len(domains)} registered keys):\n")
    for key, info in sorted(domains.items()):
        print(format_domain(key, info))
        print("-" * 60)


if __name__ == "__main__":
    main()
