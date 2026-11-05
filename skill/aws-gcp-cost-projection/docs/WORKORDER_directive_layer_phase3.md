# WORKORDER — Directive Layer, Phase 3: the preference verbs

Parent design: `docs/DESIGN_report_refinement.md` (taxonomy §3). Depends on Phase 1
(unified ledger + resolver + `apply_directives.py`, parity-green) and Phase 2 (AGY
translator, validation-before-confirm, clarification thread, unified UI) being merged.

Phase 2 shipped the conversational loop on the **existing** verbs (`set_target_service`,
`set_strategy`, `set_is_workload`, `set_assumption`, `annotate`, generic `set_field`).
Phase 3 adds the five **preference** verbs the user actually asked for — the ones that read
on an AWS→GCP cost report but have nowhere to land today:

| User says | New verb |
|---|---|
| "Use N2D for these ARM boxes", "Intel stays Intel" | `set_target_family` |
| "Use a bigger/smaller shape", "custom 4vCPU/16GB" | `set_target_shape` |
| "Price these in europe-west1" | `set_region` |
| "…and if it's not offered there, use the global price / nearest / keep" | `region_policy` (param) |
| "Use 3-yr CUD", "net my Savings Plan" | `set_pricing_basis` |

Each verb is a **selector→action directive** that rides the Phase-1 rails: resolver
(specificity > recency) → validation gate → `apply_directives.py` → `incremental_rerate`
→ gates → report provenance. **No new pipeline, no new apply phase** — Phase 3 adds verb
handlers to `apply_directives.py`, validators to the translator, and the availability data
each verb checks against. If a Phase-3 change needs a second apply phase or a parallel path,
stop — it's wrong.

Baseline: `pytest tests/` green (1265), `go test ./internal/...` green. Every step keeps
them green.

---

## 0. Shared groundwork — selector derives `arch` and `instance_family` (do first)

The new verbs select on **derived** fields that the Phase-1 resolver doesn't populate yet:
`arch` (x86/arm64/intel/amd) and `instance_family` (m7g, c7i, …). Both already exist in the
compute mapper — do not reinvent them:
- `apply_static_mappings.py` owns `family_arch`, `_ARCH_SUFFIX`, `_ARM_PREFIX`,
  `get_gcp_family` (family_mapper.py is only a shim). Export a pure
  `derive_identity(product, usage_type, instance_type) -> {arch, instance_family, vendor}`
  from there so the resolver and the translator share **one** derivation.
- `directive_resolver.py`: when a selector carries `arch` / `instance_family` / `vendor`,
  match against `derive_identity(...)` of the row — reuse `aws_normalizer.canonical_service`
  for the service field exactly as Phase 1 does. Add nothing to the matcher's precedence
  ladder except placing `family+arch` where design §2.3 already puts it (below tag/account,
  above service).

Tests (`tests/test_directive_resolver.py`): a `{service:ec2, arch:arm64}` selector hits only
Graviton rows; `{vendor:intel}` hits only Intel rows; `instance_family:["m7g"]` is exact.

---

## 1. `set_target_family` — pick the GCP family  (`apply_directives.py` + translator)

"Use N2D for these ARM boxes" / "Intel→Intel".

- **Apply:** the effective override carries `target_family` (e.g. `N2D`, `C4A`, `N4D`). In
  `apply_directives.py`, a family override re-runs the compute mapper's family→SKU step for
  the matched rows with the family **pinned** instead of auto-selected: call the existing
  `get_gcp_family` path with an override argument, then let `incremental_rerate` re-price off
  the new `gcp_sku_id`. Do **not** hand-write SKU ids in the directive — pin the family, let
  the mapper resolve the SKU, so the row still flows through `resolve_sku` and the guards.
- **Validate (translator, Phase-2 gate):** family must exist for the row's `arch` and
  `gcp_region` in `data/compute_family_availability.json`; if absent → clarification
  ("N2D isn't offered in `asia-south2`; nearest is `asia-south1`, or keep current?"),
  resolved by a `region_policy` param (§3). Family must be core+ram-priceable via the
  compute mapper, else reject.
- **Default-family memory rule still holds:** when AGY *proposes* a family unprompted it must
  honor the house default (N4D / C4D / C4A, never legacy N2D-AMD / C2D-AMD as a starting
  point) — but an explicit user "use N2D" is a pin and overrides the default. Encode the
  house defaults as data the translator reads, not as a prompt string.

Tests: `{set_target_family:C4A}` on arm rows re-pins the family and the SKU changes;
an unavailable family yields a clarification, nothing applied; AGY's *unprompted* family
suggestion is a house-default family.

---

## 2. `set_target_shape` — pick the shape / custom size  (`apply_directives.py` + translator)

"Use a bigger shape", "custom 4 vCPU / 16 GB".

- **Apply:** override carries `shape` — either a predefined machine type
  (`n4d-standard-8`) or a custom `{vcpu, ram_gb}`. The mapper emits the core+ram components
  for that shape; `resolve_sku` must find priced core & ram SKUs for the family+region.
- **Validate:** custom shape must satisfy the family's custom-shape constraints (vcpu even,
  ram within the family's per-core min/max) — read these from `compute_family_availability`
  (extend it if the constraints aren't there yet; one data edit, not code branches). A shape
  that doesn't resolve to a priced SKU → clarification, never a silent zero.
- **Interaction with §1:** shape without family keeps the current/derived family; family +
  shape together pin both. One verb per field (resolver rule) — a later `set_target_shape`
  supersedes an earlier one on the same row.

Tests: a valid custom shape re-prices; an out-of-constraint custom shape is rejected with a
reason; shape-only keeps family.

---

## 3. `set_region` + `region_policy`  (`apply_directives.py` + projection view)

"Price these in europe-west1" and the fallback behavior "global if absent / nearest / keep".

- **`set_region`:** override sets the row's `gcp_region`. Re-rating already resolves price by
  region through the projection view's `COALESCE(regional, global, any)` chain
  (`projection_view.py`); Phase 3 makes the **chosen** region and the **policy** explicit
  instead of implicit.
- **`region_policy`** (param on the region directive, not a separate verb):
  `exact | nearest_then_global | global | keep`. This maps onto the **existing** region-basis
  state the registry already models — `rate_region_basis` (`exact|global|dominant|
  cheapest_available`) + `rate_region_used`. The policy decides which basis is acceptable:
  - `exact` → require a rate in `gcp_region`, else clarification.
  - `nearest_then_global` → allow `dominant`/`cheapest_available` then `global`, stamp the
    basis + `rate_region_used` so the report discloses it (the note templates already exist).
  - `global` → force the global rate.
  - `keep` → no-op, leave the baseline mapping.
  Do **not** add a new region-fallback mechanism — set `gcp_region` + the acceptable
  `rate_region_basis` set and let `apply_rates` / the projection view do the rest.
- **Validate:** if the policy is `exact` and no in-region rate exists → blocking
  clarification offering nearest (`compute_dominant_region` / catalog) or global.

Tests: `set_region` + `exact` with no in-region rate → clarification; `nearest_then_global`
stamps `rate_region_basis=dominant` and discloses `rate_region_used`; `global` forces global.

---

## 4. `set_pricing_basis` — CUD / SP-netting  (`apply_directives.py` + `apply_rates.py`)

"Use 3-yr CUD", "net my Savings Plan into both sides". **This verb touches the headline
comparison, so it is the most guarded of the five.**

- **House default is unchanged and stays the baseline:** On-Demand vs On-Demand (decided —
  AWS `publicOnDemandCost` vs GCP OD; this already resolves the SP/RI-netting question for
  the default projection). `set_pricing_basis` is an **opt-in overlay on top of that
  baseline**, never a silent change to the headline.
- **Basis values:** `cud_1yr | cud_3yr | sp_net | od` (od = explicit reset to baseline).
  - `cud_1yr`/`cud_3yr`: apply the GCP commitment discount to matched rows via the
    **existing** CUD machinery (`apply_rates.load_cud_pct` / `CUD_PCT_FALLBACK` /
    `blended_rate`) — Phase 3 only lets a directive *select which rows* and *which term*,
    it does not invent discount math.
  - `sp_net`: net the AWS Savings Plan / RI into the AWS side (use amortized instead of
    public OD for matched rows). This is a **display/basis** change the memo already
    anticipated; show it as a separate column, don't overwrite the OD headline.
- **Validate + disclose:** any `set_pricing_basis` directive forces a review flag and a
  refinement-summary line ("rows X priced at 3-yr CUD at your request") — the comparison
  basis must never change without the report saying so, loudly, per the pricing-decision
  memory. A basis that would make a row un-sellable-cheap vs reality still trips the ratio
  clamp.

Tests: `cud_3yr` on a compute selection lowers those rows by the configured CUD pct and
flags them; `od` resets a previously-CUD'd row to baseline; the headline OD-vs-OD total is
unchanged for un-targeted rows.

---

## 5. Translator + clarification coverage (extend Phase 2, don't fork it)

- `directive_translator.py`: extend the allowed-verb set to the five above; the LLM slice
  gains the row's `arch` / `instance_family` / `vendor` (from `derive_identity`) and, for
  region/family asks, the availability facts so AGY proposes only offered combinations.
- Clarifications these verbs raise (all through the Phase-2 `refinement_thread.json`):
  family-not-in-region, custom-shape-out-of-constraint, region-has-no-rate,
  pricing-basis-confirm-blast-radius. AGY asks **only what it needs** — "use global if
  absent" pre-answers the region policy, so no question fires.
- Remove the Phase-2 "not yet supported" stub for these five verbs.

Tests (`tests/test_directive_translator.py`): each verb round-trips NL → valid proposed
directive under a mocked LLM; each verb's failure mode becomes the right clarification.

---

## 6. Report + revert (reuse Phase-2 rendering)

Phase-2 provenance already renders `applied_directives` per row and a refinement summary.
Phase 3 only ensures the new verbs read correctly in that surface: "Changed by your request:
*use N2D for ARM app servers*" with the family/shape/region/basis delta shown
(before→after), in **both** HTML/PDF and CSV/XLSX (license-split symmetry rule). Revoke →
replay with the directive `superseded` restores the baseline family/region/basis — no inverse
math, the event-sourced ledger makes it a delete-and-replay.

Tests: `tests/test_export_report.py` asserts a family/region/basis change discloses its
before→after in both outputs; revoke restores baseline.

---

## 7. Done criteria
- Five preference verbs implemented as directives on the Phase-1 rails — **no new apply
  phase, no parallel path**. `apply_directives.py` gained verb handlers; `apply_rates`/
  projection-view/compute-mapper machinery is reused, not duplicated.
- Each verb validates against real availability (`compute_family_availability.json`,
  `resolve_sku`, region rates, CUD config) before confirm; failures become clarifications,
  never silent zeros or un-priced SKUs.
- House defaults preserved: N4D/C4D/C4A family defaults for *unprompted* AGY suggestions;
  OD-vs-OD headline unchanged unless a `set_pricing_basis` directive explicitly and visibly
  overrides it.
- Guards still fire after overlay (ratio clamp, allowed-targets, region-availability,
  outlier); a user preference cannot silently produce an un-sellable SKU or absurd ratio.
- Report discloses every preference change (before→after) in HTML + CSV/XLSX; revoke restores
  baseline.
- Full `pytest tests/` + `go test ./internal/...` green.

## 8. Explicitly out of scope → Phase 4+
Reversibility/history **UX** polish (timeline, diff view — Phase 5), the long-tail taxonomy
and per-directive confirm-threshold policy (Phase 6), directive sharing across a team, and
the catalog-refresh-vs-pinned-directive precedence question (design §9 open questions) —
those are product decisions, not this workorder. Phase 3 ships the five verbs on the existing
rails; nothing here changes the deterministic baseline.
