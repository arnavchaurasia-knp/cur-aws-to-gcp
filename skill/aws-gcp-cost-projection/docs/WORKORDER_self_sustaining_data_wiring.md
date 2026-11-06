# WORK ORDER — Make provider-mirroring data self-sustaining (wire the dead links, gate the pinned ones)

**For an implementing AI with NO prior context. Read top to bottom. Do every
ORIENTATION PROBE in Phase 0 and paste its real output into RESULT before writing
code. If a probe disagrees with this file, STOP and report — do not "fix" by
guessing.**

Companion to `WORKORDER_assumption_value_domains.md` (the assumption-domain
schema). THIS work order closes the loops that let new GCP/AWS configs reach the
product **without a human** — the refresh loops. Build the domain work order first
or in parallel; this one makes the data behind those domains auto-maintained.

### Anti-hallucination rules (whole work order)
1. **Never invent a number, a rate, a machine family, a SKU id, or an API field.**
   Every value comes from (a) data already in this repo named here, (b) a GCP/AWS
   response parsed by an EXISTING fetcher this file names, or (c) the catalog
   tables whose exact columns are given in §0. If you cannot source it, leave the
   current pinned value and set its staleness flag. Do not call an LLM for a value.
2. **REGRESSION GUARD — the load-bearing anti-hallucination device.** Every wiring
   change must, on TODAY's data, reproduce the value that is pinned/hardcoded today.
   If your new "derive from provider data" path produces a *different* value than
   the current committed one, there are only two allowed outcomes: either it is a
   genuine staleness catch (then you FLAG it, do not silently apply), or your
   derivation is wrong (then STOP and fix it). You never ship a silent dollar change.
3. **This work order changes NO projected dollar amount at current data.** It
   changes *where values come from*, not the values. Verify totals unchanged to the
   cent in Phase 10.
4. **One phase at a time. Run the test suite after each.** Baseline in Phase 0.
5. Paths under `skill/aws-gcp-cost-projection/` unless they start with `internal/`
   (Go) or `frontend/` (React). Run Python from `skill/aws-gcp-cost-projection/`.

---

## 0. ORIENTATION PROBES — run first, fill RESULT

| # | Probe | Expected (confirm) | RESULT |
|---|---|---|---|
| P1 | `python -m pytest tests/ -q 2>&1 \| tail -3` | baseline pass count — WRITE IT: ____ | |
| P2 | `python -c "import duckdb;c=duckdb.connect('data/catalog.duckdb',read_only=True);print([t[0] for t in c.execute('select table_name from information_schema.tables').fetchall()]);print([r[1] for r in c.execute(\"PRAGMA table_info('skus')\").fetchall()])"` | tables `['skus','tiered_rates']`; skus cols `sku_id, service_name, resource_family, resource_group, usage_type, description, usage_unit, service_regions`; tiered_rates cols `sku_id, tier_start, rate_usd` | |
| P3 | `python -c "import candidate_pool,sys;sys.path.insert(0,'scripts')" ; grep -n "placeholder YAML\|built-in\|_FALLBACK\|hardcoded" scripts/candidate_pool.py \| head` | candidate_pool uses a hardcoded built-in family dict (lines ~56-167); `candidate_families.yaml` / `gpu_candidates.yaml` do NOT exist on disk | |
| P4 | `ls scripts/candidate_families.yaml data/candidate_families.yaml scripts/gpu_candidates.yaml 2>&1` | all "No such file" — confirms the YAML path is dead, built-in dict always wins | |
| P5 | `grep -n "guestCpus\|memoryMb\|rollup_regions\|_family_prefix" scripts/verify_compute_availability.py` | it fetches full machineTypes (which carry `guestCpus`,`memoryMb`) but `rollup_regions` keeps only `_family_prefix(name)` → bool; shape data is DISCARDED | |
| P6 | `head -6 data/egress-rates.json data/cud_pct.json data/tpu-rates.json data/accelerator-chips.json` | all four are hand-pinned: egress "Update rates here when GCP changes"; cud_pct "Verify annually"; tpu-rates "Catalog lacks on-demand TPU SKUs, so pinned … VERIFY"; accelerator-chips "VERIFY counts … before trusting" | |
| P7 | `ls internal/jobs/*refresher*.go && grep -n "exec\|Command\|catalog_manager\|verify_compute_availability\|interval\|ticker" internal/jobs/catalog_refresher.go internal/jobs/compute_availability_refresher.go \| head` | two Go refreshers exist and shell out to the Python refresh scripts on a schedule — this is the harness new refreshers chain into | |

---

## 1. THE PATTERN (one defect, many instances)

The product already fetches provider data autonomously (`catalog_refresher.go` →
`catalog_manager.py refresh` → `catalog.duckdb`; `compute_availability_refresher.go`
→ `verify_compute_availability.py` → `compute_family_availability.json`). The
defect is downstream: **the fetched data is discarded or ignored, and the values
that drive pricing are hand-pinned copies that silently rot.** When GCP adds a
machine family or changes a price, it lands in the fetch — and goes nowhere.

Three failure shapes, all confirmed:
- **Fetched-then-discarded:** `verify_compute_availability.py` pulls full machine
  shapes (`guestCpus`,`memoryMb`) and keeps only a family→bool map. The vCPU:RAM
  ratios that `candidate_pool`/`ratio_registry`/the Aurora enum need are in that
  payload and thrown away.
- **Hardcoded-ignores-fetch:** `candidate_pool.py` hardcodes the GCP family list
  (and reads a `candidate_families.yaml` that does not exist). A new GCP family is
  invisible to it forever.
- **Hand-pinned duplicate of the catalog:** `egress-rates.json`, `cud_pct.json`
  carry GCP prices the catalog already has; `tpu-rates.json`,
  `accelerator-chips.json` carry values the catalog lacks. None has a staleness
  gate, so all four rot silently.

## 2. DESIGN POLICY (encode this, don't just follow it)

> **Any data file whose values mirror provider reality (GCP prices, GCP machine
> shapes/availability, AWS specs/prices) must EITHER derive from an autonomous,
> integrity-gated fetch, OR carry an explicit staleness gate that flags it when it
> ages out. Nothing that mirrors the provider may be hand-pinned with no refresher
> and no gate.** Pure internal policy/routing files (which service maps to which,
> confidence caps, enums) are exempt — they do not mirror provider reality.

Honesty without a human-in-the-loop: refreshes APPLY automatically but pass through
automated gates (mirroring `catalog_manager.integrity_check_file`): not-$0-where-
charged (AGENTS.md red flag), within plausible bounds, no implausible jump vs prior.
**A human is summoned only when a gate trips.** Clean refreshes ask no one.

## 3. THE SELF-SUSTAINMENT INVENTORY (authoritative — do not reclassify)

| Item | Today | Class | Target source | Phase |
|---|---|---|---|---|
| `candidate_pool` GCP families + vCPU:RAM ratios | hardcoded Python dict; dead YAML path | **A: wire to existing fetch** | extend `verify_compute_availability.py` to emit shapes → `candidate_pool` reads them | 1–2 |
| GPU candidates (`gpu_candidates.yaml`) | file missing; `load_gpu_candidates` has no fallback | **A: wire to existing fetch** | GPU machine types from the same machineTypes fetch | 3 |
| `egress-rates.json` | hand-pinned GCP egress rates | **B: derive from catalog** | GCP egress SKUs in `catalog.duckdb` | 4 |
| `cud_pct.json` | hand-pinned "verify annually" | **B: derive from catalog** | committed-SKU rate ÷ on-demand-SKU rate from `catalog.duckdb` | 5 |
| `tpu-rates.json` | pinned; catalog lacks OD TPU SKUs (self-stated) | **C: stays pinned + gate** | no catalog source → add staleness gate + flag | 6 |
| `accelerator-chips.json` | pinned AWS chip counts | **C: stays pinned + gate** | AWS structural; add staleness gate + flag (optionally derive from `data/skus/*.json.gz` later) | 6 |
| `ec2-instance-types.json`, `rds-instance-types.json`, `instance-specs.json` | `refresh_ec2_specs.py` exists but manual/one-region | **D: make scheduled + gated** | existing AWS spec refresh, wired into harness | 7 |
| `aws-product-codes.json` | `refresh_aws_product_codes.py` exists | **D: make scheduled + gated** | existing refresh, wired in | 7 |
| `service_map.json`, `service_specs.json`, `service-classification.json`, `aws-aliases.json`, `pricing_matrix.json`, `passthrough_reasons.json`, `unit_dimensions.json`, `llm_guardrails.json`, `review-config.json`, `row_state_registry.json`, `directive_*`, `workload_classification.json`, `equivalences.json`, `misc_service_context.json`, `storage-sku-keywords.json`, `validation-rules.json`, `transfer-direction-map.json` | hand-curated | **E: EXEMPT (do NOT touch)** | internal policy, not provider data | — |

> If an item's current pinned value cannot be reproduced from its target source
> (Phase regression guard fails), it DROPS to Class C for that item: keep pinned,
> add the gate, and record in your summary that derivation was not reproducible.
> Never force a derived value that disagrees with the pin.

---

## 4. PHASES (targeted; test after each; regression-guard each)

### Phase 1 — Stop discarding machine shapes (extend the existing fetch)
- [ ] In `scripts/verify_compute_availability.py`, `rollup_regions()` currently
      keeps only `_family_prefix(mt["name"])`. Extend it to ALSO accumulate, per
      family, from the machine-type objects it already has:
      `vcpus = mt["guestCpus"]`, `ram_gib = mt["memoryMb"] / 1024`, and
      `ram_per_vcpu = ram_gib / vcpus`. Emit a new top-level block in
      `compute_family_availability.json`, e.g. `"family_shapes": { "n4": {
      "ram_per_vcpu": [8], "vcpu_steps": [2,4,8,...] }, ... }` — the DISTINCT
      sorted ratios and vCPU sizes actually seen per family.
- [ ] Keep the existing `regions` bool map byte-identical (apply_static_mappings
      consumes it). Only ADD `family_shapes`.
- [ ] This script needs a live GCP token to run; DO NOT run the live fetch. Add a
      unit test that feeds `rollup_regions()` a small FAKE machineTypes dict
      (2–3 types) and asserts `family_shapes` math is right. No network in tests.
- [ ] `python -m pytest tests/ -q`.

### Phase 2 — candidate_pool reads the fetch (HEADLINE; closes the Aurora loop)
- [ ] In `scripts/candidate_pool.py`, change `load_candidate_families()` to, in
      order: (1) read `data/compute_family_availability.json` `family_shapes` if
      present and non-empty and it PASSES gates (≥1 family, every ratio in a sane
      range e.g. 0.5–32); (2) else the existing built-in hardcoded dict (keep it as
      the labelled FALLBACK, do not delete). Log which source was used.
- [ ] **REGRESSION GUARD:** after wiring, run `python scripts/gen_ratio_registry.py`
      and confirm `data/ratio_registry.json` `allowed_ram_per_vcpu` is UNCHANGED vs
      git HEAD at today's data (still `[4.0, 8.0, 16.0]` unless the live fetch
      genuinely adds a family — which won't happen offline). If it changes with no
      fetch run, your reader is wrong — STOP.
- [ ] Map proof that the loop now closes: document in your summary that
      `compute_family_availability.json.family_shapes` → `candidate_pool` →
      `gen_ratio_registry.py` → `ratio_registry.json` →
      `assumption_domains.json` (Aurora enum) is now a single data flow with no
      hardcode in the middle.
- [ ] Tests + `python scripts/gen_ratio_registry.py` clean.

### Phase 3 — GPU candidates from the same fetch
- [ ] Give `load_gpu_candidates()` the same treatment: read GPU machine types from
      `family_shapes`/a GPU block in the fetch; keep a built-in fallback; NEVER let
      a missing file raise (today it does). Add a test with a fake GPU type.

### Phase 4 — egress-rates from the catalog
- [ ] Find the GCP egress SKUs in `catalog.duckdb` (query `skus` where
      `service_name`/`description` identify interzone/interregion/internet egress;
      join `tiered_rates` for `rate_usd`). Write a derivation that produces the same
      structure as `egress-rates.json`.
- [ ] **REGRESSION GUARD:** the derived rates must equal the pinned
      `egress-rates.json` values at today's catalog. If they differ, that is either
      a real catalog/pin drift (FLAG it in your summary, keep the pin, do NOT
      auto-apply) or a wrong query (fix it). Only when they MATCH do you switch the
      loader to derive-with-pinned-fallback.
- [ ] Gate the derived values (not-$0, in-range). Pinned file becomes the fallback.

### Phase 5 — CUD — MOVED to its own work order
- [ ] **DO NOT implement CUD here.** Tracing `apply_rates.py` showed CUD is ALREADY
      catalog-first (real `Commit1Yr`/`Commit3Yr` SKUs; the `cud_pct.json`
      percentages are only a fallback). The real work is removing the fabricating
      `DEFAULT (0.75,0.60)` fallback and adding CUD completeness/lifecycle — and it
      moves real dollars, so it is split out. See
      `docs/WORKORDER_cud_catalog_derivation.md`. This Phase is intentionally empty.

### Phase 6 — tpu-rates + accelerator-chips: staleness gate (no catalog source)
- [ ] These have no catalog source today. Do NOT fetch. Add to each file's `_meta`
      a `last_verified` date and a `max_age_days` (default 180). Add their check to
      the refresh script in Phase 8/9 so that when `age > max_age_days` they emit a
      `VERIFY <file> against <source_url>` anomaly and set
      `needs_human_verification: true`. Values never change automatically.

### Phase 7 — AWS spec refreshers: scheduled + gated
- [ ] `refresh_ec2_specs.py` and `refresh_aws_product_codes.py` exist. Do NOT run
      live fetches here. Add integrity gates to each (row count not collapsing vs
      prior, no empty output) and make them runnable headless. Wiring into the Go
      harness is Phase 9.

### Phase 8 — Provenance manifest + enforcement test (encodes the policy)
- [ ] New file `data/data_provenance.json`: one entry per data file in the §3
      inventory → `{ "class": "A|B|C|D|E", "source_kind": "...", "source": "...",
      "refresher": "<script or 'none'>", "gate": true|false,
      "last_refreshed_field": "<path or null>" }`.
- [ ] New test `tests/test_data_provenance.py`:
      - every `data/*.json` that is NOT class E appears in the manifest;
      - no class A/B/C/D entry has `refresher: none` AND `gate: false`
        (i.e. nothing that mirrors the provider is both un-refreshed and un-gated);
      - class E files are listed as exempt (so adding a new provider file without
        wiring it FAILS the test — this is how the policy stays enforced).

### Phase 9 — Chain the refreshers into the autonomous harness
- [ ] Following `internal/jobs/compute_availability_refresher.go`'s pattern, after a
      successful catalog rebuild / availability fetch, invoke (out-of-band, never in
      a per-job run): `gen_ratio_registry.py`, the egress/cud derivations (Phases
      4–5), the staleness check (Phase 6), and `refresh_assumption_domains.py` (from
      the domain work order). All gated; a tripped gate records an anomaly for
      System Health and does NOT apply. If full Go wiring is beyond scope on review,
      leave each script runnable standalone and document the exact call sites — do
      NOT fake the wiring.
- [ ] `go build ./...` clean; existing refresher tests pass.

### Phase 10 — Full verification
- [ ] `python -m pytest tests/ -q` — baseline + new tests pass.
- [ ] `go build ./...` and Go tests pass.
- [ ] `python scripts/gen_ratio_registry.py` → `ratio_registry.json` unchanged at
      today's data.
- [ ] Re-run one existing job's projection; AWS total and GCP total **identical to
      the cent** vs before (record both). Any change means you shipped a silent
      value change — revert and find it.

---

## 5. OPEN DECISIONS (flag, do not guess)
- **Phase 5 CUD tolerance:** exact ratio match vs ±0.02. Recommend ±0.02 with the
  mismatch-flagging behavior; confirm with the user before applying derived CUDs,
  since they move commitment dollars.
- **Phase 7 AWS specs multi-region:** `refresh_ec2_specs.py` scrapes us-east-1 only
  (historical). Whether to fetch multiple regions is a separate decision — this
  work order only makes the existing single-region refresh scheduled+gated.

## 6. DO NOT
- Do NOT touch any Class-E file (service_map, service_specs, classification,
  aliases, guardrails, pricing_matrix, etc.). They are policy, not provider data.
- Do NOT run live GCP/AWS fetches from this work order, or add a runtime web fetch
  to any pricing path. Reading `catalog.duckdb` / `data/skus/*.json.gz` /
  `compute_family_availability.json` (all refreshed by the gated harness) IS the
  intended source.
- Do NOT apply a derived value that disagrees with the current pin — flag it, keep
  the pin. (The regression guard is the whole safety model.)
- Do NOT delete any hardcoded fallback (candidate_pool dict, pinned rate files).
  They become the gated fallback, not dead weight.
- Do NOT change projected dollars at current data.
- Do NOT "fix" unrelated issues — list them at the bottom of your summary.

## 7. DONE CRITERIA
- [ ] `verify_compute_availability.py` emits `family_shapes`; `regions` map unchanged.
- [ ] `candidate_pool` reads families/ratios/GPU from the fetch with a gated
      fallback; Aurora enum now sources live with zero value change today.
- [ ] `egress-rates` and `cud_pct` derive from the catalog with a regression guard
      and gated fallback (or are documented as not-reproducible and left pinned).
- [ ] `tpu-rates` + `accelerator-chips` carry a staleness gate that flags when old.
- [ ] AWS spec refreshers are gated and headless-runnable.
- [ ] `data_provenance.json` + enforcement test exist; no provider-mirroring file
      is un-refreshed AND un-gated.
- [ ] Refreshers chained into the Go harness (or exact call sites documented).
- [ ] pytest + go build green; projection totals unchanged to the cent.

## 8. PROGRESS LOG (append-only)
- 2026-11-06 Created from a live trace. Confirmed: `verify_compute_availability.py`
  fetches machine shapes then discards them; `candidate_pool` hardcodes families and
  its YAML path is dead; `egress-rates`/`cud_pct`/`tpu-rates`/`accelerator-chips`
  are hand-pinned with no gate; AWS spec refreshers exist but are manual. Catalog is
  a flat `skus`+`tiered_rates` DB (no machine-shape columns), so shapes must come
  from the machineTypes fetch, not the pricing catalog. Enabling dependency for
  `WORKORDER_assumption_value_domains.md`'s Aurora enum (§4.5 there).
