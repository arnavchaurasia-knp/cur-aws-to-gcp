#!/usr/bin/env python3
"""
apply_directives.py — Unified application phase for the Directive Layer.

Replaces per-mechanism writers (apply_review_fixes, apply_outlier_fixes,
apply_commitment_ignores, apply_architecture_notes) with ONE single apply phase:
1. Loads catalog rows & existing mappings from projection.duckdb.
2. Loads directive ledger (<job>/directives.json) + architecture disclosures.
3. Resolves overrides deterministically (specificity > recency).
4. Applies service, strategy, workload, and annotation changes to aws_li_to_gcp_li.
5. Stamps 'applied_directives' provenance column on aws_li_to_gcp_li.

Usage:
    python3 apply_directives.py <projection.duckdb> [--check-only] [--job-dir <DIR>]
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from directives import Directive, load_ledger, save_ledger
from directive_resolver import resolve, EffectiveOverride
from directives_from_legacy import convert_legacy_to_directives
import row_state as rs

try:
    from apply_static_mappings import _gp_family_by_label, _strict_resolve_sku
except ImportError:
    _gp_family_by_label = lambda label: None
    _strict_resolve_sku = lambda svc, desc, reg: None


def get_cud_mult(service: str, term: str) -> float:
    skill_dir = os.environ.get("SKILL_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cud_path = os.path.join(skill_dir, "data", "cud_pct.json")
    r1, r3 = 1.0, 1.0
    if os.path.exists(cud_path):
        try:
            with open(cud_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            eligible = set(data.get("cud_eligible", []))
            if service in eligible:
                val = data.get(service)
                if isinstance(val, dict):
                    r1 = float(val.get("1yr_multiplier", 1.0))
                    r3 = float(val.get("3yr_multiplier", 1.0))
                elif isinstance(val, (list, tuple)) and len(val) >= 2:
                    r1, r3 = float(val[0]), float(val[1])
            else:
                default_val = data.get("DEFAULT")
                if isinstance(default_val, dict):
                    r1 = float(default_val.get("1yr_multiplier", 1.0))
                    r3 = float(default_val.get("3yr_multiplier", 1.0))
                elif isinstance(default_val, (list, tuple)) and len(default_val) >= 2:
                    r1, r3 = float(default_val[0]), float(default_val[1])
        except Exception:
            pass
    return r3 if term == "cud_3yr" else r1

try:
    import duckdb
except ImportError:
    print("apply_directives: duckdb not installed; skipping", file=sys.stderr)
    sys.exit(0)


import re

def load_architecture_disclosures() -> List[Directive]:
    """Load architecture disclosures from data/architecture-notes.json as annotate directives."""
    skill_dir = os.environ.get("SKILL_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(skill_dir, "data", "architecture-notes.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        disclosures = data.get("disclosures", [])
        directives: List[Directive] = []
        for idx, d in enumerate(disclosures):
            note = (d.get("note") or "").strip()
            pats = d.get("product_ilike_patterns") or []
            if not note or not pats:
                continue
            # Convert SQL ILIKE %X% patterns to regex on product
            escaped = [re.escape(p.strip("%")) for p in pats]
            product_re = "|".join(escaped)
            selector: Dict[str, Any] = {"product_re": product_re}
            if d.get("gcp_service_ilike"):
                selector["gcp_service"] = d["gcp_service_ilike"].strip("%")

            directives.append(Directive(
                id=f"arch_note_{d.get('label', idx)}",
                author="system",
                source_utterance=f"Architecture disclosure: {d.get('label')}",
                selector=selector,
                action={"type": "annotate", "value": note},
                status="applied",
            ))
        return directives
    except Exception as exc:
        print(f"apply_directives: could not read architecture-notes.json: {exc}", file=sys.stderr)
        return []


def re_escape(s: str) -> str:
    # helper to preserve regex wildcards while escaping literal characters
    return s.replace("(", r"\(").replace(")", r"\)").replace("[", r"\[").replace("]", r"\]")


def apply_directives_to_db(
    db_path: str,
    check_only: bool = False,
    job_dir: Optional[str] = None,
    user_id: Optional[str] = None
) -> int:
    if not os.path.exists(db_path):
        print(f"apply_directives: DB {db_path} does not exist", file=sys.stderr)
        return 0

    if not job_dir:
        # Infer job_dir from db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if os.path.basename(parent) == "projection-audit":
            job_dir = os.path.dirname(parent)
        else:
            job_dir = os.getcwd()

    if not user_id:
        user_id = os.environ.get("USER_ID") or None

    con = duckdb.connect(db_path)

    # Check tables
    tables = {r[0].lower() for r in con.execute("SHOW TABLES").fetchall()}
    if "aws_li_to_gcp_li" not in tables or "aws_li_catalog" not in tables:
        con.close()
        return 0

    # Ensure the applied_directives provenance column exists. row_state.py is the
    # ONLY sanctioned owner of DDL on aws_li_to_gcp_li; ensure_schema() idempotently
    # ALTERs in any registry column missing from an existing table.
    rs.ensure_schema(con)

    # 2. Read catalog and mapping rows
    catalog_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
    mapping_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
    has_projection_table = "gcp_projection" in tables
    projection_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('gcp_projection')").fetchall()} if has_projection_table else set()
    if has_projection_table:
        if "gcp_cost_1yr_cud" not in projection_cols:
            try:
                con.execute("ALTER TABLE gcp_projection ADD COLUMN gcp_cost_1yr_cud DOUBLE")
                con.execute("UPDATE gcp_projection SET gcp_cost_1yr_cud = gcp_projected_cost")
                projection_cols.add("gcp_cost_1yr_cud")
            except Exception:
                pass
        if "gcp_cost_3yr_cud" not in projection_cols:
            try:
                con.execute("ALTER TABLE gcp_projection ADD COLUMN gcp_cost_3yr_cud DOUBLE")
                con.execute("UPDATE gcp_projection SET gcp_cost_3yr_cud = gcp_projected_cost")
                projection_cols.add("gcp_cost_3yr_cud")
            except Exception:
                pass
    has_workload_col = "is_workload" in mapping_cols
    is_workload_sel = "m.is_workload" if has_workload_col else "TRUE AS is_workload"

    product_code_sel = "COALESCE(c.product_code, '') AS product_code" if "product_code" in catalog_cols else "'' AS product_code"
    canonical_sel = "COALESCE(c.canonical_service, '') AS canonical_service" if "canonical_service" in catalog_cols else "'' AS canonical_service"
    account_sel = "COALESCE(c.account_id, '') AS account_id" if "account_id" in catalog_cols else "'' AS account_id"
    tags_sel = "COALESCE(c.tags, '{}') AS tags" if "tags" in catalog_cols else "'{}' AS tags"
    if "aws_region" in catalog_cols:
        region_sel = "COALESCE(c.aws_region, '') AS region"
    elif "region" in catalog_cols:
        region_sel = "COALESCE(c.region, '') AS region"
    else:
        region_sel = "'' AS region"

    comp_sel = "COALESCE(m.component, '') AS component" if "component" in mapping_cols else "'' AS component"
    greg_sel = "COALESCE(m.gcp_region, '') AS gcp_region" if "gcp_region" in mapping_cols else "'' AS gcp_region"

    query = f"""
        SELECT c.aws_li_key,
               COALESCE(c.product, '') AS product,
               {product_code_sel},
               {canonical_sel},
               COALESCE(c.usage_type, '') AS usage_type,
               COALESCE(c.operation, '') AS operation,
               {region_sel},
               {account_sel},
               {tags_sel},
               m.gcp_service,
               m.strategy,
               {is_workload_sel},
               {comp_sel},
               {greg_sel}
        FROM aws_li_catalog c
        JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key
    """

    # 1. Load directives (scoped to user_id if provided)
    directives = load_ledger(job_dir, user_id=user_id)
    if not directives and not user_id:
        legacy = convert_legacy_to_directives(job_dir, con=con)
        if legacy:
            directives = legacy
            save_ledger(job_dir, directives)

    # Fold in architecture disclosures
    arch_notes = load_architecture_disclosures()
    all_directives = list(directives) + arch_notes

    if not all_directives:
        con.close()
        return 0
    rows_raw = con.execute(query).fetchall()
    rows: List[Dict[str, Any]] = []
    for r in rows_raw:
        rows.append({
            "aws_li_key": r[0],
            "product": r[1],
            "product_code": r[2],
            "canonical_service": r[3],
            "usage_type": r[4],
            "operation": r[5],
            "region": r[6],
            "account_id": r[7],
            "tags": r[8],
            "gcp_service": r[9],
            "strategy": r[10],
            "is_workload": r[11],
            "component": r[12],
            "gcp_region": r[13],
        })

    # 3. Resolve overrides
    overrides = resolve(rows, all_directives)
    if not overrides:
        con.close()
        print("apply_directives: 0 overrides applicable")
        return 0

    if check_only:
        con.close()
        print(f"apply_directives (check-only): {len(overrides)} row(s) would be modified")
        return len(overrides)

    # 4. Apply updates
    updated = 0
    row_by_key = {r["aws_li_key"]: r for r in rows}
    for key, eff in overrides.items():
        # Staged mutation / dirty-field pattern: collect assignments in a map keyed
        # by column, so a row touched by several verbs yields exactly ONE assignment
        # per column (last-writer-wins). Without it, two verbs targeting the same
        # column — e.g. a CUD pricing basis and an override rate both touching
        # gcp_projected_cost, or strategy=ignore and set_is_workload both touching
        # is_workload — would emit a duplicate SET, which DuckDB (like standard SQL)
        # rejects. Each entry is column -> (sql_expr, [bind_params]).
        main_set: Dict[str, tuple] = {}
        proj_set: Dict[str, tuple] = {}
        proj_cost_mult: Optional[float] = None  # CUD multiplier, composed at emit

        if eff.applied_directives:
            main_set["applied_directives"] = ("?", [json.dumps(eff.applied_directives)])

        if eff.target_service:
            main_set["gcp_service"] = ("?", [eff.target_service])

        if eff.strategy:
            main_set["strategy"] = ("?", [eff.strategy])
            if eff.strategy == "ignore" and has_workload_col:
                main_set["is_workload"] = ("FALSE", [])

        if has_workload_col and eff.is_workload is not None:
            main_set["is_workload"] = ("?", [eff.is_workload])

        # Phase 3: set_target_family
        if eff.target_family:
            if "gcp_family" in mapping_cols:
                main_set["gcp_family"] = ("?", [eff.target_family])
            fam_entry = _gp_family_by_label(eff.target_family)
            if fam_entry:
                _, core_desc, ram_desc, _, _, _, _ = fam_entry
                r_comp = row_by_key.get(key, {}).get("component", "core")
                target_desc = ram_desc if r_comp == "ram" else core_desc
                r_reg = row_by_key.get(key, {}).get("gcp_region") or row_by_key.get(key, {}).get("region") or "us-central1"
                new_sku = _strict_resolve_sku("Compute Engine", target_desc, r_reg)
                if new_sku and "gcp_sku_id" in mapping_cols:
                    main_set["gcp_sku_id"] = ("?", [new_sku])
                if target_desc and "gcp_sku_name" in mapping_cols:
                    main_set["gcp_sku_name"] = ("?", [target_desc])
            eff.notes.append(f"[Directive: target family pinned to {eff.target_family}]")

        # Phase 3: set_target_shape
        if eff.target_shape:
            if isinstance(eff.target_shape, dict):
                v_num = eff.target_shape.get("vcpu") or eff.target_shape.get("vcpus")
                r_num = eff.target_shape.get("ram_gb") or eff.target_shape.get("ram")
                eff.notes.append(f"[Directive: target shape pinned to custom {v_num} vCPU / {r_num} GB]")
                r_comp = row_by_key.get(key, {}).get("component", "core")
                if "unit_multiplier" in mapping_cols and r_comp == "core" and v_num is not None:
                    main_set["unit_multiplier"] = ("?", [float(v_num)])
                elif "unit_multiplier" in mapping_cols and r_comp == "ram" and r_num is not None:
                    main_set["unit_multiplier"] = ("?", [float(r_num)])
            else:
                eff.notes.append(f"[Directive: target shape pinned to {eff.target_shape}]")

        # Phase 3: set_region
        if eff.region:
            if "gcp_region" in mapping_cols:
                main_set["gcp_region"] = ("?", [eff.region])
            pol = eff.region_policy or "nearest_then_global"
            if "rate_region_basis" in mapping_cols:
                if pol == "global":
                    main_set["rate_region_basis"] = ("'global'", [])
                    if "rate_region_used" in mapping_cols:
                        main_set["rate_region_used"] = ("'global'", [])
                elif pol == "exact":
                    main_set["rate_region_basis"] = ("'exact'", [])
                    if "rate_region_used" in mapping_cols:
                        main_set["rate_region_used"] = ("?", [eff.region])
                else:  # nearest_then_global
                    main_set["rate_region_basis"] = ("'dominant'", [])
                    if "rate_region_used" in mapping_cols:
                        main_set["rate_region_used"] = ("?", [eff.region])
            eff.notes.append(f"[Directive: region set to {eff.region} (policy: {pol})]")

        # Phase 3: set_pricing_basis
        if eff.pricing_basis:
            pb = eff.pricing_basis.lower().strip()
            if "review_flags" in mapping_cols:
                main_set["review_flags"] = (
                    "CASE WHEN COALESCE(review_flags, '') = '' THEN ? ELSE review_flags || ',' || ? END",
                    [pb, pb],
                )
            svc_name = eff.target_service or row_by_key.get(key, {}).get("gcp_service") or "Compute Engine"
            if pb in ("cud_1yr", "cud_3yr"):
                proj_cost_mult = float(get_cud_mult(svc_name, pb))
                term_label = "3-yr" if pb == "cud_3yr" else "1-yr"
                eff.notes.append(f"[Directive: priced at {term_label} CUD at customer request]")
            elif pb == "sp_net":
                eff.notes.append("[Directive: AWS Savings Plan netted into basis at customer request]")
            elif pb == "od":
                eff.notes.append("[Directive: on-demand pricing basis]")

        if eff.fields:
            for fk, fv in eff.fields.items():
                if fk.lower() in mapping_cols:
                    main_set[fk] = ("?", [fv])

        # Emit the single mapping-table UPDATE (one assignment per column).
        if main_set:
            cols: List[str] = []
            params: List[Any] = []
            for col, (expr, p) in main_set.items():
                cols.append(f"{col} = {expr}")
                params.extend(p)
            params.append(key)
            con.execute(f"UPDATE aws_li_to_gcp_li SET {', '.join(cols)} WHERE aws_li_key = ?", params)
            updated += 1

        if eff.notes:
            # Directive disclosures are merged into pipeline_note (structured,
            # output-only); render_notes.py composes projection_note from it.
            # The CASE de-duplicates internally, so this never reads the note to
            # make a mapping decision. Directive provenance lives in
            # applied_directives; this is only the human explanation.
            for n in eff.notes:
                con.execute(f"""
                    UPDATE aws_li_to_gcp_li
                    SET {rs.note_merge_sql('pipeline_note')}
                    WHERE aws_li_key = ?
                """, [n, n, n, key])

        if has_projection_table:
            # Compose gcp_projected_cost from at most one base and an optional CUD
            # multiplier, so pricing-basis + ignore/override never collide on the
            # same column. Precedence: ignore (zero) is terminal; otherwise the base
            # is the override rate if given else the current cost, then the CUD
            # multiplier (if any) scales it.
            if eff.strategy == "ignore":
                proj_set["gcp_projected_cost"] = ("0.0", [])
                if "is_workload" in projection_cols:
                    proj_set["is_workload"] = ("FALSE", [])
            else:
                base_expr, base_params, has_base = "gcp_projected_cost", [], False
                if eff.override_rate is not None and eff.override_rate >= 0:
                    base_expr, base_params, has_base = "?", [float(eff.override_rate)], True
                if proj_cost_mult is not None:
                    proj_set["gcp_projected_cost"] = (f"{base_expr} * ?", base_params + [proj_cost_mult])
                elif has_base:
                    proj_set["gcp_projected_cost"] = (base_expr, base_params)

            if eff.is_workload is not None and "is_workload" in projection_cols:
                proj_set["is_workload"] = ("?", [eff.is_workload])

            if eff.fields:
                for fk, fv in eff.fields.items():
                    if fk.lower() in projection_cols:
                        proj_set[fk] = ("?", [fv])

            if proj_set:
                cols = []
                params = []
                for col, (expr, p) in proj_set.items():
                    cols.append(f"{col} = {expr}")
                    params.extend(p)
                params.append(key)
                con.execute(f"UPDATE gcp_projection SET {', '.join(cols)} WHERE aws_li_key = ?", params)

    try:
        import render_notes
        render_notes.render_notes_for_db(con)
    except Exception:
        pass

    con.close()
    print(f"apply_directives: applied {len(all_directives)} directive(s) across {updated} row(s)")
    return updated


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb> [--check-only] [--job-dir <DIR>] [--user-id <USER_ID>]", file=sys.stderr)
        return 2

    db_path = sys.argv[1]
    check_only = "--check-only" in sys.argv

    job_dir = None
    if "--job-dir" in sys.argv:
        idx = sys.argv.index("--job-dir")
        if idx + 1 < len(sys.argv):
            job_dir = sys.argv[idx + 1]

    user_id = None
    if "--user-id" in sys.argv:
        idx = sys.argv.index("--user-id")
        if idx + 1 < len(sys.argv):
            user_id = sys.argv[idx + 1]

    apply_directives_to_db(db_path, check_only=check_only, job_dir=job_dir, user_id=user_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
