# WORKORDER — declare passthrough_reason on the 54 undeclared passthroughs

Status: READY FOR AGY (careful, per-row) · Author: Claude (Opus 4.8) · 2026-11-19 · dollar-safe (declaration-only).
Origin: LOW-sweep audit 2026-11-19. Related: [[project_passthrough_reason_defect_default]], [[project_passthrough_guard_tier_redesign]].

## Problem
An inline passthrough dict in `map_*` without an explicit `passthrough_reason` defaults to `'unclassified'`,
which counts against the **5% defect_passthrough_budget_pct** gate (`data/passthrough_reasons.json`). An accurate
scan of `scripts/apply_static_mappings.py` (enclosing-dict + tail check) finds **54** `"strategy": "passthrough"`
entries with no reason declared — all latent `unclassified` defect-budget consumers. The gate only fails when the
*spend* on such rows exceeds 5%, so this is dollar-safe but should be cleaned so the budget is a real signal.

Fixed already this session: `map_ssm_parameter_store` → `unresolved_sku` (Secret Manager missing from catalog).

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
