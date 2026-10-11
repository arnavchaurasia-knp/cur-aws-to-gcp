# WORK ORDER — Split greedy service rules: yield egress/storage to shared mechanics (NEW-4)

**For an implementing AI / engineer with no prior context. Read fully. Primary target is
the spec registry `data/service_specs.json` + `scripts/spec_dispatch.py`; the change is a
DELIBERATE, documented improvement over `classify_mechanics.py` (so it will show up as a
shadow-diff, and that is expected).**

## The problem
Some service rules match ALL of a product's rows and run EARLY, before the shared
`data_transfer` / `block_storage` rules. So a service's egress or storage sub-row gets
claimed by the service and sent to the service's pricer — which only handles that
service's MAIN charge — instead of the deterministic shared pricer.

CONFIRMED live example (OpenSearch):
- `map_opensearch` only prices `.search` INSTANCE rows; its docstring says storage/IOPS/
  snapshot rows "fall through to the Phase 2 LLM."
- An OpenSearch **egress** row → `mechanic=opensearch` → `map_opensearch` can't parse it
  → LLM. This bypasses `egress_rates.py`'s deterministic egress SKUs — the very pricer
  that exists BECAUSE LLM/word-overlap egress pricing varied 2x–8x. So OpenSearch egress
  is routed to the known-mispricing path.

Likely same class: `cloudwatch` (data transfer), `redshift` (managed storage / egress),
and any other service whose spec uses a single `*_all` charge_type. EC2/RDS already do it
right (they split into `instance_hour` / `ebs_storage`/`storage` / `data_transfer`).

## Decision taken
Yield: a service's egress rows → `data_transfer`, storage rows → `block_storage`; only the
CORE rows → the service mechanic. Implement by splitting the service's `*_all` spec
charge_type into ordered charge_types (egress and storage FIRST, core catch-all LAST),
exactly like the EC2 spec. Do NOT blindly split every service — only those that (a) have a
single `*_all` charge_type AND (b) are confirmed to actually carry egress/storage sub-rows
that their mapper doesn't price.

## Steps

### 1. Shadow-diff to find the real offenders (prerequisite)
Build/run `scripts/shadow_diff_classify.py` (if it doesn't exist yet — see its own work
order): for every row of every available job DB, compare `classify_mechanics.classify`
vs `spec_dispatch.classify_via_specs`, AND additionally flag rows where a service
`*_all` charge_type captured a row whose `usage_type`/`operation` matches an egress
signal (`DataTransfer|Out-Bytes|In-Bytes|LCU`) or a storage signal (`GB-Mo|Storage|
Snapshot|IOPS`). Output a table: service → count + spend of egress/storage rows currently
swallowed by its `*_all`. **This list is the scope.** (Needs the job DBs — run on the box
that has them. If none are available, proceed with the CONFIRMED set: opensearch; and
treat cloudwatch/redshift as candidates to verify.)

### 2. For each confirmed service, split its spec charge_types
In `data/service_specs.json`, replace the single `<svc>_all` charge_type with ordered
charge_types (shared mechanics FIRST, core LAST). Pattern (opensearch shown):
```json
"opensearch": {
  "charge_types": [
    {"id": "opensearch_data_transfer",
     "match": {"usage_type_re": "DataTransfer|Out-Bytes|In-Bytes"},
     "fallback_match": {"operation_re": "data transfer (out|in)|per gb.{0,30}transfer"},
     "mechanic": "data_transfer",
     "fields": [{"name":"direction","cur_column":"lineItem/UsageType","source":"cur","required":true},
                {"name":"gb","cur_column":"lineItem/UsageAmount","source":"cur","required":true}],
     "source_rule": "data_transfer (was swallowed by opensearch_all)"},
    {"id": "opensearch_storage",
     "any_of": [{"usage_type_re":"Storage|Snapshot|IOPS"},{"unit_in":["gb-mo","gb month","gb-month"]}],
     "exclude_when": {"unit_hourly": true},
     "mechanic": "block_storage",
     "fields": [{"name":"gb_mo","cur_column":"lineItem/UsageAmount","source":"cur","required":true},
                {"name":"region","cur_column":"product/region","source":"cur","required":true}],
     "source_rule": "block_storage (was swallowed by opensearch_all)"},
    {"id": "opensearch_compute",
     "match": {},
     "mechanic": "opensearch",
     "fields": [ ...existing opensearch_all fields... ],
     "source_rule": "opensearch (core instance rows)"}
  ]
}
```
Apply the same shape to each confirmed service (cloudwatch, redshift, …) using that
service's real egress/storage usage_type signals (take them from the shadow-diff output).

### 3. This INTENTIONALLY diverges from classify_mechanics — document it
These splits make `classify_via_specs` route egress/storage DIFFERENTLY than
`classify_mechanics` (which sends them to the service mechanic). That is the FIX, not a
regression. In each split charge_type's `source_rule`, note "was swallowed by <svc>_all
in classify_mechanics; registry yields to shared mechanic." Maintain a short list in
`docs/SERVICE_SPECS_DESIGN.md` of "intended registry-vs-classify divergences" so the
shadow-diff reviewer does not treat them as bugs.

### 4. Tests — extend `tests/test_spec_dispatch.py`
- For each split service, add cases: an egress row → `data_transfer` / `<svc>_data_transfer`;
  a storage row → `block_storage` / `<svc>_storage`; a core instance row → `<svc>` /
  `<svc>_compute`.
- These cases will NOT match `classify_mechanics.classify` (which returns the service
  mechanic for all three). So in the test harness, mark these rows as **expected
  divergences**: assert `classify_via_specs` == the NEW expected mechanic, and do NOT
  assert equality with `classify_mechanics` for them (add them to an allowlist the parity
  loop skips, with a comment pointing at this work order). Every OTHER row must still match.

### 5. (Optional, later) mirror the fix in classify_mechanics for parity during transition
If classify_mechanics must stay correct until cutover, add egress/storage exclusions to the
greedy rules there too (e.g. the `opensearch` rule yields rows whose usage_type matches the
egress/storage signals). Higher risk (touches RULES ordering) — only if needed before the
registry is wired in. Prefer fixing only in the registry and cutting over.

## Acceptance
- Shadow-diff output lists the swallowed egress/storage rows per service (the scope).
- Split specs are valid JSON; `spec_dispatch.py` loads them.
- `test_spec_dispatch.py`: all non-divergent rows still match classify_mechanics; the new
  split-service rows match the NEW expected mechanic and are on the documented divergence
  allowlist.
- `docs/SERVICE_SPECS_DESIGN.md` lists each intended divergence.
- Report: services split, rows/spend moved from service-mechanic → data_transfer/
  block_storage, and confirmation that only intended rows diverge.
