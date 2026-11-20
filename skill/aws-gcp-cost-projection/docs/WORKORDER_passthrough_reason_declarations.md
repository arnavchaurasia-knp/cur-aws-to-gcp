# WORKORDER — declare passthrough_reason on the 54 undeclared passthroughs

Status: COMPLETE · Author: Claude (Opus 4.8) / AGY · 2026-11-20 · dollar-safe (declaration-only).
Origin: LOW-sweep audit 2026-11-19. Related: [[project_passthrough_reason_defect_default]], [[project_passthrough_guard_tier_redesign]].

## Problem
An inline passthrough dict in `map_*` without an explicit `passthrough_reason` defaults to `'unclassified'`,
which counts against the **5% defect_passthrough_budget_pct** gate (`data/passthrough_reasons.json`). An accurate
scan of `scripts/apply_static_mappings.py` (enclosing-dict + tail check) finds **54** `"strategy": "passthrough"`
entries with no reason declared — all latent `unclassified` defect-budget consumers. The gate only fails when the
*spend* on such rows exceeds 5%, so this is dollar-safe but should be cleaned so the budget is a real signal.

Fixed already this session: `map_ssm_parameter_store` → `unresolved_sku` (Secret Manager missing from catalog).

## Implementation Summary (2026-11-20)
All 54 undeclared passthroughs in `scripts/apply_static_mappings.py` were audited and classified:
1. **48 passthrough dicts** were given explicit `passthrough_reason` declarations from the closed enum in `data/passthrough_reasons.json`:
   - `no_gcp_equivalent`: S3 fees without GCS equivalent, VPC IPAM, Cloud CDN, Marketplace third-party, QuickSight Q & Reader, Redshift Concurrency Scaling & backup, Athena, FSx ONTAP capacity pool requests, FSx Intelligent-Tiering cold storage, RDS Db2, Backup Audit/Search/Malware.
   - `service_policy_review`: GWLB / GWLBE architecture reviews, LCU pending architecture input, Non-workload / support / marketplace subscriptions, Shield Advanced data transfer & line items, Redshift Serverless & provisioned node-hour sizing reviews, EFS cold storage, Lambda unhandled / concurrency review, DocumentDB review, SageMaker Feature Store & unhandled sub-types.
   - `unresolved_sku`: Flat hourly charge without SKU match, FSx inter-AZ data transfer, Burstable EC2 size not in table, ARM EC2 size/family not in formula tables, Windows EC2 instance type not in table, Local SSD VM attachment unknown capacity.
   - `unit_model_mismatch`: AWS Backup creation fee, GuardDuty per-GB vs per-asset, Inspector per-instance vs per-asset, Kinesis shard-hours vs throughput.
   - `no_rate_in_region`: EBS Extreme IOPS / throughput rates unreachable in region, FSx backup rate unreachable in region, FSx Windows rate unreachable in region.
   - `accelerator_manual_sizing`: Accelerator without GCP GPU equivalent, SageMaker Inferentia/Trainium.
   - `family_not_available_in_region`: Oracle Database@Google Cloud not offered in region.
2. Two legacy entries carrying invalid `"unsupported_instance_family"` in `map_elasticache` and `map_msk` were updated to valid `"unresolved_sku"`.
3. **6 genuinely ambiguous / detail-less fallback rows** were retained as undeclared (defaulting to `'unclassified'`), guarded by an approved allowlist:
   - `map_block_storage`: RDS instance row in block_storage with blank pricing_unit.
   - `map_data_transfer`: NAT Gateway in PDF bill with blank usage_type.
   - `map_quicksight`: QuickSight unknown row not Author/Reader/Q.
   - `map_redshift`: Redshift unrecognized row.
   - `map_compute_arm`: ARM EC2 usage_type unparseable exception.
   - `map_db_row`: Database engine could not be determined from CUR.
4. Added guardrail test `test_apply_static_mappings_undeclared_passthrough_guardrail` in `tests/test_passthrough_reason.py` ensuring undeclared passthrough dicts stay `<= len(APPROVED_UNDECLARED_ALLOWLIST)` and all declared reasons belong to `VALID_REASONS`.

## Task
For EACH of the remaining ~53 passthroughs, add the correct `passthrough_reason` from the closed enum
(`data/passthrough_reasons.json → reasons`), chosen from the row's own `projection_note`/context. This is per-row
judgement — do NOT automate note→reason, and do NOT guess: when a note is genuinely ambiguous, leave it
`unclassified` (the honest "undeclared" signal) rather than invent a reason.

Reason-picking guide (from the note text):
- "no GCP/GCS equivalent", "no comparable service", permanent static decision → `no_gcp_equivalent`
- target named but no catalog SKU / no rate resolved ("missing from catalog", "no SKU found") → `unresolved_sku`
- "no rate reachable in region/global" → `no_rate_in_region`
- units incompatible / "can't convert", "$/1k vs $/10k" → `unit_model_mismatch`
- service_map declares mode='review' → `service_policy_review`
- accelerator/GPU needs manual sizing → `accelerator_manual_sizing`
- family unavailable in region (ARM/x86 boundary) → `family_not_available_in_region`
- genuinely unparseable row / true fallback with no basis → leave `unclassified`

Entry line numbers from the 2026-11-19 scan (verify before editing — line numbers drift):
2451, 2514, 2566, 2839, 2957, 3071, 3089, 3524, 4083, 4265, 4296, 4840, 4916, 5134, 5223, 5284, 5345, 5931,
5963, 6019, 6065, 6079, 7050, 7069, 7127, 7192, 7234, 7249, 7878, 7914, 8040, 8115, 8167, 8213, 8268, 8364,
8668, 9319, 9454, 9474, 9798, 10405, 10459, 10737, 10811, 10868, 10889, 11041, 11065, 11087, 11232, 11247, 11262.

Examples already classified in the audit: L2514 "no GCS equivalent" → `no_gcp_equivalent`; L8364 FSx-Windows→NetApp
"passthrough at cost parity" (no rate) → `no_rate_in_region`; L10868 DocumentDB→Firestore "(review)" →
`service_policy_review`; L9454 "ARM EC2 usage_type not parseable" → `unresolved_sku` (or leave unclassified).

## Tests + verify
- A guardrail test that counts `"strategy": "passthrough"` entries lacking a reason and asserts it stays ≤ (a small
  allowlist of genuinely-ambiguous ones), so new undeclared passthroughs are caught.
- Full suite stays green (no dollar change — reason is metadata). Rescan to confirm the count dropped.
