#!/usr/bin/env python3
"""
promote_learned_codes.py <jobs-root> [--apply]

Scans job directories for learned_proposals.json files produced by
resolve_unknown_product_codes.py, validates them against aws-product-codes.json,
checks for conflicts, and (when --apply is passed) atomically promotes new
product-name mappings into data/learned-product-codes.json.

Dry run by default.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.environ.get("SKILL_DIR") or os.path.dirname(_HERE)
_DATA_DIR = os.path.join(_SKILL_DIR, "data")
_CODES_PATH = os.path.join(_DATA_DIR, "aws-product-codes.json")
_CACHE_PATH = os.path.join(_DATA_DIR, "learned-product-codes.json")


def _load_valid_codes() -> set[str]:
    try:
        with open(_CODES_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        return set(doc.get("codes", {}).keys())
    except Exception as e:
        print(f"Error loading {_CODES_PATH}: {e}", file=sys.stderr)
        return set()


def _load_existing_cache() -> dict:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {
            "_meta": {
                "description": (
                    "Learned product-name -> AWS service code map, authored by "
                    "resolve_unknown_product_codes.py's tier-3 agy pass for no-code "
                    "bills (PDF/CSV/spreadsheet). Self-building: every accepted entry "
                    "is validated against data/aws-product-codes.json's closed set. "
                    "Safe to commit; grows across bills so the model is asked less over time."
                ),
            },
            "names": {},
        }
    except Exception as e:
        print(f"Error loading {_CACHE_PATH}: {e}", file=sys.stderr)
        return {"_meta": {}, "names": {}}


def find_proposal_files(jobs_root: str) -> list[str]:
    files = []
    if not os.path.exists(jobs_root):
        return files
    for root, _dirs, filenames in os.walk(jobs_root):
        if "learned_proposals.json" in filenames:
            files.append(os.path.join(root, "learned_proposals.json"))
    return sorted(files)


def collect_proposals(proposal_files: list[str], valid_codes: set[str]):
    # key -> {
    #   "product_name": str,
    #   "codes": dict[code, {"rows": int, "usd": float, "sources": list[str]}]
    # }
    grouped: dict[str, dict] = {}
    invalid_proposals: list[dict] = []

    for fpath in proposal_files:
        try:
            with open(fpath, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            print(f"Warning: could not read {fpath}: {e}", file=sys.stderr)
            continue

        meta = data.get("_meta", {})
        job_id = meta.get("job_id") or os.path.basename(os.path.dirname(os.path.dirname(fpath)))
        proposals = data.get("proposals", [])

        for p in proposals:
            prod_name = p.get("product_name", "").strip()
            code = p.get("service_code", "").strip()
            rows = int(p.get("evidence_rows", 0) or 0)
            usd = float(p.get("evidence_usd", 0.0) or 0.0)
            canon = p.get("canonical", "")

            if not prod_name or not code:
                continue

            if code not in valid_codes:
                invalid_proposals.append({
                    "product_name": prod_name,
                    "service_code": code,
                    "job_id": job_id,
                    "file": fpath,
                })
                continue

            key = prod_name.lower()
            if key not in grouped:
                grouped[key] = {
                    "product_name": prod_name,
                    "canonical": canon,
                    "codes": {},
                }

            if code not in grouped[key]["codes"]:
                grouped[key]["codes"][code] = {
                    "rows": 0,
                    "usd": 0.0,
                    "sources": [],
                }

            grouped[key]["codes"][code]["rows"] += rows
            grouped[key]["codes"][code]["usd"] += usd
            grouped[key]["codes"][code]["sources"].append(job_id)

    return grouped, invalid_proposals


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote learned product codes from jobs to shared cache.")
    parser.add_argument("jobs_root", help="Directory containing job folders to scan")
    parser.add_argument("--apply", action="store_true", help="Apply proposed additions to data/learned-product-codes.json")
    args = parser.parse_args()

    valid_codes = _load_valid_codes()
    if not valid_codes:
        print("ERROR: No valid AWS product codes loaded from data/aws-product-codes.json", file=sys.stderr)
        return 1

    cache_doc = _load_existing_cache()
    existing_names = cache_doc.get("names", {})

    files = find_proposal_files(args.jobs_root)
    print(f"Scanned {args.jobs_root}: found {len(files)} learned_proposals.json file(s).")

    grouped, invalid_proposals = collect_proposals(files, valid_codes)

    conflicts: list[dict] = []
    already_known: list[dict] = []
    to_add: dict[str, dict] = {}

    for key, item in sorted(grouped.items()):
        prod_name = item["product_name"]
        codes_dict = item["codes"]

        # Conflict 1: Multiple conflicting codes proposed across jobs for the same product
        if len(codes_dict) > 1:
            conflicts.append({
                "product_name": prod_name,
                "reason": "Multiple conflicting codes proposed across jobs",
                "details": {c: {"rows": d["rows"], "usd": round(d["usd"], 2), "jobs": d["sources"]} for c, d in codes_dict.items()},
            })
            continue

        code = list(codes_dict.keys())[0]
        stats = codes_dict[code]

        # Conflict 2: Code conflicts with existing committed mapping
        if key in existing_names:
            if existing_names[key] == code:
                already_known.append({
                    "product_name": prod_name,
                    "service_code": code,
                    "rows": stats["rows"],
                    "usd": round(stats["usd"], 2),
                })
            else:
                conflicts.append({
                    "product_name": prod_name,
                    "reason": f"Conflict with committed mapping ({existing_names[key]})",
                    "details": {
                        "committed": existing_names[key],
                        "proposed": code,
                        "rows": stats["rows"],
                        "usd": round(stats["usd"], 2),
                        "jobs": stats["sources"],
                    },
                })
            continue

        to_add[key] = {
            "product_name": prod_name,
            "service_code": code,
            "canonical": item.get("canonical", ""),
            "rows": stats["rows"],
            "usd": round(stats["usd"], 2),
            "jobs": stats["sources"],
        }

    # Summary Output
    print("\n" + "=" * 60)
    print("PROMOTION SUMMARY")
    print("=" * 60)

    if invalid_proposals:
        print(f"\n[!] Invalid Proposals (Service code not in closed set, skipped: {len(invalid_proposals)}):")
        for inv in invalid_proposals:
            print(f"  - '{inv['product_name']}' -> '{inv['service_code']}' (job: {inv['job_id']})")

    if conflicts:
        print(f"\n[!] Conflicts (Refused, manual review required: {len(conflicts)}):")
        for conf in conflicts:
            print(f"  - '{conf['product_name']}': {conf['reason']}")
            for cd, details in conf["details"].items():
                print(f"      {cd}: {details}")

    print(f"\n[*] Already Known (Unchanged: {len(already_known)}):")
    for ak in already_known:
        print(f"  = '{ak['product_name']}' -> {ak['service_code']} ({ak['rows']} rows, ${ak['usd']:,.2f})")

    print(f"\n[+] Additions to Promote ({len(to_add)}):")
    for key, info in sorted(to_add.items()):
        print(f"  + '{info['product_name']}' -> {info['service_code']} ({info['rows']} rows, ${info['usd']:,.2f})")

    print("\n" + "-" * 60)

    if args.apply:
        if not to_add:
            print("No new entries to apply.")
            return 0

        # Apply new names atomically
        for key, info in to_add.items():
            existing_names[key] = info["service_code"]

        cache_doc["names"] = dict(sorted(existing_names.items()))

        tmp_path = _CACHE_PATH + f".tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(cache_doc, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp_path, _CACHE_PATH)
            print(f"SUCCESS: Applied {len(to_add)} new learned product code(s) to {_CACHE_PATH}")
        except Exception as e:
            print(f"ERROR: Failed writing {_CACHE_PATH}: {e}", file=sys.stderr)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return 1
    else:
        print("Dry run complete. No files modified. Run with --apply to commit these additions.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
