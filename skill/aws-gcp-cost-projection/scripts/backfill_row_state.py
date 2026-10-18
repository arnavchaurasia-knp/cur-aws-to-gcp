#!/usr/bin/env python3
"""scripts/backfill_row_state.py <projection.duckdb>

Migration shim: parses legacy projection_note text into the new state columns
(rate_source, rate_region_basis, license_status, review_flags) for job DBs created
before the row-state registry change.

Delete once no job DB predating this change can be resumed.
THIS IS THE ONLY PLACE IN THE CODEBASE PERMITTED TO READ A NOTE AS LOGIC.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb
import row_state as rs


def backfill_row_state(con: duckdb.DuckDBPyConnection) -> int:
    """Backfill state fields from legacy projection_note if columns are unpopulated."""
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "aws_li_to_gcp_li" not in tables:
        return 0

    rs.ensure_schema(con)

    # Check if already populated (no-op if any non-NULL in rate_region_basis or license_status)
    non_null_count = con.execute("""
        SELECT COUNT(*) FROM aws_li_to_gcp_li
        WHERE rate_region_basis IS NOT NULL
           OR license_status IS NOT NULL
    """).fetchone()[0]

    if non_null_count > 0:
        return 0

    # 1. rate_source mappings
    con.execute("""
        UPDATE aws_li_to_gcp_li
        SET rate_source = 'no_rate'
        WHERE (rate_source IS NULL OR rate_source = 'unknown')
          AND projection_note LIKE '%no-rate-fallback%'
    """)

    con.execute("""
        UPDATE aws_li_to_gcp_li
        SET rate_source = 'no_catalog'
        WHERE (rate_source IS NULL OR rate_source = 'unknown')
          AND projection_note LIKE '%no-catalog%'
    """)

    # 2. rate_region_basis mappings
    con.execute("""
        UPDATE aws_li_to_gcp_li
        SET rate_region_basis = 'cheapest_available'
        WHERE rate_region_basis IS NULL
          AND projection_note LIKE '%cheapest available region%'
    """)

    con.execute("""
        UPDATE aws_li_to_gcp_li
        SET rate_region_basis = 'global'
        WHERE rate_region_basis IS NULL
          AND projection_note LIKE '%global charge; priced at%'
    """)

    con.execute("""
        UPDATE aws_li_to_gcp_li
        SET rate_region_basis = 'dominant'
        WHERE rate_region_basis IS NULL
          AND projection_note LIKE '%priced at % rate — no %'
    """)

    # 3. license_status mappings
    con.execute("""
        UPDATE aws_li_to_gcp_li
        SET license_status = 'premium_not_modeled'
        WHERE license_status IS NULL
          AND projection_note LIKE '%license-premium%'
    """)

    # 4. review_flags mappings
    flag_patterns = [
        ("service_model_change", "%service model change%"),
        ("log_ingestion_model", "%log ingestion%"),
        ("architecture_review", "%architecture review recommended%"),
        ("verify_with_customer", "%verify with customer%"),
        ("region_unavailable", "%unavailable in%"),
        ("cost_may_differ", "%real cost may differ%"),
    ]

    for flag, pat in flag_patterns:
        add_expr = rs.flag_add_expr("review_flags", flag)
        con.execute(f"""
            UPDATE aws_li_to_gcp_li
            SET review_flags = {add_expr}
            WHERE projection_note LIKE ?
        """, [pat])

    con.commit()
    return 1


def main():
    if len(sys.argv) < 2:
        sys.exit(0)
    con = duckdb.connect(sys.argv[1])
    try:
        backfill_row_state(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
