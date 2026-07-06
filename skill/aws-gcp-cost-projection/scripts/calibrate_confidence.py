#!/usr/bin/env python3
"""
calibrate_confidence.py — Service-specific confidence ceilings, post-merge.

Applies deterministic caps to aws_li_to_gcp_li after the LLM mapping
completes. High-ambiguity services should never carry the same confidence
as a clean EC2→Compute Engine mapping — CUR alone cannot reveal their
full deployment topology.

Caps applied:
  OpenSearch compute → 0.70  (architecture ambiguity: GCE vs managed service)
  MSK / Kafka         → 0.70  (VM-only model ignores replication topology)
  RDS / Aurora        → 0.72  (HA intent, latency, connection limits not in CUR)
  ElastiCache         → 0.72  (cluster topology not in CUR)
  Windows instances   → 0.75  (license premium not modeled in GCP pricing)

Runs as Phase 2 post_llm_script, after merge_mappings.py.
"""

import sys
import duckdb


_CAPS = [
    # (label, WHERE clause fragment on aws_li_catalog c, optional gcp_service filter,
    #  ceiling, architecture note to append — None if no note needed)
    (
        "OpenSearch compute",
        "(c.product ILIKE '%OpenSearch%')",
        "m.gcp_service ILIKE '%Compute%'",
        0.70,
        "architecture review recommended: multiple valid GCP targets exist "
        "(Managed OpenSearch via Marketplace, Elastic Cloud, Vertex AI Search, or "
        "self-managed GCE); self-managed GCE assumed — verify migration strategy with customer",
    ),
    (
        "MSK/Kafka",
        "(c.product ILIKE '%Managed Streaming%' OR c.product ILIKE '%MSK%' "
        "OR c.product ILIKE '%Kafka%')",
        None,
        0.70,
        "architecture review recommended: MSK modeled as VM-only; "
        "replication factor, broker topology, and managed storage scaling "
        "are not captured by CUR and require separate assessment",
    ),
    (
        "RDS/Aurora",
        "(c.product ILIKE '%Relational Database%' OR c.product ILIKE '%RDS%' "
        "OR c.product ILIKE '%Aurora%')",
        None,
        0.72,
        "CUR does not reveal HA intent beyond Multi-AZ flag, storage latency "
        "requirements, connection limits, or read replica topology; "
        "verify Cloud SQL tier and HA config with customer",
    ),
    (
        "ElastiCache",
        "(c.product ILIKE '%ElastiCache%' OR c.product ILIKE '%MemoryDB%')",
        None,
        0.72,
        "ElastiCache cluster topology and multi-AZ failover not fully "
        "captured by CUR; verify Memorystore tier selection",
    ),
]


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    con = duckdb.connect(db_path)

    tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
    if "aws_li_to_gcp_li" not in tables:
        print("aws_li_to_gcp_li not found — skipping confidence calibration")
        con.close()
        sys.exit(0)

    total = 0

    for label, cat_filter, svc_filter, ceiling, note in _CAPS:
        join_filter = f"WHERE {cat_filter}"
        if svc_filter:
            join_filter += f" AND {svc_filter}"

        # Count rows above ceiling before updating
        n = con.execute(f"""
            SELECT COUNT(*) FROM aws_li_to_gcp_li m
            JOIN aws_li_catalog c USING (aws_li_key)
            {join_filter}
              AND COALESCE(m.mapping_confidence, 1.0) > {ceiling}
        """).fetchone()[0]

        if n == 0:
            continue

        if note:
            con.execute(f"""
                UPDATE aws_li_to_gcp_li
                SET mapping_confidence = LEAST(COALESCE(mapping_confidence, 1.0), {ceiling}),
                    projection_note = COALESCE(projection_note, '') || ' [{note}]'
                WHERE aws_li_key IN (
                    SELECT c.aws_li_key FROM aws_li_catalog c
                    JOIN aws_li_to_gcp_li m USING (aws_li_key)
                    {join_filter}
                      AND COALESCE(m.mapping_confidence, 1.0) > {ceiling}
                )
            """)
        else:
            con.execute(f"""
                UPDATE aws_li_to_gcp_li
                SET mapping_confidence = LEAST(COALESCE(mapping_confidence, 1.0), {ceiling})
                WHERE aws_li_key IN (
                    SELECT c.aws_li_key FROM aws_li_catalog c
                    JOIN aws_li_to_gcp_li m USING (aws_li_key)
                    {join_filter}
                      AND COALESCE(m.mapping_confidence, 1.0) > {ceiling}
                )
            """)

        print(f"  calibrate_confidence: {label}: {n} row(s) capped at {ceiling:.0%}")
        total += n

    # Windows: cap + license note (keyed on aws_li_catalog.operating_system or existing note)
    n_win = con.execute("""
        SELECT COUNT(*) FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        WHERE (c.operating_system ILIKE '%Windows%'
               OR c.projection_note ILIKE '%Windows%'
               OR c.projection_note ILIKE '%license-premium%')
          AND COALESCE(m.mapping_confidence, 1.0) > 0.75
    """).fetchone()[0]

    if n_win:
        con.execute("""
            UPDATE aws_li_to_gcp_li
            SET mapping_confidence = LEAST(COALESCE(mapping_confidence, 1.0), 0.75),
                projection_note = CASE
                    WHEN projection_note ILIKE '%license%' OR projection_note ILIKE '%BYOL%'
                    THEN projection_note
                    ELSE COALESCE(projection_note, '') ||
                         ' [Windows license not included in GCP pricing; '
                         'add BYOL or Windows Server premium before finalizing cost]'
                END
            WHERE aws_li_key IN (
                SELECT c.aws_li_key FROM aws_li_catalog c
                JOIN aws_li_to_gcp_li m USING (aws_li_key)
                WHERE (c.operating_system ILIKE '%Windows%'
                       OR c.projection_note ILIKE '%Windows%'
                       OR c.projection_note ILIKE '%license-premium%')
                  AND COALESCE(m.mapping_confidence, 1.0) > 0.75
            )
        """)
        print(f"  calibrate_confidence: Windows: {n_win} row(s) capped at 75%")
        total += n_win

    con.commit()
    con.close()
    print(f"calibrate_confidence: done — {total} total row(s) adjusted")


if __name__ == "__main__":
    main()
