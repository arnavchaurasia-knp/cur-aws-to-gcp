#!/usr/bin/env python3
"""
unit_equivalence.py — Canonical billing unit equivalence classes.

Used by resolve_sku.py (G-1b) to verify that a matched catalog SKU bills on the
same dimension that the mapping assumes (e.g. rejecting a per-second CPU SKU
when mapping a per-invocation count charge).
"""

from __future__ import annotations

import re

# Canonical equivalence groups: class -> set of lowercased tokens / spellings
_EQUIVALENCE_CLASSES: dict[str, set[str]] = {
    "time": {
        "h", "hr", "hrs", "hour", "hours", "sec", "second", "seconds", "min", "minute", "minutes",
        "giby.h", "gby.h", "gib.h", "gb.h", "gib-h", "gb-h", "gib-hr", "gb-hr", "gib/h", "gb/h",
    },
    "storage-month": {
        "giby.mo", "gb-mo", "gb-month", "gibibyte month", "gib-mo", "gigabyte-month",
        "gibibyte-month", "gibibyte mo", "gb mo", "byte-month", "bytes-month",
        "tib-mo", "tb-mo", "tib-month", "tb-month",
    },
    "data": {
        "gibibyte", "gib", "gb", "gigabyte", "byte", "bytes", "tibibyte", "tib", "tb", "terabyte",
        "mebibyte", "mib", "mb", "megabyte",
    },
    "count": {
        "count", "request", "requests", "req", "reqs", "operation", "operations",
        "op", "ops", "call", "calls", "invocations", "invocation", "action", "actions",
        "query", "queries", "event", "events", "units", "unit",
    },
    "seat": {
        "user", "users", "seat", "seats", "seat-month", "user-month", "user/month", "seat/month",
    },
}

# Reverse lookup mapping for O(1) matching
_TOKEN_TO_CLASS: dict[str, str] = {}
for cls_name, tokens in _EQUIVALENCE_CLASSES.items():
    for token in tokens:
        _TOKEN_TO_CLASS[token] = cls_name


def unit_class(unit: str | None) -> str | None:
    """Return the canonical equivalence class for a given billing unit, or None if unknown.

    Canonical classes: 'time', 'storage-month', 'data', 'count', 'seat'.
    """
    if not unit:
        return None

    clean = str(unit).strip().lower()

    # Direct canonical class name match (RV-2)
    if clean in _EQUIVALENCE_CLASSES:
        return clean

    # Exact token match
    if clean in _TOKEN_TO_CLASS:
        return _TOKEN_TO_CLASS[clean]

    # Normalize separators (e.g. "GiBy.mo" -> "giby.mo")
    norm = re.sub(r'[\s_]+', ' ', clean)
    if norm in _TOKEN_TO_CLASS:
        return _TOKEN_TO_CLASS[norm]

    # Substring heuristics for common cloud billing unit variations
    if any(k in norm for k in ["giby.mo", "gb-mo", "gib-mo", "byte-month", "bytes-month", "gibibyte month", "gigabyte month"]):
        return "storage-month"
    if norm.endswith((".h", "-h", "/h")) or any(k in norm for k in ["hour", "hrs", "hr", "/h", "sec", "second", "minute"]):
        return "time"
    if any(k in norm for k in ["gibibyte", "gigabyte", "gib", "byte"]):
        return "data"
    if any(k in norm for k in ["request", "operation", "invocation", "query", "call", "event"]):
        return "count"
    if any(k in norm for k in ["user", "seat"]):
        return "seat"

    return None
