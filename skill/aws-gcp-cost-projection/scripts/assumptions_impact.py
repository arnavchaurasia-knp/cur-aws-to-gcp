#!/usr/bin/env python3
"""assumptions_impact.py — CLI and loader for the assumption-to-SKU impact registry."""

import argparse
import json
import os
import sys

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_IMPACT_FILE = os.path.join(_DATA_DIR, "assumption_impact.json")


def load_assumption_impact() -> dict:
    """Load the assumption-to-SKU impact registry. Returns dict of key -> impact details."""
    if not os.path.exists(_IMPACT_FILE):
        return {}
    try:
        with open(_IMPACT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("impacts", {})
    except Exception as e:
        print(f"Error loading assumption impact registry: {e}", file=sys.stderr)
        return {}


def format_impact(key: str, info: dict) -> str:
    lines = [
        f"Assumption: {key}",
        f"  Services:     {', '.join(info.get('services', []))}",
        f"  Charge Types: {', '.join(info.get('charge_types', []))}",
        f"  Mapper:       {info.get('mapper', 'N/A')}",
        f"  Decision:     {info.get('decision_it_changes', 'N/A')}",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Query the assumption-to-SKU impact registry.")
    parser.add_argument("--list", action="store_true", help="List all registered assumptions and their impact.")
    parser.add_argument("--key", type=str, help="Look up a specific assumption key.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    args = parser.parse_args()

    impacts = load_assumption_impact()

    if args.json:
        if args.key:
            res = {args.key: impacts.get(args.key, {})}
        else:
            res = impacts
        print(json.dumps(res, indent=2))
        return

    if args.key:
        if args.key not in impacts:
            print(f"Unknown assumption key: {args.key}", file=sys.stderr)
            sys.exit(1)
        print(format_impact(args.key, impacts[args.key]))
        return

    # Default or --list: print all
    print(f"Assumption-to-SKU Impact Registry ({len(impacts)} registered keys):\n")
    for key, info in sorted(impacts.items()):
        print(format_impact(key, info))
        print("-" * 60)


if __name__ == "__main__":
    main()

