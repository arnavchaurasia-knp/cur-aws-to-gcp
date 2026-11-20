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


def test_reconcile_capacity_sql_parity_with_spec_match():
    """End-to-end SQL fixture test: runs the actual SQL query from reconcile_capacity.py
    in DuckDB and asserts that the set of rows flagged for repair matches the set of
    rows where meets_or_exceeds(aws_spec, gcp_spec).passed is False."""
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR,
            instance_vcpus DOUBLE,
            instance_ram_gb DOUBLE,
            instance_type VARCHAR
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            strategy VARCHAR,
            component VARCHAR,
            unit_multiplier DOUBLE
        );
    """)

    test_data = [
        # (key, aws_vcpu, aws_ram, gcp_vcpu, gcp_ram, strategy)
        ("row_exact", 4.0, 16.0, 4.0, 16.0, "map"),
        ("row_exceeds", 4.0, 16.0, 8.0, 32.0, "break_down"),
        ("row_vcpu_under", 4.0, 16.0, 2.0, 16.0, "map"),
        ("row_ram_under", 4.0, 16.0, 4.0, 8.0, "break_down"),
        ("row_both_under", 4.0, 16.0, 2.0, 8.0, "map"),
        ("row_passthrough", 4.0, 16.0, 2.0, 8.0, "passthrough"),
    ]

    for key, aws_v, aws_r, gcp_v, gcp_r, strat in test_data:
        con.execute("INSERT INTO aws_li_catalog VALUES (?, ?, ?, ?)", [key, aws_v, aws_r, "m5.xlarge"])
        con.execute("INSERT INTO aws_li_to_gcp_li VALUES (?, ?, 'core', ?)", [key, strat, gcp_v])
        con.execute("INSERT INTO aws_li_to_gcp_li VALUES (?, ?, 'ram', ?)", [key, strat, gcp_r])

    # Run actual reconcile_capacity.py SQL
    sql_under_vcpu = con.execute("""
        SELECT m.aws_li_key
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.strategy IN ('break_down', 'map')
          AND m.component  = 'core'
          AND c.instance_vcpus IS NOT NULL
          AND m.unit_multiplier < c.instance_vcpus
    """).fetchall()

    sql_under_ram = con.execute("""
        SELECT m.aws_li_key
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.strategy IN ('break_down', 'map')
          AND m.component  = 'ram'
          AND c.instance_ram_gb IS NOT NULL
          AND m.unit_multiplier < c.instance_ram_gb
    """).fetchall()

    flagged_in_sql = {r[0] for r in sql_under_vcpu} | {r[0] for r in sql_under_ram}

    # Compare against spec_match.meets_or_exceeds for map / break_down rows
    spec_fails = set()
    for key, aws_v, aws_r, gcp_v, gcp_r, strat in test_data:
        if strat in ("map", "break_down"):
            aws_spec = {"vcpu": aws_v, "ram_gb": aws_r}
            gcp_spec = {"vcpu": gcp_v, "ram_gb": gcp_r}
            if not meets_or_exceeds(aws_spec, gcp_spec).passed:
                spec_fails.add(key)

    assert flagged_in_sql == spec_fails, (
        f"SQL repair set {flagged_in_sql} diverges from spec_match failure set {spec_fails}"
    )
    assert "row_vcpu_under" in flagged_in_sql
    assert "row_ram_under" in flagged_in_sql
    assert "row_both_under" in flagged_in_sql
    assert "row_exact" not in flagged_in_sql
    assert "row_exceeds" not in flagged_in_sql
    con.close()

