#!/usr/bin/env python3
"""
directive_translator.py — Translates natural language utterances into proposed directives.

AGY proposes; it never mutates the projection directly.
Enforces Phase 2 action verbs, validates against schema, computes scope_estimate,
and generates clarifications for under-specified utterances or Phase 3 verbs.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from directives import Directive, validate_directive, ALLOWED_ACTION_VERBS
from directive_resolver import extract_row_identity, matches_selector

try:
    from apply_static_mappings import derive_identity
except Exception:
    def derive_identity(product: str = "", usage_type: str = "", instance_type: str = "") -> dict:
        return {"arch": "", "instance_family": "", "vendor": "", "workload": ""}

try:
    import duckdb
except ImportError:
    duckdb = None

PHASE3_VERBS = {
    "set_target_family": ["family", "n2d", "c2d", "t2a", "c4a", "intel", "arm", "graviton", "target family"],
    "set_target_shape": ["shape", "smaller shape", "bigger shape", "custom shape", "4vcpu", "target shape"],
    "set_region": ["region", "europe-west1", "us-east1", "asia-south1", "set region"],
    "region_policy": ["region policy", "nearest_then_global", "global if absent", "nearest then global"],
    "set_pricing_basis": ["pricing basis", "cud", "1-year commit", "3-year commit", "savings plan netting", "net my savings plan"],
}


def detect_phase3_intent(utterance: str) -> Optional[str]:
    """Check if the user utterance requests a Phase-3 only capability."""
    utt_lower = utterance.lower().strip()
    for verb, keywords in PHASE3_VERBS.items():
        for kw in keywords:
            # Match on word boundary or phrase
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, utt_lower):
                # Ensure it's not just a general word if the user asked a supported phase 2 verb
                if verb == "set_region" and any(k in utt_lower for k in ["europe", "us-", "asia-", "region"]):
                    # If user is asking to change or price in a region
                    if "in " in utt_lower or "to " in utt_lower or "region" in utt_lower:
                        return verb
                elif verb in ("set_target_family", "set_target_shape", "set_pricing_basis", "region_policy"):
                    return verb
    return None


def get_candidate_rows(
    job_dir: str,
    utterance: str,
    selected_row_keys: Optional[List[str]] = None,
    max_rows: int = 20
) -> List[Dict[str, Any]]:
    """Build a tight slice of candidate catalog rows for the LLM or translator."""
    db_path = os.path.join(job_dir, "projection-audit", "projection.duckdb")
    if not os.path.exists(db_path) or duckdb is None:
        return []

    try:
        con = duckdb.connect(db_path, read_only=True)
        tables = {r[0].lower() for r in con.execute("SHOW TABLES").fetchall()}
        if "aws_li_catalog" not in tables:
            con.close()
            return []

        cat_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
        has_map = "aws_li_to_gcp_li" in tables
        map_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()} if has_map else set()

        canon_col = "c.canonical_service" if "canonical_service" in cat_cols else "''"
        svc_col = "m.gcp_service" if "gcp_service" in map_cols else "''"
        strat_col = "m.strategy" if "strategy" in map_cols else "''"
        join_clause = "LEFT JOIN aws_li_to_gcp_li m ON c.aws_li_key = m.aws_li_key" if has_map else ""

        if selected_row_keys:
            placeholders = ",".join(["?"] * len(selected_row_keys))
            query = f"""
                SELECT c.aws_li_key, c.product, {canon_col} AS canonical_service,
                       c.usage_type, c.operation, c.aws_amortized_cost,
                       {svc_col} AS gcp_service, {strat_col} AS strategy
                FROM aws_li_catalog c
                {join_clause}
                WHERE c.aws_li_key IN ({placeholders})
                ORDER BY c.aws_amortized_cost DESC
                LIMIT {max_rows}
            """
            rows = con.execute(query, selected_row_keys).fetchall()
        else:
            # Extract keywords from utterance
            tokens = [t.lower() for t in re.findall(r'[a-zA-Z0-9_\-\.]+', utterance) if len(t) >= 2]
            # Ignore common stopwords
            stopwords = {"the", "for", "and", "this", "that", "all", "use", "make", "set", "with", "from", "these"}
            tokens = [t for t in tokens if t not in stopwords]

            if tokens:
                clauses = []
                params = []
                for t in tokens[:5]:
                    clauses.append("(c.product ILIKE ? OR c.usage_type ILIKE ? OR " + (f"{canon_col} ILIKE ?" if "canonical_service" in cat_cols else "FALSE") + ")")
                    params.extend([f"%{t}%", f"%{t}%"] + ([f"%{t}%"] if "canonical_service" in cat_cols else []))
                where_clause = "WHERE " + " OR ".join(clauses)
            else:
                where_clause = ""
                params = []

            query = f"""
                SELECT c.aws_li_key, c.product, {canon_col} AS canonical_service,
                       c.usage_type, c.operation, c.aws_amortized_cost,
                       {svc_col} AS gcp_service, {strat_col} AS strategy
                FROM aws_li_catalog c
                {join_clause}
                {where_clause}
                ORDER BY c.aws_amortized_cost DESC
                LIMIT {max_rows}
            """
            rows = con.execute(query, params).fetchall()

        con.close()

        result = []
        for r in rows:
            prod = r[1] or ""
            ut = r[3] or ""
            ident = derive_identity(prod, ut)
            result.append({
                "aws_li_key": r[0],
                "product": prod,
                "canonical_service": r[2] or "",
                "usage_type": ut,
                "operation": r[4] or "",
                "aws_amortized_cost": float(r[5] or 0.0),
                "gcp_service": r[6] or "",
                "strategy": r[7] or "",
                "arch": ident.get("arch", ""),
                "instance_family": ident.get("instance_family", ""),
                "vendor": ident.get("vendor", ""),
            })
        return result
    except Exception as exc:
        print(f"get_candidate_rows error: {exc}", file=sys.stderr)
        return []


def compute_scope_estimate(job_dir: str, directive: Directive) -> Dict[str, Any]:
    """Dry-run directive selector against the catalog rows to compute row count and aws_spend."""
    db_path = os.path.join(job_dir, "projection-audit", "projection.duckdb")
    if not os.path.exists(db_path) or duckdb is None:
        return {"rows": 0, "aws_spend": 0.0}

    try:
        con = duckdb.connect(db_path, read_only=True)
        catalog_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
        canonical_sel = "COALESCE(c.canonical_service, '') AS canonical_service" if "canonical_service" in catalog_cols else "'' AS canonical_service"
        account_sel = "COALESCE(c.account_id, '') AS account_id" if "account_id" in catalog_cols else "'' AS account_id"
        tags_sel = "COALESCE(c.tags, '{}') AS tags" if "tags" in catalog_cols else "'{}' AS tags"
        region_sel = "COALESCE(c.aws_region, '') AS region" if "aws_region" in catalog_cols else ("COALESCE(c.region, '') AS region" if "region" in catalog_cols else "'' AS region")

        query = f"""
            SELECT c.aws_li_key,
                   COALESCE(c.product, '') AS product,
                   COALESCE(c.product_code, '') AS product_code,
                   {canonical_sel},
                   COALESCE(c.usage_type, '') AS usage_type,
                   COALESCE(c.operation, '') AS operation,
                   {region_sel},
                   {account_sel},
                   {tags_sel},
                   c.aws_amortized_cost
            FROM aws_li_catalog c
        """
        rows = con.execute(query).fetchall()
        con.close()

        count = 0
        spend = 0.0
        for r in rows:
            ident = extract_row_identity({
                "aws_li_key": r[0],
                "product": r[1],
                "product_code": r[2],
                "canonical_service": r[3],
                "usage_type": r[4],
                "operation": r[5],
                "region": r[6],
                "account_id": r[7],
                "tags": r[8],
            })
            if matches_selector(directive, ident):
                count += 1
                spend += float(r[9] or 0.0)

        return {"rows": count, "aws_spend": round(spend, 2)}
    except Exception as exc:
        print(f"compute_scope_estimate error: {exc}", file=sys.stderr)
        return {"rows": 0, "aws_spend": 0.0}


def load_known_gcp_services() -> set[str]:
    """Load known valid GCP services from data/services.json and data/service_map.json."""
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    known = set()
    services_path = os.path.join(data_dir, "services.json")
    if os.path.exists(services_path):
        try:
            with open(services_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                dn = item.get("displayName")
                if dn:
                    known.add(dn.strip())
        except Exception:
            pass

    service_map_path = os.path.join(data_dir, "service_map.json")
    if os.path.exists(service_map_path):
        try:
            with open(service_map_path, "r", encoding="utf-8") as f:
                smap = json.load(f)
            for rule in smap.get("rules", []):
                gt = rule.get("gcp_target")
                if gt:
                    known.add(gt.strip())
        except Exception:
            pass

    # Ensure ubiquitous services
    known.update({"Compute Engine", "Cloud Storage", "Cloud SQL", "Cloud Run", "Google Kubernetes Engine", "Cloud NAT", "Cloud Monitoring", "Cloud Logging", "Secret Manager", "Cloud KMS", "BigQuery", "Pub/Sub", "Manual Review"})
    return known


def validate_proposed_directive(directive: Directive) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Validate proposed directive against reality checks (allowed targets, assumption range).
    Returns (is_valid, clarification_dict_if_invalid).
    """
    ok, err = validate_directive(directive)
    if not ok:
        return False, {
            "reason": "schema_invalid",
            "question": f"Directive schema validation failed: {err}",
            "options": [],
            "blocking": True,
            "directive_ref": directive.id,
        }

    act = directive.action
    act_type = act.get("type")

    if act_type == "set_target_service":
        target = act.get("value")
        known_services = load_known_gcp_services()
        # Case insensitive match
        match = next((s for s in known_services if s.lower() == str(target).strip().lower()), None)
        if not match:
            # Provide suggested options
            suggestions = [s for s in sorted(known_services) if any(word in s.lower() for word in str(target).lower().split())][:5]
            if not suggestions:
                suggestions = ["Compute Engine", "Cloud Storage", "Cloud SQL", "Cloud Run"]
            return False, {
                "reason": "invalid_target_service",
                "question": f"Target GCP service '{target}' is not recognized. Did you mean one of: {', '.join(suggestions)}?",
                "options": suggestions,
                "blocking": True,
                "directive_ref": directive.id,
            }
        else:
            # Canonicalize target casing
            act["value"] = match

    elif act_type == "set_assumption":
        key = act.get("key") or (act.get("params") or {}).get("key")
        val = act.get("value")
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        defaults_path = os.path.join(data_dir, "assumption-defaults.json")
        if os.path.exists(defaults_path) and key:
            try:
                with open(defaults_path, "r", encoding="utf-8") as f:
                    d_data = json.load(f)
                d_vals = d_data.get("values", {})
                if key not in d_vals:
                    return False, {
                        "reason": "unknown_assumption_key",
                        "question": f"Assumption key '{key}' is not defined in assumption catalog.",
                        "options": sorted(list(d_vals.keys()))[:8],
                        "blocking": True,
                        "directive_ref": directive.id,
                    }
                # Check range (e.g. non-negative or within reasonable clamp)
                if isinstance(val, (int, float)) and val < 0:
                    return False, {
                        "reason": "assumption_out_of_range",
                        "question": f"Assumption value {val} cannot be negative.",
                        "options": [],
                        "blocking": True,
                        "directive_ref": directive.id,
                    }
            except Exception:
                pass

    elif act_type == "set_target_family":
        fam = str(act.get("value") or act.get("target_family") or "").strip()
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        avail_path = os.path.join(data_dir, "compute_family_availability.json")
        req_reg = str(directive.selector.get("region") or "us-central1").lower()
        if os.path.exists(avail_path) and fam:
            try:
                with open(avail_path, "r", encoding="utf-8") as f:
                    avail_data = json.load(f)
                regs = avail_data.get("regions", {})
                reg_map = regs.get(req_reg)
                fam_key = fam.lower().replace("amd", "").replace("arm", "").strip()
                if reg_map and fam_key in reg_map and reg_map[fam_key] is False:
                    nearest = next((r for r, m in regs.items() if m.get(fam_key) is True and (r.split("-")[0] == req_reg.split("-")[0])), None)
                    if not nearest:
                        nearest = next((r for r, m in regs.items() if m.get(fam_key) is True), "us-central1")
                    return False, {
                        "reason": "family_not_in_region",
                        "question": f"{fam} isn't offered in {req_reg}; nearest is {nearest}, or keep current?",
                        "options": [f"Price in {nearest}", "Use global rate", "Keep current mapping"],
                        "blocking": True,
                        "directive_ref": directive.id,
                    }
            except Exception:
                pass

    elif act_type == "set_target_shape":
        shape = act.get("value") if act.get("value") is not None else act.get("shape")
        if isinstance(shape, dict):
            vcpu = shape.get("vcpu") or shape.get("vcpus")
            ram = shape.get("ram_gb") or shape.get("ram")
            if vcpu is not None and vcpu != 1 and vcpu % 2 != 0:
                return False, {
                    "reason": "custom_shape_out_of_constraint",
                    "question": f"Custom shape with {vcpu} vCPUs is invalid: vCPU count must be even (or 1).",
                    "options": [f"{vcpu - 1} vCPU", f"{vcpu + 1} vCPU"],
                    "blocking": True,
                    "directive_ref": directive.id,
                }
            if vcpu and ram:
                ratio = float(ram) / float(vcpu)
                if ratio < 0.5 or ratio > 8.0:
                    return False, {
                        "reason": "custom_shape_out_of_constraint",
                        "question": f"Custom shape ratio {ratio:.1f} GB/vCPU ({vcpu} vCPU / {ram} GB) is out of bounds: must be between 0.5 and 8.0 GB per vCPU.",
                        "options": [f"{vcpu * 2} GB", f"{vcpu * 4} GB"],
                        "blocking": True,
                        "directive_ref": directive.id,
                    }

    elif act_type == "set_region":
        reg = str(act.get("value") or act.get("region") or "").strip().lower()
        pol = str(act.get("region_policy") or (act.get("params") or {}).get("region_policy") or "nearest_then_global").strip().lower()
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        avail_path = os.path.join(data_dir, "compute_family_availability.json")
        if os.path.exists(avail_path) and reg:
            try:
                with open(avail_path, "r", encoding="utf-8") as f:
                    avail_data = json.load(f)
                regs = avail_data.get("regions", {})
                if pol == "exact" and reg not in regs:
                    return False, {
                        "reason": "region_has_no_rate",
                        "question": f"Region '{reg}' has no exact rate available. Would you like to use the nearest available region or global rate?",
                        "options": ["Use nearest available region", "Use global rate", "Keep current region"],
                        "blocking": True,
                        "directive_ref": directive.id,
                    }
            except Exception:
                pass

    elif act_type == "set_pricing_basis":
        pb = str(act.get("value") or act.get("pricing_basis") or "").strip().lower()
        allowed_bases = {"cud_1yr", "cud_3yr", "sp_net", "od"}
        if pb not in allowed_bases:
            return False, {
                "reason": "invalid_pricing_basis",
                "question": f"Pricing basis '{pb}' is not valid. Choose one of: 3-yr CUD, 1-yr CUD, Savings Plan netting, or On-Demand.",
                "options": ["cud_3yr", "cud_1yr", "sp_net", "od"],
                "blocking": True,
                "directive_ref": directive.id,
            }

    return True, None


def translate_utterance(
    utterance: str,
    job_dir: str,
    selected_row_keys: Optional[List[str]] = None,
    llm_func: Optional[Callable[[str], str]] = None
) -> Dict[str, Any]:
    """
    Translates a natural language user utterance into proposed Directive(s) and/or Clarifications.
    """
    utterance_clean = utterance.strip()
    if not utterance_clean:
        return {"proposed_directives": [], "clarifications": []}

    # Extract candidate rows
    candidates = get_candidate_rows(job_dir, utterance_clean, selected_row_keys)

    # Model interaction or heuristic translator
    proposed_directives: List[Directive] = []
    clarifications: List[Dict[str, Any]] = []

    if llm_func is not None:
        prompt = (
            f"User Utterance: {utterance_clean}\n"
            f"Candidate Rows: {json.dumps(candidates[:10])}\n"
            f"Allowed Action Verbs: {list(ALLOWED_ACTION_VERBS)}\n"
            "Return JSON array of Directive objects."
        )
        try:
            raw_output = llm_func(prompt)
            # Find JSON in model reply
            m = re.search(r'\[.*\]', raw_output, re.DOTALL)
            if not m:
                m = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if not m:
                return {
                    "proposed_directives": [],
                    "clarifications": [{
                        "id": f"clarif_{uuid.uuid4().hex[:8]}",
                        "reason": "invalid_llm_response",
                        "question": "Model failed to return valid JSON directive.",
                        "options": [],
                        "blocking": True
                    }]
                }
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                parsed = [parsed]
            for item in parsed:
                d = Directive.from_dict(item)
                d.status = "proposed"
                d.author = "agy"
                d.source_utterance = utterance_clean
                proposed_directives.append(d)
        except Exception as exc:
            return {
                "proposed_directives": [],
                "clarifications": [{
                    "id": f"clarif_{uuid.uuid4().hex[:8]}",
                    "reason": "llm_parsing_error",
                    "question": f"Failed to parse LLM proposal: {exc}",
                    "options": [],
                    "blocking": True
                }]
            }
    else:
        # Heuristic translation for deterministic commands
        utt_lower = utterance_clean.lower()
        d_id = f"dir_{uuid.uuid4().hex[:8]}"

        # Determine selector: specific keys if provided, or service/pattern from candidates
        selector: Dict[str, Any] = {}
        if selected_row_keys:
            selector["aws_li_keys"] = list(selected_row_keys)
        elif candidates:
            # If all candidates share the same canonical service, use service selector
            svcs = {c["canonical_service"] for c in candidates if c["canonical_service"]}
            if len(svcs) == 1:
                selector["service"] = list(svcs)[0]
            elif len(candidates) == 1:
                selector["aws_li_keys"] = [candidates[0]["aws_li_key"]]

        # Check arch / vendor filters in utterance
        if re.search(r'\b(arm|arm64|graviton)\b', utt_lower):
            selector["arch"] = "arm64"
        if re.search(r'\b(intel)\b', utt_lower):
            selector["vendor"] = "intel"
        if re.search(r'\b(amd)\b', utt_lower):
            selector["vendor"] = "amd"

        action: Optional[Dict[str, Any]] = None

        # Phase 3 heuristics
        m_fam = re.search(r'\b(?:use|set|target)?\s*(n2d|c4a|n4d|c4|n4|c2d|t2a|e2|c3|c3d)\b(?:\s*family)?', utt_lower, re.IGNORECASE)
        m_cust = re.search(r'(?:custom\s*)?(\d+)\s*(?:vcpu|cpu)[\s/]+(\d+(?:\.\d+)?)\s*(?:gb|gib|ram)', utt_lower, re.IGNORECASE)
        m_mach = re.search(r'\b([a-z0-9]+-(?:standard|highmem|highcpu)-\d+)\b', utt_lower, re.IGNORECASE)
        m_reg = re.search(r'\b(?:in|to|region)\s+([a-z]+-[a-z]+[0-9]+)\b', utt_lower, re.IGNORECASE)
        is_cud_3yr = bool(re.search(r'\b(3-yr|3-year|3 yr|3 year|3yr)\s*(?:commit|cud)?\b', utt_lower))
        is_cud_1yr = bool(re.search(r'\b(1-yr|1-year|1 yr|1 year|1yr)\s*(?:commit|cud)?\b', utt_lower))
        is_sp_net = bool(re.search(r'\b(?:savings\s*plan|sp)\s*(?:netting|net)\b|\bnet\s*(?:my\s*)?savings\s*plan\b', utt_lower))
        is_od = bool(re.search(r'\b(on-demand|ondemand|od)\b', utt_lower))

        if is_cud_3yr:
            action = {"type": "set_pricing_basis", "value": "cud_3yr"}
        elif is_cud_1yr:
            action = {"type": "set_pricing_basis", "value": "cud_1yr"}
        elif is_sp_net:
            action = {"type": "set_pricing_basis", "value": "sp_net"}
        elif is_od:
            action = {"type": "set_pricing_basis", "value": "od"}
        elif m_cust:
            action = {"type": "set_target_shape", "value": {"vcpu": int(m_cust.group(1)), "ram_gb": float(m_cust.group(2))}}
        elif m_mach:
            action = {"type": "set_target_shape", "value": m_mach.group(1)}
        elif m_fam and ("family" in utt_lower or any(f in utt_lower for f in ["n2d", "c4a", "n4d", "c4", "n4", "c2d", "t2a", "e2", "c3"])):
            action = {"type": "set_target_family", "value": m_fam.group(1).upper()}
        elif m_reg:
            reg_val = m_reg.group(1).lower()
            pol_val = "exact" if "exact" in utt_lower else ("global" if "global" in utt_lower else "nearest_then_global")
            action = {"type": "set_region", "value": reg_val, "params": {"region_policy": pol_val}}
        elif any(w in utt_lower for w in ["suppress", "ignore", "drop", "not workload"]):
            action = {"type": "set_strategy", "value": "ignore"}
        elif "passthrough" in utt_lower:
            action = {"type": "set_strategy", "value": "passthrough"}
        elif "workload" in utt_lower and ("is workload" in utt_lower or "treat as workload" in utt_lower):
            action = {"type": "set_is_workload", "value": True}
        elif "note" in utt_lower or "annotate" in utt_lower:
            m = re.search(r'(?:note|annotate)[:\s]+(.+)', utterance_clean, re.IGNORECASE)
            note_val = m.group(1).strip() if m else utterance_clean
            action = {"type": "annotate", "value": note_val}
        elif "map to " in utt_lower or "target " in utt_lower:
            m = re.search(r'(?:map to|target)\s+([A-Za-z0-9\s]+)', utterance_clean, re.IGNORECASE)
            if m:
                target_val = m.group(1).strip()
                action = {"type": "set_target_service", "value": target_val}
        elif "assumption" in utt_lower:
            m = re.search(r'([a-zA-Z0-9_]+)\s*=\s*([0-9\.]+)', utterance_clean)
            if m:
                action = {"type": "set_assumption", "key": m.group(1), "value": float(m.group(2))}

        if action:
            d = Directive(
                id=d_id,
                author="agy",
                source_utterance=utterance_clean,
                selector=selector,
                action=action,
                status="proposed",
                confidence=0.95
            )
            proposed_directives.append(d)
        else:
            clarifications.append({
                "id": f"clarif_{uuid.uuid4().hex[:8]}",
                "reason": "under_specified",
                "question": f"Could not determine the refinement action for: '{utterance_clean}'. Did you mean to ignore, passthrough, or re-target?",
                "options": ["ignore", "passthrough", "map to Compute Engine"],
                "blocking": True
            })

    # 4. Scope estimate + Validation before confirm
    validated_directives = []
    for d in proposed_directives:
        d.scope_estimate = compute_scope_estimate(job_dir, d)
        is_valid, clarif = validate_proposed_directive(d)
        if not is_valid and clarif:
            clarif["id"] = f"clarif_{uuid.uuid4().hex[:8]}"
            d.needs_clarification = clarif
            clarifications.append(clarif)
        validated_directives.append(d)

    return {
        "proposed_directives": [d.to_dict() for d in validated_directives],
        "clarifications": clarifications
    }


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <job_dir> <utterance> [--selected-keys k1,k2]", file=sys.stderr)
        return 1
    job_dir = sys.argv[1]
    utterance = sys.argv[2]
    selected_keys = None
    if "--selected-keys" in sys.argv:
        idx = sys.argv.index("--selected-keys")
        if idx + 1 < len(sys.argv):
            selected_keys = [k.strip() for k in sys.argv[idx + 1].split(",") if k.strip()]

    res = translate_utterance(utterance, job_dir, selected_keys)
    # Record in thread if possible
    try:
        from refinement_thread import add_user_message, add_agy_response
        add_user_message(job_dir, utterance)
        msg_text = "Here is the proposed directive." if res["proposed_directives"] else "I have a follow-up question regarding your request."
        add_agy_response(job_dir, msg_text, res["proposed_directives"], res["clarifications"])
    except Exception as exc:
        print(f"warning recording thread: {exc}", file=sys.stderr)

    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
