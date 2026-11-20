import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_SKILL_DIR, "scripts"))

import apply_static_mappings as asm

# We want representative rows covering each mechanic block.
# Let's inspect which mechanics exist in asm:
handlers = {
    "flat_hourly": asm.map_flat_hourly,
    "object_storage": asm.map_object_storage,
    "per_request": asm.map_per_request,
    "block_storage": asm.map_block_storage,
    "data_transfer": asm.map_data_transfer,
    "non_workload": asm.map_non_workload,
    "cloudwatch": asm.map_cloudwatch,
    "guardduty": asm.map_guardduty,
    "inspector": asm.map_inspector,
    "marketplace_thirdparty": asm.map_marketplace_thirdparty,
    "quicksight": asm.map_quicksight,
    "redshift": asm.map_redshift,
    "athena": asm.map_athena,
    "kinesis": asm.map_kinesis,
    "efs": asm.map_efs,
    "xray": asm.map_xray,
    "fsx": asm.map_fsx,
    "emr": asm.map_emr,
    "elasticache": asm.map_elasticache,
    "msk": asm.map_msk,
    "compute_windows": asm.map_compute_windows,
    "compute_arm": asm.map_compute_arm,
    "compute_burstable": asm.map_compute_burstable,
    "compute_breakdown": asm.map_compute_breakdown,
    "commitment_discount": asm.map_commitment_discount,
    "managed_db": asm.map_managed_db,
    "opensearch": asm.map_opensearch,
    "rds_extended_support": asm.map_rds_extended_support,
    "glue": asm.map_glue,
    "shield": asm.map_shield,
    "dynamodb_storage": asm.map_dynamodb_storage,
    "dynamodb_capacity": asm.map_dynamodb_capacity,
    "mwaa": asm.map_mwaa,
    "ssm_parameter_store": asm.map_ssm_parameter_store,
    "sagemaker": asm.map_sagemaker,
    "backup": asm.map_backup,
}

print(f"Total handlers: {len(handlers)}")
