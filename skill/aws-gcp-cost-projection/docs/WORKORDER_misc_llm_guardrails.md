# WORK ORDER — Consolidate LLM guardrails into one source + spec-driven misc guidance

**For an implementing AI with NO prior conversation context. Read this whole file first.**
Do exactly these steps. Do not improvise scope. When done, stop and report; a reviewer will check.

---

## 0. Context you need (read these first, do not edit them yet)

This repo projects an AWS bill onto GCP cost. Pipeline Phase 2 classifies each AWS
line item into a `mechanic_group` (which pricing engine handles it), then prices it.
A per-service registry is replacing the old flat classifier.

- `skill/aws-gcp-cost-projection/data/service_specs.json` — the per-service
  charge-type registry (43 services). Each service → `charge_types[]`; each
  charge_type has `match`, `mechanic`, `fields[]`, `source_rule`. READ its `_meta`.
- `skill/aws-gcp-cost-projection/scripts/classify_mechanics.py` — the legacy
  classifier. Function **`_misc_reason(row)`** (around line 1196) generates the
  per-row LLM guidance for rows that land in `mechanic_group='misc'`. THIS IS THE
  BEHAVIOR YOU ARE PORTING. Read it fully. Do NOT modify this file — it is the
  oracle the tests compare against.
- `skill/aws-gcp-cost-projection/scripts/spec_dispatch.py` — the spec engine:
  `dispatch(row)` returns `{service, charge_type, mechanic, fields, field_state}`
  or None. Read `classify_via_specs()` too.
- `skill/aws-gcp-cost-projection/scripts/config_loader.py` — `load_data_config(name)`
  loads `data/<name>.json`. Use it for any new data file.
- `skill/aws-gcp-cost-projection/data/service-classification.json` — has
  `passthrough_services`, `self_host_on_gce`, `sizing_sensitive`, `misc_service_hints`.
  `_misc_reason` reads these; your builder will too.
- Tests live in `skill/aws-gcp-cost-projection/tests/`. Run with:
  `SKILL_DIR="$(pwd)" python -m pytest tests/<file> -q` from the skill dir.

**Environment caveats (important):**
- Python is 3.14. **Inline regex flags mid-pattern (`(?i)`) are a syntax error.**
  Pass `re.IGNORECASE` to `re.search` instead; keep patterns flag-free.
- All scripts run with `SKILL_DIR` env var pointing at `skill/aws-gcp-cost-projection`.
  `data/` is resolved relative to `SKILL_DIR`.
- Do not touch: `classify_mechanics.py`, `merge_mappings.py`, `validate_fix.py`
  (these are the oracle / the enforcement gates — leave them as-is).

---

## 1. Create the SINGLE guardrail source: `data/llm_guardrails.json`

This is the one place universal LLM mapping rules live. Create the file EXACTLY:

```json
{
  "_meta": {
    "purpose": "Single source of truth for universal LLM mapping guardrails. Every LLM mapping/misc prompt is assembled from `universal` below plus the row's per-charge-type llm_guidance in service_specs.json. Edit guardrails HERE only. Hard enforcement of these rules stays in merge_mappings.py (phantom-SKU filter) and validate_fix.py (echo/null/passthrough gates) — those are the backstop and are intentionally separate.",
    "version": 1
  },
  "universal": [
    "Never fabricate a GCP SKU ID. Use only IDs returned by scripts/find-sku.sh. Invented IDs are stripped downstream and the row will fail its gate.",
    "Never alter, recompute, round, or 'correct' any aws_* source value (aws_amortized_cost, total_usage, usage_type, operation, region). You map; you never change the bill.",
    "When you cannot derive a fair GCP price, set strategy='passthrough' at the AWS cost — EXACTLY ONE row, break_down=false. Never split a passthrough into core/ram/components (N components = N x cost).",
    "passthrough is never silent: in projection_note state the row is carried at AWS cost and is NOT a real GCP estimate.",
    "Every mapped row's projection_note must cite the mechanism or assumption used.",
    "Set mapping_confidence honestly. If the row's guidance gives confidence_max, do not exceed it.",
    "Do not use web search. Do not query projection.duckdb directly. SKU lookups go through scripts/find-sku.sh only.",
    "Never map an accelerator / GPU / specialized-silicon row (Inferentia, Trainium, p/g/vt families) to a general-purpose CPU VM — that hides the architecture change. Passthrough + manual review instead.",
    "Never invent instance counts, replication factors, disk sizes, node counts, or any quantity not present in the row or its extracted footprint."
  ]
}
```

---

## 2. Add `llm_guidance` to each misc/review charge_type in `service_specs.json`

For the charge_types listed below ONLY, add an `llm_guidance` object. Do not change
`match`, `mechanic`, `fields`, or `source_rule`. Keep JSON valid.

Shape of `llm_guidance`:
```json
"llm_guidance": {
  "gcp_target": "<GCP service name or '(no GCP equivalent)'>",
  "strategy": "<map | passthrough | review>",
  "confidence_max": <float 0..1>,
  "formula": "<optional conversion formula string>",
  "guidance": "<one or two sentences of service-specific instruction>",
  "extra_guardrails": ["<optional service-specific rule>", "..."]
}
```

Port these values faithfully from `_misc_reason` / `service-classification.json`:

| service.charge_type | gcp_target | strategy | confidence_max | formula / guidance |
|---|---|---|---|---|
| `cognito.cognito_all` | Identity Platform | map | 0.45 | formula: `MAU ≈ total_usage ÷ 20`; guidance: CUR bills auth requests, not MAU — estimate MAU and document the assumption. |
| `ses.ses_all` | (no GCP equivalent) | passthrough | 0.3 | guidance: no GCP managed email-send equivalent; passthrough at cost parity. |
| `sagemaker.sagemaker_all` | Vertex AI | review | 0.4 | guidance: service_map forces Vertex AI; training/inference/notebook/volume all route there; do not size a CPU VM. |
| `dms.dms_all` | Database Migration Service | review | 0.4 | guidance: GCP Database Migration Service is free for homogeneous migrations; passthrough replication-instance hours, flag for review. |
| `backup.backup_other` | (no GCP equivalent) | passthrough | 0.3 | guidance: non-storage backup charges have no clean GCP equivalent; passthrough. |
| `cloudtrail.cloudtrail_other` | Cloud Logging | review | 0.4 | guidance: event→log-volume conversion has no reliable basis; passthrough until a sizing model exists. |
| `ssm.ssm_other` | (no GCP equivalent) | passthrough | 0.3 | guidance: SSM Automation/Session/Patch/Inventory have no GCP equivalent; passthrough. |
| `vpc.vpc_other` | Private Service Connect | review | 0.4 | guidance: VPC endpoints/PrivateLink → PSC; passthrough until a PSC pricer exists. |

(The cross-cutting cases — accelerator, self-host-on-GCE, data-gap — are NOT per
service; the builder in Step 3 handles them dynamically. Do not add them here.)

**Validate after editing:**
`python -c "import json; json.load(open('data/service_specs.json',encoding='utf-8')); print('ok')"`

---

## 3. Create the guidance builder: `scripts/llm_guidance.py`

This is the spec-driven replacement for `_misc_reason`. It assembles the full
per-row guidance the LLM receives, from: universal guardrails (Step 1) + the row's
per-charge-type `llm_guidance` (Step 2) + dynamic cross-cutting detectors (ported
from `_misc_reason`).

Required public function:
```python
def build_guidance(row: dict, dispatch_result: dict | None) -> dict:
    """Return the structured guidance dict for ONE row.
    row: same keys spec_dispatch/classify_mechanics use (product, usage_type,
         operation, unit, instance_type, instance_vcpus, instance_ram_gb,
         instance_count, aws_amortized_cost, canonical, product_code, ...).
    dispatch_result: output of spec_dispatch.dispatch(row), or None.
    """
```

Logic, in this order (mirror `_misc_reason` precedence):
1. Load `universal` from `data/llm_guardrails.json` via config_loader. Every
   returned dict MUST include `"guardrails": [...universal..., ...extra...]`.
2. **Dynamic detectors first (cross-cutting, not per-service):**
   a. **Accelerator** — reuse `classify_mechanics._is_accelerator(row)`. If true:
      strategy=passthrough, manual_review=true, confidence_max=0.3, guidance = the
      accelerator text from `_misc_reason`, gcp_target="Accelerator / specialized silicon".
   b. **Self-host-on-GCE** — if a fragment from `service-classification.json`
      `self_host_on_gce` matches product/usage_type: strategy=break_down (if
      `instance_vcpus` present) else map; include `extracted_footprint`
      (instance_type/vcpus/ram_gb/count); guidance = the self-host text from
      `_misc_reason` ("do NOT invent a replication factor or disk size").
   c. **Data-gap** — service identity known (`row['canonical']`) but unit AND
      operation blank and usage_type empty/ends ':na': strategy=passthrough,
      confidence_max=0.3, data_gap=true, guidance = the data-gap text.
3. **Per-charge-type guidance:** if `dispatch_result` is not None and its spec
   charge_type has `llm_guidance`, merge those fields (gcp_target/strategy/
   confidence_max/formula/guidance) and append any `extra_guardrails` to guardrails.
4. **Fallback:** if none of the above apply, use the generic unknown-service
   guidance from `_misc_reason` (passthrough if aws_amortized_cost < 10 else map,
   confidence_max 0.6).

Return a JSON-serializable dict with at least:
`{why, gcp_target, strategy, confidence_max, guidance, guardrails, ...optional
(formula, manual_review, data_gap, extracted_footprint)}`.

Imports: `config_loader`, `classify_mechanics` (for `_is_accelerator` only),
`spec_dispatch` (optional). Add the `sys.path.insert(0, scripts_dir)` preamble the
other scripts use. Keep all regex flag-free (pass re.IGNORECASE).

Add a `main()` so `python scripts/llm_guidance.py` prints guidance for a couple of
built-in sample rows (for manual sanity).

---

## 4. Create tests: `tests/test_llm_guidance.py`

Prove the builder is faithful and the guardrails are always present. Cases:

1. **Universal guardrails present on EVERY output** — for a spread of rows
   (accelerator, self-host, data-gap, cognito, ses, sagemaker, dms, unknown),
   assert every item in `llm_guardrails.json.universal` appears in the result's
   `guardrails` list.
2. **Every misc/review charge_type in service_specs.json has `llm_guidance`** —
   load service_specs.json; for each service, for each charge_type whose
   `mechanic` in ('misc',) OR that is listed in Step 2, assert `llm_guidance`
   exists with non-empty `gcp_target`, `strategy`, and `confidence_max`.
3. **Dynamic parity with `_misc_reason`** — for an accelerator row, a self-host
   row (use a fragment from service-classification.json self_host_on_gce), and a
   data-gap row: assert `build_guidance` sets the same `strategy` and
   `recommended_strategy`/`manual_review`/`data_gap` flags that
   `json.loads(classify_mechanics._misc_reason(row))` sets.
4. **Confidence cap respected** — for cognito, assert `confidence_max == 0.45`.

Make the file runnable both as `pytest` and as `python tests/test_llm_guidance.py`
(print a pass/fail table), matching the style of `tests/test_spec_dispatch.py`.

---

## 5. Run and acceptance criteria

From `skill/aws-gcp-cost-projection/`:
```
SKILL_DIR="$(pwd)" python -m pytest tests/test_llm_guidance.py tests/test_spec_dispatch.py -q
SKILL_DIR="$(pwd)" python scripts/llm_guidance.py
python -c "import json; json.load(open('data/service_specs.json',encoding='utf-8')); json.load(open('data/llm_guardrails.json',encoding='utf-8')); print('json ok')"
```

ACCEPT only if:
- New tests pass; `test_spec_dispatch.py` still passes (you changed only data +
  new files, so it must).
- `service_specs.json` and `llm_guardrails.json` are valid JSON.
- You did NOT modify `classify_mechanics.py`, `merge_mappings.py`, `validate_fix.py`,
  `spec_dispatch.py`.
- No file consumes these at runtime yet (this is foundation; wiring into the Phase 2
  prompt is a SEPARATE later work order — do not attempt it here).

## 6. Report back
List: files created, charge_types that got `llm_guidance`, test counts, and any
place where `_misc_reason` behavior could not be reproduced exactly (with the reason).
Do not expand scope beyond the steps above.

---

## ADDENDUM (review 2026-10-11) — close gaps + retire the broken `_misc_reason`

Context: a review found that `classify_mechanics._misc_reason` (the code this replaces)
is **substantially broken** — every tailored branch is dead code:
- `fragment == "DynamoDB"` / `== "Cognito"` never match: the hint `key`s are lowercase
  (`"dynamodb"`), and there is no `cognito` key at all.
- `fragment in _PASSTHROUGH_SERVICES` ({"Simple Email","SES","Inferentia","Trainium"})
  and `fragment in _SIZING_SENSITIVE` ({"EKS","ECS","Glue"}) are always False (lowercase
  keys vs capitalized sets).
- the hint tuple is unpacked as `(fragment, service_label, _, missing)` but `missing`
  is `extra_fields` (almost always `{}`), and the real `fields` dict is discarded — so
  guidance renders `"sizing fields ({}) are absent"`.
The spec-driven replacement AVOIDS all of this by construction (it reads `llm_guidance`
from `service_specs.json`, keyed by lowercase canonical). The steps below (1) prove that
with tests, (2) close the one real coverage gap, (3) harden the shared accelerator
detector, and (4) retire the dead code.

### ADD-1 — Close the DynamoDB throughput (RCU/WCU) gap
Today a DynamoDB throughput row (ReadRequestUnits/WriteRequestUnits/CapacityUnit) is
excluded from per_request and is not storage, so it dispatches to NONE → `build_guidance`
gives GENERIC fallback, losing the Firestore/Bigtable/Spanner decision rule that
`_misc_reason` intended. Fix it spec-driven:
- In `data/service_specs.json`, add a `dynamodb_throughput` charge_type to the `dynamodb`
  service, placed BEFORE `dynamodb_storage`:
  `match: {"any_of":[{"usage_type_re":"ReadRequestUnits|WriteRequestUnits|ReadCapacityUnit|WriteCapacityUnit"},{"unit_in":["rcu","wcu"]}]}`,
  `mechanic: "misc"`, and an `llm_guidance` block with
  `gcp_target: "Firestore / Bigtable / Spanner"`, `strategy: "map"`, `confidence_max: 0.5`,
  and `guidance` = the decision rule (GetItem/PutItem/Query/Scan → Firestore; BatchWrite/
  high-volume → Bigtable; transactional/ACID → Spanner; 1 RCU≈1 doc read, 1 WCU≈1 doc write;
  rcu/wcu/table_mode NOT in CUR, infer from total_usage).
- Parity: a DynamoDB RCU row still classifies to `misc` (dispatch now returns
  `misc`/`dynamodb_throughput` instead of None → fallback), so
  `classify_via_specs == classify` is unchanged. UPDATE the existing
  `tests/test_spec_dispatch.py::dynamodb_wcu_fallback` case: expected group stays `misc`,
  expected charge_type becomes `"dynamodb_throughput"` (was None).

### ADD-2 — Tests that assert each tailored branch ACTUALLY FIRES
(The dead `_misc_reason` branches survived because nothing tested that they produced
their specific text.) In `tests/test_llm_guidance.py`, assert the guidance TEXT/fields,
not just presence:
- cognito row → guidance contains "MAU" and the "÷ 20" (or "/ 20") estimate; confidence_max==0.45.
- dynamodb RCU row → guidance contains "Firestore", "Bigtable", "Spanner".
- ses row → strategy=="passthrough", gcp_target contains "no GCP equivalent".
- accelerator row → strategy=="passthrough", guidance contains "N2D" and "manual review".
- self-host row (an OpenSearch/MSK fragment) → guidance contains "do NOT invent".
- data-gap row → data_gap==True, missing_fields non-empty.
- every result → UNIVERSAL_GUARDRAILS all present in `guardrails`.

### ADD-3 — Harden the shared accelerator detector (benefits classifier too)
`build_guidance` reuses `classify_mechanics._is_accelerator`, which:
- checks only `instance_type` + `operation`, NOT `usage_type` (the primary CUR
  instance-type carrier, e.g. `BoxUsage:inf2.48xlarge`), so it misses accelerators when
  the `instance_type` column isn't populated; and
- uses an unanchored regex (`(inf\d|…|g[3-6]|…)[a-z0-9]*\.`) unlike the now-tightened
  `_has_instance_type`.
Fix in `classify_mechanics._is_accelerator`: include `usage_type` in `txt`. This also
touches classifier routing (compute_windows/compute_arm exclusions use it), so it is NOT
output-neutral by assumption — RUN `tests/test_spec_dispatch.py` +
`tests/test_classify_mechanics.py` after and confirm green; investigate any diff. If a
diff appears, keep the fix but update the affected test with the corrected expectation
(the new behavior is the correct one). Add a unit test: an inf2 row with
`instance_type=None` and `usage_type="APS3-BoxUsage:inf2.48xlarge"` → `_is_accelerator` True.

### ADD-4 — Retire the dead `_misc_reason` (ONLY after build_guidance is wired in)
`build_guidance` is not yet called by the pipeline (foundation only). Do NOT delete
`_misc_reason` until a separate wiring step routes Phase-2 misc rows through
`llm_guidance.build_guidance`. When that lands: delete `_misc_reason` and its dead
hint-loop (the `_MISC_SERVICE_HINTS` / `_PASSTHROUGH_SERVICES` / `_SIZING_SENSITIVE`
branches), and confirm no caller remains (`grep -rn _misc_reason`). Until then, leave
`_misc_reason` in place but add a one-line comment above it:
`# NOTE: tailored branches are dead (lowercase-key mismatch) — superseded by llm_guidance.build_guidance; retire when wired.`

### Acceptance for ADDENDUM
- ADD-1: dynamodb_throughput charge_type added; `test_spec_dispatch.py` still passes
  (updated case); a DynamoDB RCU row's guidance contains Firestore/Bigtable/Spanner.
- ADD-2: text-asserting tests pass and would fail if a branch were dead.
- ADD-3: `_is_accelerator` includes usage_type; both classifier test suites green
  (or diffs explained + tests updated); new inf2-usage_type unit test passes.
- ADD-4: comment added; actual deletion deferred to the wiring step.
