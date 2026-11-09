# WORKORDER — Structured-dispatch rollout across all services

**Status:** proposed · **Author pass:** 2026-11-06
**Goal:** make the whole class of "a charge was routed/priced wrong because a decision was inferred from free text or a misleading catalog rate" **negligible**, across every service/SKU — not just the data-transfer and EBS-snapshot cases that surfaced it.

**Companion:** `WORKORDER_region_fidelity_and_parts_dispatch.md` fixes the specific rows + ingest region fidelity + the egress rate. **This** workorder is the general program those fixes are instances of. Do that one's Phase 0–1 first (cardinality harness + inter-zone rate); this one depends on the harness.

---

## The principle (what we're wiring, and why it works)

Four industry approaches were weighed; this rollout implements the three that make issues negligible and explicitly excludes the one that doesn't:

| Pillar | Industry practice | In this repo | Phase |
|---|---|---|---|
| **(b) Taxonomy identity** — route on the provider's structured keys, never free text | key off `ProductCode` + structured `product/*` columns | `product_code → spec_dispatch`; `usage_type_parser` typed fields | C, D |
| **(c) Declarative specs** — every decision is an auditable data row, not a code branch | curated mapping tables (CloudHealth/Cloudability/Finout) | `service_specs.json` charge_types | A, B, D |
| **Guard layer** — reconciliation + policy-as-code makes regressions loud | invoice reconciliation, assertion gates | `pricing_matrix.json` + `assert_pricing_matrix.py`, golden tests, cardinality/cost invariants | E |
| **(d) Embedding/similarity** — probabilistic; silent wrong picks | last-resort long-tail only | LLM kept to long-tail sizing; **not** added to routing | (excluded) |

**Why (b)+(c)+guards make it negligible:** (b) removes the fragile text inference that caused both bugs; (c) makes every surviving decision explicit and reviewable; the guard layer turns a future regression from a silent dollar error into a failed gate.

---

## Current state (verified inventory — do not re-derive)

- **54 services specced (+ a `_meta` entry), 115 charge_types** in `service_specs.json`. `spec_dispatch.OWNS_CLASSIFICATION = True`. (Re-verified 2026-11-06; counts drift as the spec file grows — re-run the inventory before relying on exact totals.)
- **The specs are a declarative shell but matching is still mostly regex:** 44 charge_types match via `usage_type_re`; others use `any_field_re` ×6/`operation_re`/`exclude_*_re`. Only a minority use structured predicates (`unit_in` ×7, `unit_hourly` ×6, `is_accelerator` ×2). Exactly **1** charge_type currently carries a `parsed_match` block — the migration has not started.
- **The parts-based path already exists and is additive-safe:**
  - `spec_dispatch._eval_block` already supports typed predicates `family_in`, `sub_type_in` (+ `unit_in/_not_in/_re`, `pricing_model_in`, `line_item_type_in`, `cost_lt_zero`, `is_accelerator`).
  - `spec_dispatch._charge_type_matches` already has a **`parsed_match`** branch: an **additive OR** on typed parser fields that "can only ADD coverage, never remove an existing match." This is the migration lever.
  - `usage_type_parser.parse()` yields `{region, family, sub_type, unit, class}`, cached on the row as `_parsed`.
- **13 services have empty `product_codes`** (can't do taxonomy identity; fall back to name matching): `emr, securityhub, sqs, directconnect, globalaccelerator, memorydb, documentdb, cognito, sagemaker, dms, backup, cloudtrail, fargate`. (All 13 re-confirmed present 2026-11-06; the `_meta` spec entry also has none but is not a service.)
- **Legacy backlog:** 39 `map_*` functions in `apply_static_mappings.py`; `classify_mechanics.RULES` (line 183) + `classify_long_tail_with_trace` still serve unspecced/long-tail services.
- **Guard bones exist:** `pricing_matrix.json` (categorized lists: compute/storage/databases/networking/…), `assert_pricing_matrix.py`, golden-mapping tests, `build_corpus.py` + `spec_parity.py` (priced-corpus parity harness).

---

## Anti-hallucination contract (MANDATORY)

- **One PR per service (or small service group).** Never "migrate everything" in one PR.
- **Additive-first, always:** add the `parsed_match` block *alongside* the existing regex `match`; never delete the regex in the same PR that adds its replacement.
- **Shadow-diff before flipping:** prove the parsed path selects the **same `mechanic_group` + `gcp_sku_name`** as the regex path on the real priced corpus (`spec_parity.py` over `build_corpus.py` DBs). Byte-identical, or every diff explained as a deliberate fix.
- **STOP GATE each PR:** shadow-diff clean + `assert_pricing_matrix.py` green + that service's golden test green + cardinality/cost invariants hold. Do not start the next service until green.
- **Pricing-truth rule (AGENTS.md):** any rate/unit/free-tier fact is verified against the provider's official pricing page and cited. A `$0` catalog rate on a billed charge is a red flag, never projected.
- If a corpus bill for the service doesn't exist, **STOP and report** — don't claim parity you can't measure.

---

## Phase A — Parser field extensions (additive, pure, unit-tested)

Extend `usage_type_parser.py` with the typed fields predicates will need (regex is legitimate *here* — this is the parsing layer). Each field: pure function, `""`/`None` when unknown, unit tests from real usage types.

1. `snapshot_tier` — `"archive"` vs `"standard"` (from `SnapshotArchiveStorage` / operation `archive`) — drives the EBS-snapshot fix.
2. `transfer` sub-fields — `from_region`, `to_region`, `flow` (in/out), `to_is_internet` — drives data-transfer direction (reuse `region_prefix.decode_region_pair`, companion workorder Phase 2).
3. `os_license` — `windows`/`rhel`/`ubuntu_pro`/`none` (from product variant) — drives the OS-license compute split.
4. `commitment` — `spot`/`ri`/`sp`/`ondemand` (from `pricing_model` + usage_type) — prevents Spot↔OnDemand SKU mix-ups.
5. Make `parse_with_unit` the default so `unit` always prefers the real `pricing_unit`.

**STOP GATE:** unit tests cover ≥2 real examples per field; `parse()` output unchanged for existing keys (additive only).

---

## Phase B — New typed predicate operators (additive)

Add to `spec_dispatch._eval_block`, mirroring `sub_type_in`: `snapshot_tier_in`, `transfer_direction_in`, `os_license_in`, `commitment_in`, `class_in`. Each reads `_parsed(row)`; absent field ⇒ predicate fails closed (never a silent true).

**STOP GATE:** operator unit tests; existing predicates unchanged; no spec yet uses them (pure capability add).

---

## Phase C — `product_code` backfill (taxonomy identity for all)

For each of the 13 empty-`product_codes` services, add the real CUR `lineItem/ProductCode`(s) to its spec (confirm each against a real bill's `product_code` column, not a guess). Then make `spec_dispatch` prefer product_code identity over name matching for those services, and remove the name-sniffing fallback once covered.

**STOP GATE:** every specced service has ≥1 product_code OR a documented reason it can't (e.g. marketplace resold SKUs); dispatch for those 13 routes by code on the corpus with no regression.

---

## Phase D — Per-service `parsed_match` migration (the core rollout; one service per PR)

For each service, in **spend-priority order** (below):
1. For every charge_type, add a `parsed_match` block that reproduces the `usage_type_re`/`any_field_re` intent using typed predicates (`sub_type_in`, `class_in`, `snapshot_tier_in`, `transfer_direction_in`, `unit_in`, …).
2. **Shadow-diff** via `spec_parity.py`: parsed path must select the same `mechanic_group` + charge_type as the regex path across all corpus bills for that service.
3. Once identical, **remove the regex `match`** (the `parsed_match` becomes the `match`). Regex now lives only in `usage_type_parser`.
4. Record each charge_type's decision in `pricing_matrix.json` (Phase E runs in the same PR for that service).

**Spend-priority order** (from the global audit — biggest dollar/mis-route risk first):
EC2 (incl. OS-license + Spot/RI) → EBS (snapshot tier) → RDS/Aurora → Data Transfer → S3 → ElastiCache → OpenSearch → EKS/ECS/Fargate → Redshift → the rest, then the 13 backfilled services, then long-tail.

**STOP GATE per service:** shadow-diff clean (or diffs are explained fixes); regex `match` removed; golden test + pricing-matrix assert green.

> **Meets the equivalence program here — but in a *separate* PR.** The point where a migrated service
> asserts "AWS X ≈ GCP Y" is where `WORKORDER_equivalence_consumption_and_directive_upgrade.md` Phase 2
> writes its crosswalk stamp. Keep the same spend-priority order across both programs, but **routing and
> stamp are separate concerns → separate PRs**: land this workorder's routing cutover first (shadow-diff
> byte-identical), then a follow-up PR adds the equivalence stamp. The stamp also presupposes that
> service's claims have been *authored* (that workorder's Phase 1) — a service whose equivalence claims
> don't exist yet gets routed here but stamped later. Don't bundle routing + stamp in one diff.

---

## Phase E — Guard-layer completion (what makes recurrence negligible)

Runs per-service alongside Phase D, then globally hardened:
1. **`pricing_matrix.json` row per service×charge_type**: `{decision (SKU|FORMULA|PASS|IGNORE), gcp_target, reason, status}`. `assert_pricing_matrix.py` fails the build if a specced charge_type has no matrix row or resolves to a decision that contradicts it.
2. **Golden-mapping test per service** (extend `verify_golden_mappings.py`): a real row → expected `mechanic_group` + `gcp_sku_name`.
3. **"No $0 / no silent parity for a billed charge" gate**: a `map` row resolving to $0, or a `needs_reprice` carrying AWS cost where a canonical rate exists, fails the gate.
4. **Cardinality + cost invariants fatal** (companion workorder Phase 0): nothing lost/invented in ingestion.

**STOP GATE:** all four green on the full fixture set; a deliberately-broken spec (test fixture) trips each gate.

---

## Phase F — Legacy retirement

Once a service is fully specced + parsed + guarded, delete its `map_*` function in `apply_static_mappings.py` and its `classify_mechanics.RULES` entry. When the RULES list is empty, remove `classify_long_tail_with_trace` and the legacy fallback. Keep `usage_type_parser` and `region_prefix` (legitimate regex).

**STOP GATE:** 0 active `map_*` for specced services; `RULES` empty or only genuinely-unspecced long-tail remains, documented; full suite green.

---

## Out of scope / non-goals
- No embedding/semantic SKU routing (pillar d) — long-tail LLM sizing only.
- No change to the LLM phase, catalog refresh cadence, or CUD/commitment math.
- No catalog refetch (provider-side $0 SKUs are not fetch errors — see AGENTS.md policy).
