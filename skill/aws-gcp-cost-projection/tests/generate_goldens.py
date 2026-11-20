#!/usr/bin/env python3
"""
generate_goldens.py — Generates:
1. tests/golden/multiplier_golden.json: Snapshots (gcp_sku_name, strategy, unit_multiplier, gcp_service, component)
   for representative rows covering each mechanic block.
2. tests/golden/pipeline_golden.json: Snapshots projected costs for the benchmark corpus.
"""
import json
import os
import sys
import duckdb

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_HERE)
_SCRIPTS_DIR = os.path.join(_SKILL_DIR, "scripts")
_GOLDEN_DIR = os.path.join(_HERE, "golden")
os.makedirs(_GOLDEN_DIR, exist_ok=True)
sys.path.insert(0, _SCRIPTS_DIR)

import apply_static_mappings as asm

# ── 1. Pipeline Golden ─────────────────────────────────────────────────────────

def build_pipeline_golden():
    corpus_dir = os.path.join(_HERE, "corpus")
    pipeline_golden = {}
    for corpus in ["standard_workload", "enterprise_high_sp", "modern_serverless"]:
        db_path = os.path.join(corpus_dir, corpus, "projection-audit", "projection.duckdb")
        if not os.path.exists(db_path):
            continue
        con = duckdb.connect(db_path, read_only=True)
        rows = con.execute("SELECT aws_li_key, gcp_projected_cost FROM gcp_projection ORDER BY aws_li_key").fetchall()
        con.close()
        pipeline_golden[corpus] = {k: round(float(cost), 4) for k, cost in rows}
    
    out_path = os.path.join(_GOLDEN_DIR, "pipeline_golden.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pipeline_golden, f, indent=2, sort_keys=True)
    print(f"Generated pipeline golden: {len(pipeline_golden)} corpus jobs -> {out_path}")


# ── 2. Multiplier Golden ───────────────────────────────────────────────────────

def _make_row(**kw):
    return {
        "aws_li_key": kw.get("aws_li_key", "k1"),
        "mechanic_group": kw.get("mechanic_group", "misc"),
        "product": kw.get("product", ""),
        "usage_type": kw.get("usage_type", ""),
        "pricing_unit": kw.get("pricing_unit", kw.get("unit", "")),
        "unit": kw.get("unit", kw.get("pricing_unit", "")),
        "aws_amortized_cost": kw.get("aws_amortized_cost", 100.0),
        "aws_region": kw.get("aws_region", "us-east-1"),
        "region": kw.get("region", "us-east-1"),
        "gcp_region": kw.get("gcp_region", "us-central1"),
        "operation": kw.get("operation", ""),
        "volume_type": kw.get("volume_type", ""),
        "total_usage": kw.get("total_usage", 100.0),
        "instance_type": kw.get("instance_type", ""),
        "instance_ram_gb": kw.get("instance_ram_gb", 8.0),
        "deployment_option": kw.get("deployment_option", ""),
        "database_engine": kw.get("database_engine", ""),
        "pricing_model": kw.get("pricing_model", "OnDemand"),
        "product_code": kw.get("product_code", ""),
        "instance_vcpus": kw.get("instance_vcpus", 2),
        "operating_system": kw.get("operating_system", "Linux"),
        "license_model": kw.get("license_model", "No License required"),
        "line_item_type": kw.get("line_item_type", "Usage"),
        "billing_month": kw.get("billing_month", "2026-11"),
        "from_region": kw.get("from_region", ""),
        "to_region": kw.get("to_region", ""),
        "mechanic_rule": kw.get("mechanic_rule", ""),
    }


def get_representative_rows():
    """Build representative rows covering all mechanic groups."""
    rows = []
    
    # Load from service_usage_catalog
    catalog_path = os.path.join(_SKILL_DIR, "data", "service_usage_catalog.json")
    if os.path.exists(catalog_path):
        with open(catalog_path, "r", encoding="utf-8") as f:
            suc = json.load(f)
        for svc_name, items in suc.items():
            if svc_name.startswith("_"):
                continue
            for idx, item in enumerate(items):
                mech = item.get("expected_mechanic")
                if not mech or mech == "ignore":
                    continue
                rows.append(_make_row(
                    aws_li_key=f"suc_{svc_name}_{idx}",
                    mechanic_group=mech,
                    product=item.get("description", svc_name),
                    usage_type=item.get("usage_type", ""),
                    unit=item.get("unit", "GB-Mo"),
                    operation=item.get("operation", ""),
                    mechanic_rule=item.get("expected_charge_type", ""),
                ))

    # Add core compute rows covering EC2 instances, OS licenses, burstable, ARM, windows, GPU
    compute_cases = [
        ("ec2_m5_large", "compute_breakdown", "Amazon Elastic Compute Cloud", "BoxUsage:m5.large", "m5.large", 2, 8.0, "Linux"),
        ("ec2_c5_xlarge", "compute_breakdown", "Amazon Elastic Compute Cloud", "BoxUsage:c5.xlarge", "c5.xlarge", 4, 8.0, "Linux"),
        ("ec2_r5_2xlarge", "compute_breakdown", "Amazon Elastic Compute Cloud", "BoxUsage:r5.2xlarge", "r5.2xlarge", 8, 64.0, "Linux"),
        ("ec2_t3_micro", "compute_burstable", "Amazon Elastic Compute Cloud", "CPUCredits:t3.micro", "t3.micro", 2, 1.0, "Linux"),
        ("ec2_t4g_medium", "compute_arm", "Amazon Elastic Compute Cloud", "APS3-BoxUsage:t4g.medium", "t4g.medium", 2, 4.0, "Linux"),
        ("ec2_c7g_large", "compute_arm", "Amazon Elastic Compute Cloud", "BoxUsage:c7g.large", "c7g.large", 2, 4.0, "Linux"),
        ("ec2_win_m5", "compute_windows", "Amazon Elastic Compute Cloud", "BoxUsage:m5.large", "m5.large", 2, 8.0, "Windows"),
        ("ec2_g4dn_xl", "compute_breakdown", "Amazon Elastic Compute Cloud", "BoxUsage:g4dn.xlarge", "g4dn.xlarge", 4, 16.0, "Linux"),
        ("ec2_g5_xl", "compute_breakdown", "Amazon Elastic Compute Cloud", "BoxUsage:g5.xlarge", "g5.xlarge", 4, 16.0, "Linux"),
        ("ec2_local_ssd", "compute_breakdown", "Amazon Elastic Compute Cloud", "BoxUsage:i3en.large", "i3en.large", 2, 16.0, "Linux"),
    ]
    for key, grp, prod, ut, itype, vcpu, ram, os_name in compute_cases:
        rows.append(_make_row(
            aws_li_key=key,
            mechanic_group=grp,
            product=prod,
            usage_type=ut,
            instance_type=itype,
            instance_vcpus=vcpu,
            instance_ram_gb=ram,
            operating_system=os_name,
            unit="Hrs",
        ))

    # Add DB rows
    db_cases = [
        ("rds_mysql_m5", "managed_db", "Amazon Relational Database Service", "InstanceUsage:db.m5.large", "db.m5.large", "MySQL", 2, 8.0),
        ("rds_pg_r5", "managed_db", "Amazon Relational Database Service", "InstanceUsage:db.r5.xlarge", "db.r5.xlarge", "PostgreSQL", 4, 32.0),
        ("rds_docdb", "managed_db", "Amazon DocumentDB", "NodeUsage:db.r5.large", "db.r5.large", "DocumentDB", 2, 16.0),
        ("rds_db2", "managed_db", "Amazon Relational Database Service", "InstanceUsage:db.m5.large", "db.m5.large", "Db2", 2, 8.0),
        ("rds_ext_supp", "rds_extended_support", "Amazon Relational Database Service", "ExtendedSupport:MySQL", "", "MySQL", 2, 8.0),
    ]
    for key, grp, prod, ut, itype, engine, vcpu, ram in db_cases:
        rows.append(_make_row(
            aws_li_key=key,
            mechanic_group=grp,
            product=prod,
            usage_type=ut,
            instance_type=itype,
            database_engine=engine,
            instance_vcpus=vcpu,
            instance_ram_gb=ram,
            unit="Hrs",
        ))

    # Add other specialized mechanics
    special_cases = [
        ("shield_adv", "shield", "AWS Shield", "Shield-Advanced", "mo", "shield"),
        ("shield_dt", "shield", "AWS Shield", "Shield-Advanced-DataTransfer", "GB", "shield_data_transfer"),
        ("lambda_gb_sec", "per_request", "AWS Lambda", "Lambda-GB-Second", "Lambda-GB-Second", "compute"),
        ("lambda_req", "per_request", "AWS Lambda", "Request", "Requests", "requests"),
        ("lambda_concurrency", "per_request", "AWS Lambda", "ProvisionedConcurrency", "Hrs", "concurrency"),
        ("quicksight_author", "quicksight", "Amazon QuickSight", "QuickSight-Author", "user-month", "author"),
        ("quicksight_reader", "quicksight", "Amazon QuickSight", "QuickSight-Reader", "session", "reader"),
        ("athena_scan", "athena", "Amazon Athena", "DataScanned-Bytes", "TB", "query"),
        ("kinesis_shards", "kinesis", "Amazon Kinesis", "ShardHour", "Hrs", "messaging"),
        ("guardduty_flow", "guardduty", "Amazon GuardDuty", "FlowLogs-Bytes", "GB", "security"),
        ("inspector_ec2", "inspector", "Amazon Inspector", "EC2-Instance-Scan", "count", "security"),
        ("mkt_saas", "marketplace_thirdparty", "AWS Marketplace", "SaaS-Subscription", "mo", "marketplace"),
        ("non_workload_sup", "non_workload", "AWS Support", "Support-Business", "mo", "support"),
        ("discount_sp", "commitment_discount", "Savings Plans", "ComputeSP-1yr", "Hrs", "discount"),
        ("mwaa_env", "mwaa", "Amazon Managed Workflows for Apache Airflow", "MWAA-Environment-Small", "Hrs", "orchestration"),
        ("ssm_param", "ssm_parameter_store", "AWS Systems Manager", "ParameterStore-Advanced", "count", "parameter"),
        ("glue_dpu", "glue", "AWS Glue", "Glue-DPU-Hour", "DPU-Hours", "data_processing"),
        ("opensearch_node", "opensearch", "Amazon OpenSearch Service", "ESInstance:r5.large", "Hrs", "search"),
    ]
    for key, grp, prod, ut, unit, mr in special_cases:
        rows.append(_make_row(
            aws_li_key=key,
            mechanic_group=grp,
            product=prod,
            usage_type=ut,
            unit=unit,
            mechanic_rule=mr,
        ))

    return rows


def build_multiplier_golden():
    rows = get_representative_rows()
    print(f"Mapping {len(rows)} representative rows across mechanics...")
    
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

    by_group = {g: [] for g in handlers}
    unhandled = []
    for r in rows:
        grp = r["mechanic_group"]
        if grp in by_group:
            by_group[grp].append(r)
        else:
            unhandled.append(r)
            
    snapshot = {}
    total_mapped = 0
    for grp, grp_rows in by_group.items():
        if not grp_rows:
            continue
        handler = handlers[grp]
        res = handler(grp_rows)
        if isinstance(res, tuple):
            mapped_entries = res[0]
        else:
            mapped_entries = res
        
        for m in mapped_entries:
            key = m["aws_li_key"]
            # Snapshot the relevant fields
            snapshot[key] = {
                "gcp_service": m.get("gcp_service"),
                "gcp_sku_name": m.get("gcp_sku_name"),
                "component": m.get("component"),
                "strategy": m.get("strategy"),
                "unit_multiplier": m.get("unit_multiplier"),
                "passthrough_reason": m.get("passthrough_reason"),
            }
            total_mapped += 1

    out_path = os.path.join(_GOLDEN_DIR, "multiplier_golden.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
    print(f"Generated multiplier golden: {total_mapped} mapped rows -> {out_path}")


if __name__ == "__main__":
    build_pipeline_golden()
    build_multiplier_golden()
