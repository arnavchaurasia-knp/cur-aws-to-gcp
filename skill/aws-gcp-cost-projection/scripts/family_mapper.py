#!/usr/bin/env python3
"""family_mapper.py — Thin import shim for consolidated compute mapping (AR-3).

All compute mapping logic has been consolidated into apply_static_mappings.py.
This module re-exports functions, classes, and constants for full backwards
compatibility with existing tests and callers.
"""
from __future__ import annotations
import os
import sys

# Add scripts directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apply_static_mappings import (
    _EC2_TYPES,
    _resolve_instance_type,
    _backfill_specs_from_ec2_types,
    _ACCEL_RE,
    _aws_gpu_spec,
    parse_instance,
    get_gcp_family,
    _RHEL_VCPU_BANDS,
    _license_entries,
    _gce_machine_type_label,
    map_gce_row,
    _resolve_gpu_count,
    _gpu_profile,
    _AWS_GPU_NO_EQUIVALENT,
    _NON_NVIDIA_ACCEL_RE,
    _AWS_GPU_MODEL_ALIAS,
    _no_gpu_equivalent_entry,
    _accel_kind,
    _compute_mapper_kind,
    map_tpu_row,
    map_gpu_row,
    map_db_row,
    map_compute_breakdown,
    resolve_sku,
    cheapest_in_scope,
    cheapest_gpu_in_scope,
    _no_rate_suffix,
    _family_hourly_rate,
    _gp_family_by_label,
    _strict_resolve_sku,
    _GP_FAMILY_GENERATION,
    _AWS_WORKLOAD,
    _ARCH_SUFFIX,
    _GCP_FAMILIES,
    _ARM_PREFIX,
    _ACCEL_CHIPS,
    _TPU_RATES_CFG,
    _cfg_family_map as _cfg,
    family_arch,
    _emit,
)


def main():
    """CLI compatibility entrypoint. Prints consolidation notice and exits 0."""
    print("  family_mapper: consolidated into apply_static_mappings.py (AR-3); no-op")


if __name__ == "__main__":
    main()
