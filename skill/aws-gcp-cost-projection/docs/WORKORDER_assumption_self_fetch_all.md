# WORK ORDER — Put every assumption on a self-fetching, fully-tracked footing

**For an implementing AI with NO prior context. Read top to bottom. Do every
ORIENTATION PROBE in Phase 0 and paste real output into RESULT before writing code.
If a probe disagrees with this file, STOP and report.**

## The goal in one paragraph (plain)
Today only **2 of 18** assumption values self-refresh (`aurora_acu_vcpu`,
`aurora_acu_ram_gib`, re-derived in `refresh_assumption_domains.py` from
`ratio_registry.json`). The other 16 are `refreshable:false` — the refresher merely
staleness-flags the provider-published ones and skips the rest. The owner wants EVERY
assumption on the same footing as the repo's other self-fetching verticals
(`catalog_manager.py refresh`, `verify_compute_availability.py`,
`check_cud_coverage.py`): a refresher that actively **sources** the value (or
explicitly records that no upstream source exists), with uniform run-tracking through
`data/refresh_runs/` and the `REFRESH_SUMMARY` contract.

## The honesty constraint that shapes the whole design (READ — do not "fix" by ignoring)
`AGENTS.md` forbids fabricating a rate/ratio, and a $0 or invented value on something
real is a red flag, never a green light. **Some assumptions have no fetchable upstream
source anywhere** — there is no published Inferentia-vs-TPU benchmark, Google removed
the public Looker/Data-Studio-Pro price, DynamoDB RCU→ops is workload-dependent, and
`rds_iops_included_in_storage` is a policy switch. For these, "self-fetch" is
impossible without fabrication. This work order therefore defines **four source tiers**;
"self-fetched" is achieved honestly for Tiers A–C, and Tier D is made **uniformly
tracked** (the refresher asserts "no upstream source" as a first-class, audited
outcome) rather than faked. Do NOT convert a Tier-D key to a fabricated fetch to make
a coverage number look nicer.

The repo already has two self-fetch flavors — copy them, invent nothing new:
- **Live provider fetch:** `verify_compute_availability.py` queries the real Compute
  Engine `machineTypes.aggregatedList` API; `catalog_manager.py refresh` pulls the GCP
  Billing Catalog API.
- **In-repo derivation from a fetched artifact:** `check_cud_coverage.py` derives from
  `catalog.duckdb`; `derive_aurora_values()` derives from `ratio_registry.json`.

### Anti-hallucination / anti-fabrication rules
1. **Never invent a number.** If a source cannot be reached or does not exist, the
   refresher records `NO_SOURCE` / `STALE` / `VERIFY` for that key — it does not write
   a guessed value. Keep the existing `validate_derived_values` gates (reject $0, null,
   out-of-range, and > `max_refresh_delta_pct` jump) and apply them to EVERY new fetch.
2. **Do not change any default value or any projected dollar in this work order.** This
   builds the fetch/track machinery and wires it to *verify* current pinned values.
   Changing a value is a separate, gated event (a passing fetch that differs, within
   delta bounds) and must be logged in the run record, not hand-edited here.
3. **Reuse the pattern.** New per-key derivations go in `scripts/assumption_domains.py`
   next to `derive_aurora_values`; the refresh loop stays in
   `refresh_assumption_domains.py`; tracking stays the `REFRESH_SUMMARY` + `refresh_runs/`
   contract. No new state mechanism, no new tracking file format.
4. **Live network fetches must be fail-safe and offline-safe** (like
   `verify_compute_availability.py`): a fetch that errors or times out yields
   `STALE`/`VERIFY` for that key and NEVER aborts the run or overwrites a good pinned
   value. Tests must run without network (mock/skip the fetch).
5. **COORDINATION:** `data/assumption_impact.json` and `data/assumption-defaults.json`
   are under active concurrent editing. Re-read immediately before touching; if they
   shift, STOP and report. Prefer adding to `assumption_domains.json` (committed) and
   the scripts over editing the two live files.
6. One phase at a time; run the named test after each. Python lives under
   `skill/aws-gcp-cost-projection/`; the Go orchestrator lives at repo-root
   `internal/jobs/`.

---

## 0. ORIENTATION PROBES — run first, fill RESULT

| # | Probe | Expected (confirm) | RESULT |
|---|-------|--------------------|--------|
| P1 | `python -c "import json;d=json.load(open('data/assumption_domains.json'));print(len(d['domains']))"` | 18 domains | |
| P2 | `grep -c '"refreshable": true' data/assumption_domains.json; grep -c '"refreshable": false' data/assumption_domains.json` | ~2 true, ~16 false | |
| P3 | `sed -n '1,193p' scripts/refresh_assumption_domains.py` | current loop: derived_in_repo (aurora) + provider_published staleness + judgment skip; emits REFRESH_SUMMARY | |
| P4 | `grep -n "def derive" scripts/assumption_domains.py` | only `derive_aurora_values` exists today | |
| P5 | `ls data/refresh_runs/` | per-source `<src>.json` + `<src>.history.json`; `assumption_domains.json` present | |
| P6 | `grep -n "executeAssumptionDomainsRefresh" internal/jobs/refresh_orchestrator.go` | executor registered (~221) + defined (~860); confirm it shells `refresh_assumption_domains.py` and parses REFRESH_SUMMARY | |
| P7 | `grep -rn "instance_specs\|family_specs\|node.*gib\|aggregatedList\|machineTypes" scripts/ | head` | find the in-repo AWS/GCP spec source for node-RAM derivation (Tier A) — if none exists, Tier A3/A4 must add one or drop to Tier D | |
| P8 | `grep -n "public.*on.?demand\|publicOnDemand\|amortized\|savings_plan\|reserved" scripts/ingest.py | head` | bill fields needed for Tier-C bill-derivation (public OD cost, amortized cost, SP/RI/Spot markers) | |

If P7 finds no machine-readable AWS node-spec source, say so in RESULT: `dax_node_gib`
and `memorydb_node_gib` then CANNOT be Tier A and move to Tier D (tracked, not fetched)
until such a source is added — do not hardcode a spec table and call it a fetch.

---

## 1. SOURCE-TIER CLASSIFICATION (the design — assert each in Phase 0, don't trust blindly)

Add a `refresh_tier` field (`A|B|C|D`) and a machine-readable `source_url` /
`source_artifact` to each domain in `assumption_domains.json` so the refresher
dispatches on tier and the report is auditable.

### Tier A — derive in-repo from a fetched artifact (the aurora pattern)
| Key | Derive from | Function to add |
|---|---|---|
| `aurora_acu_ram_gib` | `ratio_registry.json` | DONE (`derive_aurora_values`) |
| `aurora_acu_vcpu` | `ratio_registry.json` | DONE |
| `dax_node_gib` | AWS DAX node (R-family) RAM specs — **only if P7 finds a spec source** | `derive_node_ram_values("dax")` |
| `memorydb_node_gib` | AWS MemoryDB `db.r6g/r7g` node RAM specs — same caveat | `derive_node_ram_values("memorydb")` |
| `lambda_mb_per_vcpu` | GCP Cloud Run max-mem-per-vCPU ceiling (provider limit) | `derive_lambda_mb_per_vcpu()` (bounds from the documented Cloud Run cap; pin source_url) |

### Tier B — fetch the provider-published scalar live (the compute-availability pattern)
| Key | Source URL | Notes |
|---|---|---|
| `glue_dpu_vcpu` | `https://aws.amazon.com/glue/pricing/` | fetch DPU definition (1 DPU = 4 vCPU); compare to pinned, flag on drift |
| `glue_dpu_ram_gib` | same | 1 DPU = 16 GB |

Tier-B fetch is fail-safe (rule 4): network error → `VERIFY`, never overwrite.

### Tier C — self-calibrate from the customer's own bill (per-job, not a global refresh)
These are **bill-derivable** — the most honest self-fetch for them is the actual
realized value in the bill being projected, computed at job time, not a global default.
| Key | Derive from (bill) |
|---|---|
| `aws_ri_1yr_discount_pct` | 1yr-RI-covered lines: `1 − amortized / public_on_demand_cost` |
| `aws_ri_3yr_discount_pct` | 3yr-RI-covered lines, same formula |
| `aws_sp_discount_pct` | SP-covered usage vs public OD |
| `aws_spot_discount_pct` | Spot usage vs public OD |
| `nat_vms_per_gateway` | ATTEMPT: NAT-gateway count vs attached VM-hours in the bill; if not reliably derivable, it stays Tier D (judgment) — flag, don't guess |

Tier-C design decision (flag, don't guess): compute the realized ratio **per job** in
`ingest.apply_od_basis` / a new `calibrate_assumptions_from_bill` step, write it to the
per-job `assumptions.json` snapshot as `value_source:"bill_derived"`, and fall back to
the committed default when the bill has no such lines. The global `assumption_domains`
refresher records these as `BILL_CALIBRATED` (sourced at job time), not `NO_SOURCE`.

### Tier D — no fetchable upstream source → tracked, never fabricated
| Key | Why unfetchable | Tracked outcome |
|---|---|---|
| `inferentia_chip_to_tpu` | no public benchmark | `NO_SOURCE` (passthrough default 0 stays) |
| `trainium_chip_to_tpu` | no public benchmark | `NO_SOURCE` |
| `dynamodb_rcu_to_reads_sec` | workload-dependent | `NO_SOURCE` (passthrough default 0) |
| `dynamodb_wcu_to_writes_sec` | workload-dependent | `NO_SOURCE` |
| `rds_iops_included_in_storage` | policy switch | `POLICY` (not refreshable by nature) |
| `looker_studio_pro_seat_usd` | Google removed public price | `NEEDS_HUMAN` (already flagged; keep until admin confirms in Cloud console) |

Tier D is the honest core of this work order: these keys become **uniformly tracked**
(every run emits their outcome), which is the "fully tracking like other verticals" the
owner asked for — the system knows and reports that they have no source, forever, until
one appears.

---

## 2. PHASES

### Phase 1 — Tag tiers + sources in the registry
- [ ] Add `refresh_tier` (`A|B|C|D`) and machine-readable `source_url`/`source_artifact`
      to every domain in `data/assumption_domains.json`. No value changes.
- [ ] Test: a unit test asserting all 18 keys carry a valid `refresh_tier` and that
      every Tier-A/B key has a resolvable source pointer.

### Phase 2 — Tier A derivations (in-repo, offline)
- [ ] Add `derive_node_ram_values()` and `derive_lambda_mb_per_vcpu()` to
      `scripts/assumption_domains.py`, each sourced from the artifact P7 confirmed.
      If P7 found no node-spec source, implement the spec source first (as a fetched
      artifact) or leave these in Tier D and say so.
- [ ] Wire them into `refresh_assumption_domains.py` alongside the aurora branch,
      through `validate_derived_values` (keep all gates).
- [ ] Test: each derivation returns the current pinned value (byte-identical — rule 2),
      and a mutated artifact is rejected by the delta gate.

### Phase 3 — Tier B live fetch (fail-safe)
- [ ] Add a `fetch_provider_scalar(key, source_url)` helper (requests + timeout +
      try/except → `VERIFY` on any failure, like `verify_compute_availability.py`).
- [ ] Wire Glue keys; on a successful fetch that matches, set
      `needs_human_verification=false` and bump `last_verified`; on drift within delta,
      record `UPDATED` in the run report (do not silently change the default in this WO —
      surface it); on drift beyond delta, `REJECT` + flag.
- [ ] Test (offline): fetch mocked to return the pinned value → OK; mocked to error →
      `VERIFY`, value untouched; mocked to return $0 → rejected by honesty gate.

### Phase 4 — Tier C bill calibration (per-job)
- [ ] Add `calibrate_assumptions_from_bill(con)` (new step or inside
      `ingest.apply_od_basis`) computing realized RI/SP/Spot discount pct from the bill,
      writing to the per-job `assumptions.json` with `value_source:"bill_derived"`;
      fall back to committed default when the bill lacks those lines.
- [ ] `nat_vms_per_gateway`: attempt derivation; if not reliable, leave as default and
      record why (Tier D) — do not guess.
- [ ] Test: a fixture bill with known RI amortized vs public OD yields the expected
      realized pct; a bill with no RI lines falls back to the default unchanged.

### Phase 5 — Uniform tracking for ALL 18 (including Tier D)
- [ ] Extend `refresh_assumption_domains.py` so the loop visits **every** key and emits
      an `item_outcome` for each: `SELF_FETCHED` (A/B success), `DERIVED`,
      `BILL_CALIBRATED` (C), `NO_SOURCE` / `POLICY` / `NEEDS_HUMAN` (D), or `STALE` /
      `VERIFY` / `REJECTED` on problems. `expected_count` = 18; `fetched_count` counts
      A/B/C successes; the Tier-D outcomes are reported, not counted as failures.
- [ ] Confirm `data/refresh_runs/assumption_domains.json` + `.history.json` capture the
      per-key outcomes (same shape other verticals use).
- [ ] Confirm the Go `executeAssumptionDomainsRefresh` still parses the (unchanged-shape)
      `REFRESH_SUMMARY` and lifecycle (retry/backoff/escalate) still works —
      `go test ./internal/jobs/...` green (NOTE: Go tests do NOT run under pytest; run
      them explicitly).

### Phase 6 — Verification
- [ ] `python -m pytest tests/ -q` green; no default value or projected dollar changed.
- [ ] `go test ./internal/jobs/...` green.
- [ ] One `--check-only` run prints an 18-row table; coverage = (A+B+C) / 18; Tier-D
      rows shown with their honest outcome, none fabricated.

---

## 3. DO NOT
- Do NOT fabricate a value for a Tier-D key to raise coverage.
- Do NOT change a default value or any projected dollar in this work order.
- Do NOT let a live fetch failure abort a run or overwrite a good pinned value.
- Do NOT invent a new tracking format — reuse `REFRESH_SUMMARY` + `refresh_runs/`.
- Do NOT edit `assumption_impact.json` / `assumption-defaults.json` blind — concurrent
  editor; re-read and STOP if they shift.

## 4. DONE CRITERIA
- [ ] Every one of the 18 keys has a `refresh_tier` and is visited each run.
- [ ] Tier A (aurora + node-RAM + lambda) derive offline through the honesty gates.
- [ ] Tier B (Glue) fetch live, fail-safe, with drift surfaced not silently applied.
- [ ] Tier C (RI/SP/Spot [+nat if reliable]) self-calibrate per-job from the bill.
- [ ] Tier D keys emit a tracked `NO_SOURCE`/`POLICY`/`NEEDS_HUMAN` outcome — honest,
      never fabricated.
- [ ] Uniform run-tracking in `refresh_runs/`; Go lifecycle unchanged and green.
- [ ] Full Python + Go suites green; no value/dollar drift.

## 5. REALISTIC COVERAGE (set expectations honestly)
- Genuinely self-sourced after this WO: **Tier A (~5) + Tier B (2) = ~7** from
  provider/spec sources, plus **Tier C (~4–5)** self-calibrated from the customer's own
  bill → ~11–12 of 18 truly self-fetched.
- The remaining ~6 (Tier D) have no upstream number to fetch and are made **uniformly
  tracked** instead. That is the honest ceiling; claiming "all 18 fetched from
  providers" would require fabrication and is explicitly out of scope.

## 6. PROGRESS LOG (append-only)
- 2026-11-12 Created. Current state: 2/18 self-refresh (aurora). Goal: uniform
  self-fetch + tracking across all 18, tiered by real source (A derive-in-repo,
  B provider-live, C bill-calibrated, D no-source-tracked), reusing the
  `verify_compute_availability` / `check_cud_coverage` / `derive_aurora_values`
  patterns and the `REFRESH_SUMMARY` + `refresh_runs/` tracking contract.
