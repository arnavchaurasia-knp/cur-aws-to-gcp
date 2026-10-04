"""Tests for spec_match.py — the shared "meets or exceeds" spec-equivalence check."""
from spec_match import meets_or_exceeds


def test_gcp_meeting_spec_passes_cleanly():
    r = meets_or_exceeds(
        {"vcpu": 2, "ram_gb": 8, "arch": "x86"},
        {"vcpu": 2, "ram_gb": 8, "arch": "x86"},
    )
    assert r.passed
    assert not r.failures
    assert not r.unchecked


def test_gcp_exceeding_spec_passes():
    r = meets_or_exceeds({"vcpu": 2, "ram_gb": 8}, {"vcpu": 4, "ram_gb": 16})
    assert r.passed


def test_gcp_under_spec_fails_with_reason():
    r = meets_or_exceeds({"vcpu": 4, "ram_gb": 16}, {"vcpu": 2, "ram_gb": 16})
    assert not r.passed
    assert any("vcpu" in f for f in r.failures)


def test_architecture_mismatch_fails_even_if_numerically_sufficient():
    # An ARM candidate must never silently substitute for an x86 AWS source
    # (or vice versa) even if vCPU/RAM look fine — confirmed real distinction
    # this codebase already cares about (Graviton/ARM handling in family_mapper).
    r = meets_or_exceeds(
        {"vcpu": 2, "ram_gb": 8, "arch": "x86"},
        {"vcpu": 2, "ram_gb": 8, "arch": "arm"},
    )
    assert not r.passed
    assert any("arch" in f for f in r.failures)


def test_missing_gcp_field_is_unchecked_not_a_silent_pass():
    # IOPS present on the AWS side but the GCP candidate spec doesn't carry
    # it — this must NOT be treated as equivalent to "IOPS matched"; the
    # fail-closed design requires it to show up as unchecked so a verifier
    # can't mistake "didn't check" for "confirmed fine".
    r = meets_or_exceeds({"vcpu": 2, "ram_gb": 8, "iops": 3000}, {"vcpu": 2, "ram_gb": 8})
    assert r.passed  # nothing present on both sides failed
    assert r.unchecked == ["iops"]
    assert "could not check" in r.reason


def test_burstable_mismatch_fails():
    r = meets_or_exceeds({"vcpu": 2, "burstable": True}, {"vcpu": 2, "burstable": False})
    assert not r.passed
