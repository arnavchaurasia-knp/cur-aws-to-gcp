#!/usr/bin/env python3
"""
equivalences.py — Equivalence crosswalk registry, loader, and validation API.

Provides:
- lookup(attribute, aws_value, gcp_value, scope) -> Claim | None
- is_provisional(claim) -> bool (decided_by != "human")
- confidence_cap(claim) -> float | None
- all_claims() -> list[Claim]
- require_claim(...) -> Claim (raises UnknownEquivalenceError if None)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Iterable

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.environ.get("SKILL_DIR") or os.path.dirname(_HERE)
_DATA_DIR = os.path.join(_SKILL_DIR, "data")
DEFAULT_EQUIVALENCES_PATH = os.path.join(_DATA_DIR, "equivalences.json")

VALID_DEGREES: frozenset[str] = frozenset([
    "equal",
    "equivalent",
    "wider",
    "narrower",
    "inexact",
    "unmatched",
    "disjoint",
])


class EquivalenceValidationError(ValueError):
    """Raised when an equivalence crosswalk file or claim is malformed."""
    pass


class UnknownEquivalenceError(ValueError):
    """Raised when a required equivalence claim is missing/unknown."""
    pass


@dataclass(frozen=True)
class Claim:
    attribute: str
    aws: str
    gcp: str
    degree: str
    scope: list[str]
    comment: str | None = None
    confidence_cap: float | None = None
    decided_by: str = "human"
    decided_on: str = ""

    def __post_init__(self):
        # Validate fields on instantiation
        if not self.attribute or not str(self.attribute).strip():
            raise EquivalenceValidationError("Claim attribute must be a non-empty string")
        if not self.aws or not str(self.aws).strip():
            raise EquivalenceValidationError("Claim aws value must be a non-empty string")
        if not self.gcp or not str(self.gcp).strip():
            raise EquivalenceValidationError("Claim gcp value must be a non-empty string")
        if self.degree not in VALID_DEGREES:
            raise EquivalenceValidationError(
                f"Unknown degree '{self.degree}'. Valid degrees: {sorted(VALID_DEGREES)}"
            )
        if not self.scope or not isinstance(self.scope, list) or len(self.scope) == 0:
            raise EquivalenceValidationError(
                f"Claim scope is required and non-empty for {self.attribute} ({self.aws} -> {self.gcp})"
            )
        for s in self.scope:
            if not isinstance(s, str) or not s.strip():
                raise EquivalenceValidationError(
                    f"Scope tokens must be non-empty strings, got: {s!r}"
                )
        if self.degree == "inexact":
            if not self.comment or not str(self.comment).strip():
                raise EquivalenceValidationError(
                    f"Claim degree 'inexact' requires non-empty comment for {self.attribute} ({self.aws} -> {self.gcp})"
                )
            if self.confidence_cap is None:
                raise EquivalenceValidationError(
                    f"Claim degree 'inexact' requires confidence_cap for {self.attribute} ({self.aws} -> {self.gcp})"
                )
            if not isinstance(self.confidence_cap, (int, float)):
                raise EquivalenceValidationError(
                    f"confidence_cap must be numeric, got: {type(self.confidence_cap)}"
                )
            if not (0.0 <= float(self.confidence_cap) <= 1.0):
                raise EquivalenceValidationError(
                    f"confidence_cap must be between 0.0 and 1.0, got {self.confidence_cap}"
                )


_CACHE: list[Claim] | None = None
_CACHE_PATH: str | None = None


def load_equivalences(filepath: str | None = None, reload: bool = False) -> list[Claim]:
    """
    Load and validate data/equivalences.json (or specified filepath).

    Enforces:
    1. Valid degrees from _meta.degrees (closed set).
    2. Non-empty scope list for every claim.
    3. Inexact degree requires comment and confidence_cap.
    4. Conflicting degrees for same (attribute, aws, gcp, scope_token) are rejected.
    """
    global _CACHE, _CACHE_PATH
    target_path = filepath or DEFAULT_EQUIVALENCES_PATH

    if not reload and _CACHE is not None and _CACHE_PATH == target_path:
        return _CACHE

    if not os.path.exists(target_path):
        raise FileNotFoundError(f"Equivalences crosswalk file not found: {target_path}")

    with open(target_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("_meta", {})
    if meta.get("format") != "crosswalk":
        raise EquivalenceValidationError(
            f"Expected _meta.format == 'crosswalk', got: {meta.get('format')}"
        )

    claims: list[Claim] = []
    # Track (attr, aws_clean, gcp_clean, scope_clean) -> degree to detect conflicts
    seen_scopes: dict[tuple[str, str, str, str], str] = {}

    for key, val in data.items():
        if key == "_meta":
            continue
        if not isinstance(val, list):
            raise EquivalenceValidationError(f"Attribute '{key}' must map to a list of claims")

        for item in val:
            if not isinstance(item, dict):
                raise EquivalenceValidationError(f"Claim in '{key}' must be a dict, got: {type(item)}")

            aws_val = str(item.get("aws", "")).strip()
            gcp_val = str(item.get("gcp", "")).strip()
            degree = str(item.get("degree", "")).strip()
            raw_scope = item.get("scope")
            comment = item.get("comment")
            cap = item.get("confidence_cap")
            decided_by = str(item.get("decided_by", "human")).strip()
            decided_on = str(item.get("decided_on", "")).strip()

            claim = Claim(
                attribute=key,
                aws=aws_val,
                gcp=gcp_val,
                degree=degree,
                scope=raw_scope,
                comment=comment,
                confidence_cap=float(cap) if cap is not None else None,
                decided_by=decided_by,
                decided_on=decided_on,
            )

            # Check conflicting degrees across overlapping scopes
            attr_k = key.strip().lower()
            aws_k = aws_val.lower()
            gcp_k = gcp_val.lower()
            for s in claim.scope:
                sk = (attr_k, aws_k, gcp_k, s.strip().lower())
                existing_deg = seen_scopes.get(sk)
                if existing_deg is not None and existing_deg != claim.degree:
                    raise EquivalenceValidationError(
                        f"Conflicting claims for ({key}, {aws_val}, {gcp_val}, {s}): "
                        f"degree '{existing_deg}' vs '{claim.degree}'"
                    )
                seen_scopes[sk] = claim.degree

            claims.append(claim)

    if filepath is None or filepath == DEFAULT_EQUIVALENCES_PATH:
        _CACHE = claims
        _CACHE_PATH = target_path

    return claims


def all_claims() -> list[Claim]:
    """Return all parsed and validated Claim objects from the registry."""
    return load_equivalences()


def lookup(
    attribute: str,
    aws_value: str,
    gcp_value: str,
    scope: str | Iterable[str],
) -> Claim | None:
    """
    Look up an equivalence claim matching (attribute, aws, gcp, scope).

    Returns None when no claim covers the tuple. Callers must treat None as
    unknown / failure.
    """
    claims = load_equivalences()
    attr_k = (attribute or "").strip().lower()
    aws_k = (aws_value or "").strip().lower()
    gcp_k = (gcp_value or "").strip().lower()

    if isinstance(scope, str):
        target_scopes = {scope.strip().lower()}
    elif isinstance(scope, Iterable):
        target_scopes = {str(s).strip().lower() for s in scope}
    else:
        return None

    for c in claims:
        if c.attribute.strip().lower() != attr_k:
            continue
        if c.aws.strip().lower() != aws_k:
            continue
        if c.gcp.strip().lower() != gcp_k:
            continue

        c_scopes = {s.strip().lower() for s in c.scope}
        if target_scopes & c_scopes:
            return c

    return None


def require_claim(
    attribute: str,
    aws_value: str,
    gcp_value: str,
    scope: str | Iterable[str],
) -> Claim:
    """
    Helper for callers when a claim is required.
    Returns Claim if found, raises UnknownEquivalenceError if unknown.
    """
    c = lookup(attribute, aws_value, gcp_value, scope)
    if c is None:
        raise UnknownEquivalenceError(
            f"No equivalence claim for {attribute}: '{aws_value}' -> '{gcp_value}' in scope {scope}"
        )
    return c


def is_provisional(claim: Claim | dict | None) -> bool:
    """Return True if the claim was not decided by a human."""
    if claim is None:
        return False
    if isinstance(claim, Claim):
        return claim.decided_by != "human"
    if isinstance(claim, dict):
        return claim.get("decided_by") != "human"
    return False


def confidence_cap(claim: Claim | dict | None) -> float | None:
    """Return the confidence cap associated with the claim, if any."""
    if claim is None:
        return None
    if isinstance(claim, Claim):
        return claim.confidence_cap
    if isinstance(claim, dict):
        return claim.get("confidence_cap")
    return None
