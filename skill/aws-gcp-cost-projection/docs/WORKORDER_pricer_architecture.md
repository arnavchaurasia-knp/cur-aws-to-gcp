# WORK ORDER — Pricer architecture / consolidation (apply_static_mappings + family_mapper + apply_rates)

**For an implementing AI / engineer with NO prior context. Read fully. These are STRUCTURAL
changes decided by the owner. They are larger than the point-fixes in
`WORKORDER_pricer_correctness.md` — do those first, then these. Each AR-item is independent; do in
order, keep BOTH classifier/pricer test suites green after each, stop/report per item.**

Test command (from `skill/aws-gcp-cost-projection/`):
```
SKILL_DIR="$(pwd)" python -m pytest tests/ -q
```
Pricing basis: **AWS On-Demand LIST vs GCP On-Demand LIST** only. Never fabricate a GCP number —
if no rate resolves, `strategy="passthrough"` with a note.

---

## AR-1 — Make the strict SKU resolver the DEFAULT; opt INTO continent fallback
**Lives in:** `scripts/apply_static_mappings.py` — `resolve_sku` (~1917), `_strict_resolve_sku`
(~2025), and every caller.
**Why:** Today `resolve_sku()` silently falls back to a same-CONTINENT SKU when the exact region
has no rate. This is documented to have priced a Delhi (asia-south2) bill off Taiwan (asia-east1)
rates (~comment at 2040). The safe variant `_strict_resolve_sku()` refuses that, but callers must
REMEMBER to use it — any new mapper that calls plain `resolve_sku` silently re-opens the bug. A
footgun that defaults to "wrong" is the wrong default.
**Fix (make it impossible to get wrong):**
1. Change the DEFAULT behaviour so an exact-region (or genuine `global`) rate is required: rename
   the current fallback-allowing body to an explicit opt-in — `resolve_sku(..., allow_continent_fallback=False)`
   with the default `False`. When `False` and no exact/global rate exists, return `SKUMeta(None,...)`
   (caller emits passthrough), never a neighbouring region's SKU.
2. The ONLY callers that pass `allow_continent_fallback=True` are the genuinely
   continent-restricted niche families the fallback was built for (T2A and any SKU the catalog
   only sells at continent granularity) — and even then the row MUST carry a
   `projection_note` saying which region's rate was substituted ("no asia-south2 rate; priced at
   asia-east1").
3. Delete `_strict_resolve_sku` once the default is strict (it becomes the default); update its
   callers to the plain `resolve_sku`.
4. Audit every `resolve_sku(` call site: core/ram/GPU/storage SKUs must NOT pass
   `allow_continent_fallback=True`.
**Test:** an m6g row in asia-south2 with no in-region Arm rate → `strategy="passthrough"` (NOT a
Taiwan-priced map row); a T2A continent-restricted SKU still resolves WITH a substitution note.

---

## AR-2 — Move the live GCP API availability/SKU check OUT of pricing, INTO a pre-job refresh step
**Lives in:** moving code OUT of `scripts/apply_static_mappings.py` (the `_live_*` functions ~765-1100,
`_live_sku_fetch`/`_live_sku_list` ~1837-1916, `_gcp_token*` ~1764-1836) INTO the maintainer
refresh step (`scripts/prefetch_skus.py`, run BEFORE jobs execute).
**Decision (owner):** the live GCP check is still REQUIRED and must run **for every SKU/service** —
but it must NOT run inline, per-row, during a job's pricing phase (it makes the deterministic
phase non-deterministic, slow on big bills, and fragile on Windows via the `gcloud` subprocess).
**Fix:**
1. In the refresh step (`prefetch_skus.py`), iterate EVERY SKU/service the registry + catalog
   know about and resolve each against the live GCP Billing/Compute APIs: SKU id, per-region
   availability, and rate. Write the results into the shared caches the pricer already reads
   (`data/resolved_skus.json` + the catalog / `gcp_sku_rates` seed) with a `catalog_version` tag.
   This is the deliberate, reviewable maintainer step that is ALLOWED to write the shared cache.
2. In `apply_static_mappings.py`, `resolve_sku()` becomes **read-only against the frozen cache +
   bundled catalog** — remove the inline live-fetch path (the `print("… trying live API")` branch
   ~1995 and the `_live_*` calls). If a SKU is genuinely absent from the frozen cache at job time,
   emit `passthrough` + a `rate_gaps.json` entry naming the SKU (so the NEXT refresh picks it up),
   never a live call mid-job.
3. Keep availability data (which regions a family/SKU is sold in) in the frozen cache so the
   strict resolver (AR-1) can answer "is this available in-region" without a network call.
**Test:** a full pricing run makes ZERO network calls / ZERO `gcloud` subprocesses (assert by
running with no credentials — pricing still completes from the frozen cache); a SKU missing from
the cache → passthrough + rate_gaps entry, not a crash or a live fetch.

---

## AR-3 — Consolidate compute pricing: fold family_mapper.py INTO apply_static_mappings.py
**Lives in:** `scripts/family_mapper.py` → merged into `scripts/apply_static_mappings.py`; update
`internal/jobs/orchestrate.go` Phase-2 `PreLLMScripts` (remove the separate `family_mapper.py`
entry once its logic lives in `apply_static_mappings.py`).
**Decision (owner):** there should be ONE compute pricer, and it lives in `apply_static_mappings.py`.
Today EC2→GCE breakdown logic is split between `family_mapper.py` (the main compute path: GCE
core+ram, GPU, TPU, local SSD) and `apply_static_mappings.py` (`map_compute_windows/arm/burstable`
+ the `cheapest_*` helpers). Two files = two places to fix every compute bug.
**Fix:**
1. Move `family_mapper.py`'s functions into `apply_static_mappings.py`: the GCE core+ram
   breakdown, `map_gpu_row`, the TPU path (`map_tpu_row`), local-SSD attachment, and the
   `_compute_mapper_kind` dispatch. Place them alongside the existing `map_compute_*` functions.
2. **De-duplicate:** `apply_static_mappings.py` already has `cheapest_in_scope` / `cheapest_family`
   / `cheapest_gpu_in_scope`. Make the moved family_mapper code call THESE (do not bring in a
   second copy). Where family_mapper had logic the static file lacks (e.g. TPU mapping, local-SSD
   capacity scaling from P-11, the `_accel_kind`/GPU-profile detection), ADD it into the shared
   path so nothing is lost.
3. **Reconcile differences:** anywhere the two files price the SAME thing differently (e.g. Arm
   handling in `map_compute_arm` vs family_mapper's Arm path), keep the MORE CORRECT one and
   write a one-line comment noting what was reconciled. Report any conflict you cannot resolve.
4. Have ONE entry in Phase 2 that does all compute. Keep the output JSON schema identical so
   `merge_mappings.py` and downstream are unaffected.
5. Port family_mapper's tests (`tests/test_family_mapper_compute.py`, `tests/test_family_mapper*.py`)
   so they exercise the consolidated code; all must pass.
**Caution:** this is the largest change here — do it in one focused pass, keep the schema and the
`cheapest_*` helpers as the single source of truth, and run the full suite after. If anything in
family_mapper has no equivalent and is unclear, STOP and report rather than dropping it.
**Test:** every existing compute test (EC2 x86, Graviton/Arm, GPU, TPU, Windows, burstable, local
SSD) passes against the consolidated `apply_static_mappings.py`; `family_mapper.py` is removed (or
reduced to a thin import shim) and the orchestrator no longer calls it separately.

---

## AR-4 — Deterministic region-rate fallback chain (global → dominant-region, availability-aware)
**Lives in:** `scripts/apply_rates.py` (the `global` synthesis ~735-752, the unreachable check
~957-969, `compute_dominant_region` ~952) and the rate pick in `scripts/projection_view.py`
(the `COALESCE(regional, global, …)` rate joins).
**Decision (owner):** when a row's exact GCP region has no rate, fall back to **global if it
exists, else the dominant (most-common) region on this bill — but only among regions where the
SKU is actually available.** Never silently price off an arbitrary / most-expensive region.
**Fix — implement this exact ordered chain for picking a SKU's rate for a row:**
1. **Exact region:** `rate WHERE region = row.gcp_region`. Use it.
2. **Global:** else `rate WHERE region = 'global'` (true multi-region/global SKUs — CDN, DNS,
   some egress). Use it.
3. **Dominant region (availability-checked):** else the bill's dominant region
   (`compute_dominant_region`) **IF the SKU has a rate there**. Use it, and annotate the row:
   `projection_note += "priced at <dominant_region> rate (no rate in <row_region>)"`.
4. **Nearest-available, disclosed:** else if the SKU has a rate in SOME region but neither the
   row's region, global, nor dominant — pick the **cheapest available region's** rate (not MAX,
   not AVG) and annotate loudly: `"no rate in <row_region>/global/<dominant>; priced at cheapest
   available region <region>"`. (Cheapest, so we never over-project a customer on a region they
   aren't even in.)
5. **No rate anywhere:** `strategy="passthrough"` + `rate_gaps.json` entry (already the
   unreachable→outlier_triage path — keep it).
**Also fix the synthetic `global` row:** today the synthesized global row AVERAGES all regional
rates (~743). Change it to the **dominant region's rate** when a dominant region exists and the
SKU has a rate there (a real region the customer uses), falling back to the average only when it
doesn't. Averaging across all regions blends in regions the customer never touches.
**Test:** (a) a row whose region has a rate → exact rate; (b) a global SKU → global rate; (c) a
row whose region lacks a rate but the dominant region has one → dominant rate + note; (d) a row
with no exact/global/dominant rate but available elsewhere → cheapest available region + loud
note; (e) a SKU with no rate anywhere → passthrough + rate_gaps.

---

## AR-5 — Assumption rows: a structured flag, a `*` marker in the report, and a bottom "Assumptions" section
**Lives in:** the mapping emit schema (all `map_*` in `apply_static_mappings.py` + family compute)
and the report layer (`scripts/render_report.py` / `export_report.py`; the view
`projection_view.py` if the flag must surface in SQL).
**Why:** several mappings are priced on a **labeled assumption**, not a clean 1:1 fact — Aurora
Serverless v2 ACU→vCPU (P-3, ~0.25), NAT Cloud-NAT VMs-per-gateway (P-15), Inferentia/Trainium→TPU
1:1 chip parity, and any future estimated ratio. The customer must be able to SEE which numbers
rest on an assumption and read the basis.
**Fix:**
1. **Structured flag on the mapping entry.** Add two optional keys to the emitted mapping dict
   (the same dict every `map_*` builds): `"is_assumption": true` and
   `"assumption": "<one-line basis>"` (e.g. "1 ACU ≈ 0.25 vCPU + 2 GiB (empirical, db.r5.xlarge)").
   Mappers that price on an assumption set these; everything else omits them. Do this via the
   shared `_emit()` helper (below) so it's one code path. Carry the flag through
   `merge_mappings.py` into `aws_li_to_gcp_li` (add an `is_assumption` / `assumption_note` column)
   so the report can read it.
2. **`*` marker in the report.** In the per-service / per-row rendering, any row with
   `is_assumption = true` gets a `*` superscript next to the GCP figure (or the instance name).
   Group multiple distinct assumptions with `*`, `**`, `†` etc., or reuse a single `*` keyed to the
   bottom section — pick one scheme and apply it consistently.
3. **Bottom "Assumptions" section.** At the end of the report, render an **Assumptions** block
   listing each distinct assumption used in THIS bill, its `*` marker, the services/rows it applies
   to, and the one-line basis. Only list assumptions actually present in the bill. This sits near
   the "Discounts by service" / net-AWS-total blocks (see `WORKORDER_pipeline_hardening.md` H-3).
**Test:** a bill containing an Aurora Serverless v2 row and a public-NAT row renders: (a) a `*` on
each of those GCP figures, (b) a bottom Assumptions section naming the ACU ratio and the NAT
VMs/gateway estimate with their bases; a bill with no assumption rows renders no `*` and no
Assumptions section.

---

## Note — shared primitives (folds into the above, not a separate item)
The GB→GiB constant is centralized in `WORKORDER_pricer_correctness.md` P-1. While doing AR-3,
also introduce the two remaining shared helpers so the consolidated file doesn't re-drift:
- a single `_emit(strategy, service, sku, note, confidence, **extra)` row-builder every mapper
  uses (kills the per-copy `strategy="map"` drift that caused P-2 and the Firestore label bug);
- a single `family_arch(family_token)` → "arm"/"x86" + tier helper (kills the per-function ARM
  lists that caused the MSK r6g bug P-6).
Apply them across the mappers as you touch them; do not do a separate big-bang rewrite.

## Report back (per item)
Files changed, tests added/ported, any conflict you could not reconcile (esp. AR-3), and
confirmation that a credential-less pricing run makes zero network calls (AR-2).
