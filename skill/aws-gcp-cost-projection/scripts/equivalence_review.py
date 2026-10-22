#!/usr/bin/env python3
"""
equivalence_review.py — Review queue for equivalence claims ranked by money.

Ranks:
1. Provisional claims (decided_by != "human") by spend exposure (USD exposed).
2. Unknown pairs hit (attribute, aws, gcp with no claim), ranked by spend.

Exposure data comes from the durable application database (internal/db, Turso/SQLite).
If the database is unconfigured or empty, explicit diagnostics are emitted.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sqlite3
import sys
from urllib import parse, request

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.environ.get("SKILL_DIR") or os.path.dirname(_HERE)
sys.path.insert(0, _SKILL_DIR)
sys.path.insert(0, os.path.join(_SKILL_DIR, "scripts"))

from scripts.equivalences import (  # noqa: E402
    all_claims,
    is_provisional,
    load_equivalences,
    lookup,
)


def _connect_db(db_path: str | None = None, db_url: str | None = None):
    """
    Connect to SQLite or Turso DB. Returns (conn_or_client, is_turso: bool, diag: str | None).
    """
    url = db_url or os.environ.get("DATABASE_URL") or os.environ.get("TURSO_DATABASE_URL") or ""

    if db_path:
        if db_path == ":memory:" or os.path.exists(db_path) or not os.path.exists(os.path.dirname(db_path) or "."):
            try:
                conn = sqlite3.connect(db_path)
                return conn, False, None
            except Exception as e:
                return None, False, f"Failed connecting to SQLite DB at {db_path}: {e}"
        else:
            try:
                conn = sqlite3.connect(db_path)
                return conn, False, None
            except Exception as e:
                return None, False, f"Failed opening SQLite DB at {db_path}: {e}"

    if url:
        if url.startswith("libsql://") or url.startswith("http://") or url.startswith("https://"):
            return url, True, None
        # SQLite file URL or path
        clean_path = url.replace("sqlite://", "").replace("sqlite:", "").split("?")[0]
        if os.path.exists(clean_path):
            try:
                conn = sqlite3.connect(clean_path)
                return conn, False, None
            except Exception as e:
                return None, False, f"Failed connecting to SQLite DB at {clean_path}: {e}"

    data_dir = os.environ.get("DATA_DIR")
    candidates = []
    if data_dir:
        candidates.append(os.path.join(data_dir, "cur-web.db"))
    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, ".cur-web", "cur-web.db"))
    candidates.append(os.path.join(os.getcwd(), "cur-web.db"))

    for c in candidates:
        if os.path.exists(c):
            try:
                conn = sqlite3.connect(c)
                return conn, False, None
            except Exception as e:
                return None, False, f"Failed connecting to SQLite DB at {c}: {e}"

    return None, False, "Durable database is unconfigured or not found."


def _query_turso(turso_url: str, sql: str) -> list[dict]:
    """Execute SQL against Turso pipeline API."""
    token = os.environ.get("TURSO_AUTH_TOKEN") or ""
    http_url = turso_url
    if http_url.startswith("libsql://"):
        http_url = "https://" + http_url[len("libsql://"):]
    pipeline_url = http_url.rstrip("/") + "/v2/pipeline"

    payload = {
        "requests": [
            {"type": "execute", "stmt": {"sql": sql}}
        ]
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = request.Request(pipeline_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results", [])
        if not results:
            return []
        resp_obj = results[0].get("response", {}).get("result", {})
        cols = [c.get("name") for c in resp_obj.get("cols", [])]
        rows = resp_obj.get("rows", [])
        out = []
        for r in rows:
            row_dict = {}
            for i, val in enumerate(r):
                row_dict[cols[i]] = val.get("value")
            out.append(row_dict)
        return out
    except Exception as e:
        print(f"Warning: Turso query failed: {e}", file=sys.stderr)
        return []


def fetch_usage_records(conn_or_url, is_turso: bool) -> tuple[list[dict], bool]:
    """
    Fetch all equivalence_usage rows.
    Returns (rows, table_exists).
    """
    sql = "SELECT job_id, attribute, aws_value, gcp_value, scope, rows_used, usd_exposed, recorded_at FROM equivalence_usage"
    if is_turso:
        rows = _query_turso(conn_or_url, sql)
        return rows, True

    conn: sqlite3.Connection = conn_or_url
    cur = conn.cursor()
    # Check if table exists
    cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='equivalence_usage'")
    if cur.fetchone()[0] == 0:
        return [], False

    cur.execute(sql)
    rows = []
    for r in cur.fetchall():
        rows.append({
            "job_id": r[0],
            "attribute": r[1],
            "aws_value": r[2],
            "gcp_value": r[3],
            "scope": r[4],
            "rows_used": r[5],
            "usd_exposed": r[6],
            "recorded_at": r[7],
        })
    return rows, True


def run_review(db_path: str | None = None, db_url: str | None = None) -> int:
    registry_claims = load_equivalences()
    num_reviewed_registry = sum(1 for c in registry_claims if not is_provisional(c))
    num_provisional_registry = sum(1 for c in registry_claims if is_provisional(c))

    conn_or_url, is_turso, diag = _connect_db(db_path=db_path, db_url=db_url)
    if diag or conn_or_url is None:
        print(f"DIAGNOSTIC: {diag}")
        print("Cannot determine equivalence usage exposure without durable DB connection.\n")
        print(
            f"REVIEWED CLAIMS: {num_reviewed_registry:<8} "
            f"PROVISIONAL: {num_provisional_registry:<8} "
            f"UNKNOWN PAIRS HIT: 0"
        )
        return 0

    rows, table_exists = fetch_usage_records(conn_or_url, is_turso)
    if not is_turso and isinstance(conn_or_url, sqlite3.Connection):
        conn_or_url.close()

    if not table_exists or len(rows) == 0:
        print("Database is empty -- no equivalence usage recorded yet.\n")
        print(
            f"REVIEWED CLAIMS: {num_reviewed_registry:<8} "
            f"PROVISIONAL: {num_provisional_registry:<8} "
            f"UNKNOWN PAIRS HIT: 0"
        )
        return 0

    # Aggregate by (attribute, aws, gcp, scope)
    grouped = collections.defaultdict(lambda: {"bills": set(), "rows": 0, "usd": 0.0})
    for r in rows:
        key = (r["attribute"], r["aws_value"], r["gcp_value"], r["scope"])
        grouped[key]["bills"].add(r["job_id"])
        grouped[key]["rows"] += int(r["rows_used"] or 0)
        grouped[key]["usd"] += float(r["usd_exposed"] or 0.0)

    provisional_hits = []
    unknown_hits = []

    for (attr, aws_v, gcp_v, sc), stats in grouped.items():
        claim = lookup(attr, aws_v, gcp_v, sc)
        bills_count = len(stats["bills"])
        usd_val = stats["usd"]

        if claim is None:
            unknown_hits.append({
                "attribute": attr,
                "pair": f"{aws_v} -> {gcp_v}",
                "scope": sc,
                "bills": bills_count,
                "usd": usd_val,
            })
        elif is_provisional(claim):
            provisional_hits.append({
                "attribute": attr,
                "pair": f"{aws_v} -> {gcp_v}",
                "degree": claim.degree,
                "scope": sc,
                "bills": bills_count,
                "usd": usd_val,
                "decided_by": claim.decided_by,
            })

    # Sort descending by USD exposed
    provisional_hits.sort(key=lambda x: -x["usd"])
    unknown_hits.sort(key=lambda x: -x["usd"])

    print("PROVISIONAL CLAIMS, BY SPEND EXPOSURE\n")
    if provisional_hits:
        print(f"  {'attribute':<15} {'aws -> gcp':<27} {'degree':<10} {'bills':>6} {'usd':>12}    {'decided_by'}")
        for p in provisional_hits:
            usd_str = f"{p['usd']:,.0f}"
            print(
                f"  {p['attribute']:<15} {p['pair']:<27} {p['degree']:<10} "
                f"{p['bills']:>6} {usd_str:>12}   {p['decided_by']}"
            )
    else:
        print("  (no provisional claims recorded in usage data)")

    print()
    if unknown_hits:
        print("UNKNOWN PAIRS HIT (AUTHORING BACKLOG), BY SPEND EXPOSURE\n")
        print(f"  {'attribute':<15} {'aws -> gcp':<27} {'scope':<15} {'bills':>6} {'usd':>12}")
        for u in unknown_hits:
            usd_str = f"{u['usd']:,.0f}"
            print(f"  {u['attribute']:<15} {u['pair']:<27} {u['scope']:<15} {u['bills']:>6} {usd_str:>12}")
        print()

    print(
        f"REVIEWED CLAIMS: {num_reviewed_registry:<8} "
        f"PROVISIONAL: {num_provisional_registry:<8} "
        f"UNKNOWN PAIRS HIT: {len(unknown_hits)}"
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description="Rank provisional and unknown equivalence claims by money.")
    parser.add_argument("--db-path", default=None, help="Path to local SQLite database.")
    parser.add_argument("--database-url", default=None, help="Database URL for Turso or SQLite.")
    args = parser.parse_args()

    return run_review(db_path=args.db_path, db_url=args.database_url)


if __name__ == "__main__":
    raise SystemExit(main())
