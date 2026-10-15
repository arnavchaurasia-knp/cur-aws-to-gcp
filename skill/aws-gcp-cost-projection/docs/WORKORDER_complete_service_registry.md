# WORK ORDER — Build the service registry for EVERY AWS service (AWS-sourced)

**For an implementing AI / engineer with NO prior context. Read fully. This is a LONG,
ongoing curation task — done service-by-service, evidence-first, with review. Each service
is a small, self-contained unit of work; do them in priority order and stop/report in
batches so a reviewer can check.**

## Goal
`data/service_specs.json` is the single per-service source of truth (identity → charge-types
→ GCP target + pricing mechanic → required fields → LLM guidance). Today it covers ~43
services. The goal is **full coverage**: every AWS service that can appear on a bill has a
registry entry, so it maps deterministically (or with curated LLM guidance) instead of
falling to a blind `misc` guess.

## Source of truth (where each fact comes from)
- **Billing model / units** → the AWS service's own pricing page on aws.amazon.com
  (e.g. "Amazon Bedrock pricing" → per-1K-input/output-tokens). This tells you the
  charge-types and their units.
- **Identity (product code)** → AWS CUR `lineItem/ProductCode` for that service; add it to
  `data/aws-product-codes.json` (code → canonical key).
- **GCP equivalent + pricing** → cloud.google.com docs/pricing for the closest GCP service.
  This is a JUDGMENT (AWS docs won't give it) — pick the honest closest equivalent.
- **Catalog** → `data/catalog.duckdb` for the real GCP SKU/rate when the mapping is a
  deterministic SKU.
- VERIFY live (don't trust memory): per the project rule, sweep the real GCP catalog/API
  and the AWS pricing page before finalizing a mapping.

## Priority order — EVIDENCE FIRST (do not try all 200+ AWS services blindly)
1. **Services that actually appear in the 75 real job DBs**, ranked by spend. Pull this list:
   query each `projection.duckdb`'s `aws_li_catalog` for distinct `product` / `product_code`
   with summed `aws_amortized_cost`; rank desc. (The AGENTS.md global audit already has a
   version of this ranking — use it as a starting point.) These cover ~90%+ of real spend.
2. **The common AWS catalog** (the ~50-80 services most customers use) even if not yet seen.
3. **Long tail** — add on demand when a new service shows up (this is what the refresh/
   backfill loop in `project_refresh_and_reviewer_layer` automates later).

## Per-service checklist — every new registry entry MUST have
For each service, add to `data/service_specs.json` (and `aws-product-codes.json` for identity):
```json
"<canonical>": {
  "display": "Amazon <Service>",
  "product_codes": ["<CUR ProductCode>"],              // identity (also add to aws-product-codes.json)
  "charge_types": [
    {
      "id": "<charge_type>",                           // e.g. input_tokens, storage, requests
      "description": "...",
      "match": { ... },                                // how to recognise this charge-type (usage_type/unit/etc.)
      "mechanic": "<existing mechanic or misc>",        // reuse a shared mechanic where the math is shared
      "fields": [ {name, cur_column, source, required} ],   // the fields THIS charge-type needs (CUR-grounded)
      "gcp_target": "<GCP service>",                    // the equivalent
      "source_rule": "<AWS pricing page / reasoning>",
      "llm_guidance": { gcp_target, strategy, confidence_max, formula?, guidance }   // for misc/review charge-types
    }
  ]
}
```

## Mapping rules (keep it honest — same discipline as the rest of the pipeline)
- **Pricing basis = On-Demand vs On-Demand** (project decision): map to GCP on-demand list;
  do not bake in commitment discounts.
- **Reuse shared mechanics** where the math is identical (compute→compute_breakdown,
  GB-Mo→block_storage, egress→data_transfer, per-call→per_request). Only invent a
  service-specific mechanic when the pricing genuinely differs.
- **No fabricated ratios.** If there's a clean unit conversion (same unit both sides) → map
  it. If a conversion needs a performance/throughput ratio with no public basis (the
  Inferentia→TPU situation) → `strategy="passthrough"` + a labeled assumption + low
  confidence, NEVER an invented multiplier.
- **Weird units are fine as charge-types.** Bedrock tokens, Aurora ACU-hours, Glue
  DPU-hours, Kinesis shard-hours — each is just a charge-type with its own unit and target
  (Bedrock→Vertex AI token pricing, ACU→Cloud SQL, DPU→Dataproc, etc.).

## Per-service validation (every entry gets a test)
- Add a dispatch case to `tests/test_spec_dispatch.py`: a representative row for the service
  → asserts the expected mechanic + charge_type, and (for misc/review) that
  `llm_guidance.build_guidance` returns the curated `gcp_target` (not the generic fallback).
- Keep both classifier suites green; a new service must not change any EXISTING row's routing.

## Tracking (so the long task is auditable)
Maintain a coverage checklist at the top of `docs/SERVICE_SPECS_DESIGN.md` (or a
`data/_registry_coverage.md`): `service | identified(product_code)? | charge_types done? |
tested? | source URLs`. Mark each service done only when all three are true.

## Report back (per batch)
List the services added this batch, their product codes, GCP targets + the AWS/GCP source
URLs you used, the tests added, and any service whose GCP mapping you could not do without a
fabricated ratio (left as labeled passthrough).

## Status (starting point)
Already in the registry (~43): ec2, rds, s3, datatransfer, opensearch, redshift, elasticache,
msk, emr, athena, glue, kinesis, quicksight, shield, dynamodb, efs, fsx, guardduty,
securityhub, inspector, xray, cloudwatch, mwaa, route53, eks, lambda, elb, sqs, sns,
apigateway, directconnect, globalaccelerator, waf, memorydb, documentdb, cognito, ses,
sagemaker, dms, backup, cloudtrail, ssm, vpc. EXTEND to full coverage per the priority order.
