#!/usr/bin/env python3
"""
check_catalog_age.py — Auto-refresh GCP SKU catalog if it is more than 1 year old.

Reads data/CATALOG_META.json to find the last fetch date. If the catalog is
stale (>365 days), runs scripts/refresh-catalog.sh and prefetch_skus.py for
the current job's GCP region.

Safe to run on every job — the check is a single JSON file read; the refresh
only fires once a year. If the catalog file is missing the refresh always runs.

Usage (called by orchestrate.go as a pre_llm_script before ingest):
    python3 scripts/check_catalog_age.py [<db_path>]
"""

import json, os, subprocess, sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config_loader import load_data_config

SKILL_DIR = os.environ.get("SKILL_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")
META_PATH   = os.path.join(SKILL_DIR, "data", "CATALOG_META.json")

_REVIEW_CONFIG = load_data_config("review-config")
REFRESH_DAYS = _REVIEW_CONFIG.get("catalog_refresh_after_days", 7)
DEFAULT_MAX_BUILD_AGE_DAYS = _REVIEW_CONFIG.get("catalog_max_build_age_days", 14)


def _days_since(iso: str) -> int:
    ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int((datetime.now(timezone.utc) - ts).total_seconds() / 86400)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Check or assert freshness of GCP SKU catalog")
    parser.add_argument("--assert-fresh", action="store_true", help="Assert catalog freshness and exit non-zero if stale or missing")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_BUILD_AGE_DAYS, help=f"Max allowed age in days when --assert-fresh is used (default {DEFAULT_MAX_BUILD_AGE_DAYS})")
    parser.add_argument("db_path", nargs="?", default=None, help="Optional DB path")
    args = parser.parse_args()

    if args.assert_fresh:
        if not os.path.exists(META_PATH):
            print(f"ERROR: CATALOG_META.json not found at {META_PATH}.\nUnknown age is not fresh.\nRun: bash scripts/refresh-catalog.sh and commit the updated data.", file=sys.stderr)
            sys.exit(1)
        try:
            with open(META_PATH, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception as e:
            print(f"ERROR: CATALOG_META.json at {META_PATH} is unparseable: {e}\nUnknown age is not fresh.\nRun: bash scripts/refresh-catalog.sh and commit the updated data.", file=sys.stderr)
            sys.exit(1)

        fetched_at = meta.get("fetched_at", "")
        if not fetched_at:
            print(f"ERROR: CATALOG_META.json has no 'fetched_at' field.\nUnknown age is not fresh.\nRun: bash scripts/refresh-catalog.sh and commit the updated data.", file=sys.stderr)
            sys.exit(1)

        try:
            age_days = _days_since(fetched_at)
        except Exception as e:
            print(f"ERROR: Invalid 'fetched_at' date '{fetched_at}': {e}\nRun: bash scripts/refresh-catalog.sh and commit the updated data.", file=sys.stderr)
            sys.exit(1)

        if age_days > args.max_age_days:
            print(
                f"ERROR: GCP SKU catalog is STALE (fetched_at={fetched_at}, {age_days} days old, max allowed is {args.max_age_days} days).\n"
                f"To fix: run 'bash scripts/refresh-catalog.sh' and commit the updated data/skus/ and data/CATALOG_META.json.",
                file=sys.stderr
            )
            sys.exit(1)

        print(f"GCP SKU catalog is fresh (fetched_at={fetched_at}, {age_days} days old, max={args.max_age_days})")
        sys.exit(0)

    # Determine GCP region from env (set by orchestrate.go) or default
    gcp_region = os.environ.get("GCP_REGION", "us-central1")

    # Check catalog age
    needs_refresh = True
    if os.path.exists(META_PATH):
        try:
            meta = json.load(open(META_PATH))
            fetched_at = meta.get("fetched_at", "")
            if fetched_at:
                age_days = _days_since(fetched_at)
                if age_days < REFRESH_DAYS:
                    print(f"  catalog: fresh ({age_days} days old, threshold={REFRESH_DAYS})")
                    needs_refresh = False
                else:
                    print(f"  catalog: STALE ({age_days} days old) — refreshing now")
        except Exception as e:
            print(f"  catalog: could not read meta ({e}) — refreshing")
    else:
        print(f"  catalog: CATALOG_META.json missing — running initial fetch")

    if not needs_refresh:
        return

    # Acquire an exclusive lock so only one concurrent job runs the refresh.
    # The second job will block here, then re-read META and see a fresh catalog.
    lock_path = META_PATH + ".lock"
    lock_fh = open(lock_path, "w", encoding="utf-8")
    try:
        import platform
        if platform.system() == "Windows":
            import msvcrt
            msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
        # Re-check after acquiring the lock — another job may have just refreshed.
        if os.path.exists(META_PATH):
            try:
                meta2 = json.load(open(META_PATH))
                fetched_at2 = meta2.get("fetched_at", "")
                if fetched_at2 and _days_since(fetched_at2) < REFRESH_DAYS:
                    print(f"  catalog: already refreshed by another job — skipping")
                    return
            except Exception:
                pass
    except Exception:
        pass  # lock failed — proceed anyway, worst case two jobs both refresh

    # Run refresh-catalog.sh (bash) or the Python catalog builder directly (Windows)
    import platform
    refresh_sh = os.path.join(SCRIPTS_DIR, "refresh-catalog.sh")
    if platform.system() == "Windows":
        # bash is not available on Windows — run the catalog index builder directly.
        # This rebuilds catalog.duckdb from the bundled data/skus/*.json.gz files
        # without fetching new data from the GCP API.
        build_script = os.path.join(SCRIPTS_DIR, "build_catalog_index.py")
        if os.path.exists(build_script):
            print(f"  catalog: rebuilding catalog.duckdb (Windows, no bash) ...")
            result = subprocess.run([sys.executable, build_script], cwd=SKILL_DIR, capture_output=False)
            if result.returncode != 0:
                print(f"  catalog: WARNING build_catalog_index.py exited {result.returncode}", file=sys.stderr)
                return
        else:
            print(f"  catalog: WARNING neither refresh-catalog.sh nor build_catalog_index.py found — skipping", file=sys.stderr)
            return
    elif not os.path.exists(refresh_sh):
        print(f"  catalog: WARNING refresh-catalog.sh not found at {refresh_sh} — skipping", file=sys.stderr)
        return
    else:
        print(f"  catalog: running refresh-catalog.sh ...")
        result = subprocess.run(["bash", refresh_sh], cwd=SKILL_DIR, capture_output=False)
        if result.returncode != 0:
            print(f"  catalog: WARNING refresh-catalog.sh exited {result.returncode} — continuing with existing catalog", file=sys.stderr)
            return

    # Re-warm the resolved_skus.json cache for this region
    prefetch = os.path.join(SCRIPTS_DIR, "prefetch_skus.py")
    if os.path.exists(prefetch):
        print(f"  catalog: warming SKU cache for {gcp_region} ...")
        subprocess.run(
            [sys.executable, prefetch, "--region", gcp_region],
            cwd=SKILL_DIR, capture_output=False
        )

    print(f"  catalog: refresh complete")
    lock_fh.close()


if __name__ == "__main__":
    main()
