#!/usr/bin/env python3
"""
license_split.py — Single source of truth for the AWS instance+license cost split.

AWS On-Demand rates are license-INCLUSIVE (a Windows m5.xlarge's OD price already
contains the license). GCP prices the license as a separate SKU. To show the AWS
side as `instance + license` symmetrically with GCP, we split the row's AWS OD cost
using a per-instance-type `license_fraction` computed from the AWS Price List
(Windows/Linux OD ratio, etc.) by scripts/refresh_ec2_specs.py and stored in
data/ec2-instance-types.json.

Used by render_report.py (HTML/PDF) and export_report.py (CSV/XLSX) so the split is
identical across every output format.
"""
from __future__ import annotations

import json
import os

_EC2_SPECS_CACHE = None


def load_ec2_specs() -> dict:
    global _EC2_SPECS_CACHE
    if _EC2_SPECS_CACHE is None:
        specs_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "ec2-instance-types.json"
        )
        try:
            with open(specs_path, "r", encoding="utf-8") as f:
                _EC2_SPECS_CACHE = json.load(f)
        except Exception:
            _EC2_SPECS_CACHE = {}
    return _EC2_SPECS_CACHE


def _os_key_from_license(lic_name: str) -> str | None:
    lic_lower = (lic_name or "").lower()
    if "windows" in lic_lower:
        return "windows"
    if "rhel" in lic_lower or "red hat" in lic_lower:
        return "rhel"
    if "suse" in lic_lower:
        return "suse"
    if "ubuntu" in lic_lower:
        return "ubuntu_pro"
    return None


def get_aws_license_split(itype, lic_name, aws_cost):
    """Return (aws_base_cost, aws_lic_cost, os_key) or None if no authoritative fraction.

    aws_lic_cost  = round(aws_cost * fraction, 2)
    aws_base_cost = round(aws_cost - aws_lic_cost, 2)   (exact sum to aws_cost)
    None  → caller falls back to showing the full AWS cost as instance_cost with a
            blank license (Phase-1 "(incl. OS license)" behavior); never fabricate.
    """
    if not itype or not lic_name or aws_cost is None or aws_cost <= 0:
        return None
    os_key = _os_key_from_license(lic_name)
    if not os_key:
        return None
    itype_info = load_ec2_specs().get(itype)
    if not itype_info or not isinstance(itype_info, dict):
        return None
    frac = (itype_info.get("license_fraction", {}) or {}).get(os_key)
    if frac is None or frac <= 0:
        return None
    aws_lic_cost = round(aws_cost * frac, 2)
    aws_base_cost = round(aws_cost - aws_lic_cost, 2)
    return (aws_base_cost, aws_lic_cost, os_key)
