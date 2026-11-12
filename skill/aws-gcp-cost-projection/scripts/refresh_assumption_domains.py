#!/usr/bin/env python3
"""refresh_assumption_domains.py — Refresh and audit assumption value domains."""

import argparse
from datetime import date, datetime
import json
import os
import re
import sys
import urllib.request

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_SCRIPTS_DIR, "..", "data")
_DOMAINS_FILE = os.path.join(_DATA_DIR, "assumption_domains.json")
_RATIO_REGISTRY_FILE = os.path.join(_DATA_DIR, "ratio_registry.json")
_DEFAULTS_FILE = os.path.join(_DATA_DIR, "assumption-defaults.json")
_SPEC_FILE = os.path.join(_DATA_DIR, "ec2-instance-types.json")

sys.path.insert(0, _SCRIPTS_DIR)
from assumption_domains import (
    derive_aurora_values,
    derive_node_ram_values,
    derive_lambda_mb_per_vcpu,
)


def fetch_provider_scalar(key: str, source_url: str, timeout_sec: int = 5) -> tuple:
    """
    Live provider fetch for Tier B (fail-safe).
    Returns (candidate_value, error_message).
    On any network, parse, or HTTP failure, returns (None, reason).
    """
    if not source_url:
        return None, "No source_url provided"
    try:
        req = urllib.request.Request(
            source_url,
            headers={"User-Agent": "cur-web-refresh/1.0 (pricing-audit; python-urllib)"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            content = resp.read().decode("utf-8", errors="replace")

        # Match Glue DPU definitions: "1 DPU provides 4 vCPU and 16 GB of memory"
        if key == "glue_dpu_vcpu":
            m = re.search(r'(?:1\s*DPU|DPU)[^.\n]*?(\d+)\s*(?:vCPU|vCPUs)', content, re.IGNORECASE)
            if m:
                return float(m.group(1)), ""
            return None, "Pattern for glue_dpu_vcpu not found in page content"
        elif key == "glue_dpu_ram_gib":
            m = re.search(r'(?:1\s*DPU|DPU)[^.\n]*?(\d+)\s*(?:GB|GiB)\s*(?:of\s*)?memory', content, re.IGNORECASE)
            if m:
                return float(m.group(1)), ""
            return None, "Pattern for glue_dpu_ram_gib not found in page content"
        else:
            return None, f"Unknown Tier B key for scalar fetch: {key}"
    except Exception as e:
        return None, f"Network or fetch failure: {e}"


def validate_derived_values(
    key: str,
    dom: dict,
    candidate_vals: list,
    max_delta_pct: float = None,
    default_val: float = None,
) -> tuple:
    """
    Validates re-derived values against honesty gates:
    1. Reject $0 / null where prior value was non-zero.
    2. Reject out-of-range vs key's domain [min, max] or structural bounds.
    3. Reject change exceeding max_delta_pct vs current value.

    Returns (is_valid, reason).
    """
    old_vals = dom.get("values")
    if not old_vals and dom.get("value") is not None:
        old_vals = [dom.get("value")]
    if not old_vals and default_val is not None:
        old_vals = [default_val]

    if not candidate_vals or any(v is None or v == 0 or v == 0.0 for v in candidate_vals):
        return False, f"REJECT {key}: candidate derived values contain $0, null, or empty ({candidate_vals})"

    # Range check
    min_b = dom.get("min")
    max_b = dom.get("max")
    if "bounds" in dom and isinstance(dom["bounds"], (list, tuple)) and len(dom["bounds"]) == 2:
        min_b = min_b if min_b is not None else dom["bounds"][0]
        max_b = max_b if max_b is not None else dom["bounds"][1]

    if min_b is not None and any(v < min_b for v in candidate_vals):
        return False, f"REJECT {key}: candidate value {candidate_vals} below min bound {min_b}"
    if max_b is not None and any(v > max_b for v in candidate_vals):
        return False, f"REJECT {key}: candidate value {candidate_vals} above max bound {max_b}"

    # Delta jump check
    if max_delta_pct is not None and old_vals and isinstance(old_vals, list) and len(old_vals) > 0:
        for cv in candidate_vals:
            closest_dist_pct = min(abs(cv - ov) / abs(ov) * 100.0 for ov in old_vals if ov != 0)
            if closest_dist_pct > max_delta_pct:
                return False, f"REJECT {key}: candidate value {cv} exceeds {max_delta_pct}% delta jump vs prior {old_vals} (closest {closest_dist_pct:.1f}%)"

    return True, ""


def refresh_domains(
    domains_path: str = None,
    registry_path: str = None,
    defaults_path: str = None,
    specs_path: str = None,
    check_only: bool = False,
    as_of_date: date = None,
) -> tuple:
    """
    Refresh assumption domains per WORKORDER_assumption_self_fetch_all.md:
    - Tier A (derived_in_repo): derive from ratio_registry.json, ec2-instance-types.json, limits
    - Tier B (provider_published): fetch provider-published scalar live (fail-safe)
    - Tier C (bill_calibrated): record as BILL_CALIBRATED (calibrated per-job from bill)
    - Tier D: record honest tracked outcomes (NO_SOURCE / POLICY / NEEDS_HUMAN)

    Returns (updated_data, report_lines, stale_count, item_outcomes).
    """
    path = domains_path or _DOMAINS_FILE
    reg_path = registry_path or _RATIO_REGISTRY_FILE
    def_path = defaults_path or _DEFAULTS_FILE
    spec_p = specs_path or _SPEC_FILE
    today = as_of_date or date.today()

    if not os.path.exists(path):
        raise FileNotFoundError(f"Assumption domains file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    defaults = {}
    if os.path.exists(def_path):
        try:
            with open(def_path, "r", encoding="utf-8") as f:
                defaults = json.load(f).get("values", {})
        except Exception:
            pass

    domains = data.get("domains", {})
    report = []
    stale_count = 0
    item_outcomes = {}

    meta = data.get("_meta", {})
    max_delta_pct = meta.get("max_refresh_delta_pct")
    if max_delta_pct is not None:
        max_delta_pct = float(max_delta_pct)

    for key, dom in sorted(domains.items()):
        tier = dom.get("refresh_tier")
        source_kind = dom.get("source_kind")

        # Determine Tier if not explicitly set
        if not tier:
            if source_kind == "derived_in_repo" or "derived" in str(source_kind):
                tier = "A"
            elif source_kind == "provider_published":
                tier = "B"
            elif source_kind == "bill_calibrated":
                tier = "C"
            else:
                tier = "D"

        # ----------------- TIER A: In-repo derivation -----------------
        if tier == "A":
            try:
                candidate_vals = None
                if key in ("aurora_acu_ram_gib", "aurora_acu_vcpu"):
                    ram_vals, vcpu_vals = derive_aurora_values(reg_path)
                    candidate_vals = ram_vals if key == "aurora_acu_ram_gib" else vcpu_vals
                elif key == "dax_node_gib":
                    candidate_vals = [derive_node_ram_values("dax", spec_p)]
                elif key == "memorydb_node_gib":
                    candidate_vals = [derive_node_ram_values("memorydb", spec_p)]
                elif key == "lambda_mb_per_vcpu":
                    candidate_vals = [derive_lambda_mb_per_vcpu(def_path)]

                if candidate_vals is not None:
                    def_val = defaults.get(key)
                    valid, reason = validate_derived_values(key, dom, candidate_vals, max_delta_pct, def_val)
                    if not valid:
                        report.append(reason)
                        item_outcomes[key] = "REJECTED"
                        if not check_only:
                            dom["needs_human_verification"] = True
                    else:
                        old_v = dom.get("values")
                        if old_v is not None and old_v != candidate_vals:
                            if not check_only:
                                dom["values"] = candidate_vals
                            report.append(f"UPDATED {key}.values: {candidate_vals}")
                        if not check_only:
                            dom["needs_human_verification"] = False
                            dom["last_verified"] = today.strftime("%Y-%m-%d")
                        item_outcomes[key] = "DERIVED"
                else:
                    item_outcomes[key] = "DERIVED"
            except Exception as e:
                report.append(f"ERROR re-deriving {key}: {e}")
                item_outcomes[key] = "REJECTED"
                if not check_only:
                    dom["needs_human_verification"] = True

        # ----------------- TIER B: Provider published live fetch -----------------
        elif tier == "B":
            source_url = dom.get("source_url") or dom.get("source")
            val = dom.get("value")
            candidate_val, fetch_err = fetch_provider_scalar(key, source_url)

            if candidate_val is None:
                # Network or parsing failure (fail-safe)
                last_verified_str = dom.get("last_verified")
                age_days = 9999
                if last_verified_str:
                    try:
                        last_verified_dt = datetime.strptime(last_verified_str, "%Y-%m-%d").date()
                        age_days = (today - last_verified_dt).days
                    except Exception:
                        pass

                if age_days > 180:
                    stale_count += 1
                    report.append(f"VERIFY {key}: pinned {val} — confirm against {source_url} (age: {age_days} days, fetch error: {fetch_err})")
                    item_outcomes[key] = "STALE"
                    if not check_only:
                        dom["needs_human_verification"] = True
                else:
                    # Within cache window; note verify request but preserve pinned value
                    report.append(f"VERIFY {key}: pinned {val} — fetch failed ({fetch_err})")
                    item_outcomes[key] = "VERIFY"
                    if not check_only:
                        dom["needs_human_verification"] = True
            else:
                # Fetch succeeded: validate honesty gates
                def_val = defaults.get(key)
                valid, reason = validate_derived_values(key, dom, [candidate_val], max_delta_pct, def_val)
                if not valid:
                    report.append(reason)
                    item_outcomes[key] = "REJECTED"
                    if not check_only:
                        dom["needs_human_verification"] = True
                else:
                    if val is not None and abs(candidate_val - val) > 1e-9:
                        report.append(f"UPDATED {key}.value: {candidate_val} (drift detected, review before applying)")
                        item_outcomes[key] = "VERIFY"
                        if not check_only:
                            dom["needs_human_verification"] = True
                    else:
                        if not check_only:
                            dom["needs_human_verification"] = False
                            dom["last_verified"] = today.strftime("%Y-%m-%d")
                        item_outcomes[key] = "SELF_FETCHED"

        # ----------------- TIER C: Bill calibrated -----------------
        elif tier == "C":
            item_outcomes[key] = "BILL_CALIBRATED"
            if not check_only:
                dom["needs_human_verification"] = False

        # ----------------- TIER D: Unfetchable / Policy / Human -----------------
        else:
            tracked = dom.get("tracked_outcome") or "NO_SOURCE"
            if tracked == "NEEDS_HUMAN" or dom.get("needs_human_verification"):
                item_outcomes[key] = "NEEDS_HUMAN"
                if not check_only:
                    dom["needs_human_verification"] = True
            elif tracked == "POLICY":
                item_outcomes[key] = "POLICY"
                if not check_only:
                    dom["needs_human_verification"] = False
            else:
                item_outcomes[key] = "NO_SOURCE"
                if not check_only:
                    dom["needs_human_verification"] = False

    if not check_only:
        data["domains"] = domains
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

    data["_item_outcomes"] = item_outcomes
    return data, report, stale_count


def main():
    parser = argparse.ArgumentParser(description="Refresh and audit assumption value domains.")
    parser.add_argument("--check-only", action="store_true", help="Print report without writing to assumption_domains.json.")
    parser.add_argument("--domains-file", type=str, help="Custom path to assumption_domains.json.")
    parser.add_argument("--registry-file", type=str, help="Custom path to ratio_registry.json.")
    parser.add_argument("--defaults-file", type=str, help="Custom path to assumption-defaults.json.")
    parser.add_argument("--specs-file", type=str, help="Custom path to ec2-instance-types.json.")
    args = parser.parse_args()

    data, report, stale_count = refresh_domains(
        domains_path=args.domains_file,
        registry_path=args.registry_file,
        defaults_path=args.defaults_file,
        specs_path=args.specs_file,
        check_only=args.check_only,
    )
    item_outcomes = data.pop("_item_outcomes", {})

    if report:
        print("Assumption Domains Refresh Report:")
        for line in report:
            print("  " + line)
    else:
        print("Assumption Domains Refresh Report: all domains up to date and verified.")

    domains = data.get("domains", {})
    total_domains = len(domains)
    fetched_count = sum(1 for o in item_outcomes.values() if o in ("SELF_FETCHED", "DERIVED", "BILL_CALIBRATED"))
    coverage_pct = round(100.0 * fetched_count / total_domains, 2) if total_domains > 0 else 100.0
    anomalies = [r for r in report if "REJECT" in r or "ERROR" in r]
    complete = (stale_count == 0 and len(anomalies) == 0)

    summary = {
        "source": "assumption_domains",
        "expected_count": total_domains,
        "fetched_count": fetched_count,
        "coverage_pct": coverage_pct,
        "complete": complete,
        "record_count": total_domains,
        "record_delta": 0,
        "anomalies": anomalies,
        "item_outcomes": item_outcomes,
    }
    print(f"REFRESH_SUMMARY {json.dumps(summary)}")

    if args.check_only and stale_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
