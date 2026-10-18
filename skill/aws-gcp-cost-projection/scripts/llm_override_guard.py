#!/usr/bin/env python3
"""
llm_override_guard.py — minimal last-resort format/schema gate before DB writes.

Business-logic validation (SKU existence, tier safety, multiplier correctness,
region coverage) is now handled by the Phase 3 / Phase 5 LLM agent itself,
guided by data/validation-rules.json. The agent runs those checks before
writing review_fixes.json — by the time apply_review_fixes.py calls this
function the override has already been validated by the LLM.

This guard exists only as a final safety net for things that are cheap to
verify mechanically and catastrophic if wrong:
  1. SKU ID format — GCP SKU IDs are XXXX-XXXX-XXXX. A malformed string
     cannot be a real SKU regardless of what the LLM said.
  2. unit_multiplier type and sign — must be a positive number. A zero or
     negative multiplier produces $0 GCP cost silently; catching it here
     prevents a bad write even if the LLM prompt failed to catch it.
  3. Scope check — the row must have been flagged by auto_review.py. This
     is enforced in apply_review_fixes.py, not here; this function does not
     re-check scope.

All business rules (tier safety, intentional passthrough protection, region
coverage, service-SKU consistency, dimensional correctness) live in
data/validation-rules.json and are enforced by the LLM agent at prompt time.
"""
from __future__ import annotations
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_loader import load_data_config as _cfg

_review_cfg = _cfg("review-config")
_MAX_MULT_VCPU   = _review_cfg.get("max_unit_multiplier_vcpu",   512)
_MAX_MULT_RAM_GB = _review_cfg.get("max_unit_multiplier_ram_gb", 8192)

# GCP SKU ID format: three groups of 4 hex characters separated by hyphens.
_SKU_ID_RE = re.compile(r'^[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}$')


try:
    from apply_static_mappings import INTENTIONAL_PASSTHROUGH_NOTE_ILIKE
except Exception:
    INTENTIONAL_PASSTHROUGH_NOTE_ILIKE = [
        "%no GCS equivalent%",
        "%no GCP equivalent%",
        "%no direct GCP equivalent%",
    ]
try:
    from projection_view import compute_dominant_region
except Exception:
    compute_dominant_region = lambda _c: "us-central1"


def _is_intentional_passthrough_note(note: str | None, reason: str | None = None) -> bool:
    if reason:
        try:
            import passthrough_reasons as _pr
            return reason in _pr.INTENTIONAL_REASONS
        except Exception:
            pass
    if not note:
        return False
    note_lower = note.lower()
    for pattern in INTENTIONAL_PASSTHROUGH_NOTE_ILIKE:
        if pattern.replace("%", "").lower() in note_lower:
            return True
    return False


def validate_override(conn, aws_li_key, component, fix):
    """
    Final format/schema/rate gate before writing an LLM override to the DB.
    Returns (ok: bool, reason: str).

    Enforces:
      1. SKU ID format (XXXX-XXXX-XXXX).
      2. Multiplier type and sanity bounds.
      3. G-2: Protection for intentional-passthrough rows (e.g. no GCP equivalent).
      4. G-1: Sourced from facts — SKU must exist in gcp_sku_rates with an OnDemand
         rate in the row's region, global, or dominant region (no fabrication).
    """
    gcp_sku_id  = fix.get("gcp_sku_id")
    unit_mult   = fix.get("unit_multiplier")

    # 1. SKU ID format check
    if gcp_sku_id and not _SKU_ID_RE.match(str(gcp_sku_id)):
        return False, (
            f"gcp_sku_id {gcp_sku_id!r} does not match the GCP SKU ID format "
            f"(expected XXXX-XXXX-XXXX hex). This is not a real SKU ID — "
            f"run find-sku.sh to get the correct one."
        )

    # 2. Multiplier type and sign check
    if unit_mult is not None:
        try:
            mult_f = float(unit_mult)
        except (TypeError, ValueError):
            return False, f"unit_multiplier {unit_mult!r} is not a number"

        if mult_f <= 0:
            return False, (
                f"unit_multiplier must be > 0 (got {mult_f}). "
                f"A zero or negative multiplier produces $0 GCP cost."
            )

        comp = (component or "core").lower()
        limit = _MAX_MULT_RAM_GB if comp == "ram" else _MAX_MULT_VCPU
        if mult_f > limit:
            return False, (
                f"unit_multiplier {mult_f} exceeds the sanity limit of {limit} "
                f"for component {comp!r}. If this instance is genuinely this large, "
                f"update max_unit_multiplier_* in data/review-config.json."
            )

    # 3. G-2: Enforce intentional-passthrough protection in the guard
    # If the current row's projection_note or passthrough_reason marks it as intentional passthrough and
    # the override sets strategy='map' (or provides a SKU to map), reject.
    override_strategy = fix.get("strategy")
    is_mapping = override_strategy == "map" or (override_strategy is None and bool(gcp_sku_id))

    if conn is not None and aws_li_key:
        try:
            cols = {c[1].lower() for c in conn.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
            reason_expr = "passthrough_reason" if "passthrough_reason" in cols else "NULL"
            curr_row = conn.execute(
                f"SELECT projection_note, {reason_expr} FROM aws_li_to_gcp_li WHERE aws_li_key = ? LIMIT 1",
                [aws_li_key]
            ).fetchone()
            if curr_row and _is_intentional_passthrough_note(curr_row[0], curr_row[1]):
                if is_mapping:
                    return False, (
                        f"Row {aws_li_key} is an intentional passthrough ({curr_row[0]!r}, reason={curr_row[1]!r}). "
                        f"Overriding to strategy='map' violates intentional-passthrough protection."
                    )
        except Exception as e:
            # Table might not exist or other query error in isolated unit tests
            pass

    # 4. G-1: SKU must exist in catalog with a real rate in gcp_sku_rates
    # (fallback: row region -> global -> dominant region)
    if conn is not None and gcp_sku_id:
        # Check if gcp_sku_rates table exists
        try:
            has_rates = conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'gcp_sku_rates'"
            ).fetchone()
        except Exception:
            has_rates = None

        if has_rates:
            # Find row's region
            row_region = None
            try:
                cat_row = conn.execute(
                    "SELECT gcp_region FROM aws_li_catalog WHERE aws_li_key = ? LIMIT 1",
                    [aws_li_key]
                ).fetchone()
                if cat_row and cat_row[0]:
                    row_region = cat_row[0].strip()
            except Exception:
                pass

            if not row_region:
                try:
                    map_row = conn.execute(
                        "SELECT gcp_region FROM aws_li_to_gcp_li WHERE aws_li_key = ? LIMIT 1",
                        [aws_li_key]
                    ).fetchone()
                    if map_row and map_row[0]:
                        row_region = map_row[0].strip()
                except Exception:
                    pass

            dom_region = compute_dominant_region(conn)
            regions_to_check = [r for r in [row_region, "global", dom_region] if r]

            # Query gcp_sku_rates
            placeholders = ",".join(["?"] * len(regions_to_check))
            rate_row = conn.execute(
                f"""
                SELECT region, rate_usd FROM gcp_sku_rates
                WHERE gcp_sku_id = ?
                  AND pricing_type = 'OnDemand'
                  AND region IN ({placeholders})
                LIMIT 1
                """,
                [gcp_sku_id] + regions_to_check
            ).fetchone()

            if not rate_row:
                return False, (
                    f"gcp_sku_id {gcp_sku_id!r} has no OnDemand rate in gcp_sku_rates "
                    f"for region(s) {regions_to_check}. SKU must exist in catalog with a real rate."
                )

    return True, ""

