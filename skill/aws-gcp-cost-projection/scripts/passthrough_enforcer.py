#!/usr/bin/env python3
"""passthrough_enforcer.py — Enforce operator-forced passthrough per assumption.

Part of WORKORDER_per_assumption_passthrough.md.
Deterministic post-classification pass:
  1. Loads enabled passthrough keys for the job (config_loader.load_assumption_passthroughs).
  2. Reads data/assumption_impact.json to find governed services and charge types.
  3. Overwrites governed rows to strategy='passthrough', unit_multiplier=1.0,
     passthrough_reason='operator_assumption', and prefixes projection_note with:
     "[passthrough: forced via assumption <key>] ".
  4. Emits diagnostics and per-key summary.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

import duckdb

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from aws_normalizer import canonical_service
from config_loader import load_assumption_passthroughs, load_data_config
import row_state as rs

try:
    from diagnostics import diag, INFO
except ImportError:
    def diag(*args, **kwargs):
        pass


def _row_matches_impact(row: dict[str, Any], key: str, impact: dict[str, Any]) -> bool:
    """Return True if row is governed by the given assumption key and impact spec."""
    product = row.get("product") or ""
    product_code = row.get("product_code") or ""
    canon = canonical_service(product, product_code)

    impact_services = impact.get("services", [])
    canon_services = {canonical_service(s) for s in impact_services}
    raw_services = {s.lower() for s in impact_services}

    # Service check
    service_matched = (
        canon in canon_services
        or product.lower() in raw_services
        or product_code.lower() in raw_services
        or any(s.lower() in product.lower() for s in impact_services)
    )
    if not service_matched:
        return False

    charge_types = [str(ct).lower() for ct in impact.get("charge_types", [])]
    if not charge_types:
        return True

    usage_type = (row.get("usage_type") or "").lower()
    operation = (row.get("operation") or "").lower()
    mechanic_group = (row.get("mechanic_group") or "").lower()
    mechanic_rule = str(row.get("mechanic_rule") or "").lower()
    instance_type = (row.get("instance_type") or "").lower()
    pricing_unit = (row.get("pricing_unit") or "").lower()

    # Key-specific exact matchers for maximum precision
    if key == "nat_vms_per_gateway":
        return (
            "natgateway" in usage_type
            or "natgateway" in operation
            or "natgateway" in product.lower()
            or "vpc_nat_hours" in mechanic_rule
            or "nat" in str(row.get("component") or "").lower()
        ) and any(h in usage_type or h in operation or h in pricing_unit for h in ("hour", "hrs", "hr"))

    if key == "dax_node_gib":
        return canon in ("dax", "amazondax") or "dax" in product.lower() or "dax" in usage_type

    if key == "memorydb_node_gib":
        return canon in ("memorydb", "amazonmemorydb") or "memorydb" in product.lower()

    if key in ("glue_dpu_vcpu", "glue_dpu_ram_gib"):
        return canon in ("glue", "awsglue") and ("dpu" in usage_type or "dpu" in operation or "dpu" in pricing_unit or not usage_type)

    if key == "looker_studio_pro_seat_usd":
        return canon in ("quicksight", "amazonquicksight") or "quicksight" in product.lower()

    if key in ("aurora_acu_vcpu", "aurora_acu_ram_gib"):
        return (
            (canon in ("rds", "aurora") or "rds" in product.lower() or "aurora" in product.lower())
            and ("serverless" in usage_type or "serverless" in operation or "acu" in usage_type or "acu" in operation)
        )

    if key == "lambda_mb_per_vcpu":
        return canon in ("lambda", "awslambda") and ("gb-second" in usage_type or "lambda-gb-second" in operation or not usage_type)

    if key == "inferentia_chip_to_tpu":
        return (
            "inf1" in instance_type
            or "inf2" in instance_type
            or "boxusage:inf" in usage_type
            or "inf1" in usage_type
            or "inf2" in usage_type
            or "inf1" in operation
            or "inf2" in operation
        )

    if key == "trainium_chip_to_tpu":
        return (
            "trn1" in instance_type
            or "trn2" in instance_type
            or "boxusage:trn" in usage_type
            or "trn1" in usage_type
            or "trn2" in usage_type
            or "trn1" in operation
            or "trn2" in operation
        )

    if key == "dynamodb_rcu_to_reads_sec":
        return (
            (canon in ("dynamodb", "amazondynamodb") or "dynamodb" in product.lower())
            and ("readcapacityunit" in usage_type or "rcu" in usage_type or "read capacity" in operation)
        )

    if key == "dynamodb_wcu_to_writes_sec":
        return (
            (canon in ("dynamodb", "amazondynamodb") or "dynamodb" in product.lower())
            and ("writecapacityunit" in usage_type or "wcu" in usage_type or "write capacity" in operation)
        )

    # Generic charge-type match
    for ct in charge_types:
        if ct in mechanic_rule or ct in mechanic_group:
            return True
        if ct in usage_type or ct in operation or ct in product.lower():
            return True

    # Fallback for flat/PDF bills where usage_type is empty and service is single-purpose
    if not usage_type and len(canon_services) == 1:
        return True

    return False


def enforce_assumption_passthroughs(
    job_dir_or_db: str | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, dict[str, Any]]:
    """Enforce operator-forced passthroughs for all active assumption keys.

    Returns summary: {key: {"rows": count, "usd": total_usd}}.
    """
    close_conn = False
    job_dir = None

    if conn is None:
        if not job_dir_or_db:
            job_dir_or_db = os.getcwd()

        if os.path.isfile(job_dir_or_db):
            db_path = job_dir_or_db
            job_dir = os.path.dirname(os.path.dirname(os.path.abspath(db_path)))
        else:
            job_dir = job_dir_or_db
            db_path = os.path.join(job_dir, "projection-audit", "projection.duckdb")
            if not os.path.exists(db_path):
                alt_db = os.path.join(job_dir, "projection.duckdb")
                if os.path.exists(alt_db):
                    db_path = alt_db

        if not os.path.exists(db_path):
            return {}

        conn = duckdb.connect(db_path)
        close_conn = True
    elif job_dir_or_db and os.path.isdir(job_dir_or_db):
        job_dir = job_dir_or_db

    try:
        tables = {t[0] for t in conn.execute("SHOW TABLES").fetchall()}
        if "aws_li_catalog" not in tables or "aws_li_to_gcp_li" not in tables:
            return {}

        enabled_keys = load_assumption_passthroughs(job_dir)
        if not enabled_keys:
            return {}

        impact_cfg = load_data_config("assumption_impact") or {}
        impacts = impact_cfg.get("impacts", {})

        rs.ensure_schema(conn)

        c_cols = {c[0] for c in conn.execute("DESCRIBE aws_li_catalog").fetchall()}
        m_cols = {c[0] for c in conn.execute("DESCRIBE aws_li_to_gcp_li").fetchall()}

        opt_cols = ["product_code", "pricing_unit", "instance_type", "mechanic_group", "mechanic_rule"]
        select_opt = ", ".join(f"c.{col}" if col in c_cols else f"'' AS {col}" for col in opt_cols)

        comp_col = "m.component" if "component" in m_cols else "'' AS component"
        pt_reason_col = "m.passthrough_reason" if "passthrough_reason" in m_cols else "'' AS passthrough_reason"

        rows = conn.execute(f"""
            SELECT c.aws_li_key, c.product, COALESCE(c.usage_type, ''), COALESCE(c.operation, ''),
                   COALESCE(c.aws_amortized_cost, 0.0), m.strategy, m.gcp_service,
                   COALESCE(m.projection_note, ''), {comp_col}, {pt_reason_col}, {select_opt}
            FROM aws_li_catalog c
            JOIN aws_li_to_gcp_li m USING (aws_li_key)
            WHERE c.is_workload
        """).fetchall()

        summary: dict[str, dict[str, Any]] = {}

        for key in sorted(enabled_keys):
            impact = impacts.get(key)
            if not impact:
                continue

            matched_keys: list[str] = []
            seen_keys: set[str] = set()
            total_usd = 0.0

            for r in rows:
                k = r[0]
                if k in seen_keys:
                    continue
                row_dict = {
                    "aws_li_key": r[0],
                    "product": r[1],
                    "usage_type": r[2],
                    "operation": r[3],
                    "aws_amortized_cost": float(r[4]),
                    "strategy": r[5],
                    "gcp_service": r[6],
                    "projection_note": r[7],
                    "component": r[8],
                    "passthrough_reason": r[9],
                    "product_code": r[10],
                    "pricing_unit": r[11],
                    "instance_type": r[12],
                    "mechanic_group": r[13],
                    "mechanic_rule": r[14],
                }

                if _row_matches_impact(row_dict, key, impact):
                    seen_keys.add(k)
                    matched_keys.append(k)
                    total_usd += float(r[4])

            if matched_keys:
                prefix = f"[passthrough: forced via assumption {key}]"
                # Update in duckdb
                for k in matched_keys:
                    # Collapse multiple component rows to single passthrough row to prevent cost duplication
                    conn.execute("""
                        DELETE FROM aws_li_to_gcp_li
                        WHERE aws_li_key = ?
                          AND rowid NOT IN (
                              SELECT min(rowid) FROM aws_li_to_gcp_li WHERE aws_li_key = ?
                          )
                    """, [k, k])

                    curr = conn.execute("SELECT strategy, gcp_service, projection_note FROM aws_li_to_gcp_li WHERE aws_li_key = ?", [k]).fetchone()
                    if curr:
                        old_strat, old_svc, old_note = curr[0], curr[1], curr[2] or ""
                        new_svc = old_svc or f"{impact.get('services', ['GCP'])[0]} (operator passthrough)"
                        if prefix not in old_note:
                            new_note = f"{prefix} {old_note}".strip()
                        else:
                            new_note = old_note

                        comp_set = ", component = NULL" if "component" in m_cols else ""
                        sku_set = ", gcp_sku_id = NULL, gcp_sku_name = NULL, gcp_sku_unit = NULL" if "gcp_sku_id" in m_cols else ""
                        pt_set = ", passthrough_reason = 'operator_assumption'" if "passthrough_reason" in m_cols else ""

                        conn.execute(f"""
                            UPDATE aws_li_to_gcp_li
                            SET strategy = 'passthrough',
                                unit_multiplier = 1.0,
                                gcp_service = ?,
                                projection_note = ?
                                {comp_set}
                                {sku_set}
                                {pt_set}
                            WHERE aws_li_key = ?
                        """, [new_svc, new_note, k])

                # Record structured state in row_state field (keep note intact)
                rs.set_state(conn, matched_keys, by="passthrough_enforcer", tier="pipeline", forced_assumption_key=key)

                summary[key] = {"rows": len(matched_keys), "usd": round(total_usd, 2)}
                diag(INFO, "passthrough_enforced", "passthrough_enforcer",
                     f"Enforced passthrough for assumption '{key}': {len(matched_keys)} row(s) (${total_usd:,.2f})",
                     rows=len(matched_keys), usd=round(total_usd, 2), phase=2, job_dir=job_dir)

        return summary
    finally:
        if close_conn:
            conn.close()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "."
    res = enforce_assumption_passthroughs(arg)
    if res:
        print(f"passthrough_enforcer: enforced {sum(v['rows'] for v in res.values())} row(s) across {len(res)} assumption(s)")
    else:
        print("passthrough_enforcer: no active assumption passthroughs to enforce")
