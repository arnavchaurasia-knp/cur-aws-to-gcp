from __future__ import annotations
#!/usr/bin/env python3
"""
classify_mechanics.py — stamp each row in aws_li_catalog with mechanic_group.

Usage:
    python3 classify_mechanics.py <projection.duckdb>

Rules applied in order (first match wins):
  1. compute_breakdown
  2. managed_db
  3. block_storage
  4. data_transfer
  5. flat_hourly
  6. per_request
  7. object_storage
  8. commitment_discount
  9. misc (fallback)

Exits 0 on success. Prints WARNING if misc > 15% of total aws_amortized_cost.
"""

import json
import sys
import re
import duckdb

# ---------------------------------------------------------------------------
# Rule definitions — each rule is (group_name, test_fn).
# test_fn(row: dict) -> bool
# ---------------------------------------------------------------------------

def _ilike(value: str | None, pattern: str) -> bool:
    """Case-insensitive substring match (SQL ILIKE '%pattern%' semantics)."""
    if value is None:
        return False
    return pattern.lower() in value.lower()

def _re(value: str | None, pattern: str) -> bool:
    if value is None:
        return False
    return bool(re.search(pattern, value, re.IGNORECASE))

# Accelerator / specialized-silicon families: AI inference/training chips
# (Inferentia inf*, Trainium trn*, Habana dl*) and GPU families (p*, g*, vt*).
# These must NOT be mapped to a general-purpose CPU VM (N2D) — that hides the
# architectural change and mis-prices badly. They are routed to misc for a
# passthrough + manual-review verdict instead.
def _is_accelerator(row) -> bool:
    txt = f"{row.get('instance_type') or ''} {row.get('operation') or ''}".lower()
    if "inferentia" in txt or "trainium" in txt:
        return True
    return bool(re.search(r'\b(inf\d|trn\d|dl\d|p[2-5]|g[3-6]|vt\d)[a-z0-9]*\.', txt))

RULES = [
    (
        # NOTE: excludes accelerator/GPU instances — those fall through to misc
        # and get a manual-review verdict rather than a wrong N2D CPU mapping.
        "compute_breakdown",
        lambda r: (
            (_ilike(r["product"], "Elastic Compute") or _ilike(r["product"], "EC2") or _ilike(r["product"], "Compute Cloud"))
            and (_re(r["usage_type"], r"BoxUsage|SpotUsage|ReservedInstances|running Linux") or _re(r["operation"], r"Instance Hour"))
            and r["unit"] in ("Hrs", "hours")
            and not _is_accelerator(r)
        ),
    ),
    (
        # managed_db owns BOTH the instance AND its storage/backup lines. Storage
        # SKU selection for a managed DB is engine- and region-specific (a generic
        # "SSD storage" pattern resolves to the wrong SKU — e.g. an IOPS SKU), so
        # these rows need the managed_db LLM's context, not a static table. Only
        # standalone EBS volumes go to block_storage (deterministic).
        "managed_db",
        lambda r: (
            _ilike(r["product"], "RDS")
            or _ilike(r["product"], "Relational Database")
            or _ilike(r["product"], "Aurora")
            or _ilike(r["product"], "ElastiCache")
            or _ilike(r["product"], "DocumentDB")
            or _ilike(r["product"], "MemoryDB")
        ),
    ),
    (
        "block_storage",
        lambda r: (
            _ilike(r["product"], "Elastic Block")
            or _re(r["usage_type"], r"EBS:Volume|EBS:Snapshot|gp2|gp3|io1|io2|sc1|st1")
            or (_re(r["usage_type"], r"Storage") and not _ilike(r["product"], "S3") and not _ilike(r["product"], "Simple Storage") and not _ilike(r["product"], "DynamoDB"))
            or _re(r["operation"], r"GP3-Storage|Provisioned GP3 storage")
        ),
    ),
    (
        "data_transfer",
        lambda r: (
            _ilike(r["product"], "DataTransfer")
            or _ilike(r["product"], "Data Transfer")
            or _re(r["usage_type"], r"DataTransfer|Data Transfer|NatGateway-Bytes")
        ),
    ),
    (
        "flat_hourly",
        lambda r: (
            (_re(r["usage_type"], r"LoadBalancerUsage|NatGateway-Hours|ElasticIP|IPAddress")
             or _re(r["operation"], r"LoadBalancer|public IPv4 address"))
            and r["unit"] in ("Hrs", "hours")
        ),
    ),
    (
        # Architecturally complex services (Cognito, DynamoDB, SES, GuardDuty,
        # Security Hub) are excluded here — they fall through to misc so the LLM
        # gets a personalized prompt per service rather than a static SKU lookup.
        "per_request",
        lambda r: (
            (
                r["unit"] in ("Requests", "Lambda-GB-Second", "Count")
                or _re(r["usage_type"], r"Requests|Invocations")
            )
            and not any(
                _ilike(r["product"], p) for p in (
                    "Cognito", "DynamoDB", "Simple Email",
                    "GuardDuty", "Security Hub",
                )
            )
        ),
    ),
    (
        "object_storage",
        lambda r: (
            (_ilike(r["product"], "S3") or _ilike(r["product"], "Simple Storage"))
            and r["unit"] in ("GB-Mo", "GB Month", "GB-Month")
            and _re(r["usage_type"], r"TimedStorage|ByteHrs")
        ),
    ),
    (
        "commitment_discount",
        lambda r: (
            r["line_item_type"] in ("RIFee", "SavingsPlanRecurringFee", "EdpDiscount")
            or r["pricing_model"] in ("Reserved", "SavingsPlan")
            or _ilike(r["product"], "Savings Plans")
            or _ilike(r["product"], "Discounts")
            or _ilike(r["product"], "CK Discounts")
        ),
    ),
]

def classify(row: dict) -> str:
    for group, test in RULES:
        try:
            if test(row):
                return group
        except Exception:
            pass
    return "misc"


# Canonical field coverage per service keyword — used to annotate misc rows.
# Maps a product-name fragment → (service_label, available_fields, missing_fields).
# missing_fields = fields that are NOT_AVAILABLE_CUR_ONLY for this service type,
# so the misc agent knows what to assume rather than hallucinate.
_MISC_SERVICE_HINTS: list[tuple[str, str, list[str], list[str]]] = [
    ("Lambda",          "AWS Lambda",           ["usage_type", "operation", "unit", "region"],
                                                 ["avg_cpu_utilization", "memory_mb_configured"]),
    ("SQS",             "Amazon SQS",           ["usage_type", "operation", "unit", "region"],
                                                 ["avg_message_size_kb", "replication_factor"]),
    ("SNS",             "Amazon SNS",           ["usage_type", "operation", "unit", "region"],
                                                 []),
    ("Kinesis",         "Amazon Kinesis",       ["usage_type", "operation", "unit", "region"],
                                                 ["shard_count", "retention_hours"]),
    ("CloudWatch",      "Amazon CloudWatch",    ["usage_type", "operation", "unit", "region"],
                                                 []),
    ("KMS",             "AWS KMS",              ["usage_type", "operation", "unit", "region"],
                                                 []),
    ("Secrets Manager", "AWS Secrets Manager",  ["usage_type", "operation", "unit", "region"],
                                                 ["secret_count"]),
    ("EKS",             "Amazon EKS",           ["usage_type", "operation", "unit", "region"],
                                                 ["node_count", "cluster_mode"]),
    ("ECS",             "Amazon ECS",           ["usage_type", "operation", "unit", "region"],
                                                 ["task_count", "avg_cpu_utilization"]),
    ("Fargate",         "AWS Fargate",          ["usage_type", "operation", "unit", "region"],
                                                 ["avg_cpu_utilization"]),
    ("API Gateway",     "Amazon API Gateway",   ["usage_type", "operation", "unit", "region"],
                                                 []),
    ("Glue",            "AWS Glue",             ["usage_type", "operation", "unit", "region"],
                                                 ["dpu_count"]),
    ("Athena",          "Amazon Athena",        ["usage_type", "operation", "unit", "region"],
                                                 ["bytes_scanned"]),
    ("Redshift",        "Amazon Redshift",      ["usage_type", "operation", "unit", "region"],
                                                 ["node_count", "cluster_mode"]),
    ("EMR",             "Amazon EMR",           ["usage_type", "operation", "unit", "region"],
                                                 ["node_count", "instance_type"]),
    # --- Architecturally complex / no-equivalent services ---
    # These fall through the per_request classify rule intentionally.
    ("Cognito",         "Amazon Cognito",       ["usage_type", "operation", "unit", "region"],
                                                 ["mau_count", "federation_type", "advanced_security_enabled"]),
    ("DynamoDB",        "Amazon DynamoDB",      ["usage_type", "operation", "unit", "region"],
                                                 ["rcu", "wcu", "table_mode", "consistency_type"]),
    ("Simple Email",    "Amazon SES",           ["usage_type", "operation", "unit", "region"],
                                                 []),
    ("GuardDuty",       "Amazon GuardDuty",     ["usage_type", "unit", "region"],
                                                 ["data_source_types", "finding_volume"]),
    ("Security Hub",    "AWS Security Hub",     ["usage_type", "unit", "region"],
                                                 ["finding_count", "integrations_enabled"]),
]


# Services with no GCP equivalent — strategy is always passthrough regardless
# of information_completeness. Checked in _misc_reason() before the sizing logic.
_PASSTHROUGH_SERVICES = frozenset(
    ["Simple Email", "SES", "GuardDuty", "Security Hub"]
)

# Services with no MANAGED GCP equivalent, mapped to self-hosted GCE. The
# orchestrator extracts their real instance footprint (instance_type / vcpus /
# ram / instance_count, derived from the CUR operation field + instance-hours),
# so they are mapped 1:1 to their actual size — NOT a fabricated cluster.
_SELF_HOST_ON_GCE = [
    "OpenSearch", "Elasticsearch",
    "Managed Streaming for Apache Kafka", "MSK", "Kafka",
]


# Sizing-marker patterns that indicate we can derive a meaningful GCP mapping
# from the operation/description field even without CUR-level detail.
_SIZING_RE = re.compile(
    r'\b[a-z][0-9][a-z]?\.[0-9]*x?large\b'        # instance type (m6g.2xlarge)
    r'|\b\d+(\.\d+)?\s*(DPU|vCPU|GB|TB|PB)\b'     # explicit sizing unit
    r'|\b\d+\s*(node|worker|broker|shard|cluster)\b',   # cluster sizing
    re.IGNORECASE
)

# Services where an incorrect size estimate is worse than passthrough.
# When information_completeness='minimal', these get strategy='passthrough'.
_SIZING_SENSITIVE = frozenset(
    ["EKS", "ECS", "Fargate", "EMR", "Redshift", "Glue", "Athena", "Kinesis"]
)


def _info_completeness(row: dict) -> str:
    """Return 'full', 'partial', or 'minimal' based on available sizing data.

    full    — instance_type/vcpus present, or usage_type has CUR-style granularity
    partial — operation or usage_type contains a concrete sizing marker
    minimal — only product name + cost; no sizing signal present
    """
    if row.get("instance_type") or row.get("instance_vcpus"):
        return "full"
    ut = row.get("usage_type") or ""
    if len(ut) > 10:  # CUR: "APS3-BoxUsage:m6g.2xlarge"; flat CSV: "" or "EC2"
        return "full"
    combined = f"{ut} {row.get('operation') or ''}"
    if _SIZING_RE.search(combined):
        return "partial"
    return "minimal"


def _misc_reason(row: dict) -> str:
    """Produce a structured reason dict for a misc-classified row."""
    product = row.get("product") or ""
    usage_type = row.get("usage_type") or ""
    unit = row.get("unit") or ""

    # Accelerator / specialized silicon (Inferentia, Trainium, GPU) → NEVER a
    # CPU VM. No like-for-like GCP equivalent (GCP uses TPUs / different GPUs),
    # so this needs a human architecture decision. Passthrough at cost parity
    # and flag for manual review — mapping to N2D would be actively misleading.
    if _is_accelerator(row):
        return json.dumps({
            "why": f"AWS accelerator/GPU instance ({row.get('instance_type') or product!r}) — no CPU-VM equivalent",
            "service_hint": "Accelerator / specialized silicon",
            "recommended_strategy": "passthrough",
            "manual_review": True,
            "mapping_guidance": (
                "This is an AI accelerator (Inferentia/Trainium) or GPU instance. Do NOT map "
                "it to a general-purpose CPU VM (N2D) — that hides the architectural change. "
                "Set strategy='passthrough' (cost parity) and mapping_confidence=0.3, and note "
                "'requires manual review: GCP TPU/GPU or accelerator service — not a like-for-like "
                "CPU mapping' in mapping-notes.md."
            ),
        })

    # No-managed-equivalent compute services → self-hosted GCE, sized to the
    # ACTUAL extracted footprint. Never fabricate replication or disk.
    for frag in _SELF_HOST_ON_GCE:
        if frag.lower() in product.lower() or frag.lower() in usage_type.lower():
            has_specs = bool(row.get("instance_vcpus"))
            return json.dumps({
                "why": f"no managed GCP equivalent for product={product!r}; self-host on GCE",
                "service_hint": f"{product} → self-hosted on Compute Engine",
                "recommended_strategy": "break_down" if has_specs else "map",
                "extracted_footprint": {
                    "instance_type": row.get("instance_type"),
                    "instance_vcpus": row.get("instance_vcpus"),
                    "instance_ram_gb": row.get("instance_ram_gb"),
                    "instance_count": row.get("instance_count"),
                },
                "mapping_guidance": (
                    "Map to self-hosted Compute Engine using the EXTRACTED footprint above "
                    "(instance_type/vcpus/ram × instance_count = the real node count from "
                    "instance-hours). break_down into core+ram components exactly like an EC2 "
                    "instance. instance_count IS the actual node count — do NOT invent a "
                    "replication factor or disk size. Storage sub-lines map to Persistent Disk "
                    "(block_storage); the instance line maps to GCE compute."
                ),
            })

    completeness = _info_completeness(row)
    actually_available = [
        f for f in ("instance_type", "instance_vcpus", "instance_ram_gb",
                    "usage_type", "operation", "unit")
        if row.get(f)
    ]

    # Try to match a known service hint
    for fragment, service_label, _, missing in _MISC_SERVICE_HINTS:
        if fragment.lower() in product.lower() or fragment.lower() in usage_type.lower():
            passthrough_only = fragment in _PASSTHROUGH_SERVICES
            sizing_sensitive = fragment in _SIZING_SENSITIVE

            if passthrough_only:
                rec_strategy = "passthrough"
                guidance = (
                    f"{service_label} has no GCP managed equivalent — passthrough at cost "
                    f"parity. Set mapping_confidence=0.3 and note in mapping-notes.md that "
                    f"no GCP comparison is possible for this service."
                )
            elif fragment == "Cognito":
                rec_strategy = "map"
                guidance = (
                    "Map Amazon Cognito to GCP Identity Platform. IMPORTANT: Cognito is "
                    "MAU-priced (Monthly Active Users) but CUR bills it per authentication "
                    "request — you cannot derive MAU count directly. Estimate: "
                    "total_usage (request count) ÷ 20 ≈ MAU (assuming 20 auth events/user/mo). "
                    "Identity Platform pricing: $0.0055/MAU above 10k free tier. "
                    "Document the MAU estimation assumption and set mapping_confidence=0.45."
                )
            elif fragment == "DynamoDB":
                rec_strategy = "map"
                guidance = (
                    "Map Amazon DynamoDB to one of: Firestore (document/key-value, ops-based "
                    "pricing), Bigtable (high-throughput wide-column, node-based pricing), or "
                    "Spanner (strongly consistent relational, node-based). "
                    "Decision rule — check operation field: "
                    "'GetItem/PutItem/Query/Scan' → Firestore (most common); "
                    "'BatchWrite/high-volume analytical' → Bigtable; "
                    "'Transactional/ACID' → Spanner. "
                    "RCU maps to Firestore read ops (1 RCU ≈ 1 document read); "
                    "WCU maps to Firestore write ops (1 WCU ≈ 1 document write). "
                    "Fields rcu/wcu/table_mode are NOT in CUR — infer from total_usage and unit. "
                    "Set mapping_confidence=0.5 and document the target service choice."
                )
            elif sizing_sensitive and completeness == "minimal":
                rec_strategy = "passthrough"
                guidance = (
                    f"{service_label}: sizing fields ({missing}) are absent and no sizing "
                    f"markers found in operation/usage_type — passthrough at cost parity is "
                    f"safer than fabricating a cluster size. Set mapping_confidence=0.3 and "
                    f"note the assumption in mapping-notes.md."
                )
            elif sizing_sensitive and completeness == "partial":
                rec_strategy = "map"
                guidance = (
                    f"Map {service_label} to its GCP equivalent using the sizing signal in "
                    f"operation/usage_type. Fields {missing} are not available — document "
                    f"any cluster-size assumptions and set mapping_confidence ≤ 0.5."
                )
            else:
                rec_strategy = "map"
                guidance = (
                    f"Map {service_label} to its GCP equivalent. "
                    f"Fields {missing} are not in CUR — use service defaults and document assumptions."
                )
            return json.dumps({
                "why": f"no mechanic rule matched; product={product!r}",
                "service_hint": service_label,
                "information_completeness": completeness,
                "actually_available": actually_available,
                "not_available_cur_only": missing,
                "recommended_strategy": rec_strategy,
                "mapping_guidance": guidance,
            })

    # Unknown service — provide generic annotation
    return json.dumps({
        "why": f"unrecognized product={product!r} usage_type={usage_type!r} unit={unit!r}",
        "service_hint": None,
        "information_completeness": completeness,
        "actually_available": actually_available,
        "not_available_cur_only": [],
        "recommended_strategy": "passthrough" if completeness == "minimal" else "map",
        "mapping_guidance": (
            "Service is unrecognized. Map by spend share: if aws_amortized_cost < $10/mo "
            "set strategy='passthrough'. Otherwise find the closest GCP equivalent by product "
            "name and usage_type, document your reasoning in mapping-notes.md, and set "
            "mapping_confidence ≤ 0.6."
        ),
    })


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <projection.duckdb>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    con = duckdb.connect(db_path)

    # --- Check table exists ---
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "aws_li_catalog" not in tables:
        print("ERROR: table aws_li_catalog not found in database.", file=sys.stderr)
        sys.exit(1)

    # --- Add column if missing ---
    existing_cols = {
        r[1].lower()
        for r in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()
    }
    if "mechanic_group" not in existing_cols:
        con.execute("ALTER TABLE aws_li_catalog ADD COLUMN mechanic_group TEXT")
        print("Added column mechanic_group to aws_li_catalog.")

    # --- Load rows ---
    # billing_format may be absent in DBs ingested before this column was added
    bf_expr = "billing_format" if "billing_format" in existing_cols else "NULL AS billing_format"
    rows = con.execute(
        f"""
        SELECT
            aws_li_key,
            product,
            usage_type,
            operation,
            pricing_unit AS unit,
            line_item_type,
            pricing_model,
            aws_amortized_cost,
            instance_type,
            instance_vcpus,
            instance_ram_gb,
            instance_count,
            {bf_expr}
        FROM aws_li_catalog
        """
    ).fetchall()

    col_names = [
        "aws_li_key", "product", "usage_type", "operation",
        "unit", "line_item_type", "pricing_model", "aws_amortized_cost",
        "instance_type", "instance_vcpus", "instance_ram_gb", "instance_count",
        "billing_format",
    ]

    # --- Classify ---
    updates: list[tuple[str, str]] = []
    misc_reasons: dict[str, str] = {}   # aws_li_key → why it landed in misc
    for raw in rows:
        row = dict(zip(col_names, raw))
        group = classify(row)
        updates.append((group, row["aws_li_key"]))
        if group == "misc":
            misc_reasons[row["aws_li_key"]] = _misc_reason(row)

    # Bulk update
    con.executemany(
        "UPDATE aws_li_catalog SET mechanic_group = ? WHERE aws_li_key = ?",
        updates,
    )
    con.commit()

    # --- Breakdown report ---
    stats = con.execute(
        """
        SELECT
            mechanic_group,
            COUNT(*) AS row_count,
            COALESCE(SUM(aws_amortized_cost), 0) AS group_spend
        FROM aws_li_catalog
        GROUP BY mechanic_group
        ORDER BY group_spend DESC
        """
    ).fetchall()

    total_spend = sum(r[2] for r in stats)
    total_rows = sum(r[1] for r in stats)

    print(f"\n{'mechanic_group':<25} {'rows':>8}  {'% rows':>8}  {'spend_usd':>14}  {'% spend':>8}")
    print("-" * 70)
    misc_spend = 0.0
    misc_rows: list[dict] = []
    for group, row_count, group_spend in stats:
        pct_rows = 100.0 * row_count / total_rows if total_rows else 0.0
        pct_spend = 100.0 * group_spend / total_spend if total_spend else 0.0
        print(f"{group:<25} {row_count:>8}  {pct_rows:>7.1f}%  {group_spend:>14,.2f}  {pct_spend:>7.1f}%")
        if group == "misc":
            misc_spend = group_spend

    print("-" * 70)
    print(f"{'TOTAL':<25} {total_rows:>8}  {'100.0%':>8}  {total_spend:>14,.2f}  {'100.0%':>8}")

    # --- Gate: misc > 15% of total spend ---
    misc_pct = 100.0 * misc_spend / total_spend if total_spend else 0.0
    if misc_pct > 15.0:
        print(f"\nWARNING: misc group is {misc_pct:.1f}% of total spend (threshold: 15%).")
        print("Misc rows:")
        misc_detail = con.execute(
            """
            SELECT aws_li_key, product, usage_type, operation,
                   line_item_type, pricing_model, aws_amortized_cost
            FROM aws_li_catalog
            WHERE mechanic_group = 'misc'
            ORDER BY aws_amortized_cost DESC
            """
        ).fetchall()
        header = f"  {'aws_li_key':<34} {'product':<30} {'usage_type':<35} {'operation':<35} {'line_item_type':<25} {'pricing_model':<15} {'amortized_cost':>14}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for r in misc_detail:
            print(
                f"  {str(r[0]):<34} {str(r[1] or ''):<30} {str(r[2] or ''):<35} "
                f"{str(r[3] or ''):<35} {str(r[4] or ''):<25} {str(r[5] or ''):<15} {r[6]:>14,.2f}"
            )

    # --- Emit phase2_manifest.json alongside the DB ---
    import os, collections
    manifest: dict[str, list] = collections.defaultdict(list)
    row_data_full = con.execute(
        """
        SELECT
            aws_li_key, mechanic_group, product, usage_type, operation,
            pricing_unit AS unit, line_item_type, pricing_model,
            aws_amortized_cost, instance_type, instance_vcpus,
            instance_ram_gb, instance_arch, workload_class,
            billing_days, instance_count, aws_effective_unit_rate,
            aws_region AS region, gcp_region, is_workload
        FROM aws_li_catalog
        ORDER BY mechanic_group, aws_amortized_cost DESC
        """
    ).fetchall()
    full_cols = [
        "aws_li_key", "mechanic_group", "product", "usage_type", "operation",
        "unit", "line_item_type", "pricing_model",
        "aws_amortized_cost", "instance_type", "instance_vcpus",
        "instance_ram_gb", "instance_arch", "workload_class",
        "billing_days", "instance_count", "aws_effective_unit_rate",
        "region", "gcp_region", "is_workload",
    ]
    for raw in row_data_full:
        row = dict(zip(full_cols, raw))
        if row["mechanic_group"] == "misc" and row["aws_li_key"] in misc_reasons:
            row["misc_annotation"] = json.loads(misc_reasons[row["aws_li_key"]])
        manifest[row["mechanic_group"]].append(row)

    # Groups resolved deterministically by apply_commitment_ignores.py /
    # apply_static_mappings.py — the LLM must NOT re-touch them, or it could
    # overwrite a stable deterministic mapping with a run-to-run-varying guess.
    skip_groups = {
        "commitment_discount",
        "flat_hourly", "object_storage", "per_request",
        "block_storage", "data_transfer",
    }
    manifest_out = {
        g: {"rows": rows, "row_count": len(rows),
            "total_spend": sum(r.get("aws_amortized_cost") or 0 for r in rows),
            "needs_llm": g not in skip_groups}
        for g, rows in manifest.items()
    }

    # Add output_dir so the LLM knows exactly where to write mapping files.
    # Use a relative path — absolute paths cause the LLM to write outside the job dir.
    manifest_out["_meta"] = {
        "output_dir": "projection-audit/mappings",
        "db_path": "projection-audit/projection.duckdb",
    }

    manifest_path = os.path.join(os.path.dirname(db_path), "phase2_manifest.json")
    # Also create the output dir now so the LLM doesn't have to
    os.makedirs(os.path.join(os.path.dirname(db_path), "mappings"), exist_ok=True)
    with open(manifest_path, "w") as fh:
        json.dump(manifest_out, fh, indent=2, default=str)
    print(f"\nWrote {manifest_path}  ({len(manifest_out)} groups)")

    con.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
