#!/usr/bin/env python3
"""
merge_mappings.py — Bulk-INSERT all mechanic-group mapping temp files into
aws_li_to_gcp_li in one atomic transaction with strict validation and SKU guardrails.

Usage:
    python3 merge_mappings.py <projection.duckdb> <mappings_dir>

The mappings_dir must contain one or more <group>_mappings.json files, each
a JSON array of mapping objects matching the aws_li_to_gcp_li schema.

Exits 0 on success. If any expected group file is missing, logs a warning and
proceeds with available files (missing groups are recorded in merge_report.json,
and downstream ensure_full_coverage.py acts as the backstop).
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from synthetic_skus import SYNTHETIC_SKU_IDS
from resolve_sku import (
    get_valid_catalog_services,
    get_valid_catalog_skus,
    resolve_intent_to_sku,
    CATALOG_DB,
)

# Columns that appear in a mapping record.
# Order matches the INSERT statement.
# M-3: gcp_sku_unit is explicitly included so mapped units are preserved.
COLUMNS = [
    "aws_li_key",
    "gcp_service",
    "gcp_sku_id",
    "gcp_sku_name",
    "gcp_sku_unit",
    "component",
    "strategy",
    "unit_multiplier",
    "gcp_region",
    "projection_note",
    "mapping_confidence",
    "is_workload",
    "break_down",
    "gcp_machine_type",
    "is_assumption",
    "assumption_note",
]


def _coerce_bool(val: any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in ("true", "1", "yes", "t"):
        return True
    if s in ("false", "0", "no", "f", ""):
        return False
    return default


def validate_row(
    r: dict, valid_li_keys: set[str] | None = None
) -> tuple[dict | None, str | None]:
    """Validate a single mapping row and coerce types (M-2).
    Returns (clean_row, None) on success or (None, reason) on failure.
    """
    if not isinstance(r, dict):
        return None, "row_is_not_a_dict"

    raw_key = r.get("aws_li_key")
    if not raw_key or not str(raw_key).strip():
        return None, "missing_aws_li_key"

    clean_key = str(raw_key).strip()
    if valid_li_keys is not None and clean_key not in valid_li_keys:
        return None, f"orphan_key:{clean_key}"

    strategy = r.get("strategy")
    if not strategy or not str(strategy).strip():
        return None, "missing_strategy"

    clean_strategy = str(strategy).strip()

    # Coerce unit_multiplier
    raw_mult = r.get("unit_multiplier")
    if raw_mult is None or raw_mult == "":
        clean_mult = 1.0
    else:
        try:
            clean_mult = float(raw_mult)
        except (ValueError, TypeError):
            return None, f"invalid_unit_multiplier:{raw_mult}"

    # Coerce mapping_confidence
    raw_conf = r.get("mapping_confidence")
    if raw_conf is None or raw_conf == "":
        clean_conf = 0.5
    else:
        try:
            clean_conf = float(raw_conf)
        except (ValueError, TypeError):
            clean_conf = 0.5

    clean_row = {
        "aws_li_key": clean_key,
        "gcp_service": str(r.get("gcp_service")).strip() if r.get("gcp_service") is not None else None,
        "gcp_sku_id": str(r.get("gcp_sku_id")).strip() if r.get("gcp_sku_id") is not None else None,
        "gcp_sku_name": str(r.get("gcp_sku_name")).strip() if r.get("gcp_sku_name") is not None else None,
        "gcp_sku_unit": str(r.get("gcp_sku_unit")).strip() if r.get("gcp_sku_unit") is not None else None,
        "component": str(r.get("component")).strip() if r.get("component") is not None else None,
        "strategy": clean_strategy,
        "unit_multiplier": clean_mult,
        "gcp_region": str(r.get("gcp_region")).strip() if r.get("gcp_region") is not None else None,
        "projection_note": str(r.get("projection_note")).strip() if r.get("projection_note") is not None else None,
        "mapping_confidence": clean_conf,
        "is_workload": _coerce_bool(r.get("is_workload"), default=True),
        "break_down": _coerce_bool(r.get("break_down"), default=False),
        "gcp_machine_type": str(r.get("gcp_machine_type")).strip() if r.get("gcp_machine_type") is not None else None,
        "is_assumption": _coerce_bool(r.get("is_assumption"), default=False),
        "assumption_note": (
            str(r.get("assumption_note") or r.get("assumption") or "").strip() or None
        ),
    }

    return clean_row, None


def load_mappings(mappings_dir: str) -> tuple[list[dict], list[str]]:
    """Return (all_rows, missing_groups)."""
    manifest_path = os.path.join(os.path.dirname(mappings_dir.rstrip("/")), "phase2_manifest.json")
    expected_groups: list[str] = []
    if os.path.exists(manifest_path):
        try:
            manifest = json.load(open(manifest_path, encoding="utf-8"))
            expected_groups = [
                g for g, meta in manifest.items()
                if not g.startswith("_") and isinstance(meta, dict) and meta.get("needs_llm", True)
            ]
        except Exception:
            pass

    all_rows: list[dict] = []
    missing: list[str] = []

    found_files = sorted(f for f in os.listdir(mappings_dir) if f.endswith("_mappings.json"))
    for fname in found_files:
        path = os.path.join(mappings_dir, fname)
        try:
            rows = json.load(open(path, encoding="utf-8"))
            if not isinstance(rows, list):
                print(f"WARNING: {fname} is not a JSON array — skipping", file=sys.stderr)
                continue
            all_rows.extend(rows)
        except Exception as e:
            print(f"WARNING: could not read {fname}: {e}", file=sys.stderr)

    found_groups = {f.replace("_mappings.json", "") for f in found_files}
    for g in expected_groups:
        if g not in found_groups:
            missing.append(g)

    return all_rows, missing


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS aws_li_to_gcp_li (
            aws_li_key          TEXT,
            gcp_service         TEXT,
            gcp_sku_id          TEXT,
            gcp_sku_name        TEXT,
            gcp_sku_unit        TEXT,
            component           TEXT,
            strategy            TEXT,
            unit_multiplier     DOUBLE,
            gcp_region          TEXT,
            projection_note     TEXT,
            mapping_confidence  DOUBLE,
            is_workload         BOOLEAN,
            break_down          BOOLEAN,
            gcp_machine_type    TEXT,
            is_assumption       BOOLEAN,
            assumption_note     TEXT
        )
    """)
    existing_cols = {r[1].lower() for r in con.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
    if "gcp_sku_unit" not in existing_cols:
        con.execute("ALTER TABLE aws_li_to_gcp_li ADD COLUMN gcp_sku_unit TEXT")
    if "gcp_machine_type" not in existing_cols:
        con.execute("ALTER TABLE aws_li_to_gcp_li ADD COLUMN gcp_machine_type TEXT")
    if "is_assumption" not in existing_cols:
        con.execute("ALTER TABLE aws_li_to_gcp_li ADD COLUMN is_assumption BOOLEAN")
    if "assumption_note" not in existing_cols:
        con.execute("ALTER TABLE aws_li_to_gcp_li ADD COLUMN assumption_note TEXT")


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <projection.duckdb> <mappings_dir>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    mappings_dir = sys.argv[2]

    if not os.path.exists(mappings_dir):
        print(f"ERROR: mappings dir not found: {mappings_dir}", file=sys.stderr)
        sys.exit(1)

    raw_rows, missing = load_mappings(mappings_dir)

    if missing:
        print(f"WARNING: {len(missing)} group(s) have no mapping file — proceeding with available files:", file=sys.stderr)
        for g in missing:
            print(f"  missing: {g}_mappings.json", file=sys.stderr)

    con = duckdb.connect(db_path)
    ensure_table(con)

    # Referential integrity check: load valid line item keys and mechanic groups from aws_li_catalog
    valid_li_keys: set[str] = set()
    li_mechanic_groups: dict[str, str] = {}
    try:
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        if "aws_li_catalog" in tables:
            cols = {c[1] for c in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
            if "mechanic_group" in cols:
                for k, mg in con.execute("SELECT aws_li_key, mechanic_group FROM aws_li_catalog").fetchall():
                    if k:
                        valid_li_keys.add(k)
                        if mg:
                            li_mechanic_groups[k] = mg
            else:
                valid_li_keys = {r[0] for r in con.execute("SELECT aws_li_key FROM aws_li_catalog").fetchall() if r[0]}
    except Exception as e:
        print(f"WARNING: could not query aws_li_catalog for key validation: {e}", file=sys.stderr)

    # Validate rows (M-2)
    clean_rows: list[dict] = []
    rejected: list[dict] = []
    orphan_keys_count = 0

    for r in raw_rows:
        clean, reason = validate_row(r, valid_li_keys if valid_li_keys else None)
        if clean is not None:
            clean_rows.append(clean)
        else:
            rejected.append({"aws_li_key": r.get("aws_li_key"), "reason": reason})
            if reason and reason.startswith("orphan_key"):
                orphan_keys_count += 1

    if rejected:
        print(f"REJECTED {len(rejected)} mapping rows: {len(rejected)} invalid or orphan rows", file=sys.stderr)

    # C-1 / G-1: Check catalog availability (fail-closed tracking)
    valid_skus = get_valid_catalog_skus()
    valid_services = get_valid_catalog_services()
    sku_validation_ran = bool(valid_skus)

    if not sku_validation_ran:
        print("WARNING: catalog.duckdb unavailable or empty — SKU validation skipped (sku_validation_ran=false)", file=sys.stderr)

    # C-1c: If catalog unavailable, fail-closed by capping confidence on all SKU-bearing rows
    unvalidated_skus_count = 0
    if not sku_validation_ran:
        for r in clean_rows:
            if r.get("strategy") in ("map", "break_down") and r.get("gcp_sku_id"):
                r["mapping_confidence"] = min(r.get("mapping_confidence", 0.5), 0.5)
                note = "[sku-unvalidated: catalog unavailable]"
                existing = r.get("projection_note") or ""
                if note not in existing:
                    r["projection_note"] = f"{existing} {note}".strip()
                unvalidated_skus_count += 1

    # G-1 & G-1b: Intent resolution and SKU validation
    nulled_count = 0
    for r in clean_rows:
        svc = r.get("gcp_service")
        # Validate gcp_service against catalog if available
        if valid_services and svc and svc not in valid_services:
            if r.get("strategy") in ("map", "break_down"):
                r["strategy"] = "passthrough"
                r["gcp_sku_id"] = None
                r["mapping_confidence"] = min(r.get("mapping_confidence", 0.5), 0.4)
                note = f"[invalid-service: '{svc}' not in catalog services; downgraded to passthrough]"
                r["projection_note"] = f"{r.get('projection_note') or ''} {note}".strip()

        # Handle SKU ID resolution / guardrail
        if r.get("strategy") in ("map", "break_down"):
            sku = r.get("gcp_sku_id")
            if not sku and r.get("gcp_sku_name"):
                # G-1 & G-1b: Resolve intent (service + sku_name + region) deterministically with unit check
                res = resolve_intent_to_sku(
                    svc,
                    r.get("gcp_sku_name"),
                    r.get("gcp_region"),
                    expected_unit=r.get("gcp_sku_unit"),
                    mechanic_group=li_mechanic_groups.get(r.get("aws_li_key")),
                    component=r.get("component"),
                )
                if res.sku_id:
                    r["gcp_sku_id"] = res.sku_id
                    if not r.get("gcp_sku_unit") and res.unit:
                        r["gcp_sku_unit"] = res.unit
                    if res.note:
                        r["mapping_confidence"] = min(r.get("mapping_confidence", 0.5), 0.5)
                        r["projection_note"] = f"{r.get('projection_note') or ''} {res.note}".strip()
                else:
                    # Unit mismatch or no matching SKU -> passthrough + loud note
                    r["strategy"] = "passthrough"
                    r["gcp_sku_id"] = None
                    r["mapping_confidence"] = min(r.get("mapping_confidence", 0.5), 0.4)
                    note = res.note or f"[no-catalog-sku: no catalog SKU for '{r.get('gcp_sku_name')}'; downgraded to passthrough]"
                    r["projection_note"] = f"{r.get('projection_note') or ''} {note}".strip()
            elif sku:
                # Existing SKU ID -> validate against catalog / synthetic whitelist
                if sku_validation_ran and sku not in valid_skus and sku not in SYNTHETIC_SKU_IDS:
                    print(f"  INVALID SKU nulled: {sku!r} on {r.get('aws_li_key')} — not in catalog", file=sys.stderr)
                    r["gcp_sku_id"] = None
                    r["strategy"] = "passthrough"
                    r["mapping_confidence"] = min(r.get("mapping_confidence", 0.5), 0.4)
                    note = f"[fabricated-sku: '{sku}' not in catalog; downgraded to passthrough]"
                    r["projection_note"] = f"{r.get('projection_note') or ''} {note}".strip()
                    nulled_count += 1

    # Deduplication pass 1: exact (aws_li_key, component) duplicates
    seen: dict[tuple, dict] = {}
    deduped_exact_count = 0
    for r in clean_rows:
        key = (r.get("aws_li_key"), r.get("component"))
        if key in seen:
            deduped_exact_count += 1
        seen[key] = r
    deduped_rows = list(seen.values())

    # Deduplication pass 2: break_down + all-passthrough collapse
    by_key: dict[str, list[dict]] = defaultdict(list)
    for r in deduped_rows:
        by_key[r.get("aws_li_key")].append(r)

    collapsed_rows: list[dict] = []
    collapsed_count = 0
    for li_key, group in by_key.items():
        all_breakdown_passthrough = (
            len(group) > 1
            and all(r.get("break_down") and r.get("strategy") == "passthrough" for r in group)
        )
        if all_breakdown_passthrough:
            representative = group[0].copy()
            representative["break_down"] = False
            representative["component"] = None
            representative["unit_multiplier"] = 1.0
            collapsed_rows.append(representative)
            collapsed_count += 1
            print(
                f"  COLLAPSED {len(group)} break_down+passthrough rows → 1 passthrough: {li_key}",
                file=sys.stderr,
            )
        else:
            collapsed_rows.extend(group)

    # Build tuples in column order
    records = []
    for r in collapsed_rows:
        row_dict = dict(r)
        if not row_dict.get("assumption_note") and row_dict.get("assumption"):
            row_dict["assumption_note"] = row_dict["assumption"]
        records.append(tuple(row_dict.get(col) for col in COLUMNS))

    # M-1: Atomic transaction (never DELETE before records are proven insertable)
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM aws_li_to_gcp_li")
        if records:
            placeholders = ", ".join(["?"] * len(COLUMNS))
            col_list = ", ".join(COLUMNS)
            con.executemany(
                f"INSERT INTO aws_li_to_gcp_li ({col_list}) VALUES ({placeholders})",
                records,
            )
        con.execute("COMMIT")
    except Exception as e:
        con.execute("ROLLBACK")
        con.close()
        print(f"FATAL: merge failed during atomic insert: {e}", file=sys.stderr)
        raise

    # M-5: Emit structured merge report
    audit_dir = os.path.dirname(os.path.abspath(db_path))
    report_path = os.path.join(audit_dir, "merge_report.json")
    report_data = {
        "inserted": len(records),
        "rejected": len(rejected),
        "nulled_skus": nulled_count,
        "deduped_exact": deduped_exact_count,
        "collapsed_breakdown": collapsed_count,
        "orphan_keys": orphan_keys_count,
        "sku_validation_ran": sku_validation_ran,
        "unvalidated_skus": unvalidated_skus_count,
        "missing_groups": missing,
        "rejected_detail": rejected,
    }
    try:
        with open(report_path, "w", encoding="utf-8") as rf:
            json.dump(report_data, rf, indent=2)
    except Exception as e:
        print(f"WARNING: could not write merge_report.json: {e}", file=sys.stderr)

    # Summary stats
    stats = con.execute("""
        SELECT
            m.mechanic_group,
            COUNT(*) AS mapped_rows
        FROM aws_li_to_gcp_li li
        JOIN aws_li_catalog m USING (aws_li_key)
        GROUP BY m.mechanic_group
        ORDER BY mapped_rows DESC
    """).fetchall() if valid_li_keys else []

    total = sum(r[1] for r in stats)
    print(f"\n{'mechanic_group':<25}  {'mapped_rows':>12}")
    print("-" * 40)
    for group, cnt in stats:
        print(f"{group or 'unknown':<25}  {cnt:>12}")
    print("-" * 40)
    print(f"{'TOTAL':<25}  {total:>12}")

    con.close()
    print(f"\nMerge complete. {len(records)} rows inserted into aws_li_to_gcp_li.")
    sys.exit(0)


if __name__ == "__main__":
    main()
