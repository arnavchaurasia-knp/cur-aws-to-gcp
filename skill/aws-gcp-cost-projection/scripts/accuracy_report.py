#!/usr/bin/env python3
"""accuracy_report.py — Ground-truth accuracy scorecard and run-vs-run diff harness.

Emits $-weighted accuracy scorecards and diffs across job runs:
  1. Coverage mix: % of spend deterministic (spec+static) vs LLM vs passthrough vs ignored.
  2. Determinism holes: spec_gap$ by service, misc$, and rows routed to LLM.
  3. OD-basis mix: public_od vs reconstructed_default vs amortized_fallback.
  4. Ratio distribution: GCP/AWS OD ratio >3x, >10x, and max outlier by service.
  5. Guard events: unit_mismatch, unit_unknown, echo, allowed_target rejections with $.

Usage:
    python accuracy_report.py <job.duckdb> [...]
    python accuracy_report.py <job_dir> [...]
    python accuracy_report.py --diff <runA> <runB>
    python accuracy_report.py <job.duckdb> --json
    python accuracy_report.py <job.duckdb> --check [--min-deterministic-cov 80.0]
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import duckdb

# Add scripts directory to sys.path
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

try:
    from diagnostics import read_all as read_diagnostics
except ImportError:
    def read_diagnostics(job_dir: str) -> List[Dict[str, Any]]:
        diag_file = os.path.join(job_dir, "projection-audit", "diagnostics.jsonl")
        if not os.path.exists(diag_file):
            diag_file = os.path.join(job_dir, "diagnostics.jsonl")
        if not os.path.exists(diag_file):
            return []
        records = []
        try:
            with open(diag_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception:
            pass
        return records


def resolve_paths(target: str) -> Tuple[str, str]:
    """Given a file path or directory, return (db_path, job_dir)."""
    p = os.path.abspath(target)
    if os.path.isfile(p):
        db_path = p
        # Job dir is parent of projection-audit, or parent of db
        parent = os.path.dirname(db_path)
        if os.path.basename(parent) == "projection-audit":
            job_dir = os.path.dirname(parent)
        else:
            job_dir = parent
    else:
        job_dir = p
        cand1 = os.path.join(job_dir, "projection-audit", "projection.duckdb")
        cand2 = os.path.join(job_dir, "projection.duckdb")
        if os.path.exists(cand1):
            db_path = cand1
        elif os.path.exists(cand2):
            db_path = cand2
        else:
            db_path = cand1  # default target
    return db_path, job_dir


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    return (num / den) if den and den != 0.0 else default


def compute_scorecard(target: str) -> Dict[str, Any]:
    """Compute a $-weighted accuracy scorecard for a single job DB."""
    db_path, job_dir = resolve_paths(target)
    job_name = os.path.basename(job_dir.rstrip("/\\"))

    if not os.path.exists(db_path):
        return {
            "job": job_name,
            "db_path": db_path,
            "job_dir": job_dir,
            "error": f"Database file not found: {db_path}",
            "status": "missing_db",
        }

    try:
        con = duckdb.connect(db_path, read_only=True)
    except Exception as exc:
        return {
            "job": job_name,
            "db_path": db_path,
            "job_dir": job_dir,
            "error": f"Failed to open duckdb: {exc}",
            "status": "corrupt_db",
        }

    try:
        tables = {
            t[0]
            for t in con.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        if "aws_li_catalog" not in tables:
            con.close()
            return {
                "job": job_name,
                "db_path": db_path,
                "job_dir": job_dir,
                "error": "No aws_li_catalog table found in database",
                "status": "no_catalog",
            }

        # Inspect available columns
        catalog_cols = {
            c[1].lower()
            for c in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()
        }
        has_mappings = "aws_li_to_gcp_li" in tables
        mapping_cols = (
            {
                c[1].lower()
                for c in con.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()
            }
            if has_mappings
            else set()
        )
        has_proj = "gcp_projection" in tables
        proj_cols = (
            {
                c[1].lower()
                for c in con.execute("PRAGMA table_info('gcp_projection')").fetchall()
            }
            if has_proj
            else set()
        )

        has_workload_col = "is_workload" in catalog_cols
        workload_where = "WHERE is_workload" if has_workload_col else "WHERE 1=1"

        # Totals
        q_totals = f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(aws_amortized_cost), 0.0),
                COALESCE(SUM({'aws_od_cost' if 'aws_od_cost' in catalog_cols else 'aws_amortized_cost'}), 0.0)
            FROM aws_li_catalog
            {workload_where}
        """
        row_tot = con.execute(q_totals).fetchone()
        tot_rows = int(row_tot[0] or 0)
        tot_aws_amortized = float(row_tot[1] or 0.0)
        tot_aws_od = float(row_tot[2] or 0.0)

        # Projected cost totals
        tot_gcp_projected = 0.0
        if has_proj:
            q_proj_tot = f"""
                SELECT COALESCE(SUM(gcp_projected_cost), 0.0)
                FROM gcp_projection
                {'WHERE is_workload' if 'is_workload' in proj_cols else ''}
            """
            tot_gcp_projected = float(con.execute(q_proj_tot).fetchone()[0] or 0.0)

        # -------------------------------------------------------------
        # 1. Coverage Mix
        # -------------------------------------------------------------
        # Categorize workload items into: deterministic, llm, passthrough, ignored, unmapped
        mix_data = {
            "deterministic": {"aws_amortized": 0.0, "aws_od": 0.0, "gcp_projected": 0.0, "rows": 0, "pct_of_spend": 0.0},
            "llm":           {"aws_amortized": 0.0, "aws_od": 0.0, "gcp_projected": 0.0, "rows": 0, "pct_of_spend": 0.0},
            "passthrough":   {"aws_amortized": 0.0, "aws_od": 0.0, "gcp_projected": 0.0, "rows": 0, "pct_of_spend": 0.0},
            "ignored":       {"aws_amortized": 0.0, "aws_od": 0.0, "gcp_projected": 0.0, "rows": 0, "pct_of_spend": 0.0},
            "unmapped":      {"aws_amortized": 0.0, "aws_od": 0.0, "gcp_projected": 0.0, "rows": 0, "pct_of_spend": 0.0},
        }

        if has_mappings:
            rule_expr = "c.mechanic_rule" if "mechanic_rule" in catalog_cols else "''"
            group_expr = "c.mechanic_group" if "mechanic_group" in catalog_cols else "''"
            decided_expr = "m.decided_by" if "decided_by" in mapping_cols else "''"
            od_expr = "c.aws_od_cost" if "aws_od_cost" in catalog_cols else "c.aws_amortized_cost"

            # Projection join
            proj_join = ""
            gcp_sum_expr = "0.0"
            if has_proj:
                proj_join = "LEFT JOIN (SELECT aws_li_key, SUM(gcp_projected_cost) as gcp_cost FROM gcp_projection GROUP BY aws_li_key) p USING (aws_li_key)"
                gcp_sum_expr = "COALESCE(p.gcp_cost, 0.0)"

            q_mix = f"""
                WITH item_level AS (
                    SELECT
                        c.aws_li_key,
                        ANY_VALUE(c.aws_amortized_cost) as aws_amortized,
                        ANY_VALUE({od_expr}) as aws_od,
                        ANY_VALUE({gcp_sum_expr}) as gcp_cost,
                        ANY_VALUE(
                            CASE
                                WHEN m.aws_li_key IS NULL THEN 'unmapped'
                                WHEN m.strategy = 'ignore' OR {group_expr} = 'non_workload' THEN 'ignored'
                                WHEN m.strategy = 'passthrough' THEN 'passthrough'
                                WHEN m.strategy IN ('map', 'break_down') AND (
                                    {rule_expr} LIKE 'spec:%'
                                    OR (COALESCE({decided_expr}, '') NOT IN ('llm', 'agy', 'phase2_llm') AND {group_expr} != 'misc')
                                ) THEN 'deterministic'
                                WHEN {group_expr} = 'misc' OR COALESCE({decided_expr}, '') IN ('llm', 'agy', 'phase2_llm') THEN 'llm'
                                ELSE 'deterministic'
                            END
                        ) as category
                    FROM aws_li_catalog c
                    LEFT JOIN aws_li_to_gcp_li m USING (aws_li_key)
                    {proj_join}
                    {workload_where}
                    GROUP BY c.aws_li_key
                )
                SELECT
                    category,
                    COUNT(*),
                    ROUND(SUM(aws_amortized), 2),
                    ROUND(SUM(aws_od), 2),
                    ROUND(SUM(gcp_cost), 2)
                FROM item_level
                GROUP BY category
            """
            for cat, r_cnt, a_cost, a_od, g_cost in con.execute(q_mix).fetchall():
                if cat in mix_data:
                    mix_data[cat]["rows"] = int(r_cnt)
                    mix_data[cat]["aws_amortized"] = float(a_cost or 0.0)
                    mix_data[cat]["aws_od"] = float(a_od or 0.0)
                    mix_data[cat]["gcp_projected"] = float(g_cost or 0.0)
                    mix_data[cat]["pct_of_spend"] = round(
                        _safe_div(float(a_od or 0.0), tot_aws_od) * 100.0, 2
                    )

        # -------------------------------------------------------------
        # 2. Determinism Holes (spec_gap, misc, llm_routed)
        # -------------------------------------------------------------
        spec_gap_data = {"total_usd": 0.0, "total_rows": 0, "by_service": []}
        misc_data = {"total_usd": 0.0, "total_rows": 0, "by_service": []}
        llm_routed_data = {"total_usd": 0.0, "total_rows": 0}

        if "mechanic_rule" in catalog_cols:
            q_spec_gap = f"""
                SELECT
                    REPLACE(c.mechanic_rule, 'spec_gap:', '') as svc,
                    COUNT(*),
                    ROUND(SUM({'aws_od_cost' if 'aws_od_cost' in catalog_cols else 'aws_amortized_cost'}), 2)
                FROM aws_li_catalog c
                {workload_where} AND c.mechanic_rule LIKE 'spec_gap:%'
                GROUP BY svc
                ORDER BY 3 DESC
            """
            for s_name, s_cnt, s_usd in con.execute(q_spec_gap).fetchall():
                u = float(s_usd or 0.0)
                n = int(s_cnt or 0)
                spec_gap_data["total_usd"] += u
                spec_gap_data["total_rows"] += n
                spec_gap_data["by_service"].append({"service": s_name, "rows": n, "usd": u})
            spec_gap_data["total_usd"] = round(spec_gap_data["total_usd"], 2)

        if "mechanic_group" in catalog_cols:
            rule_clause = "OR c.mechanic_rule = 'misc' OR c.mechanic_rule = 'spec_error'" if "mechanic_rule" in catalog_cols else ""
            q_misc = f"""
                SELECT
                    COALESCE(c.product, 'Unknown') as prod,
                    COUNT(*),
                    ROUND(SUM({'aws_od_cost' if 'aws_od_cost' in catalog_cols else 'aws_amortized_cost'}), 2)
                FROM aws_li_catalog c
                {workload_where} AND (
                    c.mechanic_group = 'misc'
                    {rule_clause}
                )
                GROUP BY prod
                ORDER BY 3 DESC
            """
            for p_name, p_cnt, p_usd in con.execute(q_misc).fetchall():
                u = float(p_usd or 0.0)
                n = int(p_cnt or 0)
                misc_data["total_usd"] += u
                misc_data["total_rows"] += n
                misc_data["by_service"].append({"product": p_name, "rows": n, "usd": u})
            misc_data["total_usd"] = round(misc_data["total_usd"], 2)

        llm_routed_data["total_usd"] = round(mix_data["llm"]["aws_od"], 2)
        llm_routed_data["total_rows"] = mix_data["llm"]["rows"]

        # -------------------------------------------------------------
        # 3. OD-Basis Mix
        # -------------------------------------------------------------
        od_basis_data = {
            "public_od":              {"usd": 0.0, "rows": 0, "pct_of_spend": 0.0},
            "reconstructed_resolved": {"usd": 0.0, "rows": 0, "pct_of_spend": 0.0},
            "reconstructed_default":  {"usd": 0.0, "rows": 0, "pct_of_spend": 0.0},
            "amortized_fallback":     {"usd": 0.0, "rows": 0, "pct_of_spend": 0.0},
        }
        basis_col = None
        basis_table = None
        if "od_basis" in proj_cols:
            basis_col = "p.od_basis"
            basis_table = "gcp_projection p"
        elif "od_basis" in catalog_cols:
            basis_col = "c.od_basis"
            basis_table = "aws_li_catalog c"

        if basis_col and basis_table:
            q_basis = f"""
                WITH item_basis AS (
                    SELECT
                        aws_li_key,
                        ANY_VALUE(COALESCE({basis_col}, 'amortized_fallback')) as b_val,
                        ANY_VALUE({'aws_od_cost' if 'aws_od_cost' in catalog_cols else 'aws_amortized_cost'}) as cost
                    FROM {basis_table}
                    {'WHERE is_workload' if 'is_workload' in (proj_cols if 'gcp_projection' in basis_table else catalog_cols) else ''}
                    GROUP BY aws_li_key
                )
                SELECT
                    b_val,
                    COUNT(*),
                    ROUND(SUM(cost), 2)
                FROM item_basis
                GROUP BY b_val
            """
            for b_name, b_cnt, b_usd in con.execute(q_basis).fetchall():
                b_key = b_name if b_name in od_basis_data else "amortized_fallback"
                od_basis_data[b_key]["rows"] += int(b_cnt)
                od_basis_data[b_key]["usd"] += float(b_usd or 0.0)

            for b_key in od_basis_data:
                od_basis_data[b_key]["usd"] = round(od_basis_data[b_key]["usd"], 2)
                od_basis_data[b_key]["pct_of_spend"] = round(
                    _safe_div(od_basis_data[b_key]["usd"], tot_aws_od) * 100.0, 2
                )
        else:
            # Fallback: all amortized
            od_basis_data["amortized_fallback"]["usd"] = tot_aws_od
            od_basis_data["amortized_fallback"]["rows"] = tot_rows
            od_basis_data["amortized_fallback"]["pct_of_spend"] = 100.0

        # -------------------------------------------------------------
        # 4. Ratio Distribution (GCP OD / AWS OD)
        # -------------------------------------------------------------
        ratio_data = {
            "over_3x": {"rows": 0, "usd_aws_od": 0.0, "usd_gcp": 0.0, "max_ratio": 0.0, "top_services": []},
            "over_10x": {"rows": 0, "usd_aws_od": 0.0, "usd_gcp": 0.0, "max_ratio": 0.0, "top_services": []},
            "under_0_2x": {"rows": 0, "usd_aws_od": 0.0, "usd_gcp": 0.0, "min_ratio": 0.0},
            "max_outlier": None,
        }

        if has_proj:
            od_val = "p.aws_od_cost" if "aws_od_cost" in proj_cols else "p.aws_amortized_cost"
            prod_val = "COALESCE(p.product, 'Unknown')"
            ut_val = "COALESCE(c.usage_type, '')" if "usage_type" in catalog_cols else "''"
            join_c = "JOIN aws_li_catalog c USING (aws_li_key)" if "usage_type" in catalog_cols else ""

            q_ratios = f"""
                WITH item_ratios AS (
                    SELECT
                        p.aws_li_key,
                        ANY_VALUE({prod_val}) as prod,
                        ANY_VALUE({ut_val}) as ut,
                        ANY_VALUE({od_val}) as aws_od,
                        SUM(p.gcp_projected_cost) as gcp_cost,
                        ROUND(SUM(p.gcp_projected_cost) / NULLIF(ANY_VALUE({od_val}), 0), 3) as ratio
                    FROM gcp_projection p
                    {join_c}
                    {'WHERE p.is_workload' if 'is_workload' in proj_cols else 'WHERE 1=1'}
                    GROUP BY p.aws_li_key
                    HAVING ANY_VALUE({od_val}) > 0.01
                )
                SELECT
                    aws_li_key, prod, ut, aws_od, gcp_cost, ratio
                FROM item_ratios
                ORDER BY ratio DESC
            """
            r_rows = con.execute(q_ratios).fetchall()
            if r_rows:
                top_r = r_rows[0]
                ratio_data["max_outlier"] = {
                    "aws_li_key": top_r[0],
                    "product": top_r[1],
                    "usage_type": top_r[2],
                    "aws_od": float(top_r[3] or 0.0),
                    "gcp_projected": float(top_r[4] or 0.0),
                    "ratio": float(top_r[5] or 0.0),
                }

                svc_3x: Dict[str, float] = {}
                svc_10x: Dict[str, float] = {}
                min_ratio_seen = 999999.0

                for r in r_rows:
                    _key, prod, _ut, a_od, g_cost, rat = r
                    if rat is None:
                        continue
                    if rat > 3.0:
                        ratio_data["over_3x"]["rows"] += 1
                        ratio_data["over_3x"]["usd_aws_od"] += float(a_od or 0.0)
                        ratio_data["over_3x"]["usd_gcp"] += float(g_cost or 0.0)
                        if rat > ratio_data["over_3x"]["max_ratio"]:
                            ratio_data["over_3x"]["max_ratio"] = float(rat)
                        svc_3x[prod] = svc_3x.get(prod, 0.0) + float(g_cost or 0.0)

                    if rat > 10.0:
                        ratio_data["over_10x"]["rows"] += 1
                        ratio_data["over_10x"]["usd_aws_od"] += float(a_od or 0.0)
                        ratio_data["over_10x"]["usd_gcp"] += float(g_cost or 0.0)
                        if rat > ratio_data["over_10x"]["max_ratio"]:
                            ratio_data["over_10x"]["max_ratio"] = float(rat)
                        svc_10x[prod] = svc_10x.get(prod, 0.0) + float(g_cost or 0.0)

                    if rat < 0.2:
                        ratio_data["under_0_2x"]["rows"] += 1
                        ratio_data["under_0_2x"]["usd_aws_od"] += float(a_od or 0.0)
                        ratio_data["under_0_2x"]["usd_gcp"] += float(g_cost or 0.0)
                        if rat < min_ratio_seen:
                            min_ratio_seen = float(rat)

                ratio_data["over_3x"]["usd_aws_od"] = round(ratio_data["over_3x"]["usd_aws_od"], 2)
                ratio_data["over_3x"]["usd_gcp"] = round(ratio_data["over_3x"]["usd_gcp"], 2)
                ratio_data["over_3x"]["top_services"] = [
                    {"product": k, "gcp_cost": round(v, 2)}
                    for k, v in sorted(svc_3x.items(), key=lambda kv: -kv[1])[:5]
                ]

                ratio_data["over_10x"]["usd_aws_od"] = round(ratio_data["over_10x"]["usd_aws_od"], 2)
                ratio_data["over_10x"]["usd_gcp"] = round(ratio_data["over_10x"]["usd_gcp"], 2)
                ratio_data["over_10x"]["top_services"] = [
                    {"product": k, "gcp_cost": round(v, 2)}
                    for k, v in sorted(svc_10x.items(), key=lambda kv: -kv[1])[:5]
                ]

                ratio_data["under_0_2x"]["usd_aws_od"] = round(ratio_data["under_0_2x"]["usd_aws_od"], 2)
                ratio_data["under_0_2x"]["usd_gcp"] = round(ratio_data["under_0_2x"]["usd_gcp"], 2)
                if min_ratio_seen < 999999.0:
                    ratio_data["under_0_2x"]["min_ratio"] = round(min_ratio_seen, 3)

        # -------------------------------------------------------------
        # 5. Guard Events (from diagnostics.jsonl & validation_report)
        # -------------------------------------------------------------
        guard_data = {
            "unit_mismatch":  {"events": 0, "rows": 0, "usd": 0.0},
            "unit_unknown":   {"events": 0, "rows": 0, "usd": 0.0},
            "echo":           {"events": 0, "rows": 0, "usd": 0.0},
            "allowed_target": {"events": 0, "rows": 0, "usd": 0.0},
            "outlier_clamped":{"events": 0, "rows": 0, "usd": 0.0},
            "error_count": 0,
            "warn_count": 0,
            "total_diag_usd": 0.0,
        }
        recs = read_diagnostics(job_dir)
        for r in recs:
            lvl = r.get("level")
            code = r.get("code", "")
            usd = float(r.get("usd") or 0.0)
            rows = int(r.get("rows") or 0)

            if lvl == "error":
                guard_data["error_count"] += 1
            elif lvl == "warn":
                guard_data["warn_count"] += 1
            guard_data["total_diag_usd"] += usd

            if code == "unit_mismatch":
                guard_data["unit_mismatch"]["events"] += 1
                guard_data["unit_mismatch"]["rows"] += rows
                guard_data["unit_mismatch"]["usd"] += usd
            elif code == "unit_unknown":
                guard_data["unit_unknown"]["events"] += 1
                guard_data["unit_unknown"]["rows"] += rows
                guard_data["unit_unknown"]["usd"] += usd
            elif code in ("gcp_service_echo", "echo"):
                guard_data["echo"]["events"] += 1
                guard_data["echo"]["rows"] += rows
                guard_data["echo"]["usd"] += usd
            elif "allowed_target" in code:
                guard_data["allowed_target"]["events"] += 1
                guard_data["allowed_target"]["rows"] += rows
                guard_data["allowed_target"]["usd"] += usd
            elif code == "outlier_clamped":
                guard_data["outlier_clamped"]["events"] += 1
                guard_data["outlier_clamped"]["rows"] += rows
                guard_data["outlier_clamped"]["usd"] += usd

        # Check validation_report.json if present
        val_rep_path = os.path.join(job_dir, "validation_report.json")
        if os.path.exists(val_rep_path):
            try:
                with open(val_rep_path, "r", encoding="utf-8") as vf:
                    val_rep = json.load(vf)
                viols = val_rep.get("violations", {})
                if "gcp_service_echo" in viols and guard_data["echo"]["events"] == 0:
                    echo_rows = viols["gcp_service_echo"]
                    guard_data["echo"]["events"] = len(echo_rows)
                    guard_data["echo"]["rows"] = len(echo_rows)
            except Exception:
                pass

        for k in ("unit_mismatch", "unit_unknown", "echo", "allowed_target", "outlier_clamped"):
            guard_data[k]["usd"] = round(guard_data[k]["usd"], 2)
        guard_data["total_diag_usd"] = round(guard_data["total_diag_usd"], 2)

        # -------------------------------------------------------------
        # 6. Service Group Cost Breakdown (for Diff Mode)
        # -------------------------------------------------------------
        service_groups = {}
        if has_proj:
            od_expr = "p.aws_od_cost" if "aws_od_cost" in proj_cols else "p.aws_amortized_cost"
            q_svc = f"""
                SELECT
                    COALESCE(p.product, 'Other') as prod,
                    COUNT(DISTINCT p.aws_li_key) as li_count,
                    ROUND(SUM({od_expr}), 2) as aws_od_cost,
                    ROUND(SUM(p.gcp_projected_cost), 2) as gcp_cost,
                    STRING_AGG(DISTINCT p.strategy, ', ') as strategies
                FROM gcp_projection p
                {'WHERE p.is_workload' if 'is_workload' in proj_cols else ''}
                GROUP BY prod
                ORDER BY 3 DESC
            """
            for s_prod, s_cnt, s_aws, s_gcp, s_strat in con.execute(q_svc).fetchall():
                service_groups[s_prod] = {
                    "rows": int(s_cnt),
                    "aws_od_cost": float(s_aws or 0.0),
                    "gcp_projected_cost": float(s_gcp or 0.0),
                    "ratio": round(_safe_div(float(s_gcp or 0.0), float(s_aws or 0.0)), 2),
                    "strategies": s_strat or "",
                }

        con.close()

        scorecard = {
            "job": job_name,
            "db_path": db_path,
            "job_dir": job_dir,
            "status": "ok",
            "headline": {
                "workload_rows": tot_rows,
                "aws_amortized_cost": round(tot_aws_amortized, 2),
                "aws_od_cost": round(tot_aws_od, 2),
                "gcp_projected_cost": round(tot_gcp_projected, 2),
                "overall_ratio": round(_safe_div(tot_gcp_projected, tot_aws_od), 3),
            },
            "coverage_mix": mix_data,
            "determinism_holes": {
                "spec_gap": spec_gap_data,
                "misc": misc_data,
                "llm_routed": llm_routed_data,
            },
            "od_basis_mix": od_basis_data,
            "ratio_distribution": ratio_data,
            "guard_events": guard_data,
            "service_groups": service_groups,
        }
        return scorecard

    except Exception as exc:
        con.close()
        return {
            "job": job_name,
            "db_path": db_path,
            "job_dir": job_dir,
            "error": f"Exception processing scorecard: {exc}",
            "status": "error",
        }


def compute_portfolio_scorecard(scorecards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll up multiple job scorecards into a combined portfolio scorecard."""
    valid_scs = [s for s in scorecards if s.get("status") == "ok"]
    if not valid_scs:
        return {"portfolio": True, "status": "no_valid_runs", "jobs_count": len(scorecards)}

    tot_rows = sum(s["headline"]["workload_rows"] for s in valid_scs)
    tot_aws_amortized = sum(s["headline"]["aws_amortized_cost"] for s in valid_scs)
    tot_aws_od = sum(s["headline"]["aws_od_cost"] for s in valid_scs)
    tot_gcp_projected = sum(s["headline"]["gcp_projected_cost"] for s in valid_scs)

    # Coverage mix rollup
    mix_data = {
        "deterministic": {"aws_amortized": 0.0, "aws_od": 0.0, "gcp_projected": 0.0, "rows": 0, "pct_of_spend": 0.0},
        "llm":           {"aws_amortized": 0.0, "aws_od": 0.0, "gcp_projected": 0.0, "rows": 0, "pct_of_spend": 0.0},
        "passthrough":   {"aws_amortized": 0.0, "aws_od": 0.0, "gcp_projected": 0.0, "rows": 0, "pct_of_spend": 0.0},
        "ignored":       {"aws_amortized": 0.0, "aws_od": 0.0, "gcp_projected": 0.0, "rows": 0, "pct_of_spend": 0.0},
        "unmapped":      {"aws_amortized": 0.0, "aws_od": 0.0, "gcp_projected": 0.0, "rows": 0, "pct_of_spend": 0.0},
    }
    for cat in mix_data:
        mix_data[cat]["rows"] = sum(s["coverage_mix"][cat]["rows"] for s in valid_scs)
        mix_data[cat]["aws_amortized"] = round(sum(s["coverage_mix"][cat]["aws_amortized"] for s in valid_scs), 2)
        mix_data[cat]["aws_od"] = round(sum(s["coverage_mix"][cat]["aws_od"] for s in valid_scs), 2)
        mix_data[cat]["gcp_projected"] = round(sum(s["coverage_mix"][cat]["gcp_projected"] for s in valid_scs), 2)
        mix_data[cat]["pct_of_spend"] = round(_safe_div(mix_data[cat]["aws_od"], tot_aws_od) * 100.0, 2)

    # OD-basis mix rollup
    od_basis_data = {
        "public_od":             {"usd": 0.0, "rows": 0, "pct_of_spend": 0.0},
        "reconstructed_default": {"usd": 0.0, "rows": 0, "pct_of_spend": 0.0},
        "amortized_fallback":    {"usd": 0.0, "rows": 0, "pct_of_spend": 0.0},
    }
    for k in od_basis_data:
        od_basis_data[k]["rows"] = sum(s["od_basis_mix"][k]["rows"] for s in valid_scs)
        od_basis_data[k]["usd"] = round(sum(s["od_basis_mix"][k]["usd"] for s in valid_scs), 2)
        od_basis_data[k]["pct_of_spend"] = round(_safe_div(od_basis_data[k]["usd"], tot_aws_od) * 100.0, 2)

    # Determinism holes rollup
    spec_gap_usd = round(sum(s["determinism_holes"]["spec_gap"]["total_usd"] for s in valid_scs), 2)
    spec_gap_rows = sum(s["determinism_holes"]["spec_gap"]["total_rows"] for s in valid_scs)
    sg_by_svc: Dict[str, Dict[str, Any]] = {}
    for s in valid_scs:
        for item in s["determinism_holes"]["spec_gap"]["by_service"]:
            svc = item["service"]
            e = sg_by_svc.setdefault(svc, {"service": svc, "rows": 0, "usd": 0.0})
            e["rows"] += item["rows"]
            e["usd"] = round(e["usd"] + item["usd"], 2)

    misc_usd = round(sum(s["determinism_holes"]["misc"]["total_usd"] for s in valid_scs), 2)
    misc_rows = sum(s["determinism_holes"]["misc"]["total_rows"] for s in valid_scs)
    misc_by_prod: Dict[str, Dict[str, Any]] = {}
    for s in valid_scs:
        for item in s["determinism_holes"]["misc"]["by_service"]:
            prod = item["product"]
            e = misc_by_prod.setdefault(prod, {"product": prod, "rows": 0, "usd": 0.0})
            e["rows"] += item["rows"]
            e["usd"] = round(e["usd"] + item["usd"], 2)

    llm_routed_usd = round(sum(s["determinism_holes"]["llm_routed"]["total_usd"] for s in valid_scs), 2)
    llm_routed_rows = sum(s["determinism_holes"]["llm_routed"]["total_rows"] for s in valid_scs)

    # Ratio distribution rollup
    ratio_data = {
        "over_3x": {
            "rows": sum(s["ratio_distribution"]["over_3x"]["rows"] for s in valid_scs),
            "usd_aws_od": round(sum(s["ratio_distribution"]["over_3x"]["usd_aws_od"] for s in valid_scs), 2),
            "usd_gcp": round(sum(s["ratio_distribution"]["over_3x"]["usd_gcp"] for s in valid_scs), 2),
            "max_ratio": max([s["ratio_distribution"]["over_3x"]["max_ratio"] for s in valid_scs] or [0.0]),
            "top_services": [],
        },
        "over_10x": {
            "rows": sum(s["ratio_distribution"]["over_10x"]["rows"] for s in valid_scs),
            "usd_aws_od": round(sum(s["ratio_distribution"]["over_10x"]["usd_aws_od"] for s in valid_scs), 2),
            "usd_gcp": round(sum(s["ratio_distribution"]["over_10x"]["usd_gcp"] for s in valid_scs), 2),
            "max_ratio": max([s["ratio_distribution"]["over_10x"]["max_ratio"] for s in valid_scs] or [0.0]),
            "top_services": [],
        },
        "under_0_2x": {
            "rows": sum(s["ratio_distribution"]["under_0_2x"]["rows"] for s in valid_scs),
            "usd_aws_od": round(sum(s["ratio_distribution"]["under_0_2x"]["usd_aws_od"] for s in valid_scs), 2),
            "usd_gcp": round(sum(s["ratio_distribution"]["under_0_2x"]["usd_gcp"] for s in valid_scs), 2),
            "min_ratio": min([s["ratio_distribution"]["under_0_2x"]["min_ratio"] for s in valid_scs if s["ratio_distribution"]["under_0_2x"]["min_ratio"] > 0] or [0.0]),
        },
        "max_outlier": None,
    }
    # Find portfolio max outlier
    outliers = [s["ratio_distribution"]["max_outlier"] for s in valid_scs if s["ratio_distribution"]["max_outlier"]]
    if outliers:
        ratio_data["max_outlier"] = sorted(outliers, key=lambda o: -o["ratio"])[0]

    # Guard events rollup
    guard_data = {
        "unit_mismatch":  {"events": 0, "rows": 0, "usd": 0.0},
        "unit_unknown":   {"events": 0, "rows": 0, "usd": 0.0},
        "echo":           {"events": 0, "rows": 0, "usd": 0.0},
        "allowed_target": {"events": 0, "rows": 0, "usd": 0.0},
        "outlier_clamped":{"events": 0, "rows": 0, "usd": 0.0},
        "error_count": sum(s["guard_events"]["error_count"] for s in valid_scs),
        "warn_count": sum(s["guard_events"]["warn_count"] for s in valid_scs),
        "total_diag_usd": round(sum(s["guard_events"]["total_diag_usd"] for s in valid_scs), 2),
    }
    for k in ("unit_mismatch", "unit_unknown", "echo", "allowed_target", "outlier_clamped"):
        guard_data[k]["events"] = sum(s["guard_events"][k]["events"] for s in valid_scs)
        guard_data[k]["rows"] = sum(s["guard_events"][k]["rows"] for s in valid_scs)
        guard_data[k]["usd"] = round(sum(s["guard_events"][k]["usd"] for s in valid_scs), 2)

    return {
        "portfolio": True,
        "jobs_count": len(valid_scs),
        "status": "ok",
        "headline": {
            "workload_rows": tot_rows,
            "aws_amortized_cost": round(tot_aws_amortized, 2),
            "aws_od_cost": round(tot_aws_od, 2),
            "gcp_projected_cost": round(tot_gcp_projected, 2),
            "overall_ratio": round(_safe_div(tot_gcp_projected, tot_aws_od), 3),
        },
        "coverage_mix": mix_data,
        "determinism_holes": {
            "spec_gap": {
                "total_usd": spec_gap_usd,
                "total_rows": spec_gap_rows,
                "by_service": sorted(sg_by_svc.values(), key=lambda x: -x["usd"])[:10],
            },
            "misc": {
                "total_usd": misc_usd,
                "total_rows": misc_rows,
                "by_service": sorted(misc_by_prod.values(), key=lambda x: -x["usd"])[:10],
            },
            "llm_routed": {"total_usd": llm_routed_usd, "total_rows": llm_routed_rows},
        },
        "od_basis_mix": od_basis_data,
        "ratio_distribution": ratio_data,
        "guard_events": guard_data,
    }


def compute_diff(run_a: Dict[str, Any], run_b: Dict[str, Any]) -> Dict[str, Any]:
    """Compute run-vs-run delta across all metrics and highlight moved service groups."""
    h_a = run_a.get("headline", {})
    h_b = run_b.get("headline", {})

    cost_delta = round(h_b.get("gcp_projected_cost", 0.0) - h_a.get("gcp_projected_cost", 0.0), 2)
    ratio_delta = round(h_b.get("overall_ratio", 0.0) - h_a.get("overall_ratio", 0.0), 3)

    coverage_deltas = {}
    for cat in ("deterministic", "llm", "passthrough", "ignored", "unmapped"):
        p_a = run_a.get("coverage_mix", {}).get(cat, {}).get("pct_of_spend", 0.0)
        p_b = run_b.get("coverage_mix", {}).get(cat, {}).get("pct_of_spend", 0.0)
        u_a = run_a.get("coverage_mix", {}).get(cat, {}).get("aws_od", 0.0)
        u_b = run_b.get("coverage_mix", {}).get(cat, {}).get("aws_od", 0.0)
        coverage_deltas[cat] = {
            "baseline_pct": p_a,
            "new_pct": p_b,
            "pct_delta": round(p_b - p_a, 2),
            "baseline_usd": u_a,
            "new_usd": u_b,
            "usd_delta": round(u_b - u_a, 2),
        }

    od_basis_deltas = {}
    for k in ("public_od", "reconstructed_resolved", "reconstructed_default", "amortized_fallback"):
        p_a = run_a.get("od_basis_mix", {}).get(k, {}).get("pct_of_spend", 0.0)
        p_b = run_b.get("od_basis_mix", {}).get(k, {}).get("pct_of_spend", 0.0)
        u_a = run_a.get("od_basis_mix", {}).get(k, {}).get("usd", 0.0)
        u_b = run_b.get("od_basis_mix", {}).get(k, {}).get("usd", 0.0)
        od_basis_deltas[k] = {
            "baseline_pct": p_a,
            "new_pct": p_b,
            "pct_delta": round(p_b - p_a, 2),
            "baseline_usd": u_a,
            "new_usd": u_b,
            "usd_delta": round(u_b - u_a, 2),
        }

    # Determinism holes delta
    spec_gap_delta = round(
        run_b.get("determinism_holes", {}).get("spec_gap", {}).get("total_usd", 0.0)
        - run_a.get("determinism_holes", {}).get("spec_gap", {}).get("total_usd", 0.0),
        2,
    )
    misc_delta = round(
        run_b.get("determinism_holes", {}).get("misc", {}).get("total_usd", 0.0)
        - run_a.get("determinism_holes", {}).get("misc", {}).get("total_usd", 0.0),
        2,
    )
    llm_delta = round(
        run_b.get("determinism_holes", {}).get("llm_routed", {}).get("total_usd", 0.0)
        - run_a.get("determinism_holes", {}).get("llm_routed", {}).get("total_usd", 0.0),
        2,
    )
    det_cov_a = run_a.get("coverage_mix", {}).get("deterministic", {}).get("pct_of_spend", 0.0)
    det_cov_b = run_b.get("coverage_mix", {}).get("deterministic", {}).get("pct_of_spend", 0.0)
    det_cov_delta = round(det_cov_b - det_cov_a, 2)

    # Trend verdict: improving = deterministic coverage up AND (spec_gap + misc + llm) down.
    nondet_delta = spec_gap_delta + misc_delta + llm_delta
    if det_cov_delta > 0.5 and nondet_delta < -0.01:
        trend = "improving"
    elif det_cov_delta < -0.5 or nondet_delta > 0.01:
        trend = "worsening"
    else:
        trend = "flat"

    # Ratio delta
    r3_a = run_a.get("ratio_distribution", {}).get("over_3x", {}).get("usd_gcp", 0.0)
    r3_b = run_b.get("ratio_distribution", {}).get("over_3x", {}).get("usd_gcp", 0.0)
    r10_a = run_a.get("ratio_distribution", {}).get("over_10x", {}).get("usd_gcp", 0.0)
    r10_b = run_b.get("ratio_distribution", {}).get("over_10x", {}).get("usd_gcp", 0.0)

    # Moved service groups
    svc_a = run_a.get("service_groups", {})
    svc_b = run_b.get("service_groups", {})
    all_svcs = sorted(set(svc_a.keys()) | set(svc_b.keys()))

    moved_services = []
    for s_name in all_svcs:
        item_a = svc_a.get(s_name, {"aws_od_cost": 0.0, "gcp_projected_cost": 0.0, "strategies": ""})
        item_b = svc_b.get(s_name, {"aws_od_cost": 0.0, "gcp_projected_cost": 0.0, "strategies": ""})
        g_diff = round(item_b["gcp_projected_cost"] - item_a["gcp_projected_cost"], 2)
        strat_changed = item_a["strategies"] != item_b["strategies"]

        if abs(g_diff) > 0.01 or strat_changed:
            moved_services.append({
                "service": s_name,
                "aws_od_cost": item_b["aws_od_cost"] or item_a["aws_od_cost"],
                "baseline_gcp": item_a["gcp_projected_cost"],
                "new_gcp": item_b["gcp_projected_cost"],
                "delta_usd": g_diff,
                "delta_pct": round(
                    _safe_div(g_diff, item_a["gcp_projected_cost"]) * 100.0, 1
                ) if item_a["gcp_projected_cost"] else 0.0,
                "baseline_strategies": item_a["strategies"],
                "new_strategies": item_b["strategies"],
            })

    moved_services.sort(key=lambda s: -abs(s["delta_usd"]))

    return {
        "run_a": run_a.get("job"),
        "run_b": run_b.get("job"),
        "headline_delta": {
            "baseline_aws_od": h_a.get("aws_od_cost", 0.0),
            "new_aws_od": h_b.get("aws_od_cost", 0.0),
            "baseline_gcp_projected": h_a.get("gcp_projected_cost", 0.0),
            "new_gcp_projected": h_b.get("gcp_projected_cost", 0.0),
            "gcp_projected_delta": cost_delta,
            "baseline_ratio": h_a.get("overall_ratio", 0.0),
            "new_ratio": h_b.get("overall_ratio", 0.0),
            "ratio_delta": ratio_delta,
        },
        "coverage_mix_deltas": coverage_deltas,
        "od_basis_deltas": od_basis_deltas,
        "determinism_holes_deltas": {
            "spec_gap_usd_delta": spec_gap_delta,
            "misc_usd_delta": misc_delta,
            "llm_usd_delta": llm_delta,
        },
        "determinism_trend": {
            "baseline_deterministic_pct": det_cov_a,
            "new_deterministic_pct": det_cov_b,
            "deterministic_pct_delta": det_cov_delta,
            "nondeterministic_usd_delta": round(nondet_delta, 2),
            "verdict": trend,
        },
        "ratio_deltas": {
            "over_3x_gcp_delta": round(r3_b - r3_a, 2),
            "over_10x_gcp_delta": round(r10_b - r10_a, 2),
        },
        "moved_services": moved_services,
    }


def format_scorecard_text(sc: Dict[str, Any]) -> str:
    """Format a scorecard as human-readable text."""
    if sc.get("status") != "ok":
        return f"=== ACCURACY SCORECARD: {sc.get('job')} ===\nERROR: {sc.get('error', 'unknown error')}\n"

    h = sc["headline"]
    lines = []
    lines.append("=" * 76)
    lines.append(f"ACCURACY SCORECARD: {sc.get('job', 'Portfolio Rollup')}")
    lines.append("=" * 76)
    lines.append(f"Workload Rows:       {h['workload_rows']:,}")
    lines.append(f"AWS Amortized Cost:  ${h['aws_amortized_cost']:,.2f}")
    lines.append(f"AWS On-Demand Cost:  ${h['aws_od_cost']:,.2f}")
    lines.append(f"GCP Projected Cost:  ${h['gcp_projected_cost']:,.2f}")
    lines.append(f"Headline Ratio:      {h['overall_ratio']:.3f}x")
    lines.append("-" * 76)

    # 1. Coverage Mix
    lines.append("1. COVERAGE MIX ($-weighted by AWS On-Demand):")
    lines.append(f"  {'Category':<18} {'AWS OD $':>13} {'% Spend':>9} {'GCP Proj $':>13} {'Rows':>8}")
    mix = sc["coverage_mix"]
    for cat in ("deterministic", "llm", "passthrough", "ignored", "unmapped"):
        m = mix[cat]
        lines.append(f"  {cat.capitalize():<18} ${m['aws_od']:>12,.2f} {m['pct_of_spend']:>8.1f}% ${m['gcp_projected']:>12,.2f} {m['rows']:>8,}")
    lines.append("-" * 76)

    # 2. Determinism Holes
    holes = sc["determinism_holes"]
    sg = holes["spec_gap"]
    misc = holes["misc"]
    llm = holes["llm_routed"]
    lines.append("2. DETERMINISM HOLES:")
    lines.append(f"  Spec Gap Spend:    ${sg['total_usd']:,.2f} across {sg['total_rows']} rows")
    if sg["by_service"]:
        for item in sg["by_service"][:5]:
            lines.append(f"    - {item['service']:<24} ${item['usd']:>10,.2f} ({item['rows']} rows)")
    lines.append(f"  Misc Group Spend:  ${misc['total_usd']:,.2f} across {misc['total_rows']} rows")
    if misc["by_service"]:
        for item in misc["by_service"][:5]:
            lines.append(f"    - {item['product']:<24} ${item['usd']:>10,.2f} ({item['rows']} rows)")
    lines.append(f"  Routed to LLM:     ${llm['total_usd']:,.2f} across {llm['total_rows']} rows")
    lines.append("-" * 76)

    # 3. OD-Basis Mix
    lines.append("3. OD-BASIS MIX (Public List vs Estimation):")
    lines.append(f"  {'Basis Type':<26} {'AWS OD $':>13} {'% Spend':>9} {'Rows':>8}")
    ob = sc["od_basis_mix"]
    for k in ("public_od", "reconstructed_resolved", "reconstructed_default", "amortized_fallback"):
        m = ob[k]
        lines.append(f"  {k:<26} ${m['usd']:>12,.2f} {m['pct_of_spend']:>8.1f}% {m['rows']:>8,}")
    lines.append("-" * 76)

    # 4. Ratio Distribution
    r = sc["ratio_distribution"]
    lines.append("4. RATIO DISTRIBUTION (GCP / AWS OD):")
    lines.append(f"  Ratio > 3x:        {r['over_3x']['rows']} rows, AWS: ${r['over_3x']['usd_aws_od']:,.2f}, GCP: ${r['over_3x']['usd_gcp']:,.2f} (max {r['over_3x']['max_ratio']:.1f}x)")
    lines.append(f"  Ratio > 10x:       {r['over_10x']['rows']} rows, AWS: ${r['over_10x']['usd_aws_od']:,.2f}, GCP: ${r['over_10x']['usd_gcp']:,.2f} (max {r['over_10x']['max_ratio']:.1f}x)")
    lines.append(f"  Ratio < 0.2x:      {r['under_0_2x']['rows']} rows, AWS: ${r['under_0_2x']['usd_aws_od']:,.2f}, GCP: ${r['under_0_2x']['usd_gcp']:,.2f}")
    if r["max_outlier"]:
        mo = r["max_outlier"]
        lines.append(f"  Top Outlier:       {mo['ratio']:.1f}x on {mo['product']} ({mo['usage_type']}) AWS: ${mo['aws_od']:,.2f} -> GCP: ${mo['gcp_projected']:,.2f}")
    lines.append("-" * 76)

    # 5. Guard Events
    g = sc["guard_events"]
    lines.append("5. GUARD & DIAGNOSTIC EVENTS:")
    lines.append(f"  Unit Mismatch:     {g['unit_mismatch']['events']} events (${g['unit_mismatch']['usd']:,.2f} affected)")
    lines.append(f"  Unit Unknown:      {g['unit_unknown']['events']} events (${g['unit_unknown']['usd']:,.2f} affected)")
    lines.append(f"  Service Echo:      {g['echo']['events']} events (${g['echo']['usd']:,.2f} affected)")
    lines.append(f"  Allowed Target:    {g['allowed_target']['events']} events (${g['allowed_target']['usd']:,.2f} affected)")
    lines.append(f"  Total Diagnostics: {g['error_count']} errors, {g['warn_count']} warnings (${g['total_diag_usd']:,.2f} affected)")
    lines.append("=" * 76)
    return "\n".join(lines)


def format_diff_text(diff: Dict[str, Any]) -> str:
    """Format run-vs-run diff as human-readable text."""
    lines = []
    lines.append("=" * 76)
    lines.append(f"ACCURACY RUN DIFF: {diff.get('run_a')} (Baseline) vs {diff.get('run_b')} (New)")
    lines.append("=" * 76)
    hd = diff["headline_delta"]
    lines.append(f"GCP Projected Cost:  ${hd['baseline_gcp_projected']:,.2f} -> ${hd['new_gcp_projected']:,.2f} (Diff: ${hd['gcp_projected_delta']:+,.2f})")
    lines.append(f"Headline Ratio:      {hd['baseline_ratio']:.3f}x -> {hd['new_ratio']:.3f}x (Diff: {hd['ratio_delta']:+.3f}x)")
    lines.append("-" * 76)

    lines.append("COVERAGE MIX SHIFTS (% of AWS On-Demand spend):")
    lines.append(f"  {'Category':<18} {'Baseline %':>12} {'New %':>10} {'Delta %':>10} {'Delta $':>13}")
    cm = diff["coverage_mix_deltas"]
    for cat in ("deterministic", "llm", "passthrough", "ignored", "unmapped"):
        m = cm[cat]
        lines.append(f"  {cat.capitalize():<18} {m['baseline_pct']:>11.1f}% {m['new_pct']:>9.1f}% {m['pct_delta']:>+9.1f}% ${m['usd_delta']:>+12,.2f}")
    lines.append("-" * 76)

    lines.append("OD-BASIS SHIFTS:")
    ob = diff["od_basis_deltas"]
    for k in ("public_od", "reconstructed_resolved", "reconstructed_default", "amortized_fallback"):
        m = ob[k]
        lines.append(f"  {k:<26} {m['baseline_pct']:>5.1f}% -> {m['new_pct']:>5.1f}% ({m['pct_delta']:>+5.1f}%) | Diff: ${m['usd_delta']:>+10,.2f}")
    lines.append("-" * 76)

    dh = diff["determinism_holes_deltas"]
    dt = diff.get("determinism_trend", {})
    lines.append("DETERMINISM TREND (refresh-loop feedback):")
    lines.append(f"  Deterministic Coverage: {dt.get('baseline_deterministic_pct', 0):.1f}% -> {dt.get('new_deterministic_pct', 0):.1f}% ({dt.get('deterministic_pct_delta', 0):+.1f}%)")
    lines.append(f"  Spec Gap Spend Diff:    ${dh.get('spec_gap_usd_delta', 0):+,.2f}")
    lines.append(f"  Misc Spend Diff:        ${dh.get('misc_usd_delta', 0):+,.2f}")
    lines.append(f"  LLM Routed Spend Diff:  ${dh.get('llm_usd_delta', 0):+,.2f}")
    verdict = dt.get("verdict", "flat")
    verdict_mark = {"improving": "improving", "worsening": "worsening", "flat": "flat"}[verdict]
    lines.append(f"  Trend Verdict:        {verdict_mark}")
    lines.append("-" * 76)

    rd = diff["ratio_deltas"]
    lines.append("RATIO EXTREMES DELTA:")
    lines.append(f"  GCP Spend > 3x Diff:  ${rd['over_3x_gcp_delta']:+,.2f}")
    lines.append(f"  GCP Spend > 10x Diff: ${rd['over_10x_gcp_delta']:+,.2f}")
    lines.append("-" * 76)

    mv = diff.get("moved_services", [])
    lines.append(f"SERVICE GROUPS MOVED ({len(mv)} services):")
    if mv:
        lines.append(f"  {'Service':<28} {'AWS OD $':>11} {'Baseline GCP':>13} {'New GCP':>13} {'Delta $':>12}")
        for s in mv[:15]:
            lines.append(f"  {s['service'][:28]:<28} ${s['aws_od_cost']:>10,.2f} ${s['baseline_gcp']:>12,.2f} ${s['new_gcp']:>12,.2f} ${s['delta_usd']:>+11,.2f}")
    else:
        lines.append("  No service groups moved.")
    lines.append("=" * 76)
    return "\n".join(lines)


def check_thresholds(
    sc: Dict[str, Any],
    min_deterministic_cov: float = 80.0,
    max_spec_gap_pct: float = 5.0,
    max_ratio_10x_pct: float = 2.0,
    max_guard_errors: int = 0,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Evaluate accuracy thresholds against a scorecard."""
    checks = []
    tot_aws_od = sc.get("headline", {}).get("aws_od_cost", 0.0)

    # 1. Deterministic coverage
    det_cov = sc.get("coverage_mix", {}).get("deterministic", {}).get("pct_of_spend", 0.0)
    passed_cov = det_cov >= min_deterministic_cov
    checks.append({
        "name": "min_deterministic_coverage",
        "threshold": f">= {min_deterministic_cov:.1f}%",
        "actual": f"{det_cov:.1f}%",
        "passed": passed_cov,
    })

    # 2. Spec gap spend %
    spec_gap_usd = sc.get("determinism_holes", {}).get("spec_gap", {}).get("total_usd", 0.0)
    spec_gap_pct = _safe_div(spec_gap_usd, tot_aws_od) * 100.0
    passed_sg = spec_gap_pct <= max_spec_gap_pct
    checks.append({
        "name": "max_spec_gap_spend_pct",
        "threshold": f"<= {max_spec_gap_pct:.1f}%",
        "actual": f"{spec_gap_pct:.1f}% (${spec_gap_usd:,.2f})",
        "passed": passed_sg,
    })

    # 3. Ratio > 10x spend %
    r10_usd = sc.get("ratio_distribution", {}).get("over_10x", {}).get("usd_aws_od", 0.0)
    r10_pct = _safe_div(r10_usd, tot_aws_od) * 100.0
    passed_r10 = r10_pct <= max_ratio_10x_pct
    checks.append({
        "name": "max_ratio_10x_spend_pct",
        "threshold": f"<= {max_ratio_10x_pct:.1f}%",
        "actual": f"{r10_pct:.1f}% (${r10_usd:,.2f})",
        "passed": passed_r10,
    })

    # 4. Diagnostic errors
    err_cnt = sc.get("guard_events", {}).get("error_count", 0)
    passed_err = err_cnt <= max_guard_errors
    checks.append({
        "name": "max_diagnostic_errors",
        "threshold": f"<= {max_guard_errors}",
        "actual": str(err_cnt),
        "passed": passed_err,
    })

    all_passed = all(c["passed"] for c in checks)
    return all_passed, checks


def main():
    parser = argparse.ArgumentParser(description="Accuracy ground-truth scorecard and diff harness.")
    parser.add_argument("targets", nargs="*", help="Job DB path(s) or job directory(ies).")
    parser.add_argument("--diff", nargs=2, metavar=("RUN_A", "RUN_B"), help="Compare two job runs.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    parser.add_argument("--check", action="store_true", help="Enforce accuracy thresholds and exit non-zero on breach.")
    parser.add_argument("--min-deterministic-cov", type=float, default=80.0, help="Min deterministic coverage percentage (default: 80.0).")
    parser.add_argument("--max-spec-gap-pct", type=float, default=5.0, help="Max spec gap spend percentage (default: 5.0).")
    parser.add_argument("--max-ratio-10x-pct", type=float, default=2.0, help="Max ratio >10x spend percentage (default: 2.0).")
    parser.add_argument("--max-guard-errors", type=int, default=0, help="Max diagnostic error events (default: 0).")

    args = parser.parse_args()

    # Diff Mode
    if args.diff:
        run_a_target, run_b_target = args.diff
        sc_a = compute_scorecard(run_a_target)
        sc_b = compute_scorecard(run_b_target)
        diff_res = compute_diff(sc_a, sc_b)
        if args.json:
            print(json.dumps(diff_res, indent=2))
        else:
            print(format_diff_text(diff_res))
        sys.exit(0)

    # Scorecard Mode
    if not args.targets:
        parser.print_help()
        sys.exit(1)

    scorecards = [compute_scorecard(t) for t in args.targets]

    if len(scorecards) == 1:
        result = scorecards[0]
    else:
        portfolio = compute_portfolio_scorecard(scorecards)
        result = {"portfolio": portfolio, "individual_jobs": scorecards}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if len(scorecards) == 1:
            print(format_scorecard_text(scorecards[0]))
        else:
            for sc in scorecards:
                print(format_scorecard_text(sc))
                print()
            print(format_scorecard_text(result["portfolio"]))

    # Threshold checks
    if args.check:
        sc_to_check = result["portfolio"] if len(scorecards) > 1 else scorecards[0]
        passed, check_details = check_thresholds(
            sc_to_check,
            min_deterministic_cov=args.min_deterministic_cov,
            max_spec_gap_pct=args.max_spec_gap_pct,
            max_ratio_10x_pct=args.max_ratio_10x_pct,
            max_guard_errors=args.max_guard_errors,
        )

        if not args.json:
            print("\nTHRESHOLD CHECK RESULTS:")
            for c in check_details:
                status_str = "PASS" if c["passed"] else "FAIL"
                print(f"  [{status_str}] {c['name']:<28} actual: {c['actual']:<16} threshold: {c['threshold']}")
            if passed:
                print("\nALL ACCURACY THRESHOLDS PASSED.")
            else:
                print("\nBREACH: ONE OR MORE ACCURACY THRESHOLDS FAILED.")

        if not passed:
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
