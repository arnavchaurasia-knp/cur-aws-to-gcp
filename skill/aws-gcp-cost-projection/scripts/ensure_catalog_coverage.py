#!/usr/bin/env python3
"""
ensure_catalog_coverage.py — Pre-Phase-4 catalog health check + auto-refresh.

For every (gcp_service, gcp_region, gcp_sku_name) row needed by this job:
  1. Check whether the bundled catalog has a matching SKU.
  2. If not, try the GCP Cloud Billing API to refresh the service's catalog file.
  3. If API is unavailable, fall back to the existing bundled data.
  4. Repair NULL gcp_sku_name on object_storage rows (Phase 5 sometimes clears it
     when it incorrectly sets strategy='passthrough' for a missing-rate row).

Runs before apply_rates.py so the rate-loader always finds what it needs.

Auth: uses GOOGLE_CLOUD_API_KEY env var, or falls back to `gcloud auth print-access-token`.
No credentials → audit-only mode (logs gaps, no catalog update).
"""

import gzip
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

JOB_DIR  = os.getcwd()
DB_PATH  = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
SKILL_DIR = os.environ.get("SKILL_DIR", "")
DATA_DIR  = os.path.join(SKILL_DIR, "data")

# S3 storage class → GCS SKU name (mirrors apply_static_mappings.py)
S3_CLASS_MAP = {
    "standard":             "Regional Storage",
    "intelligent":          "Regional Storage",
    "standardia":           "Nearline Storage",
    "standard-ia":          "Nearline Storage",
    "onezone-ia":           "Nearline Storage",
    "glacier instant":      "Coldline Storage",
    "glacier flexible":     "Coldline Storage",
    "glacier deep archive": "Archive Storage",
    "reducedredundancy":    "Regional Storage",
}
S3_CLASS_DEFAULT_SKU = "Regional Storage"

# Services that have a real GCP equivalent — a passthrough here is always a
# mis-triage (usually the LLM escaping a missing rate), never a genuine
# "no GCP equivalent". Mirrors MAPPABLE_SERVICE_PATTERNS in validate_fix.py.
MAPPABLE_SERVICE_PATTERNS = (
    "relational database service", "rds", "aurora",
    "elasticache",
    "elastic block store", "ebs",
    "data transfer", "datatransfer",
    "elastic load balancing", "load balanc",
    "elastic compute cloud",
    "simple storage service", "s3",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gcp_token():
    key = os.environ.get("GOOGLE_CLOUD_API_KEY") or os.environ.get("GCP_API_KEY")
    if key:
        return ("key", key)
    try:
        tok = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        if tok:
            return ("bearer", tok)
    except Exception:
        pass
    return None


def _gcp_get(url, token_info):
    kind, value = token_info
    if kind == "key":
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}key={value}"
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {value}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _load_services():
    path = os.path.join(DATA_DIR, "services.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return {s["displayName"]: s["serviceId"] for s in json.load(f)}


def _load_catalog(service_id):
    path = os.path.join(DATA_DIR, "skus", f"{service_id}.json.gz")
    if not os.path.exists(path):
        return []
    with gzip.open(path, "rt") as f:
        return json.load(f)


def _save_catalog(service_id, skus):
    path = os.path.join(DATA_DIR, "skus", f"{service_id}.json.gz")
    with gzip.open(path, "wt") as f:
        json.dump(skus, f)


def _has_coverage(skus, sku_name, gcp_region):
    """True if any SKU in the catalog matches sku_name (word overlap) and covers gcp_region."""
    sku_name_lower = sku_name.lower()
    name_words = set(sku_name_lower.split())
    for s in skus:
        desc = s.get("description", "").lower()
        desc_words = set(desc.split())
        if len(desc_words & name_words) < 1:
            continue
        geo = s.get("geoTaxonomy", {})
        if geo.get("type") == "GLOBAL":
            return True
        if not gcp_region or gcp_region == "global":
            return True
        if gcp_region in s.get("serviceRegions", []):
            return True
    return False


def _fetch_service_skus(service_id, token_info):
    """Fetch all SKUs for a service from the GCP Cloud Billing API."""
    skus = []
    page_token = ""
    while True:
        url = f"https://cloudbilling.googleapis.com/v1/services/{service_id}/skus?pageSize=5000"
        if page_token:
            url += f"&pageToken={urllib.parse.quote(page_token)}"
        try:
            data = _gcp_get(url, token_info)
        except Exception as e:
            print(f"  [catalog] API fetch failed: {e}")
            break
        skus.extend(data.get("skus", []))
        page_token = data.get("nextPageToken", "")
        if not page_token:
            break
    return skus


# ---------------------------------------------------------------------------
# NULL gcp_sku_name repair for object_storage rows
# ---------------------------------------------------------------------------

def _repair_null_sku_names(conn):
    """
    Phase 5 LLM sometimes sets strategy='passthrough' on object_storage rows
    (when it can't find a rate), which clears gcp_sku_name. Re-inject the
    correct SKU name from the S3 class lookup table so apply_rates.py can
    resolve the SKU from the catalog.
    """
    rows = conn.execute("""
        SELECT m.aws_li_key, cat.usage_type, cat.product
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog cat USING (aws_li_key)
        WHERE m.gcp_sku_name IS NULL
          AND cat.mechanic_group = 'object_storage'
    """).fetchall()

    repaired = 0
    for aws_li_key, usage_type, product in rows:
        ut_lower = (usage_type or "").lower()
        sku_name = S3_CLASS_DEFAULT_SKU
        for cls, name in S3_CLASS_MAP.items():
            if cls in ut_lower:
                sku_name = name
                break

        conn.execute("""
            UPDATE aws_li_to_gcp_li
            SET gcp_service = 'Cloud Storage',
                gcp_sku_name = ?,
                strategy = 'map'
            WHERE aws_li_key = ? AND gcp_sku_name IS NULL
        """, (sku_name, aws_li_key))
        print(f"  [repair] {aws_li_key[:12]}… ({usage_type}) → Cloud Storage / {sku_name}")
        repaired += 1

    if repaired:
        print(f"  Repaired {repaired} NULL gcp_sku_name object_storage row(s)")
    return repaired


def _repair_illegal_passthrough(conn):
    """
    A passthrough on a mappable service (RDS/EBS/ELB/S3/…) is always a
    mis-triage — GCP has an equivalent by definition, so passthrough there is
    the LLM escaping a missing rate. Restore strategy='map' for any such row
    that still carries a gcp_service + gcp_sku_name; apply_rates.py + the
    global-fallback rate will then price it. This generalizes the old S3-only
    repair to every mappable service and closes the passthrough retry loop.
    """
    like = " OR ".join(["LOWER(cat.product) LIKE ?"] * len(MAPPABLE_SERVICE_PATTERNS))
    params = [f"%{p}%" for p in MAPPABLE_SERVICE_PATTERNS]
    rows = conn.execute(f"""
        SELECT m.aws_li_key, cat.product, m.gcp_service, m.gcp_sku_name
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog cat USING (aws_li_key)
        WHERE m.strategy = 'passthrough'
          AND cat.is_workload = true
          AND ({like})
    """, params).fetchall()

    restored, unresolved = 0, 0
    for aws_li_key, product, gcp_service, gcp_sku_name in rows:
        if gcp_service and gcp_sku_name:
            conn.execute(
                "UPDATE aws_li_to_gcp_li SET strategy = 'map' "
                "WHERE aws_li_key = ? AND strategy = 'passthrough'",
                (aws_li_key,),
            )
            print(f"  [repair] {aws_li_key[:12]}… passthrough→map ({product} / {gcp_sku_name})")
            restored += 1
        else:
            # No service/SKU to restore to and not an object_storage row the
            # S3 table can fix — flag it loudly rather than let it loop silently.
            print(f"  [warn] {aws_li_key[:12]}… passthrough on mappable {product!r} "
                  f"but no gcp_service/gcp_sku_name to restore — needs a mapping")
            unresolved += 1

    if restored:
        print(f"  Restored {restored} illegal-passthrough row(s) to strategy='map'")
    if unresolved:
        print(f"  {unresolved} mappable passthrough row(s) could not be auto-restored")
    return restored


def _downgrade_enterprise_plus(conn, services):
    """Downgrade Cloud SQL 'Enterprise Plus' mappings to the standard 'Enterprise'
    edition. Enterprise Plus is GCP's premium tier (~2x); for a like-for-like RDS
    migration the Enterprise edition is the correct default. The LLM tends to
    over-pick Plus, inflating managed_db. We swap to the Enterprise SKU with the
    same structure (Zonal/Regional × vCPU/RAM/Storage) covering the same region;
    apply_rates then loads the cheaper Enterprise rate automatically. No hardcoded
    SKU ids — the catalog is the contract.
    """
    rows = conn.execute("""
        SELECT m.aws_li_key, m.gcp_sku_id, m.gcp_sku_name, c.gcp_region
        FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.gcp_service = 'Cloud SQL' AND m.gcp_sku_name LIKE '%Enterprise Plus%'
    """).fetchall()
    if not rows:
        return 0

    service_id = services.get("Cloud SQL")
    catalog = _load_catalog(service_id) if service_id else []
    if not catalog:
        return 0

    def _find_enterprise(tier, kind, gcp_region):
        # tier: 'regional'|'zonal'; kind: 'ram'|'storage'|'vcpu'
        for s in catalog:
            d = s.get("description", "")
            dl = d.lower()
            if "enterprise" not in dl or "plus" in dl or "extended" in dl:
                continue
            if tier not in dl:
                continue
            if kind == "ram" and "ram" not in dl:
                continue
            if kind == "vcpu" and "vcpu" not in dl:
                continue
            if kind == "storage" and "storage" not in dl:
                continue
            # vCPU/RAM SKUs must not be storage rows and vice-versa
            if kind != "storage" and "storage" in dl:
                continue
            geo = s.get("geoTaxonomy", {})
            regions = s.get("serviceRegions", [])
            if geo.get("type") == "GLOBAL" or not gcp_region or gcp_region == "global" or gcp_region in regions:
                return s["skuId"], d
        return None, None

    swapped = 0
    for aws_li_key, sku_id, sku_name, gcp_region in rows:
        nl = (sku_name or "").lower()
        tier = "regional" if "regional" in nl else "zonal"
        kind = "ram" if "ram" in nl else ("storage" if "storage" in nl else "vcpu")
        new_id, new_name = _find_enterprise(tier, kind, gcp_region)
        if new_id and new_id != sku_id:
            conn.execute(
                "UPDATE aws_li_to_gcp_li SET gcp_sku_id = ?, gcp_sku_name = ? "
                "WHERE aws_li_key = ? AND gcp_sku_id = ?",
                (new_id, new_name, aws_li_key, sku_id),
            )
            print(f"  [downgrade] {aws_li_key[:12]}… Enterprise Plus → Enterprise ({kind}/{tier})")
            swapped += 1

    if swapped:
        print(f"  Downgraded {swapped} Cloud SQL Enterprise Plus → Enterprise mapping(s)")
    return swapped


# ---------------------------------------------------------------------------
# Coverage check + refresh
# ---------------------------------------------------------------------------

def _check_and_refresh(conn, services_map, token_info):
    """
    For each (gcp_service, gcp_region, gcp_sku_name) needed, check catalog
    coverage. Refresh from API for any service with a gap.
    """
    needed = conn.execute("""
        SELECT DISTINCT m.gcp_service, cat.gcp_region, m.gcp_sku_name
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog cat USING (aws_li_key)
        WHERE m.gcp_sku_name IS NOT NULL
          AND m.strategy IN ('map', 'break_down')
    """).fetchall()

    if not needed:
        print("  No mapped rows with gcp_sku_name — nothing to check")
        return

    # Group gaps by service
    service_gaps = {}  # service_name → [(region, sku_name), ...]
    for gcp_service, gcp_region, gcp_sku_name in needed:
        service_id = services_map.get(gcp_service)
        if not service_id:
            print(f"  [skip] {gcp_service}: not in services.json")
            continue
        skus = _load_catalog(service_id)
        if not _has_coverage(skus, gcp_sku_name, gcp_region):
            service_gaps.setdefault(gcp_service, []).append((gcp_region, gcp_sku_name))

    if not service_gaps:
        print("  All required SKUs have catalog coverage — no refresh needed")
        return

    print(f"  Catalog gaps detected for {len(service_gaps)} service(s):")
    for svc, gaps in service_gaps.items():
        for region, sku_name in gaps:
            print(f"    {svc} / {sku_name!r} / {region}")

    if not token_info:
        print("  No GCP auth (set GOOGLE_CLOUD_API_KEY or run `gcloud auth login`)")
        print("  Proceeding with bundled catalog — global-fallback rates will be used")
        return

    # Refresh services with gaps
    refreshed = 0
    for gcp_service in service_gaps:
        service_id = services_map.get(gcp_service)
        if not service_id:
            continue
        print(f"  [fetch] Refreshing {gcp_service} from GCP Cloud Billing API...")
        try:
            fresh_skus = _fetch_service_skus(service_id, token_info)
        except Exception as e:
            print(f"  [fetch] FAILED for {gcp_service}: {e}")
            continue
        if fresh_skus:
            _save_catalog(service_id, fresh_skus)
            print(f"  [fetch] {gcp_service}: {len(fresh_skus)} SKUs saved")
            refreshed += 1

            # Verify coverage improved
            still_missing = []
            for region, sku_name in service_gaps[gcp_service]:
                if not _has_coverage(fresh_skus, sku_name, region):
                    still_missing.append((region, sku_name))
            if still_missing:
                for region, sku_name in still_missing:
                    print(f"  [warn] After refresh, still no coverage: {gcp_service} / {sku_name!r} / {region}")
            else:
                print(f"  [fetch] Coverage gap closed for {gcp_service}")

    if refreshed:
        print(f"  Refreshed {refreshed} catalog file(s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        import duckdb
    except ImportError:
        print("duckdb not available — skipping catalog coverage check")
        return

    if not os.path.exists(DB_PATH):
        print("DB not found — skipping catalog coverage check")
        return

    if not SKILL_DIR:
        print("SKILL_DIR not set — skipping catalog coverage check")
        return

    conn = duckdb.connect(DB_PATH)
    services_map = _load_services()
    if not services_map:
        print("services.json not found — skipping catalog coverage check")
        return

    print("=== ensure_catalog_coverage ===")

    # Step 1a: repair NULL gcp_sku_names caused by Phase 5 passthrough overwrite (S3)
    _repair_null_sku_names(conn)

    # Step 1b: restore any illegal passthrough on a mappable service to 'map'
    _repair_illegal_passthrough(conn)

    # Step 1c: downgrade Cloud SQL Enterprise Plus → Enterprise (premium-tier over-pick)
    _downgrade_enterprise_plus(conn, services_map)

    # Step 2: check catalog coverage for all needed SKUs, refresh from API if needed
    token_info = _gcp_token()
    print(f"  GCP auth: {token_info[0] if token_info else 'none — audit-only'}")
    _check_and_refresh(conn, services_map, token_info)

    conn.close()
    print("=== ensure_catalog_coverage done ===")


if __name__ == "__main__":
    main()
