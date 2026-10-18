# WORK ORDER — Passthrough redesign, guard hardening, tier default, repair relocation

**For an implementing AI / engineer with NO prior context. Read fully. Each item is
independent unless noted; do in the given order, stop/report per item. These span the
pricer + review + guard + coverage files — each item says exactly where it lives.**

## Background (why this exists)

`scripts/ensure_catalog_coverage.py` is named for catalog coverage but contains THREE
repair blocks (`_repair_null_sku_names`, `_repair_illegal_passthrough`,
`_downgrade_enterprise_plus`) that clean up mistakes made in EARLIER phases. A repair
script is a confession: an upstream phase produced something wrong and we patched the
symptom downstream instead of fixing the source.

Root cause (verified in code): `scripts/llm_override_guard.py` was deliberately reduced to
a **format-only** gate. All real business protections (SKU-exists, tier safety, intentional-
passthrough protection) were moved OUT of code and INTO `data/validation-rules.json` as
instructions the LLM is *trusted* to self-enforce at prompt time. When the LLM doesn't, the
bad write lands → blows up → gets clamped → gets repaired. Every repair is one missing fence.

This work order moves the fences back into deterministic code (sourced from facts, not
hardcoded), redesigns the overloaded `passthrough` strategy, and relocates the repair code
out of the coverage file.

**Project pricing basis (applies throughout): compare AWS On-Demand LIST vs GCP On-Demand
LIST.** Rates always come from the catalog DB (`gcp_sku_rates`) or the GCP Billing API —
NEVER fabricated. (Separately in flight: a Phase-4 rate-resolution bug is being fixed by
others — do not touch rate resolution here; assume it is handled.)

---

## G-1 — Enforce "SKU must exist in catalog with a real rate" in the guard (the no-fabrication rule)

**Lives in:** `scripts/llm_override_guard.py` (`validate_override`), used by
`scripts/apply_review_fixes.py`.
**Problem:** `validate_override` only checks the SKU *format* (`XXXX-XXXX-XXXX`) and that the
multiplier is positive. `data/validation-rules.json` rule `sku_exists_in_region` (BLOCK)
says the SKU must EXIST and COVER the region — but that is only an instruction the LLM is
trusted to follow. A well-formed but non-existent/region-missing SKU passes the guard, then
prices to $0 or a wrong rate.
**Fix:** in `validate_override`, after the format check, query the DB: the `gcp_sku_id` must
exist in `gcp_sku_rates` with a `pricing_type='OnDemand'` row whose `region` is the row's
`gcp_region`, OR `global`, OR the bill's dominant region (same fallback chain
`projection_view.py` uses). If no such rate row exists → return `(False, reason)` so
`apply_review_fixes.py` rejects the override (it already handles a False return by skipping
and counting an error). The guard has `conn` in scope — use it.
**Also apply to the Phase-2 write path:** the same check belongs wherever the Phase-2 misc/
managed_db/compute LLM mappings are merged (`scripts/merge_mappings.py`) — a merged row
whose SKU has no catalog rate must not silently become `strategy='map'`. Follow G-3's
decision for what to do when it fails (reprice → else passthrough).
**Test:** add to `tests/test_llm_override_guard.py` (create if absent): (a) a well-formed SKU
that IS in `gcp_sku_rates` for the region → accepted; (b) a well-formed SKU NOT in
`gcp_sku_rates` → rejected with a clear reason; (c) a SKU present only in `global` → accepted
via fallback.

---

## G-2 — Enforce intentional-passthrough protection in the guard

**Lives in:** `scripts/llm_override_guard.py` (`validate_override`).
**Problem:** `validation-rules.json` rule `no_intentional_passthrough_override` (BLOCK)
protects rows whose `projection_note` says "no GCP equivalent" / "no GCS equivalent" /
"no direct GCP equivalent" (a static mapper deliberately left them passthrough). This is
LLM-trust only; nothing in code stops an override from un-protecting them. This is the exact
bug the docstring at `apply_review_fixes.py:156-160` describes (a 50x Glacier Early-Delete
regression).
**Fix:** reuse the canonical phrase list — import
`intentional_passthrough_exclude_clause` / the phrase constants from
`scripts/apply_static_mappings.py` (do NOT hand-copy the phrases; they already drifted once).
In `validate_override`, read the row's current `projection_note`; if it matches an
intentional-passthrough phrase AND the override would set `strategy='map'`, reject.
**Test:** a row with "no GCS equivalent" in its note + an override to map → rejected; a normal
row → unaffected.

---

## P-1 — Split the overloaded `passthrough` strategy (legitimate vs escape)

**Lives in:** conceptual + `scripts/apply_rates.py`, `scripts/outlier_gate.py`,
`scripts/llm_guidance.py`, `scripts/classify_mechanics.py` (guidance text),
`scripts/render_report.py`, `scripts/projection_view.py`.
**Problem:** `strategy='passthrough'` currently means TWO different things:
  1. **Legitimate** — "no GCP equivalent exists" (SES, Glacier Early-Delete). Carrying the
     AWS cost is the correct, honest answer. A permanent FACT.
  2. **Escape** — "a GCP equivalent exists but I couldn't get a rate / the row looked thin,
     so I bailed." A FAILURE wearing a decision's clothes.
Conflating them is the root of the `_repair_null_sku_names` and `_repair_illegal_passthrough`
repairs: downstream code can't tell an honest passthrough from a give-up.
**Fix (design — confirm shape with reviewer before coding):**
- Keep `strategy='passthrough'` for the LEGITIMATE case ONLY. It must be justified by a
  `projection_note` intentional-passthrough phrase (G-2's list) — i.e. a registry/static-
  mapper fact. The report keeps showing these as "Passthrough (no GCP equivalent)".
- Introduce an explicit **`needs_reprice`** state (new `strategy` value, or a boolean column
  `needs_reprice` — reviewer picks; a column avoids touching every `strategy` CASE) for the
  escape case: "a rate could not be resolved for a mappable row." This row is NOT a finished
  estimate — it must be re-resolved (G-3), never silently carried.
- Update every place that currently *creates* an escape-passthrough to emit `needs_reprice`
  instead: `apply_rates.py` (the `rate_source IS NULL` → passthrough transitions),
  `outlier_gate.py` (the clamp — see G-4), and the `llm_guidance.py` fallback
  (`fb_strategy = "passthrough" if cost<10 ...` at ~line 228) when the service IS mappable.
- `projection_view.py`: a `needs_reprice` row that reaches the view un-resolved prices as
  passthrough (AWS cost) BUT is flagged loudly (see P-2).
**Test:** a mappable row with no rate → `needs_reprice`, not passthrough; an SES row → stays
passthrough. Both classifier suites + pricer suites stay green.

---

## P-2 — Loud surfacing of `needs_reprice` leftovers

**Lives in:** `scripts/render_report.py` + `data_gaps.json` writer.
**Problem:** today an escape-passthrough silently becomes a carried AWS number in the report —
the customer can't tell a real estimate from a give-up.
**Fix:** any row still `needs_reprice` at report time is listed in the report's data-gaps /
loud-failure section ("$X could not be priced on GCP — carried at AWS cost; needs a rate")
and in `data_gaps.json`. It still contributes AWS cost to the total (so totals aren't
understated) but is clearly labeled as not a GCP-native estimate.
**Test:** a bill with one unresolved `needs_reprice` row renders it in the data-gaps section
with its dollar amount.

---

## G-3 — Reprice flow: LLM re-resolves `needs_reprice`, else passthrough

**Lives in:** Phase 5 review path (`scripts/auto_review.py` candidate generation +
`scripts/apply_review_fixes.py`), guided by `data/validation-rules.json`.
**Decision (confirmed with owner):** a `needs_reprice` row must be repriced by the **LLM**,
not a deterministic mapper (the static mapper would re-derive the same wrong SKU, since it's a
fixed script). Guardrail: the LLM MUST obtain the rate from the catalog DB (`find-sku.sh` /
`gcp_sku_rates`) or the GCP Billing API — it may NEVER fabricate a price (enforced by G-1's
guard check at write time). **If the LLM cannot find a real rate, the row falls back to
legitimate `passthrough`** (carried at AWS cost) with a note "no GCP rate found — carried at
AWS cost", and surfaces per P-2.
**Fix:** ensure `auto_review.py` includes `needs_reprice` rows in its flagged-candidate set so
the Phase-5 LLM is asked to reprice them; ensure the reprice override passes through G-1's
hardened guard; ensure a failed reprice lands as passthrough + note, not a silent carry.
**Test:** a `needs_reprice` row the LLM can resolve → becomes map with a real catalog rate; one
it cannot → becomes passthrough with the "no GCP rate found" note.

---

## G-4 — Clamp must not destroy deterministic mappings

**Lives in:** `scripts/outlier_gate.py` (~line 192).
**Problem:** the outlier clamp does `SET strategy='passthrough', gcp_sku_id=NULL,
gcp_sku_name=NULL` on any blown-ratio row — INCLUDING `DETERMINISTIC_GROUPS` rows
(object_storage etc., see `classify_mechanics.DETERMINISTIC_GROUPS`). Erasing the SKU name is
exactly what forces `ensure_catalog_coverage._repair_null_sku_names` to re-stamp it.
**Fix:** the clamp must NOT null out the SKU identity. Instead mark the row `needs_reprice`
(P-1) while KEEPING `gcp_sku_id`/`gcp_sku_name` for reference, and stamp the existing
`[outlier_gate: ...]` note. The reprice flow (G-3) then re-resolves it. A blowup means the
RATE was wrong, not that the mapping should be deleted.
**Test:** an object_storage row with a blown ratio → `needs_reprice`, SKU name preserved, not
NULL.

---

## T-1 — Deterministic DB-edition tier default (Enterprise, not Plus)

**Lives in:** the managed_db mapping path (`data/service_specs.json` rds/aurora entry +
`scripts/spec_dispatch.py` / wherever the Cloud SQL SKU edition is chosen) and
`data/validation-rules.json` (add a tier rule).
**Problem:** there is NO rule governing Cloud SQL edition. The Phase-2 LLM free-picks and
over-picks "Enterprise Plus" (~2x). The only existing fix is the downstream
`_downgrade_enterprise_plus` repair.
**Decision (confirmed with owner):**
1. **Default edition = Enterprise** (standard), not Enterprise Plus, for a like-for-like
   migration — expressed as a deterministic default in the spec/mapper, so the LLM starts
   from Enterprise.
2. The LLM may UPGRADE to Enterprise Plus ONLY with explicit CUR evidence that the source
   workload needs Plus-tier features (near-zero-downtime failover, Data Cache). Add a
   `validation-rules.json` rule `db_edition_default_enterprise` (BLOCK on an unjustified Plus
   pick) mirroring the burstable-safety rule.
3. **When CUR gives no edition evidence** (the common case): pick by GCP tier capability
   match if determinable from the instance/workload; if NOT determinable, fall back to the
   edition whose price is CLOSEST to the AWS cost — and stamp a note
   `[tier chosen by price-proximity — no CUR evidence of edition; confirm with customer]`.
   (Caveat to honor: price-proximity anchors GCP to AWS and can hide a real price gap — hence
   the mandatory note so it's never mistaken for evidence-based.)
**Test:** a Cloud SQL row with no edition evidence → Enterprise (or price-closest edition)
with the review note; a row with evidence of Plus features → Plus, justified in the note.

---

## R-1 — Relocate the repair code out of the coverage file

**Lives in:** `scripts/ensure_catalog_coverage.py` → `scripts/validate_fix.py` (the
pipeline's designated autofixer) OR a new `scripts/repair_mappings.py` (reviewer picks).
**Decision (confirmed with owner):** keep the repair code for safety (defense-in-depth) but
move it OUT of a file named for coverage. After G-1/G-2/P-1/G-4/T-1 land, each repair's root
cause is fixed, so each repair becomes a thin backstop — then delete the ones proven dead.
**Fix:**
1. Move `_repair_null_sku_names`, `_repair_illegal_passthrough`, `_downgrade_enterprise_plus`
   (and their helpers/imports) from `ensure_catalog_coverage.py` into the chosen repair file.
   Update the orchestrator (`internal/jobs/orchestrate.go`) Phase-4 wiring if the call site
   moves (they currently run inside `ensure_catalog_coverage.main()`; if folding into
   `validate_fix.py`, call them from there instead — validate_fix already runs in Phase 4
   PostLLMScripts).
2. `ensure_catalog_coverage.py` keeps ONLY Step 2 (the actual coverage check + API refresh)
   and `main()` shrinks to it. The file name now matches what it does.
3. Leave a one-line comment at each moved repair noting the root-cause item that should
   eventually retire it (G-4 → null-sku repair, G-1/P-1 → illegal-passthrough repair,
   T-1 → Enterprise-Plus downgrade).
**Test:** existing behavior preserved (the repairs still run, just from the new location);
coverage check still runs. All suites green. No repair logic remains in
`ensure_catalog_coverage.py`.

---

## Report back (per item)
Files changed, the test added, and anything that needed a decision you couldn't make.
Do NOT hardcode SKU ids or rates anywhere — the catalog DB / API is the contract.
