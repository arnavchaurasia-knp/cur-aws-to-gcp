#!/usr/bin/env python3
"""refresh_assumption_domains.py — Refresh and audit assumption value domains."""

import argparse
from datetime import date, datetime
import json
import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_SCRIPTS_DIR, "..", "data")
_DOMAINS_FILE = os.path.join(_DATA_DIR, "assumption_domains.json")
_RATIO_REGISTRY_FILE = os.path.join(_DATA_DIR, "ratio_registry.json")

sys.path.insert(0, _SCRIPTS_DIR)
from assumption_domains import derive_aurora_values


def validate_derived_values(key: str, dom: dict, candidate_vals: list, max_delta_pct: float = None) -> tuple:
    """
    Validates re-derived values against honesty gates:
    1. Reject $0 / null where prior value was non-zero.
    2. Reject out-of-range vs key's domain [min, max] or structural bounds.
    3. Reject change exceeding max_delta_pct vs current value.

    Returns (is_valid, reason).
    """
    old_vals = dom.get("values")
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


def refresh_domains(domains_path: str = None, registry_path: str = None, check_only: bool = False, as_of_date: date = None) -> tuple:
    """
    Refresh assumption domains per WORKORDER_assumption_value_domains.md §4.4:
    - derived_in_repo: re-derive values from ratio_registry.json
    - provider_published: check age; if > 180 days, flag for verification
    - judgment: skip

    Returns (updated_data, report_lines, stale_count).
    """
    path = domains_path or _DOMAINS_FILE
    reg_path = registry_path or _RATIO_REGISTRY_FILE
    today = as_of_date or date.today()

    if not os.path.exists(path):
        raise FileNotFoundError(f"Assumption domains file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    domains = data.get("domains", {})
    report = []
    stale_count = 0

    meta = data.get("_meta", {})
    max_delta_pct = meta.get("max_refresh_delta_pct")
    if max_delta_pct is not None:
        max_delta_pct = float(max_delta_pct)

    # 1. derived_in_repo
    try:
        ram_vals, vcpu_vals = derive_aurora_values(reg_path)
        candidates = {
            "aurora_acu_ram_gib": ram_vals,
            "aurora_acu_vcpu": vcpu_vals,
        }
        for k, c_vals in candidates.items():
            if k in domains:
                dom = domains[k]
                valid, reason = validate_derived_values(k, dom, c_vals, max_delta_pct)
                if not valid:
                    report.append(reason)
                    if not check_only:
                        dom["needs_human_verification"] = True
                else:
                    old_v = dom.get("values")
                    if old_v != c_vals:
                        if not check_only:
                            dom["values"] = c_vals
                            dom["needs_human_verification"] = False
                        report.append(f"UPDATED {k}.values: {c_vals}")
    except Exception as e:
        report.append(f"ERROR re-deriving Aurora values: {e}")

    # 2. provider_published & judgment
    for key, dom in sorted(domains.items()):
        source_kind = dom.get("source_kind")
        if source_kind == "provider_published":
            last_verified_str = dom.get("last_verified")
            val = dom.get("value")
            src = dom.get("source")
            if last_verified_str:
                try:
                    last_verified_dt = datetime.strptime(last_verified_str, "%Y-%m-%d").date()
                    age_days = (today - last_verified_dt).days
                except Exception:
                    age_days = 9999
            else:
                age_days = 9999

            if age_days > 180:
                stale_count += 1
                report.append(f"VERIFY {key}: pinned {val} — confirm against {src} (age: {age_days} days)")
                if not check_only:
                    dom["needs_human_verification"] = True
            else:
                if not check_only and dom.get("needs_human_verification") and age_days <= 180:
                    dom["needs_human_verification"] = False
        elif source_kind == "judgment":
            # Judgment: skip (nothing to refresh)
            pass

    if not check_only:
        data["domains"] = domains
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

    return data, report, stale_count


def main():
    parser = argparse.ArgumentParser(description="Refresh and audit assumption value domains.")
    parser.add_argument("--check-only", action="store_true", help="Print report without writing to assumption_domains.json.")
    parser.add_argument("--domains-file", type=str, help="Custom path to assumption_domains.json.")
    parser.add_argument("--registry-file", type=str, help="Custom path to ratio_registry.json.")
    args = parser.parse_args()

    data, report, stale_count = refresh_domains(
        domains_path=args.domains_file,
        registry_path=args.registry_file,
        check_only=args.check_only
    )

    if report:
        print("Assumption Domains Refresh Report:")
        for line in report:
            print("  " + line)
    else:
        print("Assumption Domains Refresh Report: all domains up to date and verified.")

    domains = data.get("domains", {})
    total_domains = len(domains)
    fetched_count = total_domains - stale_count
    coverage_pct = round(100.0 * fetched_count / total_domains, 2) if total_domains > 0 else 100.0
    anomalies = [r for r in report if "VERIFY" in r or "REJECT" in r or "ERROR" in r]
    complete = (stale_count == 0 and len(anomalies) == 0)
    item_outcomes = {k: ("REJECTED" if (v.get("needs_human_verification") and any(k in r and "REJECT" in r for r in anomalies))
                         else ("STALE" if (v.get("needs_human_verification") or False) else "OK"))
                     for k, v in domains.items()}

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
