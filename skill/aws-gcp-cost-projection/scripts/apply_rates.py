#!/usr/bin/env python3
import duckdb
import os
import sys
import json
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from projection_view import create_projection_view
from egress_rates import EGRESS_SKUS

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
DATA_DIR = os.path.join(os.environ.get("SKILL_DIR", ""), "data")
CATALOG_DB = os.path.join(DATA_DIR, "catalog.duckdb")

# CUD discount multipliers — the SINGLE source of truth, shared verbatim with
# validate_fix.py. Both read data/cud_pct.json so 1yr/3yr math is identical no
# matter which script sets a rate. Fallback mirrors validate_fix's fallback.
_CUD_PCT_FALLBACK = {
    "Compute Engine": (0.70, 0.55),
    "Cloud SQL": (0.75, 0.60),
    "Cloud Spanner": (0.75, 0.60),
    "Cloud Bigtable": (0.75, 0.60),
    "Memorystore": (0.80, 0.65),
    "Cloud Memorystore": (0.80, 0.65),
    "Cloud Memorystore for Redis": (0.80, 0.65),
    "Cloud Memorystore for Memcached": (0.80, 0.65),
    "AlloyDB": (0.75, 0.60),
    "Cloud Run": (0.83, 0.67),
    "DEFAULT": (0.75, 0.60),
}


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
            print(f"WARNING: could not load cud_pct.json ({e}); using fallback multipliers.")
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
    total_cost = 0.0
    for i, tier in enumerate(tiered_rates):
        tier_start = tier.get("startUsageAmount", 0)
        tier_end = tiered_rates[i+1].get("startUsageAmount") if i+1 < len(tiered_rates) else float("inf")
        tier_qty = max(0, min(total_qty, tier_end) - tier_start)
        total_cost += tier_qty * tier.get("rate", 0)
    return total_cost / total_qty if total_qty > 0 else tiered_rates[-1].get("rate", 0)

def _score_sku_match(description: str, gcp_sku_name: str) -> int:
    """Score how well a catalog SKU description matches the LLM-provided gcp_sku_name.
    Higher = better. Uses word-level intersection — no hardcoded rules needed."""
    desc_words = set(description.lower().split())
    name_words = set(gcp_sku_name.lower().split())
    return len(desc_words & name_words)


def _resolve_sku_for_row(gcp_service, gcp_sku_name, gcp_region, *_ignored):
    """Return the best gcp_sku_id for a NULL-sku mapped row using catalog description search.

    Queries catalog.duckdb (indexed) instead of scanning gzip files. Same word-overlap
    scoring as before; same free-tier / intra-zone exclusions; same region-preference logic.
    Extra positional args are accepted but ignored (backwards-compat with old signature).
    """
    if not gcp_sku_name or not os.path.exists(CATALOG_DB):
        return None

    cat = duckdb.connect(CATALOG_DB, read_only=True)
    try:
        rows = cat.execute("""
            SELECT sku_id, description, service_regions
            FROM skus
            WHERE service_name = ? AND usage_type = 'OnDemand'
        """, [gcp_service]).fetchall()
    finally:
        cat.close()

    best_exact_score, best_exact_id = (-1, 0), None
    best_any_score,   best_any_id   = (-1, 0), None

    for sku, desc, regions in rows:
        dl = desc.lower()
        if "free tier" in dl or "promotional" in dl or "trial" in dl:
            continue
        if "intra zone" in dl or "intra-zone" in dl or "intra region" in dl or "intra-region" in dl:
            continue
        score = _score_sku_match(desc, gcp_sku_name)
        if score <= 0:
            continue
        # Composite rank: (word-overlap score, -extra words). Tighter match wins ties.
        rank = (score, -len(desc.split()))
        if gcp_region and gcp_region in (regions or []):
            if rank > best_exact_score or (rank == best_exact_score and (best_exact_id is None or sku < best_exact_id)):
                best_exact_score, best_exact_id = rank, sku
        if rank > best_any_score or (rank == best_any_score and (best_any_id is None or sku < best_any_id)):
            best_any_score, best_any_id = rank, sku

    return best_exact_id or best_any_id


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
                     "gcp_service='Manual Sizing Required', gcp_sku_id=NULL, gcp_sku_name=NULL, "
                     "unit_multiplier=NULL, mapping_confidence=0.3, component='accelerator', "
                     "projection_note='AI accelerator (Inferentia/Trainium/GPU) — Manual sizing required (A3/A2/G2/L4/H100/etc.)' "
                     "WHERE aws_li_key=? AND rowid=?", [k, keep])
        fixed += 1
    if fixed:
        print(f"  Enforced accelerator passthrough on {fixed} row(s) (no CPU-VM mapping)")
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
            gcp_sku_id = NULL,
            projection_note = 'ElastiCache → Memorystore for Memcached (cache workload; not Cloud SQL)'
        WHERE aws_li_key IN (
            SELECT cat.aws_li_key FROM aws_li_catalog cat
            WHERE LOWER(cat.product) LIKE '%elasticache%'
        ) AND gcp_service = 'Cloud SQL'
    """)
    print(f"  Remapped {n} ElastiCache row(s) Cloud SQL → Memorystore for Memcached")
    return n


def fix_managed_db_storage_rows(conn, *_ignored):
    """Aurora/RDS 'Storage and I/O' rows are billed in GB-Mo, not Hrs. The LLM
    maps them to vCPU/RAM SKUs (correct for instance rows), which multiplies
    millions of GB against $/vCPU-hr and blows the total by 10,000x.

    Detect any managed-DB row where pricing_unit is not hours and the current
    SKU is a vCPU/RAM SKU, then remap to the correct Cloud SQL SSD Storage SKU.
    This is the same class of fix as enforce_accelerator_passthrough — a unit
    mismatch the LLM cannot reliably avoid."""

    rows = conn.execute("""
        SELECT m.aws_li_key, m.gcp_service, m.gcp_sku_name, c.gcp_region,
               c.pricing_unit, c.product
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.strategy = 'map'
          AND m.gcp_service = 'Cloud SQL'
          AND (m.gcp_sku_name ILIKE '%vCPU%' OR m.gcp_sku_name ILIKE '% RAM%'
               OR m.gcp_sku_name ILIKE '%Core%')
          AND (c.pricing_unit NOT IN ('Hrs', 'hours', 'Hour')
               OR c.pricing_unit IS NULL)
          AND (c.product ILIKE '%Aurora%' OR c.product ILIKE '%RDS%'
               OR c.product ILIKE '%Relational%')
    """).fetchall()

    if not rows:
        return 0

    fixed = 0
    for key, svc, sku_name, gcp_region, pricing_unit, product in rows:
        # Determine storage engine from product name for SKU precision
        if "postgresql" in (product or "").lower() or "aurora" in (product or "").lower():
            storage_sku = "Cloud SQL for PostgreSQL: Zonal - SSD storage"
        elif "mysql" in (product or "").lower():
            storage_sku = "Cloud SQL for MySQL: Zonal - SSD storage"
        else:
            storage_sku = "Cloud SQL: Zonal - SSD storage"

        new_sku_id = _resolve_sku_for_row("Cloud SQL", storage_sku, gcp_region, services, data_dir)
        if not new_sku_id:
            # Fallback: passthrough rather than keep the wrong vCPU SKU
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET
                    strategy = 'passthrough',
                    gcp_sku_id = NULL, gcp_sku_name = NULL,
                    mapping_confidence = 0.6,
                    projection_note = 'Aurora/RDS Storage+IO row — unit mismatch with vCPU SKU; '
                                      'passthrough at cost parity until Cloud SQL storage SKU resolved'
                WHERE aws_li_key = ?
            """, [key])
        else:
            conn.execute("""
                UPDATE aws_li_to_gcp_li SET
                    gcp_sku_id = ?,
                    gcp_sku_name = ?,
                    mapping_confidence = 0.85,
                    projection_note = ?
                WHERE aws_li_key = ?
            """, [new_sku_id, storage_sku,
                  f"Aurora/RDS Storage+IO → {storage_sku} (unit-mismatch fix: was vCPU SKU)",
                  key])
        fixed += 1

    if fixed:
        print(f"  Fixed {fixed} managed-DB storage row(s) mis-mapped to vCPU SKUs")
    return fixed


def flag_license_exposure(conn):
    """Flag Windows / SQL Server / Oracle rows so a commercial-license bill is
    never SILENTLY under-projected. GCP compute here is priced license-EXCLUSIVE
    (no Windows/SQL Server premium modeled), so we cap confidence and stamp a
    projection_note. Runs after the Phase-5 LLM (so it can't be clobbered) and
    in Phase 4. Idempotent via the marker guard. Deterministic — no rate table."""
    try:
        rows = conn.execute(f"""
            SELECT DISTINCT m.aws_li_key,
                   COALESCE(cat.operating_system,'') os, COALESCE(cat.database_engine,'') eng
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog cat USING (aws_li_key)
            WHERE cat.is_workload AND m.strategy IN ('map','break_down')
              AND COALESCE(m.projection_note,'') NOT LIKE '%license-premium-not-modeled%'
              AND (
                LOWER(COALESCE(cat.operating_system,'')) LIKE '%windows%'
                OR LOWER(COALESCE(cat.database_engine,'')) LIKE '%sql server%'
                OR LOWER(COALESCE(cat.database_engine,'')) LIKE '%sqlserver%'
                OR LOWER(COALESCE(cat.database_engine,'')) LIKE '%oracle%'
                OR LOWER(COALESCE(cat.operation,'')) LIKE '%sql server%'
                OR LOWER(COALESCE(cat.operation,'')) LIKE '%oracle%'
                OR LOWER(COALESCE(cat.usage_type,'')) LIKE '%windows%'
              )
        """).fetchall()
    except Exception as e:
        print(f"  license-exposure flag skipped: {e}")
        return 0

    flagged = 0
    for aws_li_key, os_, eng in rows:
        lic = "Windows" if "windows" in os_.lower() else (eng or "commercial")
        note = (f"{_LICENSE_MARKER} {lic} license premium NOT modeled — GCP compute is "
                f"priced license-exclusive; add OS/DB licensing separately.")
        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET projection_note = CASE WHEN projection_note IS NULL OR projection_note=''
                                       THEN ? ELSE projection_note || ' ' || ? END,
                mapping_confidence = LEAST(COALESCE(mapping_confidence, 1.0), 0.5)
            WHERE aws_li_key = ?
        """, (note, note, aws_li_key))
        flagged += 1
    if flagged:
        print(f"  Flagged {flagged} license-exposed row(s) (Windows/SQL Server/Oracle) — confidence capped, note stamped")
    return flagged


def main():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        return

    conn = duckdb.connect(DB_PATH)

    # Deterministic mapping-correctness enforcement (runs before SKU resolution
    # so corrected rows get the right rate). Both override the LLM's choices:
    #   - accelerators (Inferentia/Trainium/GPU) → single passthrough, never CPU VM
    #   - ElastiCache → Memorystore for Memcached, never Cloud SQL
    enforce_accelerator_passthrough(conn)
    remap_elasticache_to_memorystore(conn)
    fix_managed_db_storage_rows(conn)

    # Add rate_source column if not yet present (idempotent — safe to re-run)
    try:
        conn.execute("ALTER TABLE aws_li_to_gcp_li ADD COLUMN rate_source VARCHAR")
    except Exception:
        pass  # column already exists

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
    null_sku_rows = conn.execute("""
        SELECT m.aws_li_key, m.gcp_service, m.gcp_sku_name, c.gcp_region
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.gcp_sku_id IS NULL
          AND m.strategy IN ('map', 'break_down')
    """).fetchall()

    resolved = 0
    word_overlap_keys = set()
    for aws_li_key, gcp_service, gcp_sku_name, gcp_region in null_sku_rows:
        sku_id = _resolve_sku_for_row(gcp_service, gcp_sku_name, gcp_region)
        if sku_id:
            conn.execute(
                "UPDATE aws_li_to_gcp_li SET gcp_sku_id = ? WHERE aws_li_key = ? AND gcp_sku_id IS NULL",
                (sku_id, aws_li_key),
            )
            word_overlap_keys.add(aws_li_key)
            resolved += 1
            print(f"  resolved SKU: {gcp_service} / {gcp_sku_name!r} -> {sku_id}")

    if resolved:
        print(f"Auto-resolved {resolved} NULL gcp_sku_id row(s) from catalog (word-overlap)")

    # Get all required SKUs (including any just resolved above)
    skus_used = conn.execute("""
        SELECT DISTINCT m.gcp_sku_id, m.gcp_service
        FROM aws_li_to_gcp_li m
        WHERE m.gcp_sku_id IS NOT NULL
    """).fetchall()

    if not skus_used:
        print("No SKUs to fill rates for.")
        return

    # Clear existing rate table just in case
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
    conn.execute("DELETE FROM gcp_sku_rates")

    # Load all needed SKU rates from catalog.duckdb in one indexed batch query.
    # Replaces per-SKU gzip file scanning (O(N) per lookup) with a single JOIN
    # (O(log N) via sku_id index). Both Preemptible and Commit* rows are fetched
    # now so the CUD synthesis below can fill any gaps with percentage fallbacks.
    if not os.path.exists(CATALOG_DB):
        print(f"WARNING: catalog.duckdb not found at {CATALOG_DB} — rate fill skipped.")
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
              AND s.usage_type IN ('OnDemand', 'Preemptible', 'Commit1Yr', 'Commit3Yr')
            ORDER BY s.sku_id, s.usage_type, t.tier_start
        """, sku_ids).fetchall()
    finally:
        cat.close()

    # Group tiers per (sku_id, usage_type); keep one metadata record per key.
    sku_tiers: dict[tuple, list] = defaultdict(list)
    sku_meta: dict[tuple, tuple] = {}
    for sku_id, svc_name, desc, rf, rg, ut, unit, regions, tier_start, rate_usd in catalog_rows:
        key = (sku_id, ut)
        sku_tiers[key].append({"startUsageAmount": tier_start, "rate": rate_usd})
        if key not in sku_meta:
            sku_meta[key] = (svc_name, desc, rf, rg, unit, regions or [])

    # Pre-fetch max usage per SKU for tiered-rate blending (one query, all SKUs).
    max_usage_rows = conn.execute("""
        SELECT m.gcp_sku_id, MAX(c.total_usage)
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.gcp_sku_id IS NOT NULL
        GROUP BY m.gcp_sku_id
    """).fetchall()
    max_usage: dict[str, float] = {r[0]: (r[1] or 0.0) for r in max_usage_rows}

    # Synthetic egress SKU IDs are injected directly into gcp_sku_rates below —
    # they are never in catalog.duckdb, so exclude them from the "not found" report.
    synthetic_ids = {sku_id for sku_id, _, _ in EGRESS_SKUS.values()}
    found_skus = {k[0] for k in sku_tiers}
    for sku_id in sku_ids:
        if sku_id not in found_skus and sku_id not in synthetic_ids:
            print(f"SKU {sku_id} ({sku_to_service.get(sku_id)}) not found in catalog.duckdb")

    # Insert rates for all pricing types fetched from catalog.
    # usage_type in catalog maps 1:1 to pricing_type in gcp_sku_rates.
    for (sku_id, ut), tiers in sku_tiers.items():
        gcp_service = sku_to_service.get(sku_id, sku_meta[(sku_id, ut)][0])
        _, desc, rf, rg, unit, regions = sku_meta[(sku_id, ut)]

        if len(tiers) > 1:
            base_rate = blended_rate(tiers, max_usage.get(sku_id, 0.0))
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
            
    # CUD synthesis. Discount MULTIPLIERS come from data/cud_pct.json — the SINGLE
    # source of truth shared with validate_fix.py, so both scripts apply identical
    # CUD math and the 1yr/3yr columns never vary by which path last touched them.
    # Only the per-service committable resource_group set lives here (that's rate-
    # table structure, not a discount rate). rg_list=None → apply CUD to all
    # OnDemand rows of the service (e.g. Memorystore node capacity).
    #
    # Service list must cover every service the validator's cud_coverage gate
    # expects — apply_rates wipes the rate table each run, so an uncovered
    # committable service silently falls back to OnDemand for 1yr/3yr and trips
    # the gate.
    cud_pct = load_cud_pct()
    _CUD_GROUPS = [
        ("Compute Engine", "('CPU','RAM','GPU')",
         "https://cloud.google.com/compute/docs/instances/signing-up-committed-use-discounts"),
        # rg_list=None: apply CUD to ALL Cloud SQL OnDemand SKUs rather than
        # filtering by resource_group. CUR/PDF bills for RDS come in as
        # instance-hour charges — the resource_group column is blank — so the
        # old resource_group IN (...) clause never matched and Cloud SQL rows
        # always showed OD=1yr=3yr (no discount applied at all).
        ("Cloud SQL", None, "https://cloud.google.com/sql/cud"),
        ("AlloyDB", None, "https://cloud.google.com/alloydb/pricing"),
        ("Cloud Memorystore for Memcached", None,
         "https://cloud.google.com/memorystore/docs/memcached/committed-use-discounts"),
        ("Cloud Memorystore for Redis", None,
         "https://cloud.google.com/memorystore/docs/redis/committed-use-discounts"),
        ("Cloud Memorystore", None, "https://cloud.google.com/memorystore/pricing"),
    ]
    _default_pct = cud_pct.get("DEFAULT", (0.75, 0.60))
    for svc, rg_list, url in _CUD_GROUPS:
        r1, r3 = cud_pct.get(svc, _default_pct)
        rg_clause = f"AND resource_group IN {rg_list}" if rg_list else ""
        conn.execute(f"""
            INSERT INTO gcp_sku_rates
            SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
                   resource_group, 'Commit1Yr', region, unit,
                   rate_usd * {r1}, 'doc-percentage', '{url}'
            FROM gcp_sku_rates
            WHERE gcp_service = '{svc}' AND pricing_type = 'OnDemand'
              {rg_clause}
            ON CONFLICT DO NOTHING
        """)
        conn.execute(f"""
            INSERT INTO gcp_sku_rates
            SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
                   resource_group, 'Commit3Yr', region, unit,
                   rate_usd * {r3}, 'doc-percentage', '{url}'
            FROM gcp_sku_rates
            WHERE gcp_service = '{svc}' AND pricing_type = 'OnDemand'
              {rg_clause}
            ON CONFLICT DO NOTHING
        """)

    # CUD fallback for Compute Engine SKUs whose resource_group is not in ('CPU','RAM','GPU').
    # ARM/T2A and some specialized families have different resource_group values (e.g. blank,
    # 'Compute') and miss the CUD synthesis above. Apply the multiplier-based fallback for any
    # Compute Engine OnDemand SKU still lacking a Commit1Yr row — these show up as query E
    # outliers (OD=1yr=3yr) and were being sent to the LLM unnecessarily.
    r1_ce, r3_ce = cud_pct.get("Compute Engine", _default_pct)
    url_ce = "https://cloud.google.com/compute/docs/instances/signing-up-committed-use-discounts"
    for pricing_type, mult in [("Commit1Yr", r1_ce), ("Commit3Yr", r3_ce)]:
        conn.execute(f"""
            INSERT INTO gcp_sku_rates
            SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
                   resource_group, '{pricing_type}', region, unit,
                   rate_usd * {mult}, 'doc-percentage-fallback', '{url_ce}'
            FROM gcp_sku_rates
            WHERE gcp_service = 'Compute Engine' AND pricing_type = 'OnDemand'
              AND NOT EXISTS (
                  SELECT 1 FROM gcp_sku_rates r2
                  WHERE r2.gcp_sku_id = gcp_sku_rates.gcp_sku_id
                    AND r2.pricing_type = '{pricing_type}'
                    AND r2.region = gcp_sku_rates.region
              )
            ON CONFLICT DO NOTHING
        """)

    # Preemptible rate synthesis for Compute Engine CPU/RAM SKUs.
    # GCP Preemptible (and Spot VM) price ≈ 22% of On-Demand in most regions.
    # Synthesised here so the gcp_projection VIEW can select the correct rate for
    # rows where pricing_model = 'Spot'.
    conn.execute("""
        INSERT INTO gcp_sku_rates
        SELECT gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
               resource_group, 'Preemptible', region, unit,
               rate_usd * 0.22, 'preemptible-factor',
               'https://cloud.google.com/compute/docs/instances/preemptible'
        FROM gcp_sku_rates
        WHERE gcp_service = 'Compute Engine' AND pricing_type = 'OnDemand'
          AND resource_group IN ('CPU', 'RAM', 'GPU')
        ON CONFLICT DO NOTHING
    """)

    # Global fallback: for every SKU that has regional rates but no 'global' row,
    # synthesize a 'global' row by averaging the regional rates. This makes the
    # gcp_projection VIEW resilient to NULL gcp_region in aws_li_catalog — the
    # COALESCE(regional, global) fallback in the VIEW will always find a rate.
    conn.execute("""
        INSERT INTO gcp_sku_rates
        SELECT r.gcp_sku_id, r.gcp_service, r.gcp_sku_name, r.resource_family,
               r.resource_group, r.pricing_type, 'global', r.unit,
               AVG(r.rate_usd), 'global-fallback', MIN(r.audit_url)
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
    """)

    # Inject canonical network-egress rates (deterministic, by direction) so the
    # data_transfer mappings resolve to a stable, correct $/GB instead of a
    # fuzzy catalog SKU whose rate swings 2x-8x run-to-run. 'global' region so
    # the VIEW's COALESCE(regional, global) always finds them.
    for _sku_id, _sku_name, _rate in EGRESS_SKUS.values():
        conn.execute("""
            INSERT INTO gcp_sku_rates VALUES
            (?, 'Compute Engine', ?, 'Network', 'Egress', 'OnDemand', 'global', 'gibibyte', ?, 'canonical-egress', 'published GCP egress list')
            ON CONFLICT DO NOTHING
        """, (_sku_id, _sku_name, _rate))

    # Flag commercial-license rows (Windows/SQL Server/Oracle) so they are never
    # silently under-projected — confidence capped + note stamped.
    flag_license_exposure(conn)

    # Create the projection VIEW now that rates exist, so the Phase-4 gate
    # (no_null_projected_cost) and the validator autofix can query it. Phase 5's
    # detect_outliers.py re-creates it idempotently from the same shared SQL.
    create_projection_view(conn)

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
        conn.execute(
            f"""
            UPDATE aws_li_to_gcp_li
            SET strategy = 'passthrough',
                projection_note = COALESCE(projection_note || ' ', '') ||
                    '[no-rate-fallback: passthrough at cost parity — OnDemand rate missing for resolved SKU]'
            WHERE aws_li_key IN ({placeholders})
            """,
            keys,
        )
        create_projection_view(conn)
        print(f"  Rate-gap fallback: {len(keys)} NULL-cost row(s) → passthrough")

    # ── Populate rate_source ───────────────────────────────────────────────────
    # Order matters: no_rate first (subset of passthrough), then passthrough,
    # then exact_sku, then word_overlap. Remaining unknowns → 'unknown'.
    conn.execute("""
        UPDATE aws_li_to_gcp_li
        SET rate_source = 'no_rate'
        WHERE projection_note LIKE '%no-rate-fallback%'
    """)
    conn.execute("""
        UPDATE aws_li_to_gcp_li
        SET rate_source = 'passthrough'
        WHERE strategy IN ('passthrough', 'ignore') AND rate_source IS NULL
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

    # Normalize gcp_service to match the catalog's gcp_service field for all mapped rows.
    # The LLM often writes shortened names ("Cloud KMS", "Memorystore", "Cloud Run") that
    # differ from the catalog's canonical name ("Cloud Key Management Service (KMS)", etc.).
    # detect_outliers query D flags these mismatches. Fixing here avoids 700+ false positives.
    norm_result = conn.execute("""
        UPDATE aws_li_to_gcp_li m
        SET gcp_service = (
            SELECT r.gcp_service FROM gcp_sku_rates r
            WHERE r.gcp_sku_id = m.gcp_sku_id
            LIMIT 1
        )
        WHERE m.strategy IN ('map', 'break_down')
          AND m.gcp_sku_id IS NOT NULL
          AND m.gcp_service != (
            SELECT r.gcp_service FROM gcp_sku_rates r
            WHERE r.gcp_sku_id = m.gcp_sku_id
            LIMIT 1
          )
    """)
    norm_count = conn.execute("""
        SELECT COUNT(*) FROM aws_li_to_gcp_li m
        WHERE m.strategy IN ('map', 'break_down')
          AND m.gcp_sku_id IS NOT NULL
          AND m.gcp_service != (
            SELECT r.gcp_service FROM gcp_sku_rates r
            WHERE r.gcp_sku_id = m.gcp_sku_id LIMIT 1
          )
    """).fetchone()[0]
    if norm_count:
        print(f"  Normalized gcp_service for {norm_count} rows to match SKU catalog.")

    print("Rate fill complete.")

if __name__ == "__main__":
    main()
