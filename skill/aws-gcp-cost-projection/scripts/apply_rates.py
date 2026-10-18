#!/usr/bin/env python3
import duckdb
import os
import sys
import json
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from log_utils import get_logger, fatal
from projection_view import create_projection_view, compute_dominant_region
from egress_rates import EGRESS_SKUS
from synthetic_skus import SYNTHETIC_SKU_IDS
from resolve_sku import resolve_intent_to_sku
from apply_static_mappings import GCP_COMPUTE_ENGINE, CUD_PCT_FALLBACK as _CUD_PCT_FALLBACK

log = get_logger("apply_rates")

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
DATA_DIR = os.path.join(os.environ.get("SKILL_DIR", ""), "data")
CATALOG_DB = os.path.join(DATA_DIR, "catalog.duckdb")


def _load_tpu_rates():
    """Pinned Cloud TPU on-demand rates (data/tpu-rates.json). Catalog has no
    on-demand TPU SKU, so family_mapper.map_tpu_row emits a synthetic SKU whose
    rate is injected here, same pattern as EGRESS_SKUS / Looker Studio."""
    try:
        with open(os.path.join(DATA_DIR, "tpu-rates.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


_TPU_RATES = _load_tpu_rates()
_TPU_SKU_ID = _TPU_RATES.get("synthetic_sku_id", "TPU-V5E-ONDEMAND")


def inject_tpu_rates(conn):
    """Insert the pinned TPU v5e on-demand rate into gcp_sku_rates for every
    configured region plus a 'global' fallback (so the projection view's
    COALESCE(regional, global, any) always resolves). Injects OnDemand only;
    Reserved/Spot/CUD TPU columns are not pinned and will fall back."""
    if not _TPU_RATES:
        return
    sku = _TPU_RATES.get("synthetic_sku_id", "TPU-V5E-ONDEMAND")
    name = _TPU_RATES.get("gcp_sku_name", "Cloud TPU v5e (on-demand, per chip-hour)")
    rows = dict(_TPU_RATES.get("by_region", {}))
    default = _TPU_RATES.get("default_usd_per_chip_hour")
    if default is not None:
        rows.setdefault("global", default)
    for region, rate in rows.items():
        conn.execute(
            """
            INSERT INTO gcp_sku_rates VALUES
            (?, 'Cloud TPU', ?, 'Compute', 'TPU', 'OnDemand', ?, 'chip-hour', ?, 'canonical-tpu', 'https://cloud.google.com/tpu/pricing')
            ON CONFLICT DO NOTHING
            """,
            (sku, name, region, rate),
        )


def load_cud_pct():
    """Load CUD multipliers from data/cud_pct.json, falling back to the dict above."""
    json_path = os.path.join(DATA_DIR, "cud_pct.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                raw = json.load(f)
            result = {}
            for svc, vals in raw.items():
                if svc == "_meta":
                    continue
                if isinstance(vals, dict) and "1yr_multiplier" in vals and "3yr_multiplier" in vals:
                    result[svc] = (float(vals["1yr_multiplier"]), float(vals["3yr_multiplier"]))
            if result:
                return result
        except Exception as e:
            log.warning(f"Could not load cud_pct.json ({e}); using fallback multipliers")
    return dict(_CUD_PCT_FALLBACK)

CONTAINER_CODES = {
    "us": ["us-central1", "us-east1", "us-east4", "us-east5", "us-south1", "us-west1", "us-west2", "us-west3", "us-west4"],
    "eu": ["europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west6", "europe-west8", "europe-west9", "europe-west10", "europe-west12", "europe-north1", "europe-central2", "europe-southwest1"],
    "europe": ["europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west6", "europe-west8", "europe-west9", "europe-west10", "europe-west12", "europe-north1", "europe-central2", "europe-southwest1"],
    "asia": ["asia-east1", "asia-east2", "asia-northeast1", "asia-northeast2", "asia-northeast3", "asia-south1", "asia-south2", "asia-southeast1", "asia-southeast2"],
    "northamerica": ["northamerica-northeast1", "northamerica-northeast2", "northamerica-south1"],
    "southamerica": ["southamerica-east1", "southamerica-west1"],
    "australia": ["australia-southeast1", "australia-southeast2"],
    "me": ["me-central1", "me-central2", "me-west1"],
    "middleeast": ["me-central1", "me-central2", "me-west1"],
    "africa": ["africa-south1"]
}

def blended_rate(tiered_rates, total_qty):
    if not tiered_rates:
        return 0.0
    total_cost = 0.0
    for i, tier in enumerate(tiered_rates):
        tier_start = tier.get("startUsageAmount", 0)
        tier_end = tiered_rates[i+1].get("startUsageAmount") if i+1 < len(tiered_rates) else float("inf")
        tier_qty = max(0, min(total_qty, tier_end) - tier_start)
        total_cost += tier_qty * tier.get("rate", 0)
    return total_cost / total_qty if total_qty > 0 else tiered_rates[0].get("rate", 0.0)


# P-14: Canonical GCP internet egress tiers (cumulative monthly GB ladder)
_INTERNET_EGRESS_TIERS = [
    {"startUsageAmount": 0, "rate": 0.12},
    {"startUsageAmount": 1024, "rate": 0.11},
    {"startUsageAmount": 10240, "rate": 0.08},
]


# Resolution now routes through resolve_sku.resolve_intent_to_sku (G-1c)


import re as _re_mod
# Only Inferentia/Trainium/DL-AMI have no GCP equivalent at all — these stay as passthrough.
# NVIDIA GPU families (g*, p*) ARE mapped by family_mapper.py to G2/A2/A3+GPU components;
# those mappings are intentionally NOT overridden here.
_ACCEL_RE = _re_mod.compile(r'\b(inf\d|trn\d|dl\d|vt\d)[a-z0-9]*\.')


def enforce_accelerator_passthrough(conn):
    """AI accelerators / GPUs (Inferentia, Trainium, GPU families) must NEVER be
    priced as a CPU VM. Deterministically collapse any such row's mapping to a
    single passthrough (manual-review) row — overriding whatever Phase 2/5 did.
    UPDATE-only (no delete): keep the first component row as passthrough, set the
    rest to ignore, so cost = AWS parity once (no break_down double-count)."""
    cols = {r[1].lower() for r in conn.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
    if "is_workload" not in cols:
        return 0
    rows = conn.execute("""
        SELECT aws_li_key, COALESCE(operation,'')||' '||COALESCE(instance_type,'') sig
        FROM aws_li_catalog WHERE is_workload
    """).fetchall()
    accel = [k for k, sig in rows
             if "inferentia" in sig.lower() or "trainium" in sig.lower() or _ACCEL_RE.search(sig.lower())]
    fixed = 0
    for k in accel:
        comps = conn.execute(
            "SELECT rowid, strategy FROM aws_li_to_gcp_li WHERE aws_li_key = ? ORDER BY rowid", [k]
        ).fetchall()
        if not comps or all(s == "passthrough" for _, s in comps):
            continue
        keep = comps[0][0]
        conn.execute("UPDATE aws_li_to_gcp_li SET strategy='ignore', unit_multiplier=0, "
                     "projection_note='accelerator component folded into passthrough' "
                     "WHERE aws_li_key=? AND rowid<>?", [k, keep])
        conn.execute("UPDATE aws_li_to_gcp_li SET strategy='passthrough', "
                     "passthrough_reason='accelerator_manual_sizing', decided_by='apply_rates.py', "
                     "gcp_service='Manual Sizing Required', gcp_sku_id=NULL, gcp_sku_name=NULL, "
                     "unit_multiplier=NULL, mapping_confidence=0.3, component='accelerator', "
                     "projection_note='AI accelerator (Inferentia/Trainium/GPU) — Manual sizing required (A3/A2/G2/L4/H100/etc.)' "
                     "WHERE aws_li_key=? AND rowid=?", [k, keep])
        fixed += 1
    if fixed:
        log.info(f"Enforced accelerator passthrough on {fixed} row(s)")
    return fixed


_LICENSE_MARKER = "[license-premium-not-modeled]"


def remap_elasticache_to_memorystore(conn):
    """ElastiCache (cache) must map to Memorystore, not Cloud SQL. The LLM often
    mis-picks Cloud SQL's 'Custom Core/RAM' SKUs; Memorystore for Memcached has
    identically-named 'Custom Core/RAM' SKUs with the same vCPU+RAM break_down
    model, so we relabel the service and clear the sku_id — apply_rates then
    re-resolves the Memorystore SKU by the same name. Deterministic, no rate table."""
    n = conn.execute("""
        SELECT COUNT(*) FROM aws_li_to_gcp_li m JOIN aws_li_catalog cat USING (aws_li_key)
        WHERE LOWER(cat.product) LIKE '%elasticache%' AND m.gcp_service = 'Cloud SQL'
    """).fetchone()[0]
    if not n:
        return 0
    conn.execute("""
        UPDATE aws_li_to_gcp_li SET
            gcp_service = 'Cloud Memorystore for Memcached',
            gcp_sku_name = CASE
                WHEN LOWER(COALESCE(component, '')) = 'ram' OR LOWER(COALESCE(gcp_sku_name, '')) LIKE '%ram%'
                    THEN 'Memorystore for Memcached Custom RAM'
                ELSE 'Memorystore for Memcached Custom Core'
            END,
            gcp_sku_id = NULL,
            projection_note = 'ElastiCache → Memorystore for Memcached (cache workload; not Cloud SQL)'
        WHERE aws_li_key IN (
            SELECT cat.aws_li_key FROM aws_li_catalog cat
            WHERE LOWER(cat.product) LIKE '%elasticache%'
        ) AND gcp_service = 'Cloud SQL'
    """)
    log.info(f"Remapped {n} ElastiCache row(s) to Memorystore for Memcached")
    return n


def fix_managed_db_storage_rows(conn, *_ignored):
    """Aurora/RDS 'Storage and I/O' rows are billed in GB-Mo, not Hrs. The LLM
    maps them to vCPU/RAM SKUs (correct for instance rows), which multiplies
    millions of GB against $/vCPU-hr and blows the total by 10,000x.

    Detect any managed-DB row where pricing_unit is not hours and the current
    SKU is a vCPU/RAM SKU, then remap to the correct Cloud SQL SSD Storage SKU.
    This is the same class of fix as enforce_accelerator_passthrough — a unit
    mismatch the LLM cannot reliably avoid.

    Two follow-on bugs in the original version of this fix, both now closed:
      - It updated every matching row (core AND ram) independently via
        `WHERE aws_li_key = ?` with no component filter, but never reset
        unit_multiplier — leaving the OLD vCPU-count/RAM-GiB-count multiplier
        in place against a $/GB-mo storage rate, and billing the same storage
        SKU twice (once per leftover component). Now: keep only the 'core'
        component as a single 'storage' row with unit_multiplier=1.0 (rate
        already applies per GB-mo of c.total_usage), and delete the 'ram' row.
      - It always picked the Zonal storage tier, even for Multi-AZ (AWS's HA
        deployment), which should map to Cloud SQL's Regional (HA) storage
        tier. Now checks usage_type for "Multi-AZ".
    """

    # Aurora Serverless v2 ACU-hour rows (usage_type contains "ServerlessV2" or
    # ":ACU") are legitimately mapped to a vCPU/RAM SKU by map_managed_db() and
    # must never be caught here. The pricing_unit exclusion below used to be the
    # only guard against that ('ACU-Hrs'/'ACU-hours' excluded) — but ingest.py
    # leaves pricing_unit as an empty string ('') for these rows rather than
    # 'ACU-Hrs', and '' is neither NULL nor literally in the exclusion list, so
    # it slipped through and this function silently overwrote a CORRECT compute
    # mapping with an incorrect storage one. Excluding by usage_type directly is
    # the real fix — it doesn't depend on pricing_unit being populated correctly.
    cols = {r[1].lower() for r in conn.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
    if "pricing_unit" not in cols:
        return 0
    deploy_col = "c.deployment_option" if "deployment_option" in cols else "'' AS deployment_option"
    rows = conn.execute(f"""
        SELECT m.aws_li_key, m.component, c.gcp_region,
               c.product, c.usage_type, {deploy_col}
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.strategy = 'map'
          AND m.gcp_service = 'Cloud SQL'
          AND (m.gcp_sku_name ILIKE '%vCPU%' OR m.gcp_sku_name ILIKE '% RAM%'
               OR m.gcp_sku_name ILIKE '%Core%')
          AND c.pricing_unit NOT IN ('Hrs', 'hours', 'Hour', 'ACU-Hrs', 'ACU-hours', '')
          AND c.pricing_unit IS NOT NULL
          AND COALESCE(c.usage_type, '') NOT ILIKE '%ServerlessV2%'
          AND COALESCE(c.usage_type, '') NOT ILIKE '%:ACU%'
          AND c.line_item_type NOT IN ('DiscountedUsage', 'SavingsPlanCoveredUsage')
          AND (c.product ILIKE '%Aurora%' OR c.product ILIKE '%RDS%'
               OR c.product ILIKE '%Relational%')
    """).fetchall()

    if not rows:
        return 0

    # Group by aws_li_key — a single AWS storage/IO line item can carry both
    # 'core' and 'ram' component rows from the original (wrong) vCPU/RAM mapping.
    by_key = {}
    for key, component, gcp_region, product, usage_type, deployment_option in rows:
        by_key.setdefault(key, {"components": [], "gcp_region": gcp_region,
                                 "product": product, "usage_type": usage_type,
                                 "deployment_option": deployment_option})
        by_key[key]["components"].append(component)

    fixed = 0
    for key, info in by_key.items():
        gcp_region, product, usage_type = info["gcp_region"], info["product"], info["usage_type"]
        deploy_opt = info.get("deployment_option") or ""
        blob = f"{product or ''} {usage_type or ''} {deploy_opt}".lower()
        is_multi_az = "multi-az" in blob or "multiaz" in blob
        tier = "Regional" if is_multi_az else "Zonal"

        # Determine storage engine from product name for SKU precision.
        # Check MySQL / MariaDB first (AR-E) so "Amazon Aurora (MySQL)" is not
        # misclassified as PostgreSQL by the "aurora" substring check.
        prod_lower = (product or "").lower()
        if "mysql" in prod_lower or "mariadb" in prod_lower:
            storage_sku = f"Cloud SQL for MySQL: {tier} - SSD storage"
        elif "postgresql" in prod_lower or "aurora" in prod_lower:
            storage_sku = f"Cloud SQL for PostgreSQL: {tier} - SSD storage"
        else:
            storage_sku = f"Cloud SQL: {tier} - SSD storage"

        res_sql = resolve_intent_to_sku("Cloud SQL", storage_sku, gcp_region, expected_unit="storage-month")
        new_sku_id = res_sql.sku_id if res_sql else None

        # Collapse to a single 'storage' component row — delete any extras
        # (e.g. the leftover 'ram' row) so storage is never billed twice.
        keep_component = info["components"][0]
        extra_components = info["components"][1:]
        if extra_components:
            placeholders = ",".join(["?"] * len(extra_components))
            conn.execute(
                f"DELETE FROM aws_li_to_gcp_li WHERE aws_li_key = ? AND component IN ({placeholders})",
                [key] + extra_components,
            )

        if not new_sku_id:
            # Fallback: passthrough rather than keep the wrong vCPU SKU
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET
                    strategy = 'passthrough',
                    passthrough_reason = 'unit_model_mismatch',
                    decided_by = 'apply_rates.py',
                    gcp_sku_id = NULL, gcp_sku_name = NULL,
                    component = 'storage',
                    unit_multiplier = 1.0,
                    mapping_confidence = 0.6,
                    projection_note = 'Aurora/RDS Storage+IO row — unit mismatch with vCPU SKU; '
                                      'passthrough at cost parity until Cloud SQL storage SKU resolved'
                WHERE aws_li_key = ? AND component = ?
            """, [key, keep_component])
        else:
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET
                    gcp_sku_id = ?,
                    gcp_sku_name = ?,
                    component = 'storage',
                    unit_multiplier = 1.0,
                    mapping_confidence = 0.85,
                    projection_note = ?
                WHERE aws_li_key = ? AND component = ?
            """, [new_sku_id, storage_sku,
                  f"Aurora/RDS Storage+IO → {storage_sku} (unit-mismatch fix: was vCPU/RAM SKU"
                  + (", Multi-AZ→Regional tier)" if is_multi_az else ")"),
                  key, keep_component])
        fixed += 1

    if fixed:
        log.info(f"Fixed {fixed} managed-DB storage row(s) mis-mapped to vCPU SKUs")
    return fixed


def flag_license_exposure(conn):
    """Flag commercial-license rows whose GCP license is NOT modeled, so such a
    bill is never SILENTLY under-projected. Windows / SQL Server / RHEL / Ubuntu Pro
    / SUSE licenses ARE modeled now (apply_static_mappings emits a 'license' /
    'sql_license' component), so those rows are EXCLUDED here via the NOT EXISTS
    check and keep full confidence. Only rows with a commercial OS/DB (e.g. Oracle
    BYOL) that received NO license component get the note + confidence cap. Runs
    after the Phase-5 LLM (so it can't be clobbered) and in Phase 4. Idempotent via
    the marker guard. Deterministic — no rate table."""
    try:
        rows = conn.execute(f"""
            SELECT DISTINCT m.aws_li_key,
                   COALESCE(cat.operating_system,'') os, COALESCE(cat.database_engine,'') eng
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog cat USING (aws_li_key)
            WHERE cat.is_workload AND m.strategy IN ('map','break_down')
              AND COALESCE(m.license_status, '') != 'premium_not_modeled'
              AND (
                LOWER(COALESCE(cat.operating_system,'')) LIKE '%windows%'
                OR LOWER(COALESCE(cat.operating_system,'')) LIKE '%oracle%'
                OR LOWER(COALESCE(cat.database_engine,'')) LIKE '%sql server%'
                OR LOWER(COALESCE(cat.database_engine,'')) LIKE '%sqlserver%'
                OR LOWER(COALESCE(cat.database_engine,'')) LIKE '%oracle%'
                OR LOWER(COALESCE(cat.operation,'')) LIKE '%sql server%'
                OR LOWER(COALESCE(cat.operation,'')) LIKE '%oracle%'
                OR LOWER(COALESCE(cat.usage_type,'')) LIKE '%windows%'
                OR LOWER(COALESCE(cat.product,'')) LIKE '%oracle%'
              )
              AND NOT EXISTS (
                SELECT 1 FROM aws_li_to_gcp_li lic
                WHERE lic.aws_li_key = m.aws_li_key
                  AND lic.component IN ('license', 'sql_license')
              )
        """).fetchall()
    except Exception as e:
        log.warning(f"License-exposure flag skipped: {e}")
        return 0

    flagged = 0
    for aws_li_key, os_, eng in rows:
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET license_status = 'premium_not_modeled',
                mapping_confidence = LEAST(COALESCE(mapping_confidence, 1.0), 0.5)
            WHERE aws_li_key = ?
        """, (aws_li_key,))
        flagged += 1
    if flagged:
        log.info(f"Flagged {flagged} license-exposed row(s) — confidence capped")
    return flagged


def _write_progress(phase_name="Rate-Card Fill", note="Fetching GCP rates via script"):
    try:
        with open(os.path.join(JOB_DIR, "progress.json"), "w") as f:
            json.dump({"phase": 4, "phase_name": phase_name, "last_activity": note}, f)
    except Exception as e:
        log.warning(f"Could not write progress.json: {e}")


def _write_rate_gaps(gaps):
    """Write rate-fill-gaps.md listing unreachable SKUs for Phase 5."""
    if not gaps:
        return
    out = os.path.join(JOB_DIR, "projection-audit", "rate-fill-gaps.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Rate-Fill Gaps\n\n")
        f.write("Rows moved to `outlier_triage` because no reachable rate was found "
                "for their region. Phase 5 owns resolution.\n\n")
        f.write("| gcp_service | gcp_region | gcp_sku_id | unreachable_rows |\n")
        f.write("|---|---|---|---|\n")
        for svc, region, sku_id, count in gaps:
            f.write(f"| {svc} | {region} | {sku_id} | {count} |\n")
    log.info(f"Wrote rate-fill-gaps.md ({len(gaps)} gap(s))")


def main():
    _write_progress()

    if not os.path.exists(DB_PATH):
        fatal(log, "projection.duckdb not found — cannot fill rates", phase=4)
        return

    conn = duckdb.connect(DB_PATH)

    try:
        import row_state
        row_state.ensure_schema(conn)
        import backfill_row_state
        backfill_row_state.backfill_row_state(conn)
    except Exception as e:
        log.warning(f"ensure_schema / backfill_row_state failed: {e}")

    # Snapshot rows whose sku_id was already pinned before apply_rates ran.
    # These come from static mappers (resolve_sku) or from LLM-provided exact IDs.
    # They get rate_source='exact_sku'. Rows still NULL here are word-overlap resolved.
    pinned_keys = set(
        r[0] for r in conn.execute(
            "SELECT aws_li_key FROM aws_li_to_gcp_li WHERE gcp_sku_id IS NOT NULL AND strategy IN ('map','break_down')"
        ).fetchall()
    )

    # Auto-resolve NULL gcp_sku_id for mapped rows using catalog lookup rules.
    # This prevents Phase 5 from seeing NULL projected cost and wrongly setting passthrough.
    cat_cols = {r[1].lower() for r in conn.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
    mg_expr = "c.mechanic_group" if "mechanic_group" in cat_cols else "NULL"
    null_sku_rows = conn.execute(f"""
        SELECT m.aws_li_key, m.gcp_service, m.gcp_sku_name, c.gcp_region,
               m.gcp_sku_unit, {mg_expr}, m.component
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.gcp_sku_id IS NULL
          AND m.strategy IN ('map', 'break_down')
    """).fetchall()

    resolved = 0
    word_overlap_keys = set()
    for aws_li_key, gcp_service, gcp_sku_name, gcp_region, gcp_sku_unit, mechanic_group, component in null_sku_rows:
        res = resolve_intent_to_sku(
            gcp_service,
            gcp_sku_name,
            gcp_region,
            expected_unit=gcp_sku_unit,
            mechanic_group=mechanic_group,
            component=component,
        )
        comp_filter = "AND component = ?" if component is not None else "AND component IS NULL"
        comp_args = [component] if component is not None else []

        if res.sku_id:
            if res.note:
                conn.execute(
                    f"""UPDATE aws_li_to_gcp_li 
                       SET gcp_sku_id = ?,
                           gcp_sku_unit = COALESCE(?, gcp_sku_unit),
                           rate_source = COALESCE(rate_source, 'word_overlap'),
                           projection_note = COALESCE(NULLIF(projection_note, '') || '; ', '') || ?
                       WHERE aws_li_key = ? AND gcp_sku_id IS NULL {comp_filter}""",
                    [res.sku_id, res.unit, res.note, aws_li_key] + comp_args,
                )
                if "[unit-unverified" in res.note:
                    conn.execute(
                        f"UPDATE aws_li_to_gcp_li SET mapping_confidence = LEAST(mapping_confidence, 0.5) WHERE aws_li_key = ? {comp_filter}",
                        [aws_li_key] + comp_args,
                    )
            else:
                conn.execute(
                    f"""UPDATE aws_li_to_gcp_li 
                       SET gcp_sku_id = ?,
                           gcp_sku_unit = COALESCE(?, gcp_sku_unit),
                           rate_source = COALESCE(rate_source, 'word_overlap')
                       WHERE aws_li_key = ? AND gcp_sku_id IS NULL {comp_filter}""",
                    [res.sku_id, res.unit, aws_li_key] + comp_args,
                )
            word_overlap_keys.add(aws_li_key)
            resolved += 1
            log.debug(f"Resolved SKU: {gcp_service} / {gcp_sku_name!r} -> {res.sku_id}")
        else:
            note = res.note or "[unresolved-sku: no catalog match — needs reprice]"
            try:
                cols = {c[1].lower() for c in conn.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
                reprice_col_clause = "needs_reprice = TRUE," if "needs_reprice" in cols else ""
            except Exception:
                reprice_col_clause = ""
            conn.execute(
                f"""UPDATE aws_li_to_gcp_li 
                   SET strategy = 'needs_reprice',
                       {reprice_col_clause}
                       rate_source = COALESCE(rate_source, 'no_rate'),
                       projection_note = COALESCE(NULLIF(projection_note, '') || '; ', '') || ?
                   WHERE aws_li_key = ? AND gcp_sku_id IS NULL {comp_filter}""",
                [note, aws_li_key] + comp_args,
            )


    if resolved:
        log.info(f"Auto-resolved {resolved} NULL gcp_sku_id row(s) via catalog word-overlap")

    # Get all required SKUs (including any just resolved above)
    # AR-N: Deduplicate sku_id -> service deterministically via MIN(gcp_service)
    skus_used = conn.execute("""
        SELECT m.gcp_sku_id, MIN(m.gcp_service)
        FROM aws_li_to_gcp_li m
        WHERE m.gcp_sku_id IS NOT NULL
        GROUP BY m.gcp_sku_id
        ORDER BY m.gcp_sku_id
    """).fetchall()

    # Ensure rate table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gcp_sku_rates (
            gcp_sku_id      VARCHAR,
            gcp_service     VARCHAR,
            gcp_sku_name    VARCHAR,
            resource_family VARCHAR,
            resource_group  VARCHAR,
            pricing_type    VARCHAR,
            region          VARCHAR,
            unit            VARCHAR,
            rate_usd        DOUBLE,
            source          VARCHAR,
            audit_url       VARCHAR,
            PRIMARY KEY (gcp_sku_id, pricing_type, region)
        )
    """)

    if not skus_used:
        log.info("No SKUs to fill rates for — nothing to do")
        # AR-M: stamp rate_source on early return
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET rate_source = 'passthrough'
            WHERE strategy = 'passthrough' AND rate_source IS NULL
        """)
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET rate_source = 'ignored'
            WHERE strategy = 'ignore' AND rate_source IS NULL
        """)
        conn.execute("UPDATE aws_li_to_gcp_li SET rate_source = 'unknown' WHERE rate_source IS NULL")
        create_projection_view(conn)
        return

    # Load all needed SKU rates from catalog.duckdb in one indexed batch query.
    # Replaces per-SKU gzip file scanning (O(N) per lookup) with a single JOIN
    # (O(log N) via sku_id index). Both Preemptible and Commit* rows are fetched
    # now so the CUD synthesis below can fill any gaps with percentage fallbacks.
    if not os.path.exists(CATALOG_DB):
        # No catalog at all — convert every mapped row to passthrough and surface the gap.
        log.warning(f"catalog.duckdb not found at {CATALOG_DB} — all mapped rows set to passthrough")
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET strategy = 'passthrough',
                passthrough_reason = 'unresolved_sku',
                decided_by = 'apply_rates.py',
                rate_source = 'no_catalog'
            WHERE strategy IN ('map', 'break_down')
        """)
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET rate_source = 'no_catalog'
            WHERE rate_source IS NULL
        """)
        count = conn.execute(
            "SELECT COUNT(*) FROM aws_li_to_gcp_li WHERE strategy = 'passthrough' "
            "AND rate_source = 'no_catalog'"
        ).fetchone()[0]
        _write_rate_gaps([("ALL", "ALL", "N/A — catalog.duckdb missing", count)])
        try:
            import render_notes
            render_notes.render_notes_for_db(conn)
        except Exception as e:
            log.warning(f"render_notes_for_db in apply_rates failed: {e}")
        create_projection_view(conn)
        log.info(f"Rate fill skipped — {count} row(s) set to passthrough. "
              f"Run: bash scripts/refresh-catalog.sh  to rebuild the catalog.")
        return

    sku_ids = [sku_id for sku_id, _ in skus_used]
    # Map sku_id → gcp_service as declared in mappings (for the INSERT below).
    sku_to_service = {sku_id: svc for sku_id, svc in skus_used}

    cat = duckdb.connect(CATALOG_DB, read_only=True)
    try:
        placeholders = ",".join(["?" for _ in sku_ids])
        catalog_rows = cat.execute(f"""
            SELECT s.sku_id, s.service_name, s.description, s.resource_family,
                   s.resource_group, s.usage_type, s.usage_unit, s.service_regions,
                   t.tier_start, t.rate_usd
            FROM skus s
            JOIN tiered_rates t ON t.sku_id = s.sku_id
            WHERE s.sku_id IN ({placeholders})
              AND s.usage_type IN ('OnDemand', 'Commit1Yr', 'Commit3Yr')
            ORDER BY s.sku_id, s.usage_type, t.tier_start
        """, sku_ids).fetchall()
    finally:
        cat.close()

    # ── Rate Fill Transaction (AR-C) ──────────────────────────────────────────
    # Wrap all rate table mutations and downstream view/triage updates in an atomic
    # transaction. If a crash or error occurs mid-run, the transaction rolls back so
    # gcp_sku_rates is NOT left empty or partially refilled, preventing zero-rate projections.
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM gcp_sku_rates")

        # Group tiers per (sku_id, usage_type); keep one metadata record per key.
        sku_tiers: dict[tuple, list] = defaultdict(list)
        sku_meta: dict[tuple, tuple] = {}
        for sku_id, svc_name, desc, rf, rg, ut, unit, regions, tier_start, rate_usd in catalog_rows:
            key = (sku_id, ut)
            sku_tiers[key].append({"startUsageAmount": tier_start, "rate": rate_usd})
            if key not in sku_meta:
                sku_meta[key] = (svc_name, desc, rf, rg, unit, regions or [])

        # Pre-fetch monthly average usage per SKU for tiered-rate blending (one query, all SKUs).
        # GCP tier ladders reset monthly, so on multi-month bills we blend at single-month volume (Section F).
        cols = {r[0] for r in conn.execute("DESCRIBE aws_li_catalog").fetchall()}
        month_expr = "GREATEST(1, COUNT(DISTINCT NULLIF(c.billing_month, '')))" if "billing_month" in cols else "1"
        total_usage_rows = conn.execute(f"""
            SELECT m.gcp_sku_id, SUM(c.total_usage) / {month_expr}
            FROM aws_li_to_gcp_li m
            JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.gcp_sku_id IS NOT NULL
            GROUP BY m.gcp_sku_id
        """).fetchall()
        total_usage: dict[str, float] = {r[0]: float(r[1] or 0.0) for r in total_usage_rows}

        # Synthetic SKU IDs are injected directly into gcp_sku_rates below —
        # they are never in catalog.duckdb, so exclude them from the "not found" report.
        synthetic_ids = SYNTHETIC_SKU_IDS | {_TPU_SKU_ID}
        found_skus = {k[0] for k in sku_tiers}
        for sku_id in sku_ids:
            if sku_id not in found_skus and sku_id not in synthetic_ids:
                log.warning(f"SKU {sku_id} ({sku_to_service.get(sku_id)}) not found in catalog.duckdb — skipping")

        # Insert rates for all pricing types fetched from catalog.
        # usage_type in catalog maps 1:1 to pricing_type in gcp_sku_rates.
        for (sku_id, ut), tiers in sku_tiers.items():
            catalog_svc = sku_meta[(sku_id, ut)][0]
            gcp_service = catalog_svc or sku_to_service.get(sku_id, "")
            _, desc, rf, rg, unit, regions = sku_meta[(sku_id, ut)]

            if len(tiers) > 1:
                base_rate = blended_rate(tiers, total_usage.get(sku_id, 0.0))
            else:
                base_rate = tiers[0]["rate"]

            # Expand container codes (e.g. 'us' → list of us-* regions).
            expanded: set[str] = set()
            for r in regions:
                if r.lower() in CONTAINER_CODES:
                    expanded.update(CONTAINER_CODES[r.lower()])
                else:
                    expanded.add(r)

            # ON CONFLICT DO NOTHING: container-code expansion can produce overlapping
            # regions; the same sku_id may appear in multiple catalog rows.
            for region in expanded:
                conn.execute("""
                    INSERT INTO gcp_sku_rates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                """, (sku_id, gcp_service, desc, rf, rg, ut, region, unit, base_rate,
                      "catalog.duckdb", f"catalog.duckdb#{sku_id}"))

        # Normalize gcp_service to match the catalog's canonical service_name for all mapped rows (NAME-1).
        # Must run BEFORE CUD synthesis (~700), outlier checks, and report grouping so name-keyed
        # steps (like cud_pct.json lookup) match by construction rather than failing on LLM casual names.
        norm_count = conn.execute("""
            SELECT COUNT(*) FROM aws_li_to_gcp_li m
            JOIN (
                SELECT r.gcp_sku_id, r.gcp_service AS canonical_service
                FROM gcp_sku_rates r
                QUALIFY ROW_NUMBER() OVER (PARTITION BY r.gcp_sku_id ORDER BY r.gcp_service, r.region) = 1
            ) c ON m.gcp_sku_id = c.gcp_sku_id
            WHERE m.strategy IN ('map', 'break_down')
              AND COALESCE(m.gcp_service, '') != c.canonical_service
        """).fetchone()[0]
        if norm_count:
            conn.execute("""
                UPDATE aws_li_to_gcp_li m
                SET gcp_service = c.canonical_service
                FROM (
                    SELECT r.gcp_sku_id, r.gcp_service AS canonical_service
                    FROM gcp_sku_rates r
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY r.gcp_sku_id ORDER BY r.gcp_service, r.region) = 1
                ) c
                WHERE m.gcp_sku_id = c.gcp_sku_id
                  AND m.strategy IN ('map', 'break_down')
                  AND COALESCE(m.gcp_service, '') != c.canonical_service
            """)
            log.info(f"Normalized gcp_service for {norm_count} rows to match SKU catalog (NAME-1)")

        # Unit consistency check: demoted to debug (G-1b) because unit validation
        # is now enforced upfront at resolution time in resolve_intent_to_sku.
        try:
            unit_mismatches = conn.execute("""
                SELECT m.aws_li_key, m.gcp_sku_name, m.gcp_sku_unit, r.unit,
                       c.product, m.unit_multiplier
                FROM aws_li_to_gcp_li m
                JOIN aws_li_catalog c USING (aws_li_key)
                JOIN gcp_sku_rates r ON r.gcp_sku_id = m.gcp_sku_id
                                    AND r.pricing_type = 'OnDemand'
                                    AND r.region = m.gcp_region
                WHERE m.gcp_sku_unit IS NOT NULL
                  AND m.gcp_sku_unit != r.unit
                LIMIT 20
            """).fetchall()
            for li_key, sku_name, mapped_unit, rate_unit, product, mult in unit_mismatches:
                log.debug(f"Unit mismatch: {product} / {sku_name}: "
                      f"mapper assumed '{mapped_unit}', SKU actually billed per '{rate_unit}' "
                      f"(unit_multiplier={mult})")
        except Exception as e:
            log.debug(f"Unit consistency check failed: {e}")

        # CUD synthesis.
        # First check how many real Commit rows came from catalog vs. need synthesis.
        real_cud_count = conn.execute(
            "SELECT COUNT(*) FROM gcp_sku_rates WHERE pricing_type IN ('Commit1Yr','Commit3Yr') "
            "AND source = 'catalog.duckdb'"
        ).fetchone()[0]
        if real_cud_count:
            log.info(f"CUD: {real_cud_count} real Commit1Yr/3Yr rows loaded from catalog")
        else:
            log.info("CUD: no real Commit rows in catalog — synthesizing from OnDemand × cud_pct.json")

        # Only synthesize CUD for services that actually appear in this bill's mappings.
        # Avoids generating thousands of unused CUD rows for services not in the bill.
        mapped_services = {r[0] for r in conn.execute(
            "SELECT DISTINCT gcp_service FROM aws_li_to_gcp_li WHERE strategy IN ('map','break_down')"
        ).fetchall()}

        # cud_pct.json is the SINGLE source of truth for which services have CUDs and
        # at what multipliers. Adding a new CUD-eligible service requires only a new entry
        # in data/cud_pct.json — no Python change here.
        cud_pct = load_cud_pct()
        _default_pct = cud_pct.get("DEFAULT", (0.75, 0.60))

        synthesized_cud = 0
        for svc, (r1, r3) in cud_pct.items():
            if svc == "DEFAULT":
                continue
            # Skip services not present in this bill — no point generating unused rows.
            if svc not in mapped_services and svc != GCP_COMPUTE_ENGINE:
                continue
            rg_clause = "AND resource_group IN ('CPU','RAM','GPU')" if svc == GCP_COMPUTE_ENGINE else ""
            for pricing_type, mult in [("Commit1Yr", r1), ("Commit3Yr", r3)]:
                n = conn.execute(f"""
                    INSERT INTO gcp_sku_rates
                    SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
                           resource_group, '{pricing_type}', region, unit,
                           rate_usd * ?, 'doc-percentage', 'cud_pct.json'
                    FROM gcp_sku_rates
                    WHERE gcp_service = ? AND pricing_type = 'OnDemand'
                      {rg_clause}
                    ON CONFLICT DO NOTHING
                """, [mult, svc]).rowcount
                synthesized_cud += (n or 0)
        if synthesized_cud:
            log.info(f"CUD synthesis: {synthesized_cud} row(s) generated from cud_pct.json percentages")

        # Compute Engine CUD fallback: catch any CPU/RAM/GPU SKUs that missed the main
        # pass above (e.g. ARM families with non-standard SKU name patterns).
        r1_ce, r3_ce = cud_pct.get(GCP_COMPUTE_ENGINE, _default_pct)
        for pricing_type, mult in [("Commit1Yr", r1_ce), ("Commit3Yr", r3_ce)]:
            conn.execute(f"""
                INSERT INTO gcp_sku_rates
                SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
                       resource_group, '{pricing_type}', region, unit,
                       rate_usd * ?, 'doc-percentage-fallback', 'cud_pct.json'
                FROM gcp_sku_rates
                WHERE gcp_service = 'Compute Engine' AND pricing_type = 'OnDemand'
                  AND resource_group IN ('CPU', 'RAM', 'GPU')
                  AND NOT EXISTS (
                      SELECT 1 FROM gcp_sku_rates r2
                      WHERE r2.gcp_sku_id = gcp_sku_rates.gcp_sku_id
                        AND r2.pricing_type = '{pricing_type}'
                        AND r2.region = gcp_sku_rates.region
                  )
                ON CONFLICT DO NOTHING
            """, [mult])

        # Preemptible rate synthesis removed per WORKORDER Section E (OD-vs-OD).

    # Global fallback (AR-4): for every SKU that has regional rates but no 'global' row,
        # synthesize a 'global' row using the customer's dominant region's rate if available,
        # falling back to the average regional rate. This makes the gcp_projection VIEW
        # resilient to NULL gcp_region in aws_li_catalog while avoiding unweighted skew from
        # regions the customer never uses.
        dom_region = compute_dominant_region(conn)
        conn.execute("""
            INSERT INTO gcp_sku_rates
            SELECT r.gcp_sku_id, r.gcp_service, r.gcp_sku_name, r.resource_family,
                   r.resource_group, r.pricing_type, 'global', r.unit,
                   COALESCE(
                       MAX(CASE WHEN r.region = ? THEN r.rate_usd END),
                       AVG(r.rate_usd)
                   ),
                   'global-fallback', MIN(r.audit_url)
            FROM gcp_sku_rates r
            WHERE r.region != 'global'
            GROUP BY r.gcp_sku_id, r.gcp_service, r.gcp_sku_name, r.resource_family,
                     r.resource_group, r.pricing_type, r.unit
            HAVING NOT EXISTS (
                SELECT 1 FROM gcp_sku_rates g
                WHERE g.gcp_sku_id = r.gcp_sku_id
                  AND g.pricing_type = r.pricing_type
                  AND g.region = 'global'
            )
            ON CONFLICT DO NOTHING
        """, (dom_region,))

        # Inject canonical network-egress rates (deterministic, by direction) so the
        # data_transfer mappings resolve to a stable, correct $/GB instead of a
        # fuzzy catalog SKU whose rate swings 2x-8x run-to-run. 'global' region so
        # the VIEW's COALESCE(regional, global) always finds them.
        for _sku_id, _sku_name, _rate in EGRESS_SKUS.values():
            if _sku_id == "GCP-EGRESS-INTERNET":
                eff_usage = total_usage.get(_sku_id)
                if eff_usage is None:
                    u_row = conn.execute(f"""
                        SELECT SUM(c.total_usage) / {month_expr}
                        FROM aws_li_to_gcp_li m
                        JOIN aws_li_catalog c USING (aws_li_key)
                        WHERE m.gcp_sku_id = ? OR m.gcp_sku_name = ?
                    """, [_sku_id, _sku_name]).fetchone()
                    eff_usage = float(u_row[0] or 0.0) if u_row else 0.0
                eff_rate = blended_rate(_INTERNET_EGRESS_TIERS, eff_usage)
            else:
                eff_rate = _rate
            conn.execute("""
                INSERT INTO gcp_sku_rates VALUES
                (?, 'Compute Engine', ?, 'Network', 'Egress', 'OnDemand', 'global', 'gibibyte', ?, 'canonical-egress', 'published GCP egress list')
                ON CONFLICT DO NOTHING
            """, (_sku_id, _sku_name, eff_rate))

        # Looker Studio Pro (per-user): $9/user/mo published list price. Looker
        # Studio is billed as a SaaS product outside standard Cloud Billing SKUs,
        # so it is not in the bundled catalog — inject it the same way the egress
        # rates above are injected, so map_quicksight()'s Author-seat rows resolve
        # to strategy='map' with a stable, correct $/user rate instead of falling
        # to passthrough for lack of a catalog SKU.
        # Rate source: cloud.google.com/looker-studio/pricing (Pro per-user tier).
        conn.execute("""
            INSERT INTO gcp_sku_rates VALUES
            ('GCP-LOOKER-STUDIO-PRO-USER', 'Looker Studio Pro', 'Looker Studio Pro (per user)',
             'ApplicationServices', 'BusinessIntelligence', 'OnDemand', 'global', 'user',
             9.0, 'canonical-looker-studio-pro', 'https://cloud.google.com/looker-studio/pricing')
            ON CONFLICT DO NOTHING
        """)

        # Cloud TPU v5e (per chip-hour): pinned on-demand rate for Inferentia/Trainium
        # rows mapped by family_mapper.map_tpu_row(). No on-demand TPU SKU exists in the
        # bundled catalog, so inject it the same way egress / Looker Studio are injected.
        inject_tpu_rates(conn)

        # Cloud Logging Log Ingestion: NOT injected as a pseudo-SKU — the real
        # catalog SKU "Log Storage cost" (143F-A1B0-E0BE) already carries the
        # correct rate: despite its misleading name, its tiered_rates entry
        # (0-50 GiB free, 50+ GiB: $0.50/GiB) IS GCP's real published log
        # ingestion pricing. apply_static_mappings.py's map_cloudwatch()
        # resolves it dynamically by name via resolve_sku() for both
        # PutLogEvents and generic log-volume rows, so a catalog refresh (rate
        # change, new region coverage) is picked up automatically instead of
        # going stale behind a fixed injected value.

        # Cloud Logging Log Storage (extended retention): NOT injected as a
        # pseudo-SKU — the real catalog SKU "Log Retention cost"
        # (F4AE-5A52-ACE3, $0.01/GiBy.mo) already carries the correct rate.
        # apply_static_mappings.py's map_cloudwatch() resolves it dynamically by
        # name via resolve_sku(). The catalog's confusingly-similar "Log Storage
        # cost" SKU (143F-A1B0-E0BE) is NOT this rate — despite the name, its
        # tiered_rates entry is (0-50 GiB free, 50+ GiB: $0.50/GiB), the
        # INGESTION tier structure — a prior fix here mistakenly matched that
        # SKU and injected a hardcoded pseudo-rate to work around it, which
        # just meant the real catalog SKU (and any future rate change to it)
        # was silently ignored. Using the real catalog entry means a future
        # catalog refresh stays accurate automatically instead of going stale
        # behind a fixed injected value.

        # Regional External Application Load Balancer Data Processing: $0.008/GiB.
        # The bundled catalog only carries the Forwarding Rule Minimum (hourly) SKU;
        # inject the data-processing SKU so ALB LCU-hr rows resolve to strategy='map'.
        # Mapping assumption: 1 LCU-hr ≈ 1 GB when bandwidth is the dominant LCU dimension
        # (typical HTTP workloads); connection-heavy workloads (WebSockets, long-lived TCP)
        # will have fewer actual GB per LCU-hr, making this an over-estimate of GCP cost.
        # Rate source: cloud.google.com/vpc/network-pricing (LB data processing).
        conn.execute("""
            INSERT INTO gcp_sku_rates VALUES
            ('GCP-REXT-ALB-DATA-GIB', 'Networking',
             'Regional External Application Load Balancer Data Processing',
             'Network', 'LoadBalancing', 'OnDemand', 'global', 'gibibyte',
             0.008, 'canonical-alb-data', 'https://cloud.google.com/vpc/network-pricing')
            ON CONFLICT DO NOTHING
        """)

        # Global External Passthrough Network Load Balancer Data Processing: $0.008/GiB.
        # Same injection pattern as ALB above — NLB LCU-hr rows need this to map.
        # Rate source: cloud.google.com/vpc/network-pricing.
        conn.execute("""
            INSERT INTO gcp_sku_rates VALUES
            ('GCP-REXT-NLB-DATA-GIB', 'Networking',
             'Global External Passthrough Network Load Balancer Data Processing',
             'Network', 'LoadBalancing', 'OnDemand', 'global', 'gibibyte',
             0.008, 'canonical-nlb-data', 'https://cloud.google.com/vpc/network-pricing')
            ON CONFLICT DO NOTHING
        """)

        # Flag commercial-license rows (Windows/SQL Server/Oracle) so they are never
        # silently under-projected — confidence capped + note stamped.
        flag_license_exposure(conn)

        # Create the projection VIEW now that rates exist, so the Phase-4 gate
        # (no_null_projected_cost) and the validator autofix can query it. Phase 5's
        # detect_outliers.py re-creates it idempotently from the same shared SQL.
        create_projection_view(conn)

        dominant_region = compute_dominant_region(conn)

        # Sanity check: any mapped row whose SKU has SOME rate rows but none reachable
        # for its specific region silently projects $0. Catch and route to outlier_triage.
        # REGION-1: A row is reachable if it has a rate in its own region, global (including global-fallback),
        # or the dominant region. Only rows with NO rate anywhere go to outlier_triage.
        unreachable = conn.execute("""
            SELECT m.gcp_service, c.gcp_region, m.gcp_sku_id, COUNT(*) AS unreachable_rows
            FROM aws_li_to_gcp_li m
            JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.gcp_sku_id IS NOT NULL
              AND m.strategy IN ('map','break_down')
              AND NOT EXISTS (
                  SELECT 1 FROM gcp_sku_rates r
                  WHERE r.gcp_sku_id = m.gcp_sku_id
                    AND (
                        r.region = c.gcp_region
                        OR r.region = 'global'
                        OR r.region = ?
                    )
              )
            GROUP BY m.gcp_service, c.gcp_region, m.gcp_sku_id
        """, [dominant_region]).fetchall()

        if unreachable:
            _write_rate_gaps(unreachable)
            # Batch update unreachable rows into outlier_triage (AR-O)
            conn.execute("""
                UPDATE aws_li_to_gcp_li
                SET strategy = 'outlier_triage',
                    projection_note = COALESCE(NULLIF(projection_note, '') || ' ', '') ||
                        '[rate-fill-gap: no rate for region — routed to Phase 5]'
                WHERE strategy IN ('map', 'break_down')
                  AND gcp_sku_id IS NOT NULL
                  AND aws_li_key IN (
                      SELECT m.aws_li_key
                      FROM aws_li_to_gcp_li m
                      JOIN aws_li_catalog c USING (aws_li_key)
                      WHERE m.strategy IN ('map', 'break_down')
                        AND m.gcp_sku_id IS NOT NULL
                        AND NOT EXISTS (
                            SELECT 1 FROM gcp_sku_rates r
                            WHERE r.gcp_sku_id = m.gcp_sku_id
                              AND (
                                  r.region = c.gcp_region
                                  OR r.region = 'global'
                                  OR r.region = ?
                              )
                        )
                  )
            """, [dominant_region])
            create_projection_view(conn, dominant_region)
            log.info(f"Sanity check: {len(unreachable)} unreachable SKU/region pair(s) moved to outlier_triage")
        else:
            log.info("Sanity check: all mapped SKUs have reachable rates")

        # Safety net: any mapped row still lacking an OnDemand rate after fill
        # (e.g. resolved to a Preemptible-only SKU, or SKU genuinely absent from catalog)
        # must not block the gate. Convert to passthrough so the report always generates.
        null_keys = conn.execute("""
            SELECT p.aws_li_key
            FROM gcp_projection p
            WHERE p.strategy IN ('map', 'break_down')
              AND p.gcp_projected_cost IS NULL
              AND p.aws_amortized_cost > 1
        """).fetchall()
        if null_keys:
            keys = [k[0] for k in null_keys]
            placeholders = ",".join(["?" for _ in keys])
            try:
                cols = {c[1].lower() for c in conn.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
                reprice_col_clause = "needs_reprice = TRUE," if "needs_reprice" in cols else ""
            except Exception:
                reprice_col_clause = ""
            conn.execute(
                f"""
                UPDATE aws_li_to_gcp_li
                SET strategy = 'needs_reprice',
                    {reprice_col_clause}
                    rate_source = 'no_rate'
                WHERE aws_li_key IN ({placeholders})
                """,
                keys,
            )
            create_projection_view(conn, dominant_region)
            log.info(f"Rate-gap fallback: {len(keys)} NULL-cost row(s) set to needs_reprice")


        # ── Populate rate_source ───────────────────────────────────────────────────
        # Order matters: no_rate first (subset of passthrough), then passthrough,
        # then exact_sku, then word_overlap. Remaining unknowns → 'unknown'.
        #
        # 'passthrough' and 'ignore' are NOT the same thing and must not share a
        # rate_source label. strategy='passthrough' means "no reliable GCP
        # equivalent exists — AWS cost carried as a placeholder, needs manual
        # sizing." strategy='ignore' means the row was deliberately excluded from
        # GCP pricing on purpose (negative-cost credits/refunds, Savings Plan
        # commitment artifacts already reflected in amortized cost, RDS IOPS fees
        # bundled into Cloud SQL's storage price, etc.) — there is nothing to size
        # and nothing wrong with these rows. Tagging both 'passthrough' previously
        # made the report's "N passthrough rows — no reliable GCP equivalent;
        # manual sizing required" caption apply to every ignore row too, wildly
        # overstating how many rows actually need manual review.
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET rate_source = 'passthrough'
            WHERE strategy = 'passthrough' AND rate_source IS NULL
        """)
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET rate_source = 'ignored'
            WHERE strategy = 'ignore' AND rate_source IS NULL
        """)
        if pinned_keys:
            conn.execute(f"""
                UPDATE aws_li_to_gcp_li
                SET rate_source = 'exact_sku'
                WHERE strategy IN ('map','break_down') AND rate_source IS NULL
                  AND aws_li_key IN ({','.join(['?']*len(pinned_keys))})
            """, list(pinned_keys))
        if word_overlap_keys:
            conn.execute(f"""
                UPDATE aws_li_to_gcp_li
                SET rate_source = 'word_overlap'
                WHERE rate_source IS NULL
                  AND aws_li_key IN ({','.join(['?']*len(word_overlap_keys))})
            """, list(word_overlap_keys))
        conn.execute("UPDATE aws_li_to_gcp_li SET rate_source = 'unknown' WHERE rate_source IS NULL")

        # Annotate global rows and region-fallback rows priced at the dominant region rate (H-4 / REGION-1)
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET rate_region_basis = 'global',
                rate_region_used = ?
            WHERE aws_li_key IN (
                SELECT c.aws_li_key
                FROM aws_li_catalog c
                JOIN aws_li_to_gcp_li m USING (aws_li_key)
                WHERE (c.gcp_region = 'global' OR c.gcp_region IS NULL OR c.gcp_region = ''
                       OR c.aws_region = 'global' OR c.aws_region IS NULL OR c.aws_region = '')
                  AND m.gcp_sku_id IS NOT NULL
                  AND m.strategy IN ('map', 'break_down')
                  AND NOT EXISTS (
                      SELECT 1 FROM gcp_sku_rates r WHERE r.gcp_sku_id = m.gcp_sku_id AND r.region = 'global' AND r.source != 'global-fallback'
                  )
                  AND EXISTS (
                      SELECT 1 FROM gcp_sku_rates r WHERE r.gcp_sku_id = m.gcp_sku_id AND (r.region = ? OR (r.region = 'global' AND r.source = 'global-fallback'))
                  )
            )
            AND rate_region_basis IS NULL
        """, [dominant_region, dominant_region])

        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET rate_region_basis = 'dominant',
                rate_region_used = ?
            FROM aws_li_catalog c
            WHERE aws_li_to_gcp_li.aws_li_key = c.aws_li_key
              AND c.gcp_region IS NOT NULL AND c.gcp_region != '' AND c.gcp_region != 'global'
              AND aws_li_to_gcp_li.gcp_sku_id IS NOT NULL
              AND aws_li_to_gcp_li.strategy IN ('map', 'break_down')
              AND NOT EXISTS (
                  SELECT 1 FROM gcp_sku_rates r
                  WHERE r.gcp_sku_id = aws_li_to_gcp_li.gcp_sku_id
                    AND r.region = c.gcp_region
              )
              AND NOT EXISTS (
                  SELECT 1 FROM gcp_sku_rates r
                  WHERE r.gcp_sku_id = aws_li_to_gcp_li.gcp_sku_id
                    AND r.region = 'global'
                    AND r.source != 'global-fallback'
              )
              AND EXISTS (
                  SELECT 1 FROM gcp_sku_rates r
                  WHERE r.gcp_sku_id = aws_li_to_gcp_li.gcp_sku_id
                    AND (r.region = ? OR (r.region = 'global' AND r.source = 'global-fallback'))
              )
              AND aws_li_to_gcp_li.rate_region_basis IS NULL
        """, [dominant_region, dominant_region])

        try:
            import render_notes
            render_notes.render_notes_for_db(conn)
        except Exception as e:
            log.warning(f"render_notes_for_db in apply_rates failed: {e}")

        # Re-create projection view with dominant region
        create_projection_view(conn, dominant_region)

        # Cap confidence on rows falling back to cheapest available region
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET mapping_confidence = LEAST(mapping_confidence, 0.4)
            WHERE rate_region_basis = 'cheapest_available'
        """)
        conn.execute("COMMIT")
        log.info("Rate fill complete")
    except Exception:
        conn.execute("ROLLBACK")
        raise

if __name__ == "__main__":
    main()

