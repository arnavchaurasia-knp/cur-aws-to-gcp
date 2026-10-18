#!/usr/bin/env python3
"""Single source of truth for passthrough reason codes.

Loads data/passthrough_reasons.json and exposes the enum plus the
intentional/defect split. Import this instead of hardcoding reason strings.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_loader import load_data_config as _cfg

_CFG = _cfg("passthrough_reasons") or {}
REASONS = _CFG.get("reasons", {})
DEFECT_BUDGET_PCT = float(_CFG.get("defect_passthrough_budget_pct", 5.0))

VALID_REASONS = frozenset(REASONS.keys())
INTENTIONAL_REASONS = frozenset(
    k for k, v in REASONS.items() if v.get("kind") == "intentional"
)
DEFECT_REASONS = frozenset(
    k for k, v in REASONS.items() if v.get("kind") == "defect"
)
UNCLASSIFIED = "unclassified"


def is_valid(reason) -> bool:
    return reason in VALID_REASONS


def kind(reason) -> str:
    """Return 'intentional', 'defect', or 'unknown'."""
    return REASONS.get(reason, {}).get("kind", "unknown")


def sql_in_list(reasons) -> str:
    """Return a SQL literal list, e.g. "'a', 'b'". Safe: keys are from our own
    JSON file and are validated identifiers, never user input."""
    safe = [r for r in sorted(reasons) if r.replace("_", "").isalnum()]
    return ", ".join("'" + r + "'" for r in safe) or "''"
