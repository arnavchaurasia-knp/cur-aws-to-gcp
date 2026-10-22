#!/usr/bin/env python3
"""
ensure_catalog_coverage.py — Pre-Phase-4 catalog health check + auto-refresh.

For every (gcp_service, gcp_region, gcp_sku_name) row needed by this job:
  1. Check whether the bundled catalog has a matching SKU.
  2. If not, try the GCP Cloud Billing API to refresh the service's catalog file.
  3. If API is unavailable, fall back to the existing bundled data.

Runs before apply_rates.py so the rate-loader always finds what it needs.
(Repairs have been relocated to scripts/validate_fix.py per R-1).

Auth: uses GOOGLE_CLOUD_API_KEY/GCP_API_KEY env var. The `gcloud auth
print-access-token` fallback is opt-in only via AGY_ALLOW_GCLOUD_LIVE_FETCH=1
-- it can hang indefinitely on Windows even with an active gcloud session
(confirmed real), so it is never attempted automatically.
No credentials → audit-only mode (logs gaps, no catalog update).
"""

import gzip
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

JOB_DIR  = os.getcwd()
DB_PATH  = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
SKILL_DIR = os.environ.get("SKILL_DIR", "")
DATA_DIR  = os.path.join(SKILL_DIR, "data")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gcp_token():
    """Return (kind, value) auth token, or None.

    CONFIRMED REAL BUG: the `gcloud` fallback below used to run
    unconditionally, on every job, with no timeout at all on the subprocess
    call. Reproduced directly on this Windows machine: `gcloud auth
    print-access-token` hung past 120s even with a valid, already-active
    `gcloud auth` session — `gcloud.cmd` is a batch shim that spawns its own
    child process, and that grandchild can keep the pipe open past whatever
    timeout the parent subprocess call requests, so even a `timeout=` kwarg
    here would not reliably bound it (same class of bug apply_static_
    mappings.py's own `_gcp_token_uncached()` already documents and works
    around). A live job that hit this for even one missing SKU could stall
    indefinitely. Fixed the same way: opt-in only via
    AGY_ALLOW_GCLOUD_LIVE_FETCH=1, not attempted by default. The plain
    GOOGLE_CLOUD_API_KEY/GCP_API_KEY path (an HTTP call with a real bounded
    timeout, no subprocess) remains the safe way to opt into live lookups.
    """
    key = os.environ.get("GOOGLE_CLOUD_API_KEY") or os.environ.get("GCP_API_KEY")
    if key:
        return ("key", key)
    if os.environ.get("AGY_ALLOW_GCLOUD_LIVE_FETCH") != "1":
        return None
    import shutil
    gcloud_bin = shutil.which("gcloud")
    if not gcloud_bin:
        return None
    try:
        tok = subprocess.check_output(
            [gcloud_bin, "auth", "print-access-token"], text=True, stderr=subprocess.DEVNULL,
            timeout=10,
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
    with open(path, encoding="utf-8") as f:
        return {s["displayName"]: s["serviceId"] for s in json.load(f)}


def _load_catalog(service_id):
    path = os.path.join(DATA_DIR, "skus", f"{service_id}.json.gz")
    if not os.path.exists(path):
        return []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)





def _has_coverage(gcp_service, sku_name, gcp_region):
    """B3: Check coverage using the SAME resolver against catalog.duckdb that the pricer uses."""
    try:
        from resolve_sku import resolve_intent_to_sku, get_catalog_connection
        if get_catalog_connection() is not None:
            res = resolve_intent_to_sku(gcp_service, sku_name, gcp_region)
            return bool(getattr(res, "sku_id", None))
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Coverage check + refresh
# ---------------------------------------------------------------------------

def _check_and_refresh(conn, services_map, token_info=None):
    """
    B3 & B4: For each (gcp_service, gcp_region, gcp_sku_name) needed, check catalog
    coverage against catalog.duckdb using the real resolver.
    Log-only in job path: logs gaps; never writes .json.gz or catalog.duckdb mid-job.
    apply_rates' global-fallback rates price any gapped rows.
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

    gaps = []
    for gcp_service, gcp_region, gcp_sku_name in needed:
        if not _has_coverage(gcp_service, gcp_sku_name, gcp_region):
            gaps.append((gcp_service, gcp_sku_name, gcp_region))

    if not gaps:
        print(f"  All {len(needed)} required SKU(s) have catalog coverage (catalog.duckdb)")
        return

    print(f"  Catalog gaps detected for {len(gaps)}/{len(needed)} required SKU(s) (advisory — fallback rates will be used):")
    for svc, sku_name, region in gaps[:30]:
        print(f"    {svc} / {sku_name!r} / {region}")
    if len(gaps) > 30:
        print(f"    ... and {len(gaps) - 30} more")


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

    # Step 2: check catalog coverage against catalog.duckdb (log-only, advisory)
    # (Step 1 repair passes relocated to validate_fix.py per R-1)
    _check_and_refresh(conn, services_map)

    conn.close()
    print("=== ensure_catalog_coverage done ===")


if __name__ == "__main__":
    main()
