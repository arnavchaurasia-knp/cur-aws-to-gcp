# WORK ORDER — Tactical: fix 3 mispriced charge-types (WAF requests, S3 requests, Aurora I/O)

**For an implementing AI / engineer with no prior context. Read fully.** This is the
**surgical** companion to the strategic `WORKORDER_generic_mechanic_overclaim.md`. Goal:
stop three specific, high-dollar mispricings that are live right now, without waiting for
the registry refactor. Each fix must be verified against a real job DB, not reasoned from
the spec.

## Evidence (job `8ded6e7d-9c3e-40da-9d0d-e430b9e3a26c`, prospect "op")

Measured from its `projection-audit/projection.duckdb`:

| Charge-type | AWS | GCP (projected) | mechanic_group / rule | What it did wrong |
|---|---|---|---|---|
| Aurora `ServerlessV2IOOptimize` I/O | $6,783 × ~12 rows (~$81K) | ~$400/row (~$5K) | `managed_db` / `managed_db#10` | Priced the **I/O-operations** charge as **ACU compute-hours** (note literally reads "Aurora Serverless v2 ACU-hrs → Cloud SQL"). |
| WAF `RequestV2-Tier2`, `WebACLV2` | $525.6, $61.9 | **$0.00** | `per_request` / `per_request#33` | Mapped to Networking SKU `0A6D-B3AF-D81E` at rate **7.5e-07** → zero. |
| S3 `APS3-Requests-Tier2` | $2,705.8 (and $291.8) | **$3.5** (and $6.8) | `per_request` / `per_request#17` | Mapped to GCS Class B op SKU; AWS $/1,000 vs GCS $/10,000 unit model → ~770× under. |

Root mechanism (see strategic work order for the full story): the generic `per_request`
and `managed_db` mechanic rules in `classify_mechanics.py` claim these rows **before** any
service-specific handler, and because both groups are in `DETERMINISTIC_GROUPS`, the
`service_map.json` corrections (`waf → Cloud Armor`, etc.) are **skipped as "already
priced"** in `service_classifier.py`. So the dedicated passthrough logic that already
exists is never reached.

## The fixes (each independent; ship in this order)

### FIX 1 — Aurora/RDS per-I/O-request rows must not be priced as compute
The passthrough/ignore logic already exists (`apply_static_mappings.py` ~line 3637:
"Aurora/RDS per-I/O request fee — Cloud SQL storage pricing includes I/O; no separate
per-I/O charge on GCP"). The bug is **routing**: `ServerlessV2IOOptimize` / `IOOp` /
`StorageIOUsage` rows reach `managed_db` and get the ACU-hours treatment instead.
- In `classify_mechanics.py`, **before** the `managed_db` rule, add a guard that routes
  RDS/Aurora rows whose `usage_type`/`operation` matches `I/?O.?Op|ServerlessV2IOOp|
  StorageIOUsage|IO-?Optimized.*(Request|Usage)|per.?IO` to the dedicated I/O handler
  (strategy that yields the "no separate I/O charge → passthrough-at-cost OR ignore"
  outcome — match whatever `apply_static_mappings.py`'s per-I/O branch already produces).
- Do NOT catch Aurora **storage** (`IO-OptimizedStorage`, GB-Mo) with this — that is a
  real storage charge and already prices correctly (`block_storage`, ×0.52 observed).
  Scope the regex to the **request/operation** unit, not GB-Mo.
- Decision to confirm with the maintainer: passthrough-at-AWS-cost vs ignore($0). Current
  code comment implies "no separate charge on GCP" → leaning **ignore**, but $81K of I/O
  silently vanishing flatters GCP. Recommend **passthrough at AWS cost** with a loud note,
  so the number is visible, not hidden. Flag this explicitly in the PR.

### FIX 2 — WAF per-request rows → Cloud Armor passthrough, not a $0 SKU
- In `classify_mechanics.py`, the WAF WebACL/Rule branch already exists (~line 704). Extend
  it (or add a sibling guard **before** the generic `per_request` rule at ~line 729) so WAF
  rows (`product ILIKE '%waf%'` OR `canonical_service='waf'`) with request units
  (`RequestV2`, `Requests`, `Captcha`, `Challenge`) route to a WAF handler, NOT generic
  `per_request`.
- Target outcome: `strategy='passthrough'`, `gcp_service='Cloud Armor'`, carry AWS cost
  (the `service_map.json` `waf` rule's intent). Cloud Armor request pricing ($0.60/M) is
  component-based; until a per-component pricer exists, passthrough at AWS cost is the
  honest number — never a 7.5e-07 SKU that zeroes it.
- Also fix the **deterministic-immunity** blocker so service_map can correct it even if the
  row still lands in `per_request`: see strategic work order FIX-B (the `service_classifier`
  review-skip). Either routing OR the immunity fix closes this; do both for defense in depth.

### FIX 3 — S3 request rows → passthrough at cost parity, not GCS Class A/B SKU
- The passthrough logic exists (`apply_static_mappings.py` ~line 3107–3172: "S3 per-request
  charge — tier unknown; passthrough at cost parity"). Again the bug is routing: the row is
  claimed by generic `per_request#17` and mapped to a GCS ops SKU with an incompatible unit
  model ($/1,000 vs $/10,000).
- In `classify_mechanics.py`, before generic `per_request`, route S3 request rows
  (`canonical_service='s3'` AND `usage_type ILIKE '%Requests-Tier%'`) to the S3 request
  passthrough handler. Keep S3 **storage** (`TimedStorage`) and **egress**
  (`DataTransfer-Out`) on their current correct paths — they price fine (×0.86, ×0.82).
- Rationale is already in-repo and in AGENTS.md ("S3 requests → PASS; unit models
  incompatible, $/1k vs $/10k class ops") — this just restores that path.

## Acceptance (verify on the job DB, not in the abstract)
Re-run the pipeline on job `8ded6e7d`'s `input.csv` (or a fixture carved from it) and assert
in `projection.duckdb`:
1. Aurora `ServerlessV2IOOp` rows: `strategy != 'map'` to a compute SKU; projection is
   passthrough-at-cost (or ignore, per the decision), and projection_note names the I/O
   treatment — **not** "ACU-hrs".
2. WAF `RequestV2`/`WebACL` rows with `aws_amortized_cost > 0`: `gcp_projected_cost > 0`,
   `gcp_service='Cloud Armor'`, `strategy='passthrough'`. No row with cost > $50 projects $0.
3. S3 `Requests-Tier2` rows: projected cost within a sane band of AWS cost (passthrough →
   ≈ parity), not <1% of it.
4. No regression: S3 storage/egress, RDS compute+storage, EC2 ratios unchanged.
5. Add targeted unit tests in `tests/test_classify_mechanics.py` for each of the three
   routing guards (row in → expected mechanic_group out), each asserting it does NOT fall
   into `per_request`/`managed_db`.

## Guardrail
These are routing guards added ahead of greedy rules — exactly the kind of accretion the
strategic registry work removes. Mark each new guard in a comment with
`# TACTICAL: migrate into service_specs.json <svc> charge_type (see WORKORDER_generic_mechanic_overclaim.md)`
so they are found and folded in when the registry lands, not left as permanent cruft.

## Files
- `scripts/classify_mechanics.py` — three routing guards before `managed_db` / `per_request`.
- `scripts/service_classifier.py` — (shared with strategic FIX-B) stop deterministic-immunity from skipping service_map corrections on these rows.
- `scripts/apply_static_mappings.py` — verify the existing Aurora-I/O and S3-request branches produce the intended outcome once rows actually reach them.
- `tests/test_classify_mechanics.py` — routing tests.

## Out of scope
- The catalog staleness (only 75 SKUs; refresh failing) that makes nearest-SKU resolution
  pick garbage rates like 7.5e-07 — tracked separately; note it in the PR so the reviewer
  knows some residual rate error is catalog-driven, not routing-driven.
