#!/usr/bin/env python3
"""Weekly catalog refresh — triggered by Windows Task Scheduler.
Thin logging wrapper around `catalog_manager.py refresh`, which fetches from the
GCP API (complete-only, atomic), gates on source integrity, and rebuilds
catalog.duckdb — all in one step. (Previously this ran catalog_health_check.py
--refresh and then build_catalog_index.py separately; catalog_manager folds both
in and refuses to rebuild from a broken source.)"""
import datetime
import os
import subprocess
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS   = os.path.join(SKILL_DIR, "scripts")
LOG_PATH  = os.path.join(SKILL_DIR, "data", "catalog_refresh.log")


def run(script, extra_args=()):
    path = os.path.join(SCRIPTS, script)
    if not os.path.exists(path):
        return f"SKIP {script} (not found)\n"
    result = subprocess.run(
        [sys.executable, path] + list(extra_args),
        cwd=SKILL_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    out = result.stdout + (result.stderr or "") + f"exit={result.returncode}\n"
    return out


def main():
    stamp = datetime.datetime.now().isoformat()
    lines = [f"\n--- {stamp} ---\n"]

    print(f"[{stamp}] Running catalog refresh (fetch + integrity-gate + rebuild)...")
    lines.append(run("catalog_manager.py", ["refresh"]))

    lines.append("--- done ---\n")

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"Complete. Log: {LOG_PATH}")


if __name__ == "__main__":
    main()
