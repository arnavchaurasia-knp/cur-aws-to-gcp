#!/usr/bin/env python3
"""
catalog_manager.py — single, capable GCP SKU catalog tool.

Consolidates what used to be split across catalog_health_check.py,
check_catalog_age.py (the refresh half), and weekly_catalog_refresh.py into one
script with three modes. It validates BOTH ends of the catalog chain:

    GCP Billing API -> data/skus/*.json.gz -> build_catalog_index.py -> data/catalog.duckdb
                        (raw source dump)      (compile step)            (what the pricer reads)

The pricer (resolve_sku.py + apply_rates.py) reads ONLY catalog.duckdb. The
.json.gz files are the raw source the duckdb is compiled from. So:
  * SOURCE health  = integrity of data/skus/*.json.gz (deep, not just "non-empty").
  * ARTIFACT health = coverage of catalog.duckdb (what the pricer actually sees).
  * A refresh updates the SOURCE, then REBUILDS the artifact — gated on integrity,
    so a broken source can never be compiled into the catalog the pricer uses.

MODES
  check     (default) Read-only. Deep source integrity + artifact sanity + age.
                      Writes nothing. Exit 0 healthy / 1 problems / 2 config error.
  refresh             Single-writer (locked). Fetch from the GCP API
                      (complete-only, atomic), gate on integrity, then rebuild
                      catalog.duckdb and update CATALOG_META.json. This is the
                      out-of-band refresher — do NOT run it inside a projection job.
  coverage  --db P    In-job, ADVISORY/log-only. For a job's projection.duckdb,
                      check every needed (service, sku, region) against
                      catalog.duckdb via resolve_sku. Logs gaps; writes nothing;
                      always exits 0 (apply_rates' global-fallback prices gaps).

SCOPE: this tool owns catalog source/artifact health only. It does NOT contain
the three Phase-4 "repair" passes (NULL-sku, illegal-passthrough, Enterprise-Plus
downgrade) that live in ensure_catalog_coverage.py — those patch upstream mapping
bugs and belong with that work, not here.

Usage:
    python scripts/catalog_manager.py check        [--skill-dir PATH]
    python scripts/catalog_manager.py refresh [--all] [--skill-dir PATH]
    python scripts/catalog_manager.py coverage --db <jobdir|projection.duckdb>

Auth (refresh only): GOOGLE_CLOUD_API_KEY / GCP_API_KEY (bounded HTTP, safe).
The `gcloud auth print-access-token` fallback is opt-in only via
AGY_ALLOW_GCLOUD_LIVE_FETCH=1 — it can hang indefinitely on Windows even with an
active session (confirmed real), so it is never attempted automatically.
"""

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

STALE_DAYS = 7                 # catalog considered stale past this age
COUNT_FLOOR_FRACTION = 0.5     # a service dropping below this x baseline = collapsed dump

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _paths(skill_dir=None):
    skill = skill_dir or os.environ.get("SKILL_DIR") or os.path.dirname(SCRIPT_DIR)
    data = os.path.join(skill, "data")
    return {
        "skill": skill,
        "data": data,
        "skus": os.path.join(data, "skus"),
        "meta": os.path.join(data, "CATALOG_META.json"),
        "services": os.path.join(data, "services.json"),
        "catalog_db": os.path.join(data, "catalog.duckdb"),
    }


# ---------------------------------------------------------------------------
# GCP auth + fetch (refresh only)
# ---------------------------------------------------------------------------

def _gcp_token():
    """Return (kind, value) auth token, or None. gcloud fallback is opt-in only
    (AGY_ALLOW_GCLOUD_LIVE_FETCH=1) because gcloud.cmd can hang unbounded on
    Windows — the plain API-key HTTP path has a real bounded timeout."""
    key = os.environ.get("GOOGLE_CLOUD_API_KEY") or os.environ.get("GCP_API_KEY")
    if key:
        return ("key", key)
    if os.environ.get("AGY_ALLOW_GCLOUD_LIVE_FETCH") != "1":
        return None
    for candidate in ["gcloud", r"C:\Users\Public\google-cloud-sdk\bin\gcloud.cmd"]:
        resolved = shutil.which(candidate) or (candidate if os.path.exists(candidate) else None)
        if not resolved:
            continue
        try:
            tok = subprocess.check_output(
                [resolved, "auth", "print-access-token"],
                text=True, stderr=subprocess.DEVNULL, timeout=10,
            ).strip()
            if tok:
                return ("bearer", tok)
        except Exception:
            continue
    return None


def _gcp_get(url, token_info):
    kind, value = token_info
    if kind == "key":
        sep = "&" if "?" in url else "?"
        req = urllib.request.Request(f"{url}{sep}key={value}")
    else:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {value}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _fetch_service_skus(service_id, token_info):
    """Fetch all SKUs for a service. Returns (skus, complete).
    complete is False if ANY page errored — the caller must then DISCARD the
    partial result and keep the existing file (never promote a partial pull)."""
    skus, page_token = [], ""
    while True:
        url = f"https://cloudbilling.googleapis.com/v1/services/{service_id}/skus?pageSize=5000"
        if page_token:
            url += f"&pageToken={urllib.parse.quote(page_token)}"
        try:
            data = _gcp_get(url, token_info)
        except Exception as e:
            print(f"    [fetch] page error for {service_id}: {e} — discarding partial result")
            return skus, False
        skus.extend(data.get("skus", []))
        page_token = data.get("nextPageToken", "")
        if not page_token:
            return skus, True


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _atomic_write_gz(final_path, obj):
    """Write a gzipped JSON file atomically (temp + os.replace), so a crash or a
    concurrent reader never sees a half-written .gz."""
    d = os.path.dirname(final_path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    os.close(fd)
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, final_path)             # atomic promote
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Source integrity (the .json.gz dump)
# ---------------------------------------------------------------------------

def _max_effective_time(skus):
    """Newest pricingInfo.effectiveTime across SKUs, as an aware datetime or None."""
    best = None
    for s in skus:
        for pi in (s.get("pricingInfo") or []):
            et = pi.get("effectiveTime")
            if not et:
                continue
            try:
                ts = datetime.fromisoformat(et.replace("Z", "+00:00"))
            except Exception:
                continue
            if best is None or ts > best:
                best = ts
    return best


def integrity_check_file(path, baseline_count=None):
    """Deep integrity of one .json.gz. Returns a dict:
      status: OK | MISSING | CORRUPT | EMPTY | DEGRADED
      count, priced (SKUs with a usable unitPrice), issues[], age_days
    DEGRADED = loads and non-empty but either the count collapsed vs baseline or
    almost no SKUs carry pricing (a structurally broken dump)."""
    if not os.path.exists(path):
        return {"status": "MISSING", "count": 0, "priced": 0, "issues": ["file absent"], "age_days": None}
    try:
        with gzip.open(path, "rt") as f:
            skus = json.load(f)
    except Exception as e:
        return {"status": "CORRUPT", "count": 0, "priced": 0, "issues": [f"gzip/json: {e}"], "age_days": None}
    if not isinstance(skus, list) or len(skus) == 0:
        return {"status": "EMPTY", "count": 0, "priced": 0, "issues": ["no SKUs"], "age_days": None}

    issues, priced = [], 0
    required = ("skuId", "description", "serviceRegions", "pricingInfo")
    missing_fields = 0
    for s in skus:
        if not all(k in s and s[k] not in (None, "", []) for k in required):
            missing_fields += 1
            continue
        expr = (s.get("pricingInfo") or [{}])[0].get("pricingExpression", {})
        if expr.get("tieredRates"):
            priced += 1
    count = len(skus)

    status = "OK"
    if missing_fields > count * 0.5:
        status = "DEGRADED"
        issues.append(f"{missing_fields}/{count} SKUs missing required fields")
    if priced == 0:
        status = "DEGRADED"
        issues.append("no SKU carries a usable unitPrice")
    if baseline_count and count < baseline_count * COUNT_FLOOR_FRACTION:
        status = "DEGRADED"
        issues.append(f"count {count} collapsed vs baseline {baseline_count}")

    age_days = None
    et = _max_effective_time(skus)
    if et:
        age_days = (datetime.now(timezone.utc) - et).days
    return {"status": status, "count": count, "priced": priced, "issues": issues, "age_days": age_days}


def _load_meta(p):
    if os.path.exists(p["meta"]):
        try:
            return json.load(open(p["meta"], encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _scan_source(p, services, meta):
    """Integrity-check every service's .json.gz. Returns (results, healthy_bool)."""
    baselines = (meta.get("sku_counts") or {})
    results, healthy = {}, True
    for svc in services:
        name, sid = svc["displayName"], svc["serviceId"]
        res = integrity_check_file(os.path.join(p["skus"], f"{sid}.json.gz"), baselines.get(sid))
        results[sid] = {"name": name, **res}
        if res["status"] != "OK":
            healthy = False
    return results, healthy


# ---------------------------------------------------------------------------
# Lock (single-writer for refresh)
# ---------------------------------------------------------------------------

class _FileLock:
    def __init__(self, path):
        self.path = path
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "w", encoding="utf-8")
        try:
            import platform
            if platform.system() == "Windows":
                import msvcrt
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_EX)
        except Exception:
            pass  # lock best-effort; worst case two refreshers serialize via atomic writes
        return self

    def __exit__(self, *a):
        try:
            self.fh.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# MODE: check (read-only)
# ---------------------------------------------------------------------------

def cmd_check(p):
    if not os.path.exists(p["services"]):
        print(f"ERROR: services.json not found at {p['services']}")
        return 2
    services = json.load(open(p["services"], encoding="utf-8"))
    meta = _load_meta(p)

    print("=" * 66)
    print("GCP SKU Catalog — health check (read-only)")
    print("=" * 66)
    print(f"Services in allow-list : {len(services)}")

    # Catalog age (global, from META)
    is_stale = True
    fetched_at = meta.get("fetched_at", "")
    if fetched_at:
        try:
            ts = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).days
            is_stale = age >= STALE_DAYS
            print(f"Catalog age            : {age}d [{'STALE' if is_stale else 'fresh'} — threshold {STALE_DAYS}d]")
        except Exception as e:
            print(f"Catalog age            : unreadable META ({e})")
    else:
        print("Catalog age            : CATALOG_META.json missing / no fetched_at")

    # Source integrity
    results, healthy = _scan_source(p, services, meta)
    bad = {sid: r for sid, r in results.items() if r["status"] != "OK"}
    print(f"\n{'Service':<45} {'status':<9} {'SKUs':>6} {'priced':>7}")
    print("-" * 70)
    for sid, r in results.items():
        flag = "" if r["status"] == "OK" else "  <-- " + "; ".join(r["issues"])
        print(f"  {r['name'][:43]:<43} {r['status']:<9} {r['count']:>6} {r['priced']:>7}{flag}")

    # Artifact sanity (what the pricer reads)
    artifact_ok = _artifact_sanity(p)

    print("-" * 70)
    print(f"Source OK      : {len(results) - len(bad)}/{len(results)}")
    print(f"Source problems: {len(bad)}  (MISSING/CORRUPT/EMPTY/DEGRADED)")
    print(f"Artifact (catalog.duckdb): {'OK' if artifact_ok else 'PROBLEM'}")
    print(f"Stale          : {'YES' if is_stale else 'no'}")

    if bad or is_stale:
        print("\nHINT: run `catalog_manager.py refresh` (needs GCP creds) to fetch + rebuild.")
    code = 0 if (not bad and artifact_ok and not is_stale) else 1
    print(f"\nExit code: {code}")
    return code


def _artifact_sanity(p):
    """catalog.duckdb exists, opens read-only, and its skus/tiered_rates tables are non-empty."""
    if not os.path.exists(p["catalog_db"]):
        print("  [artifact] catalog.duckdb MISSING")
        return False
    try:
        import duckdb
        con = duckdb.connect(p["catalog_db"], read_only=True)
        tabs = {t[0] for t in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
        if not {"skus", "tiered_rates"} <= tabs:
            print(f"  [artifact] catalog.duckdb missing tables (have {sorted(tabs)})")
            con.close()
            return False
        n_sku = con.execute("SELECT count(*) FROM skus").fetchone()[0]
        n_rate = con.execute("SELECT count(*) FROM tiered_rates").fetchone()[0]
        con.close()
        if n_sku == 0 or n_rate == 0:
            print(f"  [artifact] catalog.duckdb empty (skus={n_sku}, rates={n_rate})")
            return False
        return True
    except Exception as e:
        print(f"  [artifact] catalog.duckdb unreadable: {e}")
        return False


# ---------------------------------------------------------------------------
# MODE: refresh (single-writer; fetch -> integrity gate -> rebuild)
# ---------------------------------------------------------------------------

def cmd_refresh(p, refresh_all=False):
    if not os.path.exists(p["services"]):
        print(f"ERROR: services.json not found at {p['services']}")
        return 2
    services = json.load(open(p["services"], encoding="utf-8"))
    meta = _load_meta(p)

    token = _gcp_token()
    print("=" * 66)
    print(f"GCP SKU Catalog — refresh ({'all' if refresh_all else 'gaps only'})")
    print(f"Auth: {token[0] if token else 'none'}")
    print("=" * 66)
    if not token:
        print("No GCP credentials (set GOOGLE_CLOUD_API_KEY). Nothing fetched.")
        return 1

    with _FileLock(p["meta"] + ".lock"):
        # Decide which services to fetch
        results, _ = _scan_source(p, services, meta)
        if refresh_all:
            targets = services
        else:
            targets = [s for s in services if results[s["serviceId"]]["status"] != "OK"]
        if not targets:
            print("All sources healthy — nothing to fetch. (Use --all to force.)")
        else:
            print(f"Fetching {len(targets)} service(s)...")

        fetched_any = False
        for svc in targets:
            name, sid = svc["displayName"], svc["serviceId"]
            print(f"  {name} ...", end="", flush=True)
            skus, complete = _fetch_service_skus(sid, token)
            if not complete:
                print(" INCOMPLETE — keeping existing file")
                continue
            # Integrity-gate the freshly fetched set BEFORE promoting it over the old one.
            baseline = (meta.get("sku_counts") or {}).get(sid)
            if not skus:
                print(" empty response — keeping existing file")
                continue
            if baseline and len(skus) < baseline * COUNT_FLOOR_FRACTION:
                print(f" suspicious ({len(skus)} vs baseline {baseline}) — keeping existing file")
                continue
            _atomic_write_gz(os.path.join(p["skus"], f"{sid}.json.gz"), skus)
            print(f" {len(skus)} SKUs saved")
            fetched_any = True

        # Full-source integrity gate BEFORE rebuilding the artifact the pricer reads.
        results2, healthy = _scan_source(p, services, _load_meta(p))
        bad = {sid: r for sid, r in results2.items() if r["status"] != "OK"}
        if bad:
            print(f"\nREFUSING to rebuild catalog.duckdb — {len(bad)} source(s) still unhealthy:")
            for sid, r in bad.items():
                print(f"  {r['name']}: {r['status']} ({'; '.join(r['issues'])})")
            print("Existing catalog.duckdb left untouched.")
            return 1

        # Rebuild the artifact from the validated source (build_catalog_index does its
        # own temp+atomic-replace of catalog.duckdb).
        if fetched_any or not os.path.exists(p["catalog_db"]):
            print("\nRebuilding catalog.duckdb from validated source ...")
            env = dict(os.environ, SKILL_DIR=p["skill"])
            r = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, "build_catalog_index.py")],
                               cwd=p["skill"], env=env)
            if r.returncode != 0:
                print(f"  build_catalog_index.py exited {r.returncode} — catalog.duckdb may be stale")
                return 1
        else:
            print("\nNo new source fetched — catalog.duckdb left as-is.")

        # Update META (global fetched_at + per-service baseline counts)
        counts = {sid: r["count"] for sid, r in results2.items() if r["count"]}
        _write_meta(p, counts)
        print("CATALOG_META.json updated.")
    return 0


def _write_meta(p, sku_counts):
    meta = {
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sku_count": sum(sku_counts.values()),
        "sku_counts": sku_counts,
    }
    fd, tmp = tempfile.mkstemp(dir=p["data"], suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        os.replace(tmp, p["meta"])
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# MODE: coverage (in-job, advisory/log-only)
# ---------------------------------------------------------------------------

def cmd_coverage(p, db_arg):
    """Check every needed (service, sku, region) for a job against catalog.duckdb
    using the SAME resolver the pricer uses. Logs gaps; writes nothing; exit 0."""
    db = db_arg
    if os.path.isdir(db):
        db = os.path.join(db, "projection-audit", "projection.duckdb")
    if not os.path.exists(db):
        print(f"[coverage] job DB not found: {db} — skipping")
        return 0
    try:
        import duckdb
    except ImportError:
        print("[coverage] duckdb unavailable — skipping")
        return 0

    sys.path.insert(0, SCRIPT_DIR)
    try:
        from resolve_sku import resolve_intent_to_sku, get_catalog_connection
    except Exception as e:
        print(f"[coverage] cannot import resolve_sku ({e}) — skipping")
        return 0
    if get_catalog_connection() is None:
        print("[coverage] catalog.duckdb unavailable — skipping (apply_rates fallback will price)")
        return 0

    con = duckdb.connect(db, read_only=True)
    needed = con.execute("""
        SELECT DISTINCT m.gcp_service, cat.gcp_region, m.gcp_sku_name
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog cat USING (aws_li_key)
        WHERE m.gcp_sku_name IS NOT NULL
          AND m.strategy IN ('map', 'break_down')
    """).fetchall()
    con.close()

    gaps = []
    for svc, region, sku_name in needed:
        res = resolve_intent_to_sku(svc, sku_name, region)
        if not getattr(res, "sku_id", None):
            gaps.append((svc, sku_name, region))

    print(f"[coverage] needed={len(needed)} gaps={len(gaps)} (advisory — apply_rates fallback prices gaps)")
    for svc, sku_name, region in gaps[:50]:
        print(f"  GAP: {svc} / {sku_name!r} / {region}")
    if len(gaps) > 50:
        print(f"  ... and {len(gaps) - 50} more")
    return 0  # always advisory — never fail the job


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="GCP SKU catalog manager")
    ap.add_argument("mode", nargs="?", default="check", choices=["check", "refresh", "coverage"])
    ap.add_argument("--all", action="store_true", help="refresh: fetch every service, not just gaps")
    ap.add_argument("--db", default=None, help="coverage: job dir or projection.duckdb path")
    ap.add_argument("--skill-dir", default=None)
    args = ap.parse_args()

    p = _paths(args.skill_dir)

    if args.mode == "check":
        sys.exit(cmd_check(p))
    elif args.mode == "refresh":
        sys.exit(cmd_refresh(p, refresh_all=args.all))
    elif args.mode == "coverage":
        if not args.db:
            print("coverage mode requires --db <jobdir|projection.duckdb>")
            sys.exit(2)
        sys.exit(cmd_coverage(p, args.db))


if __name__ == "__main__":
    main()
