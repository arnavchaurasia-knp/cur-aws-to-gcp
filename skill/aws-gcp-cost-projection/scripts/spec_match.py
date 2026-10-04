#!/usr/bin/env python3
"""
spec_match.py — one shared definition of "does this GCP candidate meet or
exceed the AWS source's spec," used by the CUR Line Analysis Tool, and meant
to replace the three independent reimplementations of this comparison found
in auto_review.py, reconcile_capacity.py, and (implicitly) family_mapper.py.

This module does NOT know where GCP capability numbers come from — callers
pass both sides in as plain dicts. Today that means family_mapper.py's
hardcoded per-family vCPU/RAM tables; the intent is for every spec comparison
in the codebase to eventually call meets_or_exceeds() instead of writing its
own vCPU/RAM delta check, so there is exactly one definition of "equivalent."

Recognized keys in a spec dict (all optional — only keys present on BOTH
sides are compared; a key missing from the GCP side is NOT treated as a
failure, since not every service has every field, but it IS reported in
`unchecked` so a verifier can tell "passed" apart from "couldn't check"):
    vcpu            float, vCPU count
    ram_gb          float, RAM in GB
    iops            float, provisioned/baseline IOPS
    throughput_mbps float, provisioned/baseline throughput
    arch            str, "x86" | "arm"
    burstable       bool
    storage_tier    str, e.g. "ssd" | "hdd" | "balanced" | "extreme"
    ha_topology     str, "zonal" | "regional"
"""
from __future__ import annotations

from dataclasses import dataclass, field

_NUMERIC_KEYS = ("vcpu", "ram_gb", "iops", "throughput_mbps")
_EXACT_MATCH_KEYS = ("arch", "burstable", "storage_tier", "ha_topology")


@dataclass
class SpecMatchResult:
    passed: bool
    failures: list[str] = field(default_factory=list)   # keys that failed, with reason
    unchecked: list[str] = field(default_factory=list)  # keys present on AWS side only

    @property
    def reason(self) -> str:
        if self.passed and not self.unchecked:
            return "meets or exceeds AWS spec on every checked field"
        if self.passed:
            return (
                "meets or exceeds AWS spec on every checked field; "
                f"could not check: {', '.join(self.unchecked)}"
            )
        return "; ".join(self.failures)


def meets_or_exceeds(aws_spec: dict, gcp_spec: dict) -> SpecMatchResult:
    """True only if the GCP candidate is at least as capable as the AWS
    source on every field BOTH sides provide a value for. A missing GCP-side
    field is recorded in `unchecked`, never silently treated as a pass —
    callers (per the CUR Line Analysis Tool's fail-closed design) must not
    mark a row verified_ok when unchecked is non-empty without a secondary
    justification (e.g. a policy_facts.json entry covering that field)."""
    failures: list[str] = []
    unchecked: list[str] = []

    for key in _NUMERIC_KEYS:
        if key not in aws_spec:
            continue
        if key not in gcp_spec:
            unchecked.append(key)
            continue
        aws_v, gcp_v = aws_spec[key], gcp_spec[key]
        if gcp_v < aws_v:
            failures.append(
                f"{key}: GCP {gcp_v} < AWS {aws_v} (GCP must meet or exceed, never substitute a lower spec)"
            )

    for key in _EXACT_MATCH_KEYS:
        if key not in aws_spec:
            continue
        if key not in gcp_spec:
            unchecked.append(key)
            continue
        aws_v, gcp_v = aws_spec[key], gcp_spec[key]
        if aws_v != gcp_v:
            failures.append(f"{key}: AWS {aws_v!r} != GCP {gcp_v!r} (exact match required, not interchangeable)")

    return SpecMatchResult(passed=not failures, failures=failures, unchecked=unchecked)
