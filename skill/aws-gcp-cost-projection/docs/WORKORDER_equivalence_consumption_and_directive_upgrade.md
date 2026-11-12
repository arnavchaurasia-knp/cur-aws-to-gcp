# WORKORDER — Equivalence crosswalk: author → consume → close the loop (+ directive-layer upgrade)

**Status:** proposed · **Author pass:** 2026-11-06 (rev 2 — re-scoped after verifying the crosswalk is
~2% populated, not ~350 claims)
**Goal:** make cross-cloud equivalence an auditable, degree-stamped, money-ranked, self-healing
property of every priced row — and give the directive layer the three capabilities that consuming it
requires.

> **What changed from rev 1 (read this).** Rev 1 assumed the crosswalk held ~350 claims and that the
> only gap was *consumption*. **Verified false.** `data/equivalences.json` holds **8 claims, all under a
> single attribute (`storage_class`)**. The "~350" in `WORKORDER_equivalence_crosswalk_review.md` is the
> *projected* size, not the current one. So consumption of the crosswalk as it stands would stamp NULL
> on ~98% of mapped spend. **The real work is authoring (and first, defining the attribute taxonomy);
> consumption interleaves behind it, per service, money-ranked.** This revision leads with authoring and
> sizes it with a census instead of assuming it is done.

---

## Prerequisites — blockers that must land first (verified 2026-11-11)

This program assumes `spec_dispatch` cleanly classifies each row to a **service** charge_type, so the
charge_type's declared equivalence attribute (Phase 2) can fire. That assumption is **false today for the
highest-$ rows**, because of a known, separate bug:

- **BLOCKER (hard) — generic-mechanic over-claim + `DETERMINISTIC_GROUPS` immunity**
  (`WORKORDER_generic_mechanic_overclaim.md`, FIX-B). Generic shared mechanics `per_request` / `managed_db`
  match and **over-claim service-specific charge-types** before the service's own charge_type can; then the
  `DETERMINISTIC_GROUPS` immunity (`service_classifier.py` review-skip guard) makes those rows immune to
  correction. Confirmed instances are exactly the rows this program most needs to reach:
  **Aurora I/O → `managed_db#10`, priced as ACU compute-hours** (the very case Phase 6.5 wants to model as
  "$0 folded into Cloud SQL"); **WAF `RequestV2` → `per_request`**; **S3 `Requests-Tier2` → `per_request`**.
  While a generic mechanic owns the row: (a) Phase 2 stamps the *wrong* attribute or none, because the
  service charge_type never matched; (b) Phase 3 findings can't correct it (immunity); (c) Phase 6.5's
  assumption can't be applied because the Aurora charge_type isn't what priced the row.
  **→ FIX-B (split the immunity) + the service-split (stop `per_request`/`managed_db` claiming
  service-specific charge-types) must precede Phase 2 for any over-claimed service.** Clean services
  (e.g. storage) are unaffected and may proceed; this is a per-service gate, consistent with the
  money-ranked rollout.

- **BLOCKER (ordering) — finish the spec-dispatch cutover.** Phase 2 stamps are emitted by `spec_dispatch`
  from the charge_type's declared equivalence; a service still served by legacy `classify_mechanics.RULES`
  or a legacy `map_*` function carries no declaration and so cannot be stamped. Stamp a service only after
  it is specced (already a Phase-2 rule) — and prioritize the spec-dispatch cutover for the over-claimed
  high-$ services so BLOCKER 1's fix and this program land on the same specced rows.

**Net:** the authoring work (Phase 0–1) is independent and can start now; the *consumption* work (Phase 2+)
is gated per-service on FIX-B + that service being specced. Do not start Phase 2 on Aurora/RDS, WAF, or S3
requests until the over-claim fix has landed for them — the stamp would decorate a mapping that a generic
mechanic already mispriced.

---

## Verified inventory (do not re-derive)

**Crosswalk — real current state (measured 2026-11-06):**
- `data/equivalences.json`: **8 claims total**, attribute `storage_class` only
  (degrees: 1 `equal`, 1 `equivalent`, 2 `wider`, 3 `inexact`, 1 `disjoint`). Claim shape:
  `{aws, gcp, degree, scope:[...], comment, decided_by, decided_on}`.
- Degree vocabulary: `equal | equivalent | wider | narrower | inexact | unmatched | disjoint`.
- `scripts/equivalences.py` API (use these — do **not** invent `resolve_claim`):
  - `lookup(attribute, aws_value, gcp_value, scope) -> Claim | None` — the four-coordinate key. `scope`
    is required.
  - `require_claim(...)` — same, raises `UnknownEquivalenceError` when absent.
  - `all_claims()`, `is_provisional(claim)`, `confidence_cap(claim) -> float | None`.
  - **`confidence_cap` is already MANDATORY for `inexact` claims** (0.0–1.0, enforced at claim
    construction) **but nothing in the dollar path consumes it.** The crosswalk was built expecting degree
    to *affect* something, not merely annotate — see OPEN DECISION D.
- `scripts/equivalence_review.py` — money-ranked queue (provisional claims + unknown `(attr,aws,gcp)`
  pairs by USD exposure), exposure read from the durable app DB (Turso/SQLite).
- `scripts/promote_equivalence.py` — promotes a claim's provenance (`provisional → human`).
- **Consumers in the dollar path: NONE.** No mapper / `apply_rates` / `projection_view` /
  `spec_dispatch` / `apply_directives` calls `lookup`.

**Directive layer — unified but equivalence-blind:**
- `scripts/directives.py`: directive = `{id, author, selector, action{type,value}, source_utterance,
  scope_estimate, applied_provenance}`; verbs include `set_strategy, set_service, set_family, set_shape,
  set_region, set_pricing_basis, annotate` (confirm the exact `ALLOWED_ACTION_VERBS` set before coding).
- `scripts/directive_resolver.py`: `resolve()` ranks by **specificity (`compute_specificity`) then
  recency**; no trust axis, no crosswalk awareness; superseded directives recorded for audit.
- `scripts/apply_directives.py`: the one unified apply phase (subsumes `apply_review_fixes`,
  `apply_outlier_fixes`, `apply_commitment_ignores`, `apply_architecture_notes`); stamps
  `applied_directives`; **never touches equivalence**.
- `scripts/directives_from_legacy.py`: converts `review_fixes.json` → directives, so the directive
  layer *subsumes* the correctness loop, it does not bypass it.
- Orchestration: `internal/jobs/orchestrate.go` Phase 3 "Review" (`auto_review.py` PreLLM →
  LLM `review_fixes.json` → PostLLM `apply_review_fixes.py` **or** directive path when
  `isDirectiveLayerEnabled()`); `advisory_reviewer.py` (~line 593, read-only) → `review_findings.json`
  → `analytics_rollup.go` → `review_findings` table + admin status workflow.

**Self-heal loop (reuse; note its granularity):**
- `scripts/refresh_store.py`: keyed at **service / product-code granularity**
  (`get_trusted_mapping(canonical_service_or_code)`); `confirmation_count` + `status:trusted|provisional`;
  consumed next run by `classify_mechanics.py`. **It is service-level — an equivalence correction is
  charge-type/SKU-level, so corrections close via `promote_equivalence`, NOT refresh_store.**

**Governing invariant:** the directive refactor's rule — **dollar totals must not move** on any
provenance-only change. Only Phase 6b (validation that blocks) and Phase 8's fatal gate may change a
result, both flag-first and behind the directive-layer flag, proven on the pinned corpus.

---

## Anti-hallucination contract (MANDATORY)

- **One PR per phase; within authoring/consumption phases, one attribute or one service per PR.** Never
  "author everything" or "stamp everything" in one PR.
- **Routing and equivalence are separate concerns → separate PRs.** For a service the structured-dispatch
  rollout is also migrating, land the routing cutover first (proven byte-identical), **then** a second PR
  adds the equivalence stamp. Do not combine them in one diff.
- **Additive-first; dollar parity.** Every provenance-only phase leaves `projection_view` totals
  byte-identical on the pinned corpus (`spec_parity.py` + companion Phase-0 cardinality/cost invariants).
  Prove it.
- **Pricing-truth rule (AGENTS.md):** every authored `equal`/`equivalent` claim carries a citation to the
  provider's official pricing/docs page. A degree is a reviewable assertion, not a guess.
- **If a census number, corpus bill, or exposure figure a gate needs does not exist, STOP and report.**

---

## Phase 0 — Coverage census + attribute taxonomy + provenance columns (measurement; no behavior change)

**Goal:** size the authoring work instead of assuming it, and give rows somewhere to be stamped.

**Do:**
1. **Census.** Over the pinned corpus, for every distinct cross-cloud assertion the mappers actually make
   (service × charge_type × the AWS→GCP pair they pick), compute mapped-spend and whether a crosswalk
   claim exists today. Emit `docs/equivalence_coverage_census.md`: % of mapped spend with a resolvable
   claim (expect ≈ storage-only), broken down by service, ranked by $. **This number scopes Phases 1–2.**
2. **Attribute taxonomy.** From the census, enumerate the attributes the crosswalk must grow beyond
   `storage_class` — e.g. `instance_family`, `accelerator`, `acu`, `throughput_class`, `egress_lane`,
   `managed_service`, `request_class` — each with its `scope` tokens. Write them into `equivalences.json`
   `_meta` and a short `docs/DESIGN_equivalence_attributes.md`. **No claims yet — just the dimensions.**
3. **Provenance columns** (additive migration, `internal/db` + the Python schema mappers write) — **a row
   can rely on more than one claim** (gp3→Hyperdisk asserts `storage_class` *and* a performance-tier
   equivalence), so the stamp is a SET, not a scalar:
   `equivalence_claim_ids TEXT` (JSON list), `equivalence_min_degree TEXT` (the **weakest** degree among
   the claims a row relies on — if any component is `disjoint`, the row is `disjoint`), and
   `equivalence_provenance TEXT` (`human` only if *every* component is human, else `provisional`/`none`).
   All NULL. Gates and findings key on `equivalence_min_degree`. Surface read-only in the existing admin
   row-detail panel.

**STOP GATE:** census committed with a real per-service coverage number; attribute taxonomy enumerated
(not filled); three columns present and NULL on a fresh and an existing job DB; `pytest tests/` green;
totals unchanged.

---

## Phase 1 — Author claims, money-ranked (the bulk of the real work)

**Goal:** fill the crosswalk for the attributes that carry the most spend, highest-$ first. This phase is
the existing `WORKORDER_equivalence_crosswalk_review.md` program executed against the census —
**this workorder depends on it, it does not replace it.**

**Do (one attribute per PR, in census-$ order):**
1. For each `(attribute, aws_value, gcp_value, scope)` the census shows as high-exposure-and-unmatched,
   author a claim with an honest `degree` and `decided_by`. `equal`/`equivalent` needs a pricing-page
   citation; architecture-dependent mappings (egress lanes, NAT, Aurora I/O) are `inexact`/`narrower`/
   `disjoint` with a comment stating the dependency — that honest degree is the deliverable, not a forced
   `equal`.
2. Model-authored claims land `decided_by="provisional"`; they are valid to consume but will surface for
   review (Phase 3) and can be promoted (Phase 7).
3. **If a pair is modelable but has no assumption knob yet** (e.g. a new fold/absorb rule), do NOT bake a
   constant into the claim — author a first-class assumption per the Phase-6.5 checklist (defaults + domains
   + `load_assumptions` + admin UI + rerun) and have the claim reference that key. The claim records *that*
   there is an assumption and how uncertain (`confidence_cap`); the knob records the *value*.

**Authoring strategy (this is what makes a 300+-claim phase tractable without months of human work, and
it stays inside "LLM advisory only"):** the LLM **bulk-proposes provisional claims** for every census
pair — proposing a *degree-stamped, scoped, reviewable* claim is advisory, not a silent routing pick, so
it does not violate the no-LLM-in-routing rule. Humans then **promote only the top-$ claims by exposure**
(`equivalence_review.py` already ranks them); the long tail stays `provisional` — consumed, stamped, and
flagged, but never silently trusted. An `inexact` proposal must carry a `confidence_cap` (the schema
enforces it), which makes the model state its own uncertainty rather than hide it.

**STOP GATE per attribute:** every high-$ unmatched pair in the census for that attribute now resolves via
`lookup` OR is explicitly recorded `unmatched` with a reason; `equivalence_review.py` re-run shows the
attribute's unknown-pair exposure dropped; no dollar change (authoring touches no mapper yet).

---

## Phase 2 — Map-time consumption + stamp (per service, AFTER its claims exist; provenance-only)

**Goal:** the pricing path reads the crosswalk and records the claim it relied on — the wire the program
has been missing — but only for a service whose claims Phase 1 has authored.

**The attribute belongs in the spec, not hand-wired per mapper.** A `service_specs.json` charge_type
already declares `mechanic` + `fields`; add an `equivalence: [{attribute, scope}]` block the same way
(a list, because one charge_type can assert several — gp3→Hyperdisk is `storage_class` + performance).
Then **`spec_dispatch` emits the stamp**: it already classifies the row to a charge_type, so after the
mechanic produces the (aws→gcp) pair, it calls `lookup(attribute, aws, gcp, scope)` for each declared
equivalence and writes the SET of claim ids + the derived `min_degree`. This has a clean, deliberate
consequence: **a legacy `map_*` function with no spec simply does not stamp** — the stamp arrives when the
service is specced, so the equivalence program rides the structured-dispatch migration rather than
duplicating it.

**Do (one specced service per PR, census-$ order, only after its claims are authored AND the over-claim
blocker is cleared for it — see Prerequisites; for Aurora/RDS, WAF, S3 requests that means FIX-B first):**
1. Add the `equivalence:[{attribute,scope}]` block to that service's charge_types (authored from the
   Phase-0 taxonomy).
2. In `spec_dispatch`, for each declared equivalence call `lookup`; collect claim ids; write
   `equivalence_claim_ids` + `equivalence_min_degree` + `equivalence_provenance`. No claim for a declared
   assertion → that component is `unmatched`/`none`; record the pair for `equivalence_review.py`.
3. Record each relied-on claim's `confidence_cap` on the row (do not change any dollar with it yet — see
   OPEN DECISION D).

**Explicitly NOT here:** no routing, price, or strategy change. The stamp labels a decision the code
already makes.

**STOP GATE per service:** for that service, mapped spend carrying a non-null `degree` matches the census
expectation (the remainder recorded as unmatched, not silent NULL); totals byte-identical; a unit test
asserts a known pair (e.g. storage `glacier_flexible → coldline`) stamps the expected claim id + degree.

---

## Phase 3 — Surface low-degree / provisional claims as findings (reviewer add; no directive change)

**Do:**
1. In `advisory_reviewer.py` add finding types `low_degree_equivalence` (row priced on
   `inexact|narrower|wider|disjoint`) and `provisional_claim` (`decided_by != human`). Evidence = the
   stamped claim id + degree + the row's `aws_od/gcp_od/ratio`; severity scales with `aws_od`.
2. They flow through the existing path: `review_findings.json` → `analytics_rollup.go` →
   `review_findings` table → admin status workflow. No new storage/UI.

**STOP GATE:** every provisional/low-degree row from Phase 2 yields exactly one finding, ordered by
`aws_od`; no finding for `equal`/`equivalent` human rows; round-trips to the DB table.

---

## Phase 4 — U1: equivalence re-resolution + re-stamp in `apply_directives.py`

**Goal:** when a directive changes a mapping, the row's *effective* equivalence is re-resolved so the
audit trail stays true after an override.

**Do:**
1. After a directive changes a row's effective `gcp_service`/SKU/family, re-run the declared
   equivalence(s) via `lookup` on the **new** pair(s) and overwrite `equivalence_claim_ids/min_degree/
   provenance`.
2. Record pre/post in the directive's existing `applied_provenance`
   (`{"prev_equivalence":…, "new_equivalence":…}`) — no new column.

**STOP GATE:** a `set_family` C4A→N4D directive keeps degree `equal` and updates the claim id; a cross-
boundary remap updates the degree; `applied_provenance` carries prev/new; dollars move only by the
directive's own gated effect, never by the stamp.

---

## Phase 5 — U3: provenance-trust tier in precedence (`directive_resolver.py`)

**Goal:** a `provisional` correction must never overrule a `human` decision by recency alone.

**Do:**
1. Extend the ranking key `(specificity, index)` → `(specificity, trust_rank(author), index)`;
   `trust_rank` from the existing `author`/`decided_by`: `human > system > provisional`; recency stays the
   final tie-break.
2. Extend the superseded-directive audit record to say whether it lost on specificity or on trust.

**Pre-work (MANDATORY, or the STOP GATE is a lie):** audit existing resolver tests for cases with
**equal specificity, different author, resolved by recency** — inserting trust *above* recency changes
those. Update them deliberately and document each.

**STOP GATE:** a recent `provisional` vs older `human` collision resolves to `human`; pure-specificity
outcomes unmoved; the audited recency-tie tests pass with documented new expectations; corpus totals
unchanged.

---

## Phase 6 — U2: crosswalk validation of overrides (flag-only, then gate)

**Goal:** catch an override asserting a nonsensical equivalence. "N4D instead of C4A" (`equal`) applies
clean; "RDS → Cloud Storage" (`disjoint`) is caught.

**Do (two PRs):**
1. **6a — flag-only:** in the apply/validate path, after computing a directive's resulting (attribute,
   aws, gcp, scope), `lookup`; if `disjoint`/`unmatched`, emit a `review_findings` entry
   (`directive_crosses_disjoint`, severity by `aws_od`) and **apply anyway**. Observe one corpus pass.
   Note: a **human**-authored directive (incl. a user refinement) that is `disjoint` still applies — user
   authority stands — but it is flagged so a typo is visible.
2. **6b — gate (only after 6a is clean):** a `disjoint` result from a **non-human** directive requires
   explicit confirmation before it applies; `unmatched` stays flag-only (missing ≠ wrong). This can change
   a result, so it ships behind the directive flag with pinned-corpus proof.

**STOP GATE (6a):** every disjoint/unmatched override yields one finding; totals unchanged.
**STOP GATE (6b):** a seeded non-human disjoint directive is blocked pending confirmation; a seeded
`equal`/`equivalent` directive is unaffected; totals move only where a genuinely disjoint auto-override
was previously (wrongly) applied — each move explained.

---

## Phase 7 — Close the loop via `promote_equivalence` (NOT refresh_store)

**Goal:** a human-confirmed correction becomes durable and heals future runs — at the right granularity.

**Do:**
1. When a Phase 3/6 `review_findings` entry is `accepted`/`resolved` by a human with a concrete remap,
   **author or update the crosswalk claim** (`decided_by="human"`) via `promote_equivalence.py`. Because
   the crosswalk is charge-type/attribute granular, this lands the correction at the right level —
   `refresh_store` (service-level) is the wrong target and is not used here.
2. Next run, Phase 2's `lookup` returns the now-human claim; the row stamps `human`, and the finding does
   not recur. (If a *routing* change is also needed, that still goes through the existing review-fix →
   `refresh_store` path — equivalence provenance and routing correction are separate stores by design.)

**STOP GATE:** an accepted finding upgrades/authors the claim to `human`; a follow-up run stamps `human`
and raises no repeat finding; no write to `refresh_store` from this path; totals change only on the
intended remap.

---

## Phase 8 — Visibility, fatal gates, and queue reconciliation

**Do:**
1. **Report:** each row shows `equivalence_degree` + provenance + claim id ("priced on
   `storage_class/glacier→coldline`, degree `equivalent`, human-reviewed"). Reuse the Phase-0 row-detail
   panel.
2. **Queue reconciliation (decide, don't punt):** `equivalence_review.py` (unknown/provisional backlog,
   app-DB exposure) and `review_findings` (per-job findings table) are two views. Define one as the source
   of truth for a given claim's state and make the other link to it — they must not double-count the same
   `(attribute, aws, gcp)` pair. Write the decision into `DESIGN_equivalence_attributes.md`.
3. **Fatal gates (after a full clean corpus pass):** a mapped row `degree='disjoint'` with no human
   confirmation fails the build; a row on a `provisional` claim above an `aws_od` threshold is a WARNING in
   the money-ranked queue (provisional is allowed, just surfaced).

**STOP GATE:** a deliberately-disjoint fixture fails the build; the report renders degree + provenance per
row on all corpus bills; the two queues reconcile to one state per pair with no double-count.

---

## Phase 6.5 — D: equivalence backs onto the assumption layer; gate the unbacked (DECIDED)

**The correct framing (a degree alone is not fabrication).** A `disjoint`/`inexact` mapping is NOT
automatically a made-up number — it is often a legitimate, auditable **assumption**. Aurora maps to Cloud
SQL; Aurora I/O has no per-I/O GCP charge, so the honest projection is "$0, folded into Cloud SQL storage"
— a *declared assumption* (knob `rds_iops_included_in_storage`), not a passthrough and not a fabrication.
The project already has a first-class assumption layer that drives dollars (see below). So "disjoint →
passthrough" from rev 2 was wrong; the real taxonomy is **three-way**:

| Case | Example | Honest treatment |
|---|---|---|
| **Wrong rate / wrong SKU** | standard snapshot × *archive* tier rate | a **BUG** — fix the rate. Not an equivalence/assumption case at all. |
| **No 1:1 GCP line, priced under a stated assumption** | Aurora I/O → $0 folded; NAT 32-VM cap; ACU → vCPU+RAM | **legitimate** — the number is honest *because* a declared, tunable assumption backs it. |
| **No basis to even assume** | genuinely no model | `needs_reprice`/`passthrough`, carry AWS cost with a "no GCP equivalent" note. |

**The gate (what "fabrication" actually means):** a `map` dollar is honest iff it is backed by **either**
a real-equivalent claim (`degree equal/equivalent`) **or** a declared assumption (`is_assumption=True`).
A `map` row backed by **neither** — a precise GCP number with nothing behind it — is the fabrication class.
That gate (not "disjoint → downgrade") is the rule:
1. `map` row, no equivalent claim, no assumption → **flag; then (6b-style) downgrade** to
   `needs_reprice`/`passthrough` with a "no backing" note. Ship flag-first, corpus-proven.
2. `map` row backed by an assumption → honest; ensure `is_assumption`/`assumption` is stamped and the
   assumption is a real knob (below), not a hardcode.
3. Wrong-rate bugs are out of scope here — they are a routing/rate fix in the other workorders.

**The crosswalk LINKS to the assumption layer — it does not duplicate it.** An `inexact`/`disjoint` claim
carries the assumption key(s) that justify its number, and the claim's mandatory `confidence_cap` **is**
that assumption's stated uncertainty. One source of truth: the knob lives in the assumption layer; the
claim references it.

**New assumptions MUST be wired exactly like the existing 19 (not hardcoded in a mapper).** If consuming
the crosswalk surfaces a modelable case with no existing knob, author a first-class assumption:
1. Add key + default → `data/assumption-defaults.json`.
2. Add its domain (`kind`, `values`/range, `source`, `refreshable`, `last_verified`) →
   `data/assumption_domains.json`.
3. Consume it via `config_loader.load_assumptions(job_dir)` in the mapper/`spec_dispatch` (never read the
   JSON directly, never inline the constant).
4. Stamp `is_assumption: True` + `assumption: "<key>=<value> …"` on the row (as `apply_static_mappings`
   already does for NAT / RDS-IOPS).
5. Per-job override + refinement rerun: it flows through `job_assumptions_handler.go`
   (`<job>/assumptions.json`, `RunType:"refinement"`) with no special-casing.
6. **UI:** it appears in the assumptions admin panel (`internal/jobs/admin_handlers.go` + the frontend
   assumptions surface) as an editable knob showing its domain — so a new assumption the equivalence
   program introduces is visible and tunable the day it ships, exactly like the others.

**STOP GATE:** every `map` row on the corpus is backed by a claim or an assumption, or is flagged+downgraded
(no silent unbacked `map` dollars remain); any new assumption appears in the admin UI with its domain, is
per-job overridable, triggers a refinement rerun, and stamps its rows; dollar moves are confined to
previously-unbacked rows, each explained.

---

## Dependency map

```
Phase 0 (census + taxonomy + columns)
   └─► Phase 1 (AUTHOR claims, money-ranked)        ← the bulk; depends on crosswalk_review program
          └─► Phase 2 (map-time stamp, per service, after that service is authored)
                 ├─► Phase 3 (findings)
                 ├─► Phase 4 (U1 apply re-stamp) ─┬─► Phase 5 (U3 trust tier)
                 │                                └─► Phase 6 (U2 validation: 6a flag → 6b gate)
                 ├─► Phase 6.5 (D: back onto assumption layer; gate unbacked map dollars)
                 └─► Phase 7 (loop → promote_equivalence) ─► Phase 8 (visible + fatal gates + reconcile)
```

Provenance-only (dollar-parity preserved): Phases 0,1,2,3,4,7. Can change a result (flag-first, flagged,
proven): Phases 6b, 6.5, 8. **Phase 1 is the largest effort and the real gap — do not under-resource it as
if consumption were the work.** Phase 6.5 is where a degree/assumption finally *moves* a number — it needs
the stamp (2), the findings (3), and the validation (6) trustworthy first.

---

## Out of scope / non-goals

- **No confidence/error-bar model on totals** (user-deferred). `confidence_cap` is recorded per row and
  links to its backing assumption (Phase 6.5), but is not turned into a bill-level uncertainty figure here.
- **New per-mapping assumptions ARE in scope** (Phase 6.5) — but only wired as first-class knobs through the
  existing assumption layer (defaults + domains + `load_assumptions` + admin UI + refinement rerun), never
  hardcoded in a mapper.
- **No embedding/semantic equivalence** — the crosswalk is curated; the LLM reviewer stays advisory /
  long-tail.
- **No new override verb or user-facing refinement feature** — consumes the directive layer as-is plus
  U1–U3.
- **No catalog refetch; no change to CUD/commitment math or refresh cadence.**
- **Routing migration is the other workorder** (`WORKORDER_structured_dispatch_rollout.md`). This one adds
  only the equivalence stamp, in a separate PR per service.
