#!/usr/bin/env python3
"""tests/test_spec_partition.py — structural invariants for data/service_specs.json.

A charge_type carrying "children" is a GROUP: its own `match` answers the coarse
question and its children answer the fine one. The whole value of nesting over a
flat list is that two invariants become checkable:

  1. SIBLINGS PARTITION THEIR PARENT — no row may match two non-default
     siblings. Under a flat list this is unenforceable, and it was silently
     violated: measured on 12 real bills, 340 rows / ~$54k matched two or three
     sibling charge types at once and the winner was decided purely by list
     order. See docs/ANALYSIS_spec_dispatch_validation.md.
  2. CHILDREN ARE EXHAUSTIVE — the last child of a group is a declared default
     (no positive predicates), so a row entering a group always leaves it with a
     named charge_type instead of falling through unnamed.

These are static/structural checks plus a synthetic-row ambiguity sweep.
Validation against real bills is scripts/spec_parity.py, which is not a test
because it needs job DBs that CI does not have.
"""
from __future__ import annotations

import itertools
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
sys.path.insert(0, _SCRIPTS)

import spec_dispatch as sd  # noqa: E402


def _groups():
    """Yield (service, group_charge_type) for every nested charge_type."""
    for svc, spec in sorted(sd.SPECS.items()):
        for ct in spec.get("charge_types", []):
            if ct.get("children"):
                yield svc, ct


def _all_charge_types():
    """Yield (service, dotted_id, charge_type) for every node, at any depth."""
    def walk(svc, cts, prefix=""):
        for ct in cts:
            name = prefix + ct["id"]
            yield svc, name, ct
            for sub in walk(svc, ct.get("children", []), name + "."):
                yield sub

    for svc, spec in sorted(sd.SPECS.items()):
        for item in walk(svc, spec.get("charge_types", [])):
            yield item


# --------------------------------------------------------------------------
# Structural invariants
# --------------------------------------------------------------------------
def test_every_group_ends_in_a_default_child():
    bad = []
    for svc, ct in _groups():
        children = ct["children"]
        if not children:
            bad.append(f"{svc}.{ct['id']}: group with no children")
            continue
        last = children[-1]
        if not sd.is_default_charge_type(last):
            bad.append(
                f"{svc}.{ct['id']}: last child {last['id']!r} is not a default "
                f"(it has positive predicates), so a row entering this group can "
                f"fall through unnamed. Add a catch-all child last."
            )
        for other in children[:-1]:
            if sd.is_default_charge_type(other):
                bad.append(
                    f"{svc}.{ct['id']}: child {other['id']!r} is a default but is "
                    f"not last — every child after it is unreachable."
                )
    assert not bad, "Group/default violations:\n  " + "\n  ".join(bad)


def test_top_level_defaults_are_last():
    """Same invariant one level up: a service's own catch-all must be the last
    entry, or every charge_type after it is unreachable."""
    bad = []
    for svc, spec in sorted(sd.SPECS.items()):
        cts = spec.get("charge_types", [])
        for i, ct in enumerate(cts[:-1]):
            if sd.is_default_charge_type(ct) and not ct.get("children"):
                bad.append(
                    f"{svc}: default charge_type {ct['id']!r} is at index {i} of "
                    f"{len(cts)} — entries after it can never match."
                )
    assert not bad, "Unreachable charge_types:\n  " + "\n  ".join(bad)


def test_every_charge_type_declares_a_mechanic():
    bad = [f"{svc}.{name}" for svc, name, ct in _all_charge_types()
           if not ct.get("children") and not ct.get("mechanic")]
    assert not bad, "Leaf charge_types with no mechanic:\n  " + "\n  ".join(bad)


def test_charge_type_ids_are_unique_within_a_service():
    seen = {}
    dupes = []
    for svc, name, _ in _all_charge_types():
        key = (svc, name)
        if key in seen:
            dupes.append(f"{svc}: duplicate charge_type id {name!r}")
        seen[key] = True
    assert not dupes, "\n  ".join(dupes)


def test_no_predicate_restates_service_identity():
    """A spec file is already scoped to its service, so re-testing the product
    name inside it is both redundant and the free-text coupling Layer 1 exists
    to remove. Flags product_re / product_re_not on any charge_type."""
    offenders = []
    for svc, name, ct in _all_charge_types():
        for block_name in ("match", "fallback_match", "exclude_when"):
            block = ct.get(block_name) or {}
            for key in ("product_re", "product_re_not"):
                if key in block:
                    offenders.append(
                        f"{svc}.{name}.{block_name}.{key} = {block[key]!r} — the "
                        f"spec is already scoped to {svc!r}; key on usage_type / "
                        f"operation / unit instead."
                    )
    assert not offenders, "Service identity restated inside a spec:\n  " + "\n  ".join(offenders)


# --------------------------------------------------------------------------
# Ambiguity sweep over synthetic rows
# --------------------------------------------------------------------------
def _row(**kw):
    r = {
        "aws_li_key": "k", "product": "", "usage_type": "", "operation": "",
        "unit": "Hrs", "line_item_type": "Usage", "pricing_model": "OnDemand",
        "aws_amortized_cost": 100.0, "instance_type": None, "instance_vcpus": None,
        "instance_ram_gb": None, "instance_count": None, "billing_format": None,
        "product_code": None, "canonical": None, "_bill_has_discounted_usage": True,
    }
    r.update(kw)
    return r


# Rows drawn from the shapes that actually collided on the 12-bill corpus.
AMBIGUITY_CASES = [
    ("ec2 graviton", _row(canonical="ec2", usage_type="APS3-BoxUsage:m7g.large",
                          operation="RunInstances", unit="Hrs")),
    ("ec2 burstable", _row(canonical="ec2", usage_type="APS3-BoxUsage:t3.medium",
                           operation="RunInstances", unit="Hrs")),
    ("ec2 t4g (burstable AND graviton)",
     _row(canonical="ec2", usage_type="APS3-BoxUsage:t4g.large",
          operation="RunInstances", unit="Hrs")),
    ("ec2 windows", _row(canonical="ec2", usage_type="APS3-BoxUsage:m5.large",
                         product="Amazon Elastic Compute Cloud running Windows",
                         operation="RunInstances:0002", unit="Hrs")),
    ("ec2 windows t3 (windows AND burstable)",
     _row(canonical="ec2", usage_type="APS3-BoxUsage:t3.large",
          product="Amazon Elastic Compute Cloud running Windows",
          operation="RunInstances:0002", unit="Hrs")),
    ("ec2 plain x86", _row(canonical="ec2", usage_type="APS3-BoxUsage:m5.large",
                           operation="RunInstances", unit="Hrs")),
    ("rds backup gb-mo", _row(canonical="rds", usage_type="APS3-BackupUsage",
                              operation="", unit="GB-Mo")),
    ("rds storage gb-mo", _row(canonical="rds", usage_type="APS3-StorageUsage",
                               operation="", unit="GB-Mo")),
    ("rds multi-az hours", _row(canonical="rds", usage_type="APS5-Multi-AZUsage:db.m7g.large",
                                operation="CreateDBInstance:0002", unit="Hrs")),
    ("waf webacl month", _row(canonical="waf", usage_type="",
                              operation="Web ACL per Month", unit="Months")),
    ("waf webacl hour", _row(canonical="waf", usage_type="APS3-WebACL-Hour",
                             operation="", unit="Hrs")),
]


def _non_default_matches(canon, row):
    """Dotted ids of every matching charge_type, ignoring declared defaults."""
    out = []

    def walk(cts, prefix=""):
        for ct in cts:
            if not sd._charge_type_matches(ct, row):
                continue
            name = prefix + ct["id"]
            if ct.get("children"):
                walk(ct["children"], name + ".")
            elif not sd.is_default_charge_type(ct):
                out.append(name)

    walk((sd.SPECS.get(canon) or {}).get("charge_types", []))
    return out


@pytest.mark.parametrize("label,row", AMBIGUITY_CASES, ids=[c[0] for c in AMBIGUITY_CASES])
def test_row_matches_at_most_one_non_default_charge_type(label, row):
    canon = row["canonical"]
    assert canon in sd.SPECS, f"{canon} has no spec"
    matches = _non_default_matches(canon, row)
    assert len(matches) <= 1, (
        f"{label}: matched {len(matches)} non-default charge types {matches}. "
        f"Siblings must partition their parent — tighten one of them with an "
        f"exclude_* predicate so the winner is declared, not decided by order."
    )


@pytest.mark.parametrize("label,row", AMBIGUITY_CASES, ids=[c[0] for c in AMBIGUITY_CASES])
def test_every_case_dispatches(label, row):
    d = sd.dispatch(row)
    assert d is not None, f"{label}: no charge_type matched at all (spec gap)"
    assert d.get("mechanic"), f"{label}: dispatched to {d['charge_type']} with no mechanic"


# --------------------------------------------------------------------------
# The specific routing decisions these fixes encode, pinned so a later edit
# cannot silently reverse them.
# --------------------------------------------------------------------------
def test_t4g_routes_to_burstable_not_graviton():
    """t4g is both burstable and Graviton. Burstable wins: AWS CPU credits never
    lower the AWS price, so E2 is the correct target and sending it to the
    full-performance ARM mapper would overstate the GCP cost."""
    d = sd.dispatch(_row(canonical="ec2", usage_type="APS3-BoxUsage:t4g.large",
                         operation="RunInstances", unit="Hrs"))
    assert d["charge_type"] == "instance_hour_burstable", d
    assert d["mechanic"] == "compute_burstable"


def test_windows_burstable_routes_to_windows():
    """A Windows t3 row belongs to the Windows mapper — the licence premium
    dominates the mapping choice."""
    d = sd.dispatch(_row(canonical="ec2", usage_type="APS3-BoxUsage:t3.large",
                         product="Amazon Elastic Compute Cloud running Windows",
                         operation="RunInstances:0002", unit="Hrs"))
    assert d["charge_type"] == "instance_hour_windows", d
    assert d["mechanic"] == "compute_windows"


def test_rds_multi_az_instance_hours_are_classified():
    """Real bills carried APS5-Multi-AZUsage:db.* rows that matched no RDS
    charge_type at all ($5,567 unclassified on a 12-bill corpus)."""
    d = sd.dispatch(_row(canonical="rds", usage_type="APS5-Multi-AZUsage:db.m7g.large",
                         operation="CreateDBInstance:0002", unit="Hrs"))
    assert d is not None, "Multi-AZ instance hours matched no charge_type"
    assert d["mechanic"] == "managed_db", d
