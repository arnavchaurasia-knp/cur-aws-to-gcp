# WORK ORDER — Strategic: generic shared-mechanic over-claim + deterministic-immunity

**For an implementing AI / engineer. Read fully.** This extends, and does not duplicate,
the existing plan:
- `SERVICE_SPECS_DESIGN.md` — the Stage 1/2/3 identity→charge-type→mechanic split.
- `WORKORDER_greedy_service_split.md` (NEW-4) — makes a **service** yield egress/storage to
  **shared** mechanics (service → shared).
- `WORKORDER_complete_service_registry.md` — the registry build-out.

This work order covers the **opposite and complementary** leak those don't: **generic
shared mechanics (`per_request`, `managed_db`, and peers) over-claiming service-specific
charge-types (shared → service)**, and the **`DETERMINISTIC_GROUPS` immunity** that then
prevents `service_map.json` / dedicated mappers from correcting the result. This is the
reason per-service fixes "don't pay off": they are added at a layer the router has already
bypassed.

## Why this is its own problem

`WORKORDER_greedy_service_split` fixes "a *service* rule grabbed a row that belonged to a
*shared* mechanic." The live failures below are the reverse: "a *shared* mechanic grabbed a
row that belonged to a *service*-specific handler." Both are facets of the same leaky
global first-match chain, but the fix surface is different:

- There, you split a service's `*_all` into ordered charge_types that yield to shared.
- Here, you must (a) make generic mechanics **refuse** rows that a service claims as its
  own charge-type, and (b) remove the immunity that lets a wrong-but-"deterministic"
  mechanic block a correction.

## Evidence (job `8ded6e7d`, measured — see WORKORDER_charge_type_misrouting_fix.md for the table)

- **WAF** `RequestV2`/`WebACL` ($525, $61) → `per_request#33` → Networking SKU @ 7.5e-07 →
  **$0**. `service_map.json` `waf → Cloud Armor` (mode review) was **skipped** because
  `per_request ∈ DETERMINISTIC_GROUPS` ([service_classifier.py] review-skip guard).
- **S3** `Requests-Tier2` ($2,705) → `per_request#17` → GCS Class B op (unit $/1k vs $/10k)
  → **$3.5** (~770× under). Dedicated S3-request passthrough never reached.
- **Aurora I/O** ($6,783 × ~12) → `managed_db#10` → priced as **ACU compute-hours**.

Same shape I just fixed for **VPC IPAM/PublicIPv4** rows: `flat_hourly` (a
`DETERMINISTIC_GROUP`) claimed them, `service_map`'s VPC rule couldn't set a label because
`keep` never wrote `gcp_service` and `review` is skipped for deterministic rows — so they
landed null. The fix there (make `keep` backfill `gcp_service`) was a band-aid on the same
underlying immunity bug. This work order fixes the class, not the instance.

## The generic-mechanic matchers are too broad (the root)

`classify_mechanics.py`:
- `per_request` (~line 729) matches **any** row where `unit ∈ (Requests, Count, Lambda-GB-Second)`
  OR `usage_type|product ~ /Requests|Invocations/`. That is every WAF request, every S3
  request tier, SQS/SNS, CloudFront requests, Lambda, etc. — across unrelated services with
  **incompatible unit models** — funnelled into one `map_per_request()` that picks a nearest
  SKU.
- `managed_db` similarly swallows every RDS/Aurora row including non-compute charge-types
  (I/O, extended support) and prices them as the service's main (compute) charge.

Because the mechanic is chosen **globally** and first-match-wins, and the correct handler
is **downstream** (a dedicated mapper branch or a `service_map` rule), the correct handler
never runs.

## Fixes

### FIX-A — Route charge-type **before** generic mechanic (the registry does this properly)
The durable fix is the registry: `service_specs.json` + `spec_dispatch.py` choose the
mechanic **per service, per charge-type**, so WAF-requests, S3-requests, Aurora-I/O each get
an explicit charge_type that maps to the right primitive (Cloud Armor passthrough, S3
request passthrough, I/O passthrough/ignore) — the generic `per_request`/`managed_db`
catch-alls are only ever reached for rows no service spec claims.

- Add charge_types to the relevant service specs (`waf`, `s3`, `rds`) for these sub-charges,
  ordered **before** the service's core/`*_all` catch-all (mirror the EC2/RDS split pattern
  already in the file, and the opensearch example in `WORKORDER_greedy_service_split.md`).
- This is the same "split a service's charge_types, specific-first" discipline as NEW-4,
  applied to request/IO charge-types instead of egress/storage. Coordinate with that work
  order so both sets of charge_types live in one coherent spec per service.
- `per_request` / `managed_db` remain as **mechanics** (shared pricing math) but stop being
  **classifiers of first resort** — they are selected only when a spec charge_type names
  them, never by global regex. This is exactly Stage 2→3 in `SERVICE_SPECS_DESIGN.md`.

### FIX-B — Kill the deterministic-immunity correction-blocker (do this NOW, independent of the registry)
In `service_classifier.py`, a `service_map` rule in mode `review`/`ignore` is skipped when
the row's mechanic_group is in `DETERMINISTIC_GROUPS` ("a dedicated mapper already priced
this"). That assumption is **false** when the row was claimed by a *generic* deterministic
group (`per_request`, `managed_db`, `flat_hourly`, `block_storage`) rather than a
*service-dedicated* mapper — the generic mapper produced a wrong price, and the skip then
protects the wrong price from correction.

- Split the immunity: only rows priced by a **service-dedicated** deterministic mapper
  should be immune. Generic shared mechanics (`per_request`, `managed_db` when it fell
  through to the LLM/word-overlap SKU, `flat_hourly`, `block_storage`) must **not** confer
  immunity to a service_map `review` rule that explicitly names a different target.
- Concretely: gate the immunity on a per-row signal that a real service mapper claimed it
  (e.g. `mechanic_rule` names a service mapper, or `gcp_sku_id` came from a dedicated
  mapper rather than word-overlap resolution), not merely on `mechanic_group ∈
  DETERMINISTIC_GROUPS`. When in doubt, let the service_map `review` correction win for the
  generic groups — a passthrough-at-cost is always safer than a wrong near-zero SKU.
- This single change would have prevented the WAF $0, the VPC null, and is the common root
  of the "service_map edits don't take effect" complaint. Add regression tests:
  a WAF request row in `per_request` + a `waf→Cloud Armor review` rule ⇒ corrected to
  Cloud Armor passthrough; a genuine dedicated-mapper row (e.g. `dynamodb_storage`) stays
  immune.

### FIX-C — Generic matchers must exclude service-claimed charge-types (defense in depth)
Even before the full registry, narrow `per_request` / `managed_db` matchers so they do not
claim rows a service owns:
- `per_request`: exclude rows whose `canonical_service` has a dedicated request handler
  (`waf`, `s3`, `cloudfront`, …) — those route to the service, not generic per_request.
  This mirrors the 5 exclusions `block_storage` already carries (S3/DynamoDB/Lambda/VPC/
  SageMaker) — the accretion that `SERVICE_SPECS_DESIGN.md` names as the symptom. Add the
  analogous exclusions here as a stopgap, with a `# TACTICAL` comment pointing at the
  registry migration.
- `managed_db`: exclude RDS/Aurora non-compute charge-types (I/O, extended support,
  snapshot) so only genuine instance/ACU rows reach the compute pricer.

## Sequencing
1. **FIX-B first** — smallest, highest leverage, unblocks every existing service_map rule
   immediately and prevents the whole "fixes don't pay off" class. Ship independently.
2. **FIX-C** — tactical matcher exclusions; buys correctness while the registry lands.
3. **FIX-A** — fold the tactical guards (from this and `WORKORDER_charge_type_misrouting_fix.md`)
   into `service_specs.json` and delete them from `classify_mechanics.py`, per
   `SERVICE_SPECS_DESIGN.md`'s cutover. This is where the mechanic-choice permanently moves
   out of the global regex chain.

## Acceptance
- Shadow-diff (`scripts/shadow_diff_classify.py`, see its work order) across all available
  job DBs: for every service with a dedicated request/IO charge-type, assert zero rows are
  claimed by generic `per_request`/`managed_db`.
- FIX-B test: a `review` service_map rule overrides a generic-mechanic mispricing; a
  dedicated-mapper price is left untouched.
- End-to-end on job `8ded6e7d`: the three charge-types from the tactical work order all
  price correctly through the registry path, with `per_request`/`managed_db` no longer in
  their `mechanic_group`.
- Metric to watch: count of rows where `strategy='map'` AND `gcp_projected_cost < 0.05 ×
  aws_amortized_cost` AND `aws_amortized_cost > 25` — should drop to ~0 (today it includes
  WAF, S3 requests, Aurora I/O).

## Files
- `data/service_specs.json` — request/IO charge_types for `waf`, `s3`, `rds` (FIX-A).
- `scripts/spec_dispatch.py` — ensure charge-type-before-generic ordering holds.
- `scripts/service_classifier.py` — split deterministic-immunity (FIX-B).
- `scripts/classify_mechanics.py` — generic-matcher exclusions (FIX-C), later deleted at cutover.
- `tests/test_spec_dispatch.py`, `tests/test_classify_mechanics.py` — coverage for all three.

## Related
`WORKORDER_greedy_service_split.md` (service→shared), `WORKORDER_complete_service_registry.md`,
`SERVICE_SPECS_DESIGN.md`, `WORKORDER_charge_type_misrouting_fix.md` (the tactical sibling).
