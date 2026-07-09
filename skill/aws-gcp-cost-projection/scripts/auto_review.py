#!/usr/bin/env python3
"""
auto_review.py — Phase 3 suggestion engine. NEVER modifies the database.

Detects mapping issues and pre-computes candidate fixes:
  - Illegal passthroughs: core services (EC2/RDS/S3/etc.) marked passthrough
  - Spec violations: break_down rows with wrong unit_multipliers vs instance spec

Writes:
  review_flags.md         — human-readable report with candidates (LLM input)
  review_candidates.json  — machine-readable candidates (for apply_review_fixes.py)
"""
import duckdb
import json
import os
import sys

SKILL_DIR = os.environ.get("SKILL_DIR", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))
from apply_static_mappings import resolve_sku

JOB_DIR         = os.getcwd()
DB_PATH         = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
FLAGS_FILE      = os.path.join(JOB_DIR, "review_flags.md")
CANDIDATES_FILE = os.path.join(JOB_DIR, "review_candidates.json")

# Products that must NEVER be passthrough: services with clear, direct GCP equivalents
# where a passthrough means we're not actually projecting GCP cost.
#
# Excluded from this list (legitimately passthrough):
#   - CloudWatch: alarms/dashboards/metrics have no per-unit GCP pricing equivalent
#   - Lambda: requires memory_size_mb not present in CUR; passthrough is correct fallback
#   - Data Transfer: multi-directional, many sub-types; static mapper handles what it can
#   - NAT Gateway: multi-component (gateway-hours + data-processed); excluded via NOT ILIKE below
#   - Route 53 / KMS: minor cost, pricing models differ enough that passthrough is acceptable
_NEVER_PASSTHROUGH_ILIKE = [
    "%Elastic Compute Cloud%",
    "%Elastic Block Store%",
    "%Relational Database%",
    "%Aurora%",
    "%ElastiCache%",
    "%Simple Storage%",
    "%Load Balanc%",
]

# Sub-patterns to EXCLUDE even when a product matches the list above.
# NAT Gateway sits under "Amazon Elastic Compute Cloud NatGateway" — it's multi-component
# infrastructure, not an EC2 instance, and legitimately maps to Cloud NAT passthrough.
_NEVER_PASSTHROUGH_EXCLUDE_ILIKE = [
    "%NatGateway%",
    "%Nat:%",
]


def _never_passthrough_clause():
    include = " OR ".join(f"c.product ILIKE '{p}'" for p in _NEVER_PASSTHROUGH_ILIKE)
    exclude = " AND ".join(f"c.product NOT ILIKE '{p}'" for p in _NEVER_PASSTHROUGH_EXCLUDE_ILIKE)
    return f"({include}) AND {exclude}"


def main():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        sys.exit(0)

    conn = duckdb.connect(DB_PATH)

    tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
    if "aws_li_to_gcp_li" not in tables:
        print("ERROR: aws_li_to_gcp_li table missing — Phase 2 did not complete.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # 1. Detect illegal passthroughs                                       #
    # ------------------------------------------------------------------ #

    illegal_rows = conn.execute(f"""
        SELECT c.aws_li_key, c.product, c.usage_type, c.operation,
               ROUND(c.aws_amortized_cost, 2) AS cost,
               c.gcp_region,
               m.gcp_service, m.gcp_sku_name, m.gcp_sku_id, m.projection_note
        FROM aws_li_catalog c JOIN aws_li_to_gcp_li m USING(aws_li_key)
        WHERE m.strategy = 'passthrough'
          AND ({_never_passthrough_clause()})
        ORDER BY c.aws_amortized_cost DESC
    """).fetchall()

    # ------------------------------------------------------------------ #
    # 2. Detect spec violations (break_down multiplier wrong)              #
    # ------------------------------------------------------------------ #

    SKILL_DIR_PATH = os.environ.get("SKILL_DIR", "")
    CATALOG_DB = os.path.join(SKILL_DIR_PATH, "data", "catalog.duckdb")
    spec_violations = []

    if os.path.exists(CATALOG_DB):
        try:
            conn.execute(f"ATTACH '{CATALOG_DB}' AS catalog (READ_ONLY)")
            spec_violations = conn.execute("""
                WITH gcp_caps AS (
                    SELECT
                        aws_li_key,
                        MAX(CASE WHEN component = 'core' THEN unit_multiplier ELSE 0.0 END) AS gcp_vcpu,
                        MAX(CASE WHEN component = 'ram'  THEN unit_multiplier ELSE 0.0 END) AS gcp_ram,
                        MAX(CASE WHEN component = 'core' THEN gcp_sku_id ELSE NULL END) AS core_sku
                    FROM aws_li_to_gcp_li
                    WHERE strategy IN ('map', 'break_down')
                    GROUP BY aws_li_key
                )
                SELECT
                    c.aws_li_key, c.instance_type,
                    c.instance_vcpus, c.instance_ram_gb,
                    g.gcp_vcpu, g.gcp_ram,
                    cat.description, c.gcp_region,
                    m.gcp_service, m.gcp_sku_name
                FROM aws_li_catalog c
                JOIN gcp_caps g USING (aws_li_key)
                JOIN catalog.skus cat ON cat.sku_id = g.core_sku
                JOIN aws_li_to_gcp_li m
                  ON m.aws_li_key = c.aws_li_key AND m.component = 'core'
                WHERE c.instance_vcpus IS NOT NULL AND c.instance_ram_gb IS NOT NULL
                  AND (
                    ((g.gcp_ram / g.gcp_vcpu) < (c.instance_ram_gb / c.instance_vcpus) AND g.gcp_vcpu > 0)
                    OR
                    (c.instance_type NOT LIKE 't%' AND (
                        cat.description ILIKE '%f1-micro%' OR cat.description ILIKE '%g1-small%'
                        OR cat.description ILIKE '%shared-core%' OR cat.description ILIKE '%e2-micro%'
                        OR cat.description ILIKE '%e2-small%' OR cat.description ILIKE '%e2-medium%'
                    ))
                  )
            """).fetchall()
            conn.execute("DETACH catalog")
        except Exception as e:
            print(f"Warning: catalog spec check skipped: {e}")

    conn.close()

    # ------------------------------------------------------------------ #
    # 3. Compute candidates                                                #
    # ------------------------------------------------------------------ #

    candidates = {}

    # Illegal passthrough candidates: try resolve_sku
    for row in illegal_rows:
        key = row[0]
        gcp_service  = row[6] or ""
        gcp_sku_name = row[7] or ""
        region       = row[5] or "us-central1"

        candidate  = None
        confidence = "NONE"

        if gcp_service and gcp_sku_name:
            try:
                sku_id = resolve_sku(gcp_service, gcp_sku_name, region)
                if sku_id:
                    candidate = {
                        "action": "set_sku_and_map",
                        "gcp_sku_id": sku_id,
                        "gcp_sku_name": gcp_sku_name,
                        "gcp_service": gcp_service,
                    }
                    confidence = "HIGH"
                else:
                    confidence = "LOW"
            except Exception:
                confidence = "LOW"

        candidates[key] = {
            "type": "illegal_passthrough",
            "confidence": confidence,
            "candidate": candidate,
        }

    # Spec violation candidates: correct multipliers from instance spec
    for row in spec_violations:
        key = row[0]
        instance_vcpus  = row[2]
        instance_ram_gb = row[3]
        candidates[key] = {
            "type": "spec_violation",
            "confidence": "HIGH",
            "candidate": {
                "action": "fix_multipliers",
                "core_multiplier": float(instance_vcpus) if instance_vcpus is not None else None,
                "ram_multiplier":  float(instance_ram_gb) if instance_ram_gb is not None else None,
            },
        }

    # ------------------------------------------------------------------ #
    # 4. Write review_candidates.json                                      #
    # ------------------------------------------------------------------ #

    with open(CANDIDATES_FILE, "w") as f:
        json.dump(candidates, f, indent=2)

    # ------------------------------------------------------------------ #
    # 5. Write review_flags.md                                             #
    # ------------------------------------------------------------------ #

    total_flags = len(illegal_rows) + len(spec_violations)

    with open(FLAGS_FILE, "w") as f:
        f.write("# Phase 3 Review Flags\n\n")
        f.write(f"Total flags: **{total_flags}**\n\n")

        if total_flags == 0:
            f.write("No issues detected. Return `[]` (empty array).\n")
            print("auto_review: 0 flags — no issues.")
            return

        f.write("Return `review_fixes.json` — a JSON array:\n")
        f.write('```json\n[{"aws_li_key": "...", "decision": "confirm|override|veto",\n'
                '  "gcp_sku_id": "...", "gcp_sku_name": "...",\n'
                '  "unit_multiplier": 4.0, "component": "core", "reason": "..."}]\n```\n\n')
        f.write("- `confirm`: apply the pre-computed candidate as-is\n")
        f.write("- `override`: supply your own values\n")
        f.write("- `veto`: skip (document why in reason)\n\n")
        f.write("---\n\n")

        if illegal_rows:
            f.write("## Illegal Passthrough Rows\n\n")
            f.write("Core services (EC2/RDS/S3/CloudWatch/Lambda/etc.) MUST be mapped — "
                    "passthrough is only valid for marketplace or services with no GCP equivalent.\n\n")
            for row in illegal_rows:
                key = row[0]
                cand_info = candidates.get(key, {})
                conf = cand_info.get("confidence", "NONE")
                cand = cand_info.get("candidate")

                f.write(f"### `{key}` — Confidence: `{conf}`\n\n")
                f.write(f"- **Product**: {row[1]}\n")
                f.write(f"- **Usage type**: {row[2]}\n")
                f.write(f"- **Operation**: {row[3]}\n")
                f.write(f"- **AWS cost**: ${row[4]}\n")
                f.write(f"- **Current gcp_service**: {row[6]!r}\n")
                f.write(f"- **Current gcp_sku_name**: {row[7]!r}\n")
                if row[9]:
                    f.write(f"- **projection_note**: {row[9]}\n")

                if cand:
                    f.write(f"\n**Candidate** (`{conf}`): "
                            f"set gcp_sku_id=`{cand['gcp_sku_id']}`, "
                            f"gcp_sku_name=`{cand['gcp_sku_name']}`, strategy=`map`\n\n")
                else:
                    f.write("\n**No candidate found** — provide gcp_sku_id and gcp_sku_name in override.\n\n")

        if spec_violations:
            f.write("---\n\n")
            f.write("## Spec Violations\n\n")
            f.write("These break_down rows have wrong unit_multipliers. "
                    "Correct values (HIGH confidence) are from the instance spec.\n\n")
            for row in spec_violations:
                key = row[0]
                f.write(f"### `{key}` — Confidence: `HIGH`\n\n")
                f.write(f"- **Instance**: {row[1]}\n")
                f.write(f"- **AWS spec**: {row[2]} vCPU, {row[3]} GB RAM\n")
                f.write(f"- **Current GCP mapping**: {row[4]} vCPU, {row[5]} GB RAM\n")
                f.write(f"- **SKU description**: {row[6]}\n")
                f.write(f"\n**Candidate**: set core unit_multiplier={row[2]}, "
                        f"ram unit_multiplier={row[3]}\n\n")

    print(f"auto_review: {len(illegal_rows)} illegal passthroughs, "
          f"{len(spec_violations)} spec violations. Candidates written.")


if __name__ == "__main__":
    main()
