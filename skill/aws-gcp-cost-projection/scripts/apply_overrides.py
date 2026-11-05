#!/usr/bin/env python3
"""apply_overrides.py - Applies manual line-item overrides from job_overrides.json into projection.duckdb."""
import os
import sys
import json
import duckdb

def main():
    job_dir = os.environ.get("JOB_DIR", ".")
    overrides_file = os.path.join(job_dir, "job_overrides.json")
    if not os.path.exists(overrides_file):
        return

    try:
        with open(overrides_file, "r", encoding="utf-8") as f:
            overrides = json.load(f)
    except Exception as e:
        print(f"Failed to read job_overrides.json: {e}")
        return

    if not overrides:
        return

    db_path = os.path.join(job_dir, "projection-audit", "projection.duckdb")
    if not os.path.exists(db_path):
        return

    con = duckdb.connect(db_path)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    
    for ov in overrides:
        key = ov.get("row_key")
        if not key:
            continue
        suppressed = ov.get("suppressed", False)
        gcp_target = ov.get("gcp_target", "")
        rate = ov.get("override_rate", -1)

        if suppressed:
            if "gcp_projection" in tables:
                con.execute("""
                    UPDATE gcp_projection 
                    SET is_workload = false, 
                        gcp_projected_cost = 0.0, 
                        projection_note = 'Suppressed via admin override' 
                    WHERE aws_li_key = ?
                """, [key])
            if "aws_li_to_gcp_li" in tables:
                con.execute("""
                    UPDATE aws_li_to_gcp_li 
                    SET strategy = 'ignore', 
                        projection_note = 'Suppressed via admin override' 
                    WHERE aws_li_key = ?
                """, [key])
        else:
            if gcp_target and "aws_li_to_gcp_li" in tables:
                con.execute("UPDATE aws_li_to_gcp_li SET gcp_service = ? WHERE aws_li_key = ?", [gcp_target, key])
            if rate is not None and rate >= 0 and "gcp_projection" in tables:
                con.execute("UPDATE gcp_projection SET gcp_projected_cost = ? WHERE aws_li_key = ?", [float(rate), key])

    con.close()
    print(f"Applied {len(overrides)} line-item overrides to {db_path}")

if __name__ == "__main__":
    main()

