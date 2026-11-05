#!/usr/bin/env python3
"""
directive_resolver.py — Deterministic resolver for the Directive Layer.

Resolves an ordered directive ledger against catalog rows into per-row
effective overrides using strict specificity ordering and recency tie-breaking:
  explicit aws_li_keys pin > tag/account > family+arch > service > global.

Recency breaks ties (later in ledger wins).
Losing directives are recorded as superseded for audit provenance.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set, Union

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from aws_normalizer import canonical_service as get_canonical_service
except Exception:
    def get_canonical_service(prod: str) -> str:
        p = (prod or "").lower()
        if "elastic compute cloud" in p or "ec2" in p:
            return "ec2"
        if "simple storage" in p or "s3" in p:
            return "s3"
        return p.split()[0] if p else ""

from directives import Directive, load_ledger


SPECIFICITY_PIN = 1000      # explicit aws_li_keys
SPECIFICITY_TAG_ACCT = 500  # tag or account_id
SPECIFICITY_FAMILY_ARCH = 300 # instance_family or arch
SPECIFICITY_SERVICE = 100   # service, product_code, usage_type_re
SPECIFICITY_GLOBAL = 1      # no row-specific selectors


@dataclasses.dataclass
class EffectiveOverride:
    aws_li_key: str
    target_service: Optional[str] = None
    strategy: Optional[str] = None
    is_workload: Optional[bool] = None
    assumptions: Dict[str, Any] = dataclasses.field(default_factory=dict)
    notes: List[str] = dataclasses.field(default_factory=list)
    applied_directives: List[str] = dataclasses.field(default_factory=list)
    superseded_directives: List[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aws_li_key": self.aws_li_key,
            "target_service": self.target_service,
            "strategy": self.strategy,
            "is_workload": self.is_workload,
            "assumptions": copy_dict(self.assumptions),
            "notes": list(self.notes),
            "applied_directives": list(self.applied_directives),
            "superseded_directives": list(self.superseded_directives),
        }


def copy_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(d))


def extract_row_identity(row: Dict[str, Any]) -> Dict[str, Any]:
    """Extract canonicalized identity fields from a catalog row."""
    key = row.get("aws_li_key") or row.get("key") or ""
    product = str(row.get("product") or row.get("aws_service") or "")
    product_code = str(row.get("product_code") or row.get("aws_product_code") or "")
    canonical_svc = (
        str(row.get("canonical_service") or "").strip().lower()
        or get_canonical_service(product)
        or product_code.lower()
    )
    usage_type = str(row.get("usage_type") or "")
    operation = str(row.get("operation") or "")
    region = str(row.get("region") or row.get("aws_region") or "").lower()
    account_id = str(row.get("account_id") or row.get("linked_account_id") or "")

    # Tags
    raw_tags = row.get("tags") or row.get("tag") or {}
    if isinstance(raw_tags, str):
        try:
            tags = json.loads(raw_tags)
        except Exception:
            tags = {}
    elif isinstance(raw_tags, dict):
        tags = raw_tags
    else:
        tags = {}

    # Extract instance family & architecture
    instance_type = str(row.get("instance_type") or "")
    if not instance_type and usage_type:
        m = re.search(r'(?:BoxUsage:|Fargate-)?([a-z0-9]+(?:\.[a-z0-9]+)?)', usage_type)
        if m:
            instance_type = m.group(1)

    family = ""
    arch = "x86_64"
    if instance_type:
        clean = re.sub(r'^(db\.|cache\.)', '', instance_type.lower())
        parts = clean.split('.')
        family = parts[0]
        if any(family.endswith(x) for x in ("g", "gd", "gn")) or "graviton" in usage_type.lower() or "arm" in usage_type.lower():
            arch = "arm64"
        elif family.startswith("a1"):
            arch = "arm64"

    return {
        "aws_li_key": key,
        "product": product,
        "product_code": product_code,
        "canonical_service": canonical_svc,
        "gcp_service": str(row.get("gcp_service") or ""),
        "usage_type": usage_type,
        "operation": operation,
        "region": region,
        "account_id": account_id,
        "tags": tags,
        "instance_family": family,
        "arch": arch,
    }


def compute_specificity(directive: Directive) -> int:
    """Calculate the specificity score of a directive's selector."""
    selector = directive.selector or {}
    if not selector:
        return SPECIFICITY_GLOBAL

    score = 0
    # 1. Explicit pin
    if selector.get("aws_li_keys"):
        score += SPECIFICITY_PIN

    # 2. Tag / Account
    if selector.get("tag") or selector.get("account_id"):
        score += SPECIFICITY_TAG_ACCT

    # 3. Family / Arch
    if selector.get("instance_family") or selector.get("arch"):
        score += SPECIFICITY_FAMILY_ARCH

    # 4. Service / Product / Usage pattern
    if selector.get("service") or selector.get("product_code") or selector.get("usage_type_re") or selector.get("region"):
        score += SPECIFICITY_SERVICE

    return score or SPECIFICITY_GLOBAL


def matches_selector(directive: Directive, ident: Dict[str, Any]) -> bool:
    """Check if all non-empty selector criteria in directive match the row identity."""
    selector = directive.selector or {}
    if not selector:
        return True

    # 1. aws_li_keys pin
    if "aws_li_keys" in selector and selector["aws_li_keys"]:
        pin_keys = selector["aws_li_keys"]
        if isinstance(pin_keys, str):
            pin_keys = [pin_keys]
        if ident["aws_li_key"] not in pin_keys:
            return False

    # 2. Service
    if "service" in selector and selector["service"]:
        req_svc = str(selector["service"]).strip().lower()
        row_svc = ident["canonical_service"]
        row_prod = ident["product"].lower()
        if req_svc != row_svc and req_svc not in row_prod:
            return False

    # 3. Product code
    if "product_code" in selector and selector["product_code"]:
        req_pc = str(selector["product_code"]).strip().lower()
        row_pc = ident["product_code"].lower()
        if req_pc != row_pc:
            return False

    # 4. Region
    if "region" in selector and selector["region"]:
        req_reg = str(selector["region"]).strip().lower()
        row_reg = ident["region"]
        if req_reg != row_reg:
            return False

    # 5. Account ID
    if "account_id" in selector and selector["account_id"]:
        req_acc = str(selector["account_id"]).strip()
        row_acc = ident["account_id"]
        if req_acc != row_acc:
            return False

    # 6. Tags
    if "tag" in selector and isinstance(selector["tag"], dict):
        row_tags = ident["tags"]
        for k, v in selector["tag"].items():
            if str(row_tags.get(k)) != str(v):
                return False

    # 7. Instance Family
    if "instance_family" in selector and selector["instance_family"]:
        req_fam = selector["instance_family"]
        if isinstance(req_fam, str):
            req_fam = [req_fam.lower()]
        else:
            req_fam = [str(f).lower() for f in req_fam]
        if ident["instance_family"].lower() not in req_fam:
            return False

    # 8. Arch
    if "arch" in selector and selector["arch"]:
        req_arch = str(selector["arch"]).strip().lower()
        if req_arch in ("arm", "arm64", "aarch64"):
            req_arch = "arm64"
        elif req_arch in ("x86", "x86_64", "amd64"):
            req_arch = "x86_64"
        if ident["arch"] != req_arch:
            return False

    # 9. Usage Type Regex
    if "usage_type_re" in selector and selector["usage_type_re"]:
        pattern = selector["usage_type_re"]
        search_blob = f"{ident['usage_type']} {ident['operation']} {ident['product']}"
        if not re.search(pattern, search_blob, re.IGNORECASE):
            return False

    # 10. Product Regex (exact product name match)
    if "product_re" in selector and selector["product_re"]:
        if not re.search(selector["product_re"], ident["product"], re.IGNORECASE):
            return False

    # 11. GCP Service match
    if "gcp_service" in selector and selector["gcp_service"]:
        req_gsvc = str(selector["gcp_service"]).strip().lower()
        row_gsvc = str(ident.get("gcp_service") or "").lower()
        if req_gsvc not in row_gsvc:
            return False

    return True


def _as_directive(d: Any) -> Optional[Directive]:
    if isinstance(d, Directive):
        return d
    if hasattr(d, "to_dict"):
        try:
            return Directive.from_dict(d.to_dict())
        except Exception:
            pass
    if isinstance(d, dict):
        try:
            return Directive.from_dict(d)
        except Exception:
            pass
    return None


def resolve(
    rows: List[Dict[str, Any]],
    directives: List[Union[Directive, Dict[str, Any]]]
) -> Dict[str, EffectiveOverride]:
    """
    Resolve an ordered list of directives against a list of catalog rows.
    Returns: dict mapping aws_li_key -> EffectiveOverride.
    """
    parsed_directives: List[Directive] = []
    for d in directives:
        parsed = _as_directive(d)
        if parsed is not None:
            parsed_directives.append(parsed)

    # Pre-compute specificity and index rank for each directive:
    # rank = (specificity, index)
    ranked: List[tuple[tuple[int, int], Directive]] = []
    for idx, d in enumerate(parsed_directives):
        spec = compute_specificity(d)
        ranked.append(((spec, idx), d))

    overrides: Dict[str, EffectiveOverride] = {}

    for row in rows:
        ident = extract_row_identity(row)
        key = ident["aws_li_key"]
        if not key:
            continue

        eff = EffectiveOverride(aws_li_key=key)

        # Field-level winners: field_name -> ((spec, idx), directive, value)
        field_winners: Dict[str, tuple[tuple[int, int], Directive, Any]] = {}
        # Track all directives that matched this row
        matched_directives: List[tuple[tuple[int, int], Directive]] = []

        for rank, d in ranked:
            if not matches_selector(d, ident):
                continue

            matched_directives.append((rank, d))
            act = d.action
            act_type = act.get("type")

            if act_type == "set_target_service":
                field = "target_service"
                val = act.get("value")
                if field not in field_winners or rank > field_winners[field][0]:
                    field_winners[field] = (rank, d, val)

            elif act_type == "set_strategy":
                field = "strategy"
                val = act.get("value")
                if field not in field_winners or rank > field_winners[field][0]:
                    field_winners[field] = (rank, d, val)

            elif act_type == "set_is_workload":
                field = "is_workload"
                val = act.get("value")
                if field not in field_winners or rank > field_winners[field][0]:
                    field_winners[field] = (rank, d, val)

            elif act_type == "set_assumption":
                # Assumptions can be keyed: field = f"assumption:{key}"
                key_name = act.get("key") or (act.get("params") or {}).get("key")
                val = act.get("value")
                if not key_name and isinstance(val, dict):
                    # multiple assumptions
                    for k, v in val.items():
                        afield = f"assumption:{k}"
                        if afield not in field_winners or rank > field_winners[afield][0]:
                            field_winners[afield] = (rank, d, v)
                elif key_name:
                    afield = f"assumption:{key_name}"
                    if afield not in field_winners or rank > field_winners[afield][0]:
                        field_winners[afield] = (rank, d, val)

            elif act_type == "annotate":
                note_text = str(act.get("value") or (act.get("params") or {}).get("note") or "").strip()
                if note_text and note_text not in eff.notes:
                    eff.notes.append(note_text)
                    if d.id not in eff.applied_directives:
                        eff.applied_directives.append(d.id)

        # Apply field winners
        winning_directive_ids: Set[str] = set()
        for field, (rank, d, val) in field_winners.items():
            winning_directive_ids.add(d.id)
            if field == "target_service":
                eff.target_service = val
            elif field == "strategy":
                eff.strategy = val
            elif field == "is_workload":
                eff.is_workload = val
            elif field.startswith("assumption:"):
                as_key = field.split(":", 1)[1]
                eff.assumptions[as_key] = val

        # Provenance: applied vs superseded
        for did in winning_directive_ids:
            if did not in eff.applied_directives:
                eff.applied_directives.append(did)

        for rank, d in matched_directives:
            if d.id not in eff.applied_directives:
                if d.id not in eff.superseded_directives:
                    eff.superseded_directives.append(d.id)

        # Only save override if there was at least one matching directive
        if matched_directives:
            overrides[key] = eff

    return overrides
