# WORK ORDER — ID-keyed routing registry (identity ≠ pricing), for every service × charge-type

**For an implementing AI / engineer with no prior context. Read fully.**

This supersedes the "narrow the greedy rules" framing in
`WORKORDER_generic_mechanic_overclaim.md` and completes the Layer-1 rewire described in
`project_layer1_product_code` / `SERVICE_SPECS_DESIGN.md`. It is the **structural** fix for
an entire class of bug, not a per-service patch.

## The core principle: two separate jobs, never mixed

1. **ROUTING / IDENTITY** — "What *is* this bill row?" → `(service, charge_type)`.
   A pure, deterministic **lookup keyed on stable AWS identifiers**. It has **no pricing
   opinion** — it does not decide map vs passthrough vs ignore.
2. **MAPPING / PRICING** — "Given it's `waf · requests`, is it mappable to a GCP SKU, or
   passthrough-at-cost, or ignore?" This stays entirely downstream, unchanged in
   responsibility.

Today these are tangled: the router decides the charge-type **by matching words** in the
product/usage_type name, and the word it matches often implies a pricer, so routing and
pricing are fused in a regex. That is the defect.

## Why the current router is wrong (confirmed in code)

`classify_mechanics.py` already resolves **service identity from the AWS id**:
line 1572 computes `canonical_service(product, product_code)` (product_code-first, Layer 1).
**But the ~178 charge-type rules ignore it** and match on raw `usage_type` / `product`
**words** (e.g. `per_request` fires on anything matching `/Requests|Invocations/`;
`managed_db` grabs all RDS/Aurora). So:

- The id tells us the service, then we throw it away and guess the charge-type from a word.
- Shared words collide across services: `AWSWAF ...RequestV2` and `AmazonS3
  ...Requests-Tier2` both contain "Request" → both dumped in the `per_request` bin → both
  mispriced (WAF $525→$0, S3 $2,705→$3.5, measured in job `8ded6e7d`).

The fix: **route by the AWS id, not the word.** `(product_code, usage_type/operation token)`
is a unique key; `AWSWAF+RequestV2` and `AmazonS3+Requests-Tier2` are different keys and can
never collide.

## The routing registry

### Shape
A declarative table (extend `data/service_specs.json`, or a dedicated
`data/routing_registry.json` that service_specs references) keyed on stable AWS identity:

```
service (canonical, from product_code)
  └── charge_type
        key:        matched on AWS ids, NOT free-text names
          product_code   : e.g. "AmazonEC2", "AWSWAF", "AmazonS3"   (authoritative service)
          usage_type_token: the region/AZ-stripped core token, e.g. "RequestV2-Tier2",
                            "Requests-Tier2", "BoxUsage", "ServerlessV2IOOp", "TimedStorage-ByteHrs"
          operation       : e.g. "RunInstances", "CreateDBInstance"  (when usage_type is ambiguous/blank)
        mechanic:   which shared PRICING PRIMITIVE this charge-type uses
                    (compute_breakdown | block_storage | data_transfer | per_request |
                     managed_db | flat_hourly | object_storage | ...). This names the math,
                    it is NOT a routing word.
        identity_only: true   # this table never sets strategy/target
```

The `mechanic` field here selects the pricing primitive *for this specific charge-type of
this specific service* — chosen explicitly, per charge-type, not by global regex order.
Whether the resulting price is a real SKU, a passthrough, or an ignore is decided **later**,
by the mapping layer, exactly as today.

### Key normalization (the one subtlety)
AWS `lineItem/UsageType` carries a region/AZ prefix: `APS3-`, `USE1-`, `EUW1-`, `APS5-`, etc.
Strip it to a stable token before lookup (there is already prefix-stripping logic in the
repo — reuse `aws_normalizer`; the global-audit script also has a reference regex:
`^(APS\d|USE\d|USW\d|EU\w?\d|...)-`). So `APS3-Requests-Tier2` and `USE1-Requests-Tier2`
normalize to the same token `Requests-Tier2` and hit one registry row. Keep the match on the
**token**, plus `product_code`, plus (optional) `operation` for disambiguation — never a
substring-of-name.

### Lookup algorithm (replaces the 178-rule chain)
For each row:
1. `service = canonical_service(product, product_code)` (already exists).
2. `token = normalize_usage_type(usage_type)`.
3. Look up `registry[service][charge_type where key matches (product_code, token, operation)]`.
4. Found → emit `(service, charge_type, mechanic)`. Deterministic, order-independent
   *within* a service (no cross-service ordering fragility).
5. **Not found → do NOT fall into a catch-all bin. Flag it** (see "Unknowns" below).

## This design is uniform — every service, every charge-type

The same mechanism covers everything; there are no special cases and no greedy catch-alls:
- `ec2 · compute` (`BoxUsage`/`RunInstances`) → `compute_breakdown`
- `ec2 · ebs_storage` (`EBS:VolumeUsage*`, GB-Mo) → `block_storage`
- `ec2 · data_transfer` (`DataTransfer-Out-Bytes`) → `data_transfer`
- `s3 · storage` (`TimedStorage-ByteHrs`) → `object_storage`
- `s3 · requests` (`Requests-Tier1/2`) → `per_request`
- `waf · requests` (`RequestV2-*`) → `per_request`  (now distinct from s3·requests — different key)
- `waf · acl_rule` (`WebACLV2`, `RuleV2`) → `flat_hourly`
- `rds · compute` (instance/ACU hours) → `managed_db`
- `rds · io` (`ServerlessV2IOOp`, `StorageIOUsage`) → its own charge_type (NOT managed_db compute)
- `rds · storage` (GB-Mo) → `block_storage`
… and so on for every service. The mechanics (`per_request`, `managed_db`, …) survive **only
as shared pricing primitives**, selected by a registry entry — never as first-match-wins
classifiers of raw text.

## Unknowns must be LOUD, never silent (this is what ends manual patching)
When `(product_code, token)` has no registry entry:
- Mark the row `charge_type = 'unrouted'` and route it to a **review queue / gate** (reuse
  the existing review_flags / outlier_triage machinery). It must surface as "needs a routing
  decision," NOT be silently dropped into `per_request`/`misc` and given an invented price.
- A gap is a visible, actionable item — you learn about missing charge-types because the
  system *tells you*, not because you squint at a report afterward.
- **Self-fill loop (identity only):** the LLM/reviewer proposes the `(service, charge_type,
  mechanic)` for the unrouted key (e.g. "`AWSWAF+Captcha` looks like waf·requests →
  per_request"), a human confirms once, and the confirmed entry is written back into the
  registry as a permanent deterministic row. Next bill, that key routes instantly with no
  LLM. The registry grows itself and becomes more deterministic over time. This is the
  identity-side of `project_refresh_and_reviewer_layer` — note: the loop proposes **identity
  only**, never the pricing decision.

## Migration (no big-bang; shadow-diff gated)
1. **Build the registry from real bills.** Enumerate every `(product_code, token, operation)`
   combination across all available job DBs; for each, record the mechanic
   `classify_mechanics` currently assigns. This bootstraps the table from ground truth and
   gives 100% coverage of what's actually been seen.
2. **Fix the known-wrong entries** while bootstrapping (WAF-requests, S3-requests,
   Aurora-I/O, VPC IPAM, and any other where the current mechanic is wrong — this is where
   `WORKORDER_charge_type_misrouting_fix.md`'s three fixes become registry rows, not code
   patches).
3. **Add the lookup** (`spec_dispatch.py` / the registry resolver) and run it in **shadow
   mode** next to `classify_mechanics` (reuse `scripts/shadow_diff_classify.py`). Every
   divergence is reviewed: it's either a bug the registry fixes (expected) or a regression
   (fix the registry). Maintain the "intended divergences" list in `SERVICE_SPECS_DESIGN.md`.
4. **Cut over**: the registry becomes authoritative; delete the ~178 regex rules from
   `classify_mechanics.py` (per `project_pricing_and_cutover_decisions` — specs own
   classification). Mechanics remain as pricing primitives only.
5. **Decision layer untouched.** Map/passthrough/ignore/SKU resolution stays exactly where
   it is; it now simply receives a correctly-identified `(service, charge_type)` every time.

## Acceptance
- **Identity correctness:** for every `(product_code, token)` in the job DBs, the registry
  returns exactly one `(service, charge_type)`; zero rows are `unrouted` for keys that have
  been seen before; unseen keys land in the review queue, not a catch-all.
- **No word-collision:** `waf·requests` and `s3·requests` resolve to different charge_types
  despite sharing "Request"; assert via unit tests.
- **The three regressions fixed via routing:** on job `8ded6e7d`, WAF requests, S3 Tier2
  requests, and Aurora I/O no longer carry `mechanic_group ∈ {per_request, managed_db}` by
  word-match; they route by id to their correct charge_type.
- **Shadow-diff clean:** every registry-vs-classify divergence is either on the documented
  intended-divergence list or resolved.
- **Metric:** rows where `strategy='map' AND gcp_projected_cost < 0.05×aws_amortized_cost AND
  aws_amortized_cost>25` → ~0.
- **Id, not name:** grep the new resolver — it must key on `product_code` + normalized
  `usage_type`/`operation` token; it must NOT substring-match product/usage_type free text.

## Files
- `data/service_specs.json` (or new `data/routing_registry.json`) — the id-keyed table, one
  block per service, one entry per charge_type, bootstrapped from all job DBs.
- `scripts/aws_normalizer.py` — reuse/extend `canonical_service` (service id) + add
  `normalize_usage_type` (region-prefix strip → stable token) if not already present.
- `scripts/spec_dispatch.py` — the id-keyed lookup resolver; shadow-mode then authoritative.
- `scripts/classify_mechanics.py` — the ~178 regex rules retired into the registry at cutover.
- `scripts/shadow_diff_classify.py` — registry-vs-classify diff over all job DBs.
- review_flags / outlier_triage path — the `unrouted` review queue + self-fill proposals.
- `tests/test_spec_dispatch.py` — per-service × charge-type identity tests, incl. the
  word-collision cases.

## What this explicitly is NOT
- Not a pricing-decision table. It never sets strategy/target/SKU.
- Not more exclusions bolted onto greedy rules — it *removes* the greedy rules.
- Not per-service manual patching — it's one uniform id-keyed mechanism plus a loud review
  queue that turns every future gap into a one-time confirm instead of a code change.

## Related
`SERVICE_SPECS_DESIGN.md`, `WORKORDER_greedy_service_split.md` (service→shared yield, folds
in here), `WORKORDER_charge_type_misrouting_fix.md` (its 3 fixes become registry rows),
`WORKORDER_complete_service_registry.md`, `project_layer1_product_code`,
`project_pricing_and_cutover_decisions`, `project_refresh_and_reviewer_layer`.
