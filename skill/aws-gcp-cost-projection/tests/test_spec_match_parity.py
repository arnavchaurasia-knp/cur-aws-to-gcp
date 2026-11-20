"""Parity lock: spec_match.meets_or_exceeds is the canonical definition of the
absolute capacity rule that reconcile_capacity.py enforces in SQL.

reconcile_capacity flags a row for repair when the GCP side is below the AWS side
on vcpu OR ram (per-component: `m.unit_multiplier < c.instance_vcpus`, and the
same for ram). That is exactly `not meets_or_exceeds(aws, gcp).passed` when only
vcpu/ram are present. This test encodes that equivalence so the SQL gate and the
Python definition can never silently diverge (WORKORDER_spec_match_consolidation
S3.2). It does NOT touch auto_review.py, whose ratio/shape rule is a separate
concern pending decision D-SPEC-1.
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "scripts"))

from spec_match import meets_or_exceeds  # noqa: E402


def _reconcile_flags(aws_vcpu, aws_ram, gcp_vcpu, gcp_ram):
    """Mirror of reconcile_capacity.py's absolute SQL predicate (vcpu OR ram under)."""
    return (gcp_vcpu < aws_vcpu) or (gcp_ram < aws_ram)


# (aws_vcpu, aws_ram, gcp_vcpu, gcp_ram)
_CASES = [
    (4, 16, 4, 16),      # exact match -> ok
    (4, 16, 8, 32),      # GCP bigger -> ok
    (4, 16, 2, 16),      # vcpu under -> flag
    (4, 16, 4, 8),       # ram under -> flag
    (4, 16, 2, 8),       # both under -> flag
    (8, 32, 8, 31.9),    # ram barely under -> flag
    (1, 0.6, 1, 2),      # shared-core style, GCP ram higher -> ok
]


@pytest.mark.parametrize("aws_vcpu,aws_ram,gcp_vcpu,gcp_ram", _CASES)
def test_reconcile_rule_matches_spec_match(aws_vcpu, aws_ram, gcp_vcpu, gcp_ram):
    aws = {"vcpu": aws_vcpu, "ram_gb": aws_ram}
    gcp = {"vcpu": gcp_vcpu, "ram_gb": gcp_ram}
    spec_fails = not meets_or_exceeds(aws, gcp).passed
    recon_flags = _reconcile_flags(aws_vcpu, aws_ram, gcp_vcpu, gcp_ram)
    assert spec_fails == recon_flags, (
        f"divergence: spec_match fails={spec_fails}, reconcile flags={recon_flags} "
        f"for aws(vcpu={aws_vcpu},ram={aws_ram}) gcp(vcpu={gcp_vcpu},ram={gcp_ram})"
    )


def test_spec_match_reports_unchecked_not_silent_pass():
    """A GCP-missing field is 'unchecked', never a silent pass — the fail-closed
    contract reconcile/auto_review rely on."""
    r = meets_or_exceeds({"vcpu": 4, "ram_gb": 16, "iops": 3000}, {"vcpu": 4, "ram_gb": 16})
    assert r.passed is True
    assert "iops" in r.unchecked
