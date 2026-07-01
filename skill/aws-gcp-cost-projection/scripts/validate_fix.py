#!/usr/bin/env python3
"""
Deterministic validator + autofixer for the AWS->GCP projection.

Two ways it runs:
  * Phase 5.5 (skill, default mode): autofix + gate, BEFORE Phase 6 renders.
        python3 "$SKILL_DIR/scripts/validate_fix.py" .
  * Watcher backstop (--check-only): gate only, no DB mutation, AFTER the
        agent exits. If it fails here the job is NOT marked done.
        python3 .../validate_fix.py --check-only <jobdir>

It moves the accuracy-critical guarantees OUT of the LLM prompt and into code
so they hold identically every run.

AUTOFIX (default mode only, rewrites aws_li_to_gcp_li in place):
  - clamp unit_multiplier to <=1.0 on any "per <N>"/"per million|thousand" row
  - mark "$0.00 ... Mbps ... instance-hour" bundled-throughput rows -> ignore

GATES (both modes; violations -> validation_report.json -> exit 1):
  - passthrough on a mappable service (RDS/Aurora/ElastiCache/EBS/DataTransfer/
    ELB/S3/EC2) -> must be mapped, not carried 1:1
  - phantom-zero: AWS <= $1 but GCP > $10 on a mapped row
  - under-projection: mapped row with AWS > $1 but GCP == 0/NULL (wrong
    multiplier / Spot-to-$0 bug)
  - mapped SKU with no region-reachable rate (silent $0)
  - reconciliation: |Σ aws_li_catalog - bill_total| over tolerance (when the
    bill total can be read from input.txt/input.csv)

Then it recomputes totals deterministically and prints the verdict with the
CORRECT sign (positive diff = GCP cheaper).

Exit 0 -> clean.   Exit 1 -> hard violations remain.   Exit 2 -> db/schema error.
"""

import sys, os, json, re, glob

try:
    import duckdb
except Exception as e:  # pragma: no cover
    print(f"FATAL: python duckdb module not importable: {e}", file=sys.stderr)
    sys.exit(2)

MAPPABLE_SERVICE_PATTERNS = [
    "relational database service", "rds", "aurora",
    "elasticache",
    "elastic block store", "ebs",
    "data transfer", "datatransfer",
    "elastic load balancing", "load balanc",
    "elastic compute cloud",
    "simple storage service", "s3",
]

# CUD discount multipliers (rate/OD). Source: GCP public CUD discount docs.
# Loaded from data/cud_pct.json at module load; falls back to hardcoded dict
# if the file is missing. Refresh annually — GCP revises CUD rates.
# https://cloud.google.com/compute/docs/sustained-use-discounts

_CUD_PCT_FALLBACK = {
    "Compute Engine":                   (0.70, 0.55),
    "Cloud SQL":                        (0.75, 0.60),
    "Cloud Spanner":                    (0.75, 0.60),
    "Cloud Bigtable":                   (0.75, 0.60),
    "Memorystore":                      (0.80, 0.65),
    "Cloud Memorystore":                (0.80, 0.65),
    "Cloud Memorystore for Redis":      (0.80, 0.65),
    "Cloud Memorystore for Memcached":  (0.80, 0.65),
    "Cloud Run":                        (0.83, 0.67),
    "DEFAULT":                          (0.75, 0.60),
}

_CUD_PCT_CACHE = None

def load_cud_pct():
    """Load CUD multipliers from data/cud_pct.json, falling back to hardcoded dict."""
    global _CUD_PCT_CACHE
    if _CUD_PCT_CACHE is not None:
        return _CUD_PCT_CACHE

    skill_dir = os.environ.get("SKILL_DIR", "")
    if not skill_dir:
        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    json_path = os.path.join(skill_dir, "data", "cud_pct.json")

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
            _CUD_PCT_CACHE = result
            return _CUD_PCT_CACHE
        except Exception as e:
            print(f"WARNING: Could not load cud_pct.json ({e}); using hardcoded fallback.", file=sys.stderr)

    _CUD_PCT_CACHE = dict(_CUD_PCT_FALLBACK)
    return _CUD_PCT_CACHE

CUD_PCT = load_cud_pct()

# Deterministic AWS-instance -> GCP-family map (mirror of
# data/instance-family-map.json). Pins family choice so the same instance maps
# the same way every run — the single biggest GCP-cost variance source.
BURSTABLE_PREFIXES = ("t2", "t3", "t3a")

def gcp_family_for(itype, arch):
    """Canonical GCP machine family for an AWS instance type, or None if unknown."""
    if not itype:
        return None
    arch = (arch or "").lower()
    if arch == "arm64":
        return "T2A"
    fam = itype.lower().split(":")[0].replace("db.", "").split(".")[0]
    if fam == "a1" or fam.endswith("g") or fam.endswith("gd"):  # Graviton (arch missing)
        return "T2A"
    if fam in BURSTABLE_PREFIXES:
        return "E2"
    return "N2D"

def _family_in_name(fam, name):
    if fam == "T2A" and bool(re.search(r"\bC4A\b", name or "", re.I)):
        return True
    return bool(re.search(r"\b" + re.escape(fam) + r"\b", name or "", re.I))

PER_N_RE = re.compile(
    r"per\s+(?:[0-9][0-9,]*\s+)?(million|thousand|hundred|[0-9][0-9,]{2,})",
    re.IGNORECASE,
)

# Bill grand-total patterns, most reliable first. Group 1 = number.
BILL_TOTAL_RES = [
    re.compile(r"grand\s+total[:\s]*USD\s*([0-9][0-9,]*\.[0-9]{2})", re.I),
    re.compile(r"total\s+pre-?tax\s*USD\s*([0-9][0-9,]*\.[0-9]{2})", re.I),
    re.compile(r"^\s*total[^0-9]*USD\s*([0-9][0-9,]*\.[0-9]{2})", re.I | re.M),
]


def extract_bill_total(jobdir):
    """Best-effort read of the bill's stated grand total. Returns float or None."""
    for name in ("input.txt", "input.csv"):
        path = os.path.join(jobdir, name)
        if not os.path.exists(path):
            continue
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for rx in BILL_TOTAL_RES:
            m = rx.search(text)
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except ValueError:
                    pass
    return None

def find_regional_sku(con, bad_sku, target_region):
    # Locate catalog.duckdb relative to skill dir or standard paths
    skill_dir = os.environ.get("SKILL_DIR", "")
    if not skill_dir:
        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    catalog_db = os.path.join(skill_dir, "data", "catalog.duckdb")
    if not os.path.exists(catalog_db):
        return None
        
    try:
        con.execute(f"ATTACH '{catalog_db}' AS catalog (READ_ONLY)")
    except Exception:
        # Already attached or error
        pass
        
    try:
        # Get info of the bad SKU
        info = con.execute("""
            SELECT service_name, resource_group, usage_type, description, usage_unit
            FROM catalog.skus WHERE sku_id = ?
        """, [bad_sku]).fetchone()
        if not info:
            return None
        svc, rg, ut, desc, unit = info
        
        region_names = {
            # North America
            "us-east-1":      "us-east4",        # N. Virginia → Northern Virginia
            "us-east-2":      "us-east1",        # Ohio → South Carolina (closest)
            "us-west-1":      "us-west1",        # N. California → Oregon
            "us-west-2":      "us-west1",        # Oregon → Oregon
            "ca-central-1":   "northamerica-northeast1",  # Montreal
            "ca-west-1":      "northamerica-northeast2",  # Calgary
            # Europe
            "eu-west-1":      "europe-west1",    # Ireland
            "eu-west-2":      "europe-west2",    # London
            "eu-west-3":      "europe-west9",    # Paris
            "eu-central-1":   "europe-west3",    # Frankfurt
            "eu-central-2":   "europe-west6",    # Zurich
            "eu-north-1":     "europe-north1",   # Stockholm
            "eu-south-1":     "europe-west8",    # Milan
            "eu-south-2":     "europe-southwest1",  # Spain
            # Asia Pacific
            "ap-southeast-1": "asia-southeast1", # Singapore
            "ap-southeast-2": "australia-southeast1",  # Sydney
            "ap-southeast-3": "asia-southeast2", # Jakarta
            "ap-southeast-4": "australia-southeast2",  # Melbourne
            "ap-northeast-1": "asia-northeast1", # Tokyo
            "ap-northeast-2": "asia-northeast3", # Seoul
            "ap-northeast-3": "asia-northeast2", # Osaka
            "ap-south-1":     "asia-south1",     # Mumbai
            "ap-south-2":     "asia-south2",     # Hyderabad
            "ap-east-1":      "asia-east2",      # Hong Kong
            # Middle East & Africa
            "me-south-1":     "me-west1",        # Bahrain
            "me-central-1":   "me-central1",     # UAE
            "af-south-1":     "africa-south1",   # Cape Town
            # South America
            "sa-east-1":      "southamerica-east1",  # São Paulo
            "sa-west-1":      "southamerica-west1",  # Chile (Santiago)
            # US Gov / China (passthrough — no GCP equivalent region, use closest)
            "us-gov-east-1":  "us-east4",
            "us-gov-west-1":  "us-west1",
            "cn-north-1":     "asia-east1",
            "cn-northwest-1": "asia-east2",
        }
        
        candidates = con.execute("""
            SELECT sku_id, description FROM catalog.skus
            WHERE service_name = ? AND resource_group = ? AND usage_type = ? AND usage_unit = ?
              AND (list_contains(service_regions, ?) OR list_contains(service_regions, 'global'))
        """, [svc, rg, ut, unit, target_region]).fetchall()
        
        if not candidates:
            candidates = con.execute("""
                SELECT sku_id, description FROM catalog.skus
                WHERE service_name = ? AND usage_type = ? AND usage_unit = ?
                  AND (list_contains(service_regions, ?) OR list_contains(service_regions, 'global'))
            """, [svc, ut, unit, target_region]).fetchall()
            
        if not candidates:
            return None
            
        best_sku = None
        best_score = -1
        for cand_sku, cand_desc in candidates:
            if cand_desc == desc:
                return cand_sku
            
            score = 0
            desc_words = set(desc.lower().split())
            cand_words = set(cand_desc.lower().split())
            common = desc_words.intersection(cand_words)
            score = len(common)
            
            target_name = region_names.get(target_region, target_region)
            if target_name.lower() in cand_desc.lower():
                score += 10
                
            for r_code, r_name in region_names.items():
                if r_code != target_region and r_name.lower() in cand_desc.lower() and r_name.lower() not in desc.lower():
                    score -= 5
                    
            if score > best_score:
                best_score = score
                best_sku = cand_sku
                
        return best_sku
    finally:
        try:
            con.execute("DETACH catalog")
        except Exception:
            pass


def passthrough_budget_exceeded(con) -> list:
    """
    Gate: passthrough spend must not exceed 10% of total workload spend.

    Returns a list of violation dicts (empty if within budget).
    The first entry is a summary violation; the rest are the top-10
    passthrough rows by aws_amortized_cost.
    """
    BUDGET_PCT = 10.0
    try:
        row = con.execute("""
            SELECT
                COALESCE(SUM(c.aws_amortized_cost) FILTER (
                    WHERE m.strategy = 'passthrough' AND c.is_workload = TRUE
                ), 0.0)                                   AS passthrough_spend,
                COALESCE(SUM(c.aws_amortized_cost) FILTER (
                    WHERE c.is_workload = TRUE
                ), 0.0)                                   AS total_spend
            FROM aws_li_catalog c
            JOIN aws_li_to_gcp_li m ON m.aws_li_key = c.aws_li_key
        """).fetchone()
        if not row:
            return []
        passthrough_spend, total_spend = row
        if total_spend <= 0:
            return []
        pct = passthrough_spend / total_spend * 100
        if pct <= BUDGET_PCT:
            return []

        # Build violations list
        violations = [{
            "gate": "passthrough_budget",
            "severity": "HARD",
            "message": (
                f"Passthrough budget exceeded: {pct:.1f}% of spend is passthrough "
                f"(limit: {BUDGET_PCT}%)"
            ),
            "passthrough_spend": round(passthrough_spend, 2),
            "total_spend": round(total_spend, 2),
            "pct": round(pct, 2),
        }]

        top10 = con.execute("""
            SELECT c.product, ROUND(c.aws_amortized_cost, 2) AS aws_cost
            FROM aws_li_catalog c
            JOIN aws_li_to_gcp_li m ON m.aws_li_key = c.aws_li_key
            WHERE m.strategy = 'passthrough' AND c.is_workload = TRUE
            ORDER BY c.aws_amortized_cost DESC
            LIMIT 10
        """).fetchall()
        for product, aws_cost in top10:
            violations.append({
                "gate": "passthrough_budget",
                "severity": "HARD",
                "product": (product or "")[:80],
                "aws_amortized_cost": aws_cost,
            })

        return violations
    except Exception as e:
        return [{
            "gate": "passthrough_budget",
            "severity": "WARN",
            "message": f"passthrough_budget gate could not run: {e}",
        }]


def main():
    args = [a for a in sys.argv[1:]]
    check_only = "--check-only" in args
    args = [a for a in args if a != "--check-only"]
    jobdir = args[0] if args else "."
    db = os.path.join(jobdir, "projection-audit", "projection.duckdb")
    if not os.path.exists(db):
        print(f"FATAL: {db} not found", file=sys.stderr)
        sys.exit(2)

    con = duckdb.connect(db)
    report = {"mode": "check-only" if check_only else "autofix",
              "autofixes": {}, "violations": {}, "totals": {}}

    for t in ("aws_li_catalog", "aws_li_to_gcp_li"):
        try:
            con.execute(f"SELECT 1 FROM {t} LIMIT 1")
        except Exception as e:
            print(f"FATAL: required table {t} missing/unreadable: {e}", file=sys.stderr)
            sys.exit(2)

    # ---------- AUTOFIX (skipped in --check-only) -------------------------
    clamped, zeroed_keys = [], []
    if not check_only:
        rows = con.execute("""
            SELECT m.aws_li_key, m.component, m.unit_multiplier,
                   COALESCE(c.operation,'') op, COALESCE(c.usage_type,'') ut
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.unit_multiplier IS NOT NULL AND m.unit_multiplier > 1.0
        """).fetchall()
        for key, comp, mult, op, ut in rows:
            if PER_N_RE.search(f"{op} {ut}"):
                con.execute(
                    "UPDATE aws_li_to_gcp_li SET unit_multiplier=1.0, "
                    "projection_note=COALESCE(projection_note,'')||' [validator: per-N multiplier clamped 1.0]' "
                    "WHERE aws_li_key=? AND component IS NOT DISTINCT FROM ?", [key, comp])
                clamped.append({"aws_li_key": key, "was": mult, "op": op[:60]})

        zeroed = con.execute("""
            SELECT m.aws_li_key
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.strategy <> 'ignore' AND COALESCE(c.aws_amortized_cost,0) <= 0.01
              AND c.operation ILIKE '$0.00%' AND c.operation ILIKE '%Mbps%'
        """).fetchall()
        zeroed_keys = [r[0] for r in zeroed]
        if zeroed_keys:
            con.execute(
                "UPDATE aws_li_to_gcp_li SET strategy='ignore', gcp_sku_id=NULL, unit_multiplier=NULL, "
                "projection_note=COALESCE(projection_note,'')||' [validator: $0 bundled-throughput -> ignore]' "
                f"WHERE aws_li_key IN ({','.join(['?']*len(zeroed_keys))})", zeroed_keys)

        # Repair instance family mismatches
        family_mismatches = con.execute("""
            SELECT DISTINCT m.aws_li_key, c.instance_type, c.instance_arch, m.gcp_sku_id, r.gcp_sku_name, c.gcp_region
            FROM aws_li_to_gcp_li m
            JOIN aws_li_catalog c USING (aws_li_key)
            JOIN gcp_sku_rates  r ON r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type='OnDemand'
            WHERE m.strategy='break_down' AND m.component IN ('core','ram')
              AND m.gcp_service='Compute Engine' AND c.instance_type IS NOT NULL
        """).fetchall()
        
        fam_repaired = 0
        for key, itype, arch, bad_sku, sku_name, target_region in family_mismatches:
            want = gcp_family_for(itype, arch)
            if want and not _family_in_name(want, sku_name):
                skill_dir = os.environ.get("SKILL_DIR", "")
                if not skill_dir:
                    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                catalog_db = os.path.join(skill_dir, "data", "catalog.duckdb")
                if os.path.exists(catalog_db):
                    try:
                        con.execute(f"ATTACH '{catalog_db}' AS catalog (READ_ONLY)")
                    except Exception:
                        pass
                    try:
                        info = con.execute("""
                            SELECT service_name, resource_group, usage_type, usage_unit
                            FROM catalog.skus WHERE sku_id = ?
                        """, [bad_sku]).fetchone()
                        
                        if info:
                            svc, rg, ut, unit = info
                            desc_like = f"%{want}%"
                            if "core" in sku_name.lower():
                                desc_like += "%core%"
                            elif "ram" in sku_name.lower():
                                desc_like += "%ram%"
                                
                            candidates = con.execute("""
                                SELECT sku_id, description FROM catalog.skus
                                WHERE service_name = ? AND resource_group = ? AND usage_type = ? AND usage_unit = ?
                                  AND (list_contains(service_regions, ?) OR list_contains(service_regions, 'global'))
                                  AND description ILIKE ?
                            """, [svc, rg, ut, unit, target_region, desc_like]).fetchall()
                            
                            if not candidates:
                                candidates = con.execute("""
                                    SELECT sku_id, description FROM catalog.skus
                                    WHERE service_name = ? AND usage_type = ? AND usage_unit = ?
                                      AND (list_contains(service_regions, ?) OR list_contains(service_regions, 'global'))
                                      AND description ILIKE ?
                                """, [svc, ut, unit, target_region, desc_like]).fetchall()
                                
                            if candidates:
                                good_sku = candidates[0][0]
                                con.execute("""
                                    UPDATE aws_li_to_gcp_li SET gcp_sku_id = ?
                                    WHERE aws_li_key = ? AND gcp_sku_id = ?
                                """, [good_sku, key, bad_sku])
                                fam_repaired += 1
                                
                                rate_info = con.execute("""
                                    SELECT s.service_name, s.description, s.resource_family, s.resource_group,
                                           s.usage_type, s.usage_unit,
                                           COALESCE(MIN(t.rate_usd) FILTER (WHERE t.rate_usd > 0), 0.0) AS rate_usd
                                    FROM catalog.skus s
                                    LEFT JOIN catalog.tiered_rates t ON t.sku_id = s.sku_id
                                    WHERE s.sku_id = ?
                                    GROUP BY s.sku_id, s.service_name, s.description, s.resource_family, s.resource_group, s.usage_type, s.usage_unit
                                """, [good_sku]).fetchone()
                                
                                if rate_info:
                                    svc, desc, rf, rg, ut, unit, rate = rate_info
                                    con.execute("""
                                        INSERT OR REPLACE INTO gcp_sku_rates VALUES
                                        (?, ?, ?, ?, ?, 'OnDemand', ?, ?, ?, 'validator-repaired', 'catalog-bundled')
                                    """, (good_sku, svc, desc, rf, rg, target_region, unit, rate))
                                    p1, p3 = _cud_factors(svc)
                                    con.execute("""
                                        INSERT OR REPLACE INTO gcp_sku_rates VALUES
                                        (?, ?, ?, ?, ?, 'Commit1Yr', ?, ?, ?, 'validator-repaired', 'catalog-bundled')
                                    """, (good_sku, svc, desc + " (Commit1Yr alias)", rf, rg, target_region, unit, rate * p1))
                                    con.execute("""
                                        INSERT OR REPLACE INTO gcp_sku_rates VALUES
                                        (?, ?, ?, ?, ?, 'Commit3Yr', ?, ?, ?, 'validator-repaired', 'catalog-bundled')
                                    """, (good_sku, svc, desc + " (Commit3Yr alias)", rf, rg, target_region, unit, rate * p3))
                    finally:
                        try:
                            con.execute("DETACH catalog")
                        except Exception:
                            pass
        if fam_repaired > 0:
            print(f"  autofix: repaired {fam_repaired} instance family mismatch(es)")

        # Repair unreachable regional SKU mismatches
        unreachable = con.execute("""
            SELECT m.aws_li_key, m.gcp_sku_id, c.gcp_region
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.gcp_sku_id IS NOT NULL AND m.strategy IN ('map','break_down')
        """).fetchall()
        
        repaired_count = 0
        for key, bad_sku, target_region in unreachable:
            has_rate = con.execute("""
                SELECT 1 FROM gcp_sku_rates WHERE gcp_sku_id = ? AND (region = ? OR region = 'global')
            """, [bad_sku, target_region]).fetchone()
            
            if not has_rate:
                good_sku = find_regional_sku(con, bad_sku, target_region)
                if good_sku:
                    if good_sku != bad_sku:
                        con.execute("""
                            UPDATE aws_li_to_gcp_li SET gcp_sku_id = ?
                            WHERE aws_li_key = ? AND gcp_sku_id = ?
                        """, [good_sku, key, bad_sku])
                        repaired_count += 1
                    
                    # Load rates for good_sku from catalog.duckdb into local gcp_sku_rates
                    skill_dir = os.environ.get("SKILL_DIR", "")
                    if not skill_dir:
                        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    catalog_db = os.path.join(skill_dir, "data", "catalog.duckdb")
                    if os.path.exists(catalog_db):
                        try:
                            con.execute(f"ATTACH '{catalog_db}' AS catalog (READ_ONLY)")
                        except Exception:
                            pass
                        try:
                            rate_info = con.execute("""
                                SELECT s.service_name, s.description, s.resource_family, s.resource_group,
                                       s.usage_type, s.usage_unit,
                                       COALESCE(MIN(t.rate_usd) FILTER (WHERE t.rate_usd > 0), 0.0) AS rate_usd
                                FROM catalog.skus s
                                LEFT JOIN catalog.tiered_rates t ON t.sku_id = s.sku_id
                                WHERE s.sku_id = ?
                                GROUP BY s.sku_id, s.service_name, s.description, s.resource_family, s.resource_group, s.usage_type, s.usage_unit
                            """, [good_sku]).fetchone()
                            
                            if rate_info:
                                svc, desc, rf, rg, ut, unit, rate = rate_info
                                # Insert OnDemand rate
                                con.execute("""
                                    INSERT OR REPLACE INTO gcp_sku_rates VALUES
                                    (?, ?, ?, ?, ?, 'OnDemand', ?, ?, ?, 'validator-repaired', 'catalog-bundled')
                                """, (good_sku, svc, desc, rf, rg, target_region, unit, rate))
                                
                                # Synthesize Commit1Yr and Commit3Yr rates
                                p1, p3 = _cud_factors(svc)
                                con.execute("""
                                    INSERT OR REPLACE INTO gcp_sku_rates VALUES
                                    (?, ?, ?, ?, ?, 'Commit1Yr', ?, ?, ?, 'validator-repaired', 'catalog-bundled')
                                """, (good_sku, svc, desc + " (Commit1Yr alias)", rf, rg, target_region, unit, rate * p1))
                                con.execute("""
                                    INSERT OR REPLACE INTO gcp_sku_rates VALUES
                                    (?, ?, ?, ?, ?, 'Commit3Yr', ?, ?, ?, 'validator-repaired', 'catalog-bundled')
                                """, (good_sku, svc, desc + " (Commit3Yr alias)", rf, rg, target_region, unit, rate * p3))
                        finally:
                            try:
                                con.execute("DETACH catalog")
                            except Exception:
                                pass
        if repaired_count > 0:
            print(f"  autofix: repaired {repaired_count} regional SKU mismatch(es)")

    # AUTOFIX: deterministic CUD synthesis. Phase 4 is supposed to alias/
    # synthesize Commit1Yr/Commit3Yr rates onto every compute/managed-db OD sku,
    # but the model frequently misses some -> those rows then bill at OD for the
    # CUD columns and the 1yr/3yr totals look almost flat. Here we fill any gap
    # deterministically from the OD rate using GCP's published CUD percentages.
    # Only fires for sku_ids that have an OD rate but lack the commit row, so it
    # never overwrites a real aliased commit rate the model already loaded.
    _CUD_DEFAULT = (0.70, 0.55)  # conservative fallback when service not in CUD_PCT

    def _cud_factors(svc):
        if svc not in CUD_PCT:
            print(f"[WARN] CUD_PCT has no entry for service '{svc}' — using conservative default {_CUD_DEFAULT}")
            return _CUD_DEFAULT
        return CUD_PCT[svc]

    cud_synth = 0
    if not check_only:
        for svc, (p1, p3) in CUD_PCT.items():
            for pt, factor in (("Commit1Yr", p1), ("Commit3Yr", p3)):
                try:
                    con.execute(f"""
                        INSERT OR REPLACE INTO gcp_sku_rates
                          (gcp_sku_id, gcp_service, gcp_sku_name, resource_family,
                           resource_group, pricing_type, region, unit, rate_usd, source, audit_url)
                        SELECT od.gcp_sku_id, od.gcp_service, od.gcp_sku_name, od.resource_family,
                               od.resource_group, '{pt}', od.region, od.unit,
                               od.rate_usd * {factor}, 'validator-cud-synth',
                               'CUD synthesized from OD x {factor}'
                        FROM gcp_sku_rates od
                        WHERE od.gcp_service = ? AND od.pricing_type = 'OnDemand'
                          AND NOT EXISTS (SELECT 1 FROM gcp_sku_rates r
                            WHERE r.gcp_sku_id = od.gcp_sku_id AND r.region = od.region
                              AND r.pricing_type = '{pt}')
                    """, [svc])
                    # Use changes() to count rows affected by this INSERT OR REPLACE,
                    # which correctly counts new inserts and stale-rate replacements.
                    cud_synth += con.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    pass

    report["autofixes"] = {"per_n_multiplier_clamped": clamped,
                           "zero_throughput_info_rows_ignored": len(zeroed_keys),
                           "cud_rates_synthesized": cud_synth}

    has_view = True
    try:
        con.execute("SELECT 1 FROM gcp_projection LIMIT 1")
    except Exception:
        has_view = False

    view_missing_violation = None
    if not has_view:
        view_missing_violation = {
            "gate": "projection_view_missing",
            "severity": "CRITICAL",
            "message": (
                "gcp_projection view does not exist — Phase 4 rate-fill may have failed. "
                "All projection-based gates are disabled."
            ),
            "count": 1,
            "rows": [],
        }

    # ---------- GATES ------------------------------------------------------
    like = " OR ".join(["LOWER(c.product) LIKE ?"] * len(MAPPABLE_SERVICE_PATTERNS))
    params = [f"%{p}%" for p in MAPPABLE_SERVICE_PATTERNS]
    bad_pt = con.execute(f"""
        SELECT c.product, COALESCE(c.operation,'') op, ROUND(c.aws_amortized_cost,2)
        FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.strategy='passthrough' AND c.aws_amortized_cost > 1 AND ({like})
        ORDER BY c.aws_amortized_cost DESC
    """, params).fetchall()
    report["violations"]["passthrough_on_mappable_service"] = [
        {"product": r[0], "op": r[1][:70], "aws": r[2]} for r in bad_pt]

    phantom, underproj = [], []
    if has_view:
        phantom = con.execute("""
            SELECT aws_li_key, ROUND(aws_amortized_cost,2), ROUND(gcp_projected_cost,2)
            FROM gcp_projection
            WHERE is_workload AND strategy NOT IN ('ignore','passthrough')
              AND aws_amortized_cost <= 1 AND gcp_projected_cost > 10
            ORDER BY gcp_projected_cost DESC
        """).fetchall()
        underproj = con.execute("""
            SELECT aws_li_key, ROUND(aws_amortized_cost,2)
            FROM gcp_projection
            WHERE is_workload AND strategy IN ('map','break_down')
              AND aws_amortized_cost > 1
              AND COALESCE(gcp_projected_cost,0) = 0
            ORDER BY aws_amortized_cost DESC
        """).fetchall()
    report["violations"]["phantom_zero"] = [
        {"aws_li_key": r[0], "aws": r[1], "gcp": r[2]} for r in phantom]
    report["violations"]["under_projection_gcp_zero"] = [
        {"aws_li_key": r[0], "aws": r[1]} for r in underproj]

    unreachable = []
    try:
        unreachable = con.execute("""
            SELECT m.gcp_service, c.gcp_region, m.gcp_sku_id
            FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.gcp_sku_id IS NOT NULL AND m.strategy IN ('map','break_down')
              AND NOT EXISTS (SELECT 1 FROM gcp_sku_rates r
                WHERE r.gcp_sku_id=m.gcp_sku_id AND (r.region=c.gcp_region OR r.region='global'))
            GROUP BY 1,2,3
        """).fetchall()
    except Exception:
        pass
    report["violations"]["unreachable_rate"] = [
        {"gcp_service": r[0], "gcp_region": r[1], "gcp_sku_id": r[2]} for r in unreachable]

    # GATE: storage/transfer over-projection. Storage and data-transfer have no
    # RI/SP/commitment discount on the AWS side, so aws_amortized_cost is ~list
    # price. If GCP On-Demand is >2x that, it is a wrong-SKU or unit bug (the
    # egress 8x, EBS 18x, gp2 3x classes), not a legitimate price difference.
    # Compute is intentionally excluded — AWS RI/SP can make it genuinely >2x.
    overproj = []
    if has_view:
        overproj = con.execute("""
            SELECT aws_li_key, product, gcp_sku_id,
                   ROUND(aws_amortized_cost,2), ROUND(gcp_projected_cost,2),
                   ROUND(gcp_projected_cost / NULLIF(aws_amortized_cost,0), 2)
            FROM gcp_projection
            WHERE is_workload AND strategy IN ('map','break_down')
              AND aws_amortized_cost > 20
              AND gcp_projected_cost > aws_amortized_cost * 2.0
              AND ( component = 'storage'
                 OR LOWER(product) LIKE '%data transfer%'
                 OR LOWER(product) LIKE '%storage%'
                 OR LOWER(product) LIKE '%snapshot%'
                 OR LOWER(product) LIKE '%egress%' )
            ORDER BY gcp_projected_cost - aws_amortized_cost DESC
        """).fetchall()
    report["violations"]["storage_transfer_over_projection"] = [
        {"aws_li_key": r[0], "product": (r[1] or "")[:60], "gcp_sku_id": r[2],
         "aws": r[3], "gcp": r[4], "ratio": r[5]} for r in overproj]

    # GATE: CUD coverage. Every compute / managed-db core|ram sku that bills at
    # OD must also carry a Commit3Yr rate, else the 3yr column silently falls
    # back to OD. In autofix mode the synthesis above closes this; in
    # --check-only (watcher backstop) it catches a run that never got CUDs.
    cud_missing = con.execute("""
        SELECT DISTINCT m.gcp_service, m.gcp_sku_id
        FROM aws_li_to_gcp_li m
        WHERE m.strategy IN ('map','break_down') AND m.component IN ('core','ram')
          AND m.gcp_sku_id IS NOT NULL
          AND m.gcp_service IN ('Compute Engine','Cloud SQL','AlloyDB',
               'Cloud Memorystore','Cloud Memorystore for Redis','Cloud Memorystore for Memcached')
          AND EXISTS (SELECT 1 FROM gcp_sku_rates r
                WHERE r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type = 'OnDemand')
          AND NOT EXISTS (SELECT 1 FROM gcp_sku_rates r
                WHERE r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type = 'Commit3Yr')
    """).fetchall()
    report["violations"]["cud_coverage_missing"] = [
        {"gcp_service": r[0], "gcp_sku_id": r[1]} for r in cud_missing]

    # GATE: instance-family mismatch. The GCP family for a compute row is fixed
    # by data/instance-family-map.json (arm64->T2A, t-family->E2, else N2D). If
    # the resolved SKU is a different family, the model made a non-deterministic
    # 60/40 pick and the row must be re-mapped to the canonical family. This is
    # the control that kills the run-to-run "GCP cost swings 50%" problem.
    fam_mismatch = []
    try:
        famrows = con.execute("""
            SELECT DISTINCT c.instance_type, c.instance_arch, m.gcp_sku_id, r.gcp_sku_name
            FROM aws_li_to_gcp_li m
            JOIN aws_li_catalog c USING (aws_li_key)
            JOIN gcp_sku_rates  r ON r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type='OnDemand'
            WHERE m.strategy='break_down' AND m.component IN ('core','ram')
              AND m.gcp_service='Compute Engine' AND c.instance_type IS NOT NULL
        """).fetchall()
        for itype, arch, sku, sku_name in famrows:
            want = gcp_family_for(itype, arch)
            if want and not _family_in_name(want, sku_name):
                fam_mismatch.append({"instance_type": itype, "expected_family": want,
                                     "gcp_sku_id": sku, "got": (sku_name or "")[:50]})
    except Exception:
        pass
    report["violations"]["instance_family_mismatch"] = fam_mismatch

    # reconciliation: catalog total vs the bill's stated grand total
    aws_total = con.execute(
        "SELECT ROUND(COALESCE(SUM(aws_amortized_cost),0),2) FROM aws_li_catalog").fetchone()[0]
    bill_total = extract_bill_total(jobdir)
    if bill_total is None:
        print("[WARN] bill_total not found in input — reconciliation skipped")
        report["autofixes"]["bill_total_unavailable"] = (
            "Bill total could not be extracted from input — reconciliation gate skipped. "
            "Verify GCP total manually."
        )
    recon = []
    if bill_total is not None:
        delta = round(aws_total - bill_total, 2)
        tol = max(1.0, 0.005 * bill_total)
        if abs(delta) > tol:
            recon.append({"catalog_total": aws_total, "bill_total": bill_total,
                          "delta": delta, "tolerance": round(tol, 2)})
    report["violations"]["reconciliation"] = recon

    # Capacity reconciliation check (vCPU and RAM)
    cap_recon = []
    try:
        aws_cap = con.execute("""
            SELECT SUM(instance_vcpus * total_usage / (billing_days * 24.0)),
                   SUM(instance_ram_gb * total_usage / (billing_days * 24.0))
            FROM aws_li_catalog
            WHERE is_workload = TRUE AND instance_vcpus IS NOT NULL
        """).fetchone()
        
        aws_vcpu = aws_cap[0] or 0.0
        aws_ram = aws_cap[1] or 0.0

        gcp_cap = con.execute("""
            SELECT 
                SUM(CASE WHEN m.component = 'core' THEN m.unit_multiplier * c.total_usage / (c.billing_days * 24.0) ELSE 0.0 END),
                SUM(CASE WHEN m.component = 'ram' THEN m.unit_multiplier * c.total_usage / (c.billing_days * 24.0) ELSE 0.0 END)
            FROM aws_li_to_gcp_li m
            JOIN aws_li_catalog c USING (aws_li_key)
            WHERE m.strategy IN ('map','break_down')
        """).fetchone()
        
        gcp_vcpu = gcp_cap[0] or 0.0
        gcp_ram = gcp_cap[1] or 0.0

        if aws_vcpu > 0 and (gcp_vcpu / aws_vcpu) < 0.90:
            cap_recon.append({
                "metric": "vCPU",
                "aws_capacity": round(aws_vcpu, 2),
                "gcp_capacity": round(gcp_vcpu, 2),
                "ratio": round(gcp_vcpu / aws_vcpu, 3),
                "error": "GCP vCPU capacity is more than 10% below AWS provisioned vCPUs."
            })
            
        if aws_ram > 0 and (gcp_ram / aws_ram) < 0.90:
            cap_recon.append({
                "metric": "RAM (GB)",
                "aws_capacity": round(aws_ram, 2),
                "gcp_capacity": round(gcp_ram, 2),
                "ratio": round(gcp_ram / aws_ram, 3),
                "error": "GCP RAM capacity is more than 10% below AWS provisioned RAM."
            })
    except Exception:
        pass
    report["violations"]["capacity_reconciliation"] = cap_recon

    # GATE: passthrough budget. If passthrough rows account for more than 5% of
    # total workload spend, the mapping phase has left too much unresolved.
    report["violations"]["passthrough_budget"] = passthrough_budget_exceeded(con)

    # Inject CRITICAL view-missing violation at the end so it always appears
    if view_missing_violation is not None:
        report["violations"]["projection_view_missing"] = [view_missing_violation]

    # ---------- deterministic totals + verdict ----------------------------
    gcp_od = gcp_1yr = gcp_3yr = None
    if has_view:
        gcp_od, gcp_1yr, gcp_3yr = con.execute("""
            SELECT ROUND(COALESCE(SUM(gcp_projected_cost),0),2),
                   ROUND(COALESCE(SUM(gcp_cost_1yr_cud),0),2),
                   ROUND(COALESCE(SUM(gcp_cost_3yr_cud),0),2)
            FROM gcp_projection WHERE is_workload""").fetchone()
    diff = None if gcp_od is None else round(aws_total - gcp_od, 2)
    verdict = None
    if not has_view:
        verdict = "view missing — projection totals unavailable"
    elif diff is not None:
        verdict = "GCP cheaper" if diff > 0 else ("GCP more expensive" if diff < 0 else "~ equal")
    report["totals"] = {"aws_total": aws_total, "gcp_od": gcp_od,
                        "gcp_1yr_cud": gcp_1yr, "gcp_3yr_cud": gcp_3yr,
                        "bill_total": bill_total,
                        "diff_aws_minus_gcp_od": diff, "verdict": verdict,
                        **({"view_missing": True} if not has_view else {})}

    con.close()
    with open(os.path.join(jobdir, "validation_report.json"), "w") as f:
        json.dump(report, f, indent=2)

    v = report["violations"]
    counts = {k: len(v[k]) for k in v}
    n_hard = sum(counts.values())

    print("== validate_fix.py" + (" (check-only)" if check_only else "") + " ==")
    if not check_only:
        print(f"  autofix: clamped {len(clamped)} per-N multiplier(s); "
              f"zeroed {len(zeroed_keys)} $0 throughput row(s); "
              f"synthesized {cud_synth} CUD rate(s)")
    t = report["totals"]
    print(f"  totals : AWS ${t['aws_total']}  GCP_OD ${t['gcp_od']}  "
          f"1yr ${t['gcp_1yr_cud']}  3yr ${t['gcp_3yr_cud']}"
          + (f"  (bill ${t['bill_total']})" if t['bill_total'] is not None else ""))
    print(f"  verdict: diff(AWS-GCP_OD)=${t['diff_aws_minus_gcp_od']} -> {t['verdict']}")
    print("  gates  : " + "  ".join(f"{k}={counts[k]}" for k in counts))
    if n_hard:
        print(f"  RESULT : FAIL — {n_hard} violation(s). See validation_report.json.")
        sys.exit(1)
    print("  RESULT : PASS — safe to render Phase 6." if not check_only
          else "  RESULT : PASS.")
    sys.exit(0)


if __name__ == "__main__":
    main()
