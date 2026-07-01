#!/usr/bin/env python3
import duckdb
import os
import sys

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
FLAGS_FILE = os.path.join(JOB_DIR, "review_flags.md")

def main():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        sys.exit(0)

    conn = duckdb.connect(DB_PATH)

    # 1. Auto-correct unit_multipliers for break_down rows
    conn.execute("""
        UPDATE aws_li_to_gcp_li
        SET unit_multiplier = c.instance_vcpus
        FROM aws_li_catalog c
        WHERE aws_li_to_gcp_li.aws_li_key = c.aws_li_key
          AND aws_li_to_gcp_li.strategy = 'break_down'
          AND aws_li_to_gcp_li.component = 'core'
          AND c.instance_vcpus IS NOT NULL
          AND ABS(aws_li_to_gcp_li.unit_multiplier - c.instance_vcpus) > 0.5
    """)
    
    conn.execute("""
        UPDATE aws_li_to_gcp_li
        SET unit_multiplier = c.instance_ram_gb
        FROM aws_li_catalog c
        WHERE aws_li_to_gcp_li.aws_li_key = c.aws_li_key
          AND aws_li_to_gcp_li.strategy = 'break_down'
          AND aws_li_to_gcp_li.component = 'ram'
          AND c.instance_ram_gb IS NOT NULL
          AND ABS(aws_li_to_gcp_li.unit_multiplier - c.instance_ram_gb) > 1.0
    """)

    # 2. Two-Stage Filter Validation (RAM/vCPU ratio & burstable core block)
    SKILL_DIR = os.environ.get("SKILL_DIR", "")
    CATALOG_DB = os.path.join(SKILL_DIR, "data", "catalog.duckdb")
    spec_violations = []
    
    if os.path.exists(CATALOG_DB):
        try:
            conn.execute(f"ATTACH '{CATALOG_DB}' AS catalog (READ_ONLY)")
            spec_violations = conn.execute("""
                WITH gcp_caps AS (
                    SELECT 
                        aws_li_key,
                        MAX(CASE WHEN component = 'core' THEN unit_multiplier ELSE 0.0 END) as gcp_vcpu,
                        MAX(CASE WHEN component = 'ram' THEN unit_multiplier ELSE 0.0 END) as gcp_ram,
                        MAX(CASE WHEN component = 'core' THEN gcp_sku_id ELSE NULL END) as core_sku
                    FROM aws_li_to_gcp_li
                    WHERE strategy IN ('map', 'break_down')
                    GROUP BY aws_li_key
                )
                SELECT 
                    c.aws_li_key,
                    c.instance_type,
                    c.instance_vcpus,
                    c.instance_ram_gb,
                    g.gcp_vcpu,
                    g.gcp_ram,
                    cat.description
                FROM aws_li_catalog c
                JOIN gcp_caps g USING (aws_li_key)
                JOIN catalog.skus cat ON cat.sku_id = g.core_sku
                WHERE c.instance_vcpus IS NOT NULL AND c.instance_ram_gb IS NOT NULL
                  AND (
                    -- RAM/vCPU Ratio check
                    ((g.gcp_ram / g.gcp_vcpu) < (c.instance_ram_gb / c.instance_vcpus) AND g.gcp_vcpu > 0)
                    OR
                    -- Shared-Core block: Non-burstable AWS -> Burstable GCE shared-core
                    (c.instance_type NOT LIKE 't%' AND (
                        cat.description ILIKE '%f1-micro%' OR 
                        cat.description ILIKE '%g1-small%' OR 
                        cat.description ILIKE '%shared-core%' OR 
                        cat.description ILIKE '%e2-micro%' OR 
                        cat.description ILIKE '%e2-small%' OR 
                        cat.description ILIKE '%e2-medium%'
                    ))
                  )
            """).fetchall()
            conn.execute("DETACH catalog")
        except Exception as e:
            print(f"Warning: catalog spec check skipped due to error: {e}")

    # 3. Find illegal passthroughs
    illegal_passthroughs = conn.execute("""
        SELECT c.aws_li_key, c.product, c.usage_type, c.operation, ROUND(c.aws_amortized_cost,2)
        FROM aws_li_catalog c JOIN aws_li_to_gcp_li m USING(aws_li_key)
        WHERE m.strategy = 'passthrough'
          AND (c.product ILIKE '%Elastic Compute Cloud%' OR c.product ILIKE '%EC2%'
            OR c.product ILIKE '%Block Store%' OR c.product ILIKE '%EBS%'
            OR c.product ILIKE '%Relational Database%' OR c.product ILIKE '%RDS%'
            OR c.product ILIKE '%Aurora%' OR c.product ILIKE '%ElastiCache%'
            OR c.product ILIKE '%Simple Storage%' OR c.product ILIKE '%S3%'
            OR c.product ILIKE '%Data Transfer%' OR c.product ILIKE '%Load Balanc%'
            OR c.product ILIKE '%Lambda%' OR c.product ILIKE '%Route 53%'
            OR c.product ILIKE '%KMS%' OR c.product ILIKE '%CloudWatch%')
        ORDER BY c.aws_amortized_cost DESC;
    """).fetchall()

    with open(FLAGS_FILE, "w") as f:
        if illegal_passthroughs:
            f.write("## ILLEGAL PASSTHROUGH ROWS FOUND\n\n")
            f.write("The following rows were marked as `passthrough` by Phase 2 but belong to core services that MUST be mapped to GCP. You MUST update these rows in the database to use `map` or `break_down` and assign a real GCP SKU.\n\n")
            for r in illegal_passthroughs:
                f.write(f"- **aws_li_key**: `{r[0]}` | Product: {r[1]} | Usage: {r[2]} | Cost: ${r[4]}\n")
                f.write(f"  Operation: {r[3]}\n\n")
        else:
            f.write("No illegal passthrough rows found.\n")

        if spec_violations:
            f.write("\n## MAPPING SPEC VIOLATIONS / CHEAP-OUT DETECTED\n\n")
            f.write("The following mappings violate the Two-Stage Filter boundaries. Memory-optimized or standard AWS instances must not map to under-provisioned ratios or shared-core burstable VMs (like e2-micro/small/medium). You MUST fix these SKUs:\n\n")
            for r in spec_violations:
                f.write(f"- **aws_li_key**: `{r[0]}` | AWS Instance: {r[1]} ({r[2]} vCPU, {r[3]} GB) | "
                        f"GCP Mapping: {r[4]} vCPU, {r[5]} GB | Description: {r[6]}\n")

    # 4. Find other passthroughs for review
    other_passthroughs = conn.execute("""
        SELECT c.aws_li_key, c.product, ROUND(c.aws_amortized_cost,2), m.projection_note
        FROM aws_li_catalog c JOIN aws_li_to_gcp_li m USING(aws_li_key)
        WHERE m.strategy = 'passthrough'
          AND NOT (c.product ILIKE '%Elastic Compute Cloud%' OR c.product ILIKE '%EC2%'
            OR c.product ILIKE '%Block Store%' OR c.product ILIKE '%EBS%'
            OR c.product ILIKE '%Relational Database%' OR c.product ILIKE '%RDS%'
            OR c.product ILIKE '%Aurora%' OR c.product ILIKE '%ElastiCache%'
            OR c.product ILIKE '%Simple Storage%' OR c.product ILIKE '%S3%'
            OR c.product ILIKE '%Data Transfer%' OR c.product ILIKE '%Load Balanc%'
            OR c.product ILIKE '%Lambda%' OR c.product ILIKE '%Route 53%'
            OR c.product ILIKE '%KMS%' OR c.product ILIKE '%CloudWatch%')
        ORDER BY c.aws_amortized_cost DESC;
    """).fetchall()

    with open(FLAGS_FILE, "a") as f:
        if other_passthroughs:
            f.write("\n## OTHER PASSTHROUGHS TO AUDIT\n\n")
            f.write("The following rows were marked as `passthrough` and are not strictly illegal, but should be audited. If a real GCP equivalent exists, map it.\n\n")
            for r in other_passthroughs:
                f.write(f"- **aws_li_key**: `{r[0]}` | Product: {r[1]} | Cost: ${r[2]}\n")
                f.write(f"  Note: {r[3]}\n\n")

if __name__ == "__main__":
    main()
