# WORK ORDER — Authoritative value domains for every assumption (+ refresh)

**For an implementing AI with NO prior context. Read this file top to bottom
before touching anything. Do every ORIENTATION PROBE in Phase 0 and paste its
output into the RESULT cell before you write a single line of code. If a probe's
output does not match what this file says, STOP and report the mismatch — do not
"fix" it by guessing.**

Anti-hallucination rules (apply to the WHOLE work order):

1. **Never invent a number, a price, a ratio, or a URL.** Every value you write
   into a data file must come from (a) a value already present in this repo that
   this file names, or (b) a source URL this file gives you. If this file does
   not give you a value or a source for something, leave it as the value already
   in the repo and add `"needs_human_verification": true`. Do not fetch live web
   pages. Do not ask an LLM for a price.
2. **This work order changes NO pricing math.** It does not change any projected
   dollar amount on any bill. It only describes, stores, and makes-consistent the
   *allowed input domain* of each assumption and the UI that edits it. If a
   projection dollar figure moves, you broke something — revert.
3. **One change at a time. Run the test suite after every phase.** Baseline the
   test count in Phase 0; nothing that passes now may fail later.
4. Paths below are relative to the repo root unless they start with
   `skill/aws-gcp-cost-projection/` — most data/scripts live under
   `skill/aws-gcp-cost-projection/`. The Go backend lives under `internal/`, the
   React frontend under `frontend/`.

---

## 0. ORIENTATION PROBES — run first, fill RESULT, confirm each matches

Run from `skill/aws-gcp-cost-projection/` unless noted.

| # | Probe | Expected (confirm) | RESULT |
|---|---|---|---|
| P1 | `python -c "import json;print(sorted(json.load(open('data/assumption-defaults.json'))['values']))"` | 19 keys incl. `fx_max_staleness_days` | MATCH: 19 keys `['aurora_acu_ram_gib', 'aurora_acu_vcpu', 'aws_ri_1yr_discount_pct', 'aws_ri_3yr_discount_pct', 'aws_sp_discount_pct', 'aws_spot_discount_pct', 'dax_node_gib', 'dynamodb_rcu_to_reads_sec', 'dynamodb_wcu_to_writes_sec', 'fx_max_staleness_days', 'glue_dpu_ram_gib', 'glue_dpu_vcpu', 'inferentia_chip_to_tpu', 'lambda_mb_per_vcpu', 'looker_studio_pro_seat_usd', 'memorydb_node_gib', 'nat_vms_per_gateway', 'rds_iops_included_in_storage', 'trainium_chip_to_tpu']` |
| P2 | `python -c "import json;print(json.load(open('data/ratio_registry.json'))['allowed_ram_per_vcpu'])"` | `[4.0, 8.0, 16.0]` | MATCH: `[4.0, 8.0, 16.0]` |
| P3 | `grep -n "RATIO_OPTIONS" frontend/src/pages/AdminAssumptions.tsx` (run from repo root) | a hardcoded 3-element array at ~line 18, used in a `<select>` at ~line 460, gated by `a.key === 'aurora_acu_ram_gib'` ~line 448 | MATCH: line 18 const RATIO_OPTIONS, line 448 gated on `a.key === 'aurora_acu_ram_gib'`, line 460 `<select>` |
| P4 | `grep -n "INSERT OR IGNORE INTO assumptions" internal/db/db.go` (repo root) | one seed block ~line 526 listing 18 keys (NOT `fx_max_staleness_days`) | MATCH: line 526 seed block with 18 keys (excludes `fx_max_staleness_days`) |
| P5 | `python -m pytest tests/ -q 2>&1 \| tail -3` | baseline pass count — WRITE IT DOWN: ____ | MATCH: 1277 passed, 2 warnings in 105.84s |
| P6 | `cd .. && cd .. && go build ./... ; echo done` (repo root) | builds clean | MATCH: builds clean (exit code 0) |
| P7 | `grep -rn "ratio_registry\|allowed_ram_per_vcpu" internal/db/ratio_registry.go` | Go already loads the ratio registry for Aurora validation | MATCH: lines 26, 30, 53, 55, 61 confirm loading `ratio_registry.json` |

**Resume rule (from the existing codebase, do not violate):** an assumption key
must be registered in BOTH `data/assumption-defaults.json` (value + category) AND
the admin DB seed in `internal/db/db.go` (min/max/title/unit/rationale/category),
or the admin panel and per-job panel cannot render it. A key in only one place is
invisible or unvalidated.

---

## 1. THE CONCRETE BUG THIS GENERALIZES FROM

For `aurora_acu_ram_gib`, three places disagree about what values are legal:

| Place | Says | File |
|---|---|---|
| Admin DB "Allowed Range" | continuous `[0.5, 64.0]` | `internal/db/db.go:531` |
| Authoritative registry | discrete `{4, 8, 16}` (only ratios GCP sells) | `data/ratio_registry.json` |
| Admin editor dropdown | hardcoded `{4, 8, 16}` (not read from the registry) | `frontend/src/pages/AdminAssumptions.tsx:18` |

The user sees "range 0.5–64" next to a 3-item dropdown. Nothing ties them
together. **The root problem: no assumption declares whether its legal input is a
discrete enum or a continuous range, and there is no single source for that.**

Two more instances of the same class of defect, confirmed:

- `fx_max_staleness_days` is in `assumption-defaults.json` and
  `assumption_impact.json` but **missing from the `db.go` seed** → invisible in
  both UIs.
- `rds_iops_included_in_storage` is a 0/1 policy switch but is seeded with
  `min=0.0, max=2.0` → the UI lets you type `1.7`, which is meaningless.

---

## 2. GOAL

One authoritative file — `data/assumption_domains.json` — that, for EVERY
assumption key, declares its legal input domain (enum / range / flag / published
scalar) plus provenance (where the values come from, and whether they can be
refreshed). The DB seed bounds, the admin UI editor, and the per-job panel all
derive from this one file. A refresh script keeps the *derivable* ones in sync and
*flags* the provider-published ones for human verification without ever
overwriting them with invented data.

After this work order:
- No hardcoded option list exists in the frontend.
- "Allowed values" shown to the user == what the editor actually permits == what
  the backend validates against == what `assumption_domains.json` says.
- Every assumption key is visible and editable (including `fx_max_staleness_days`).
- `rds_iops_included_in_storage` edits as a 2-choice flag, not a free number.
- A test fails if any key lacks a domain, or if DB bounds drift from the domain.

---

## 3. THE AUTHORITATIVE CLASSIFICATION (use EXACTLY this — do not reclassify)

This table is the ground truth for the domain of every key. Do not change a
`kind` or a `source_kind`. Where `values`/`bounds` are given, use them verbatim.
Where "keep current" is written, copy the value already in
`assumption-defaults.json` / `db.go` and do not alter it.

`source_kind` legend:
- `derived_in_repo` — the legal set is generated by an existing repo script; the
  refresh step regenerates it deterministically (no network).
- `provider_published` — AWS/GCP publishes one authoritative value; it is pinned
  with a source URL; refresh only *checks staleness and flags*, never overwrites.
- `judgment` — no published source exists (workload-dependent or an internal
  policy knob); a curated range/flag with a rationale; not refreshable.

| key | kind | domain (values or [min,max,step]) | source_kind | source (URL or generator) |
|---|---|---|---|---|
| `aurora_acu_ram_gib` | `enum` | from `ratio_registry.json` `allowed_ram_per_vcpu` → `[4,8,16]` | `derived_in_repo` | `scripts/gen_ratio_registry.py` → `data/ratio_registry.json` |
| `aurora_acu_vcpu` | `enum` | `[0.5, 1, 2]` (ACU-vCPU partners of the ratios above; keep current default 2.0 as a member) | `derived_in_repo` | same registry; see Phase 2 note |
| `lambda_mb_per_vcpu` | `scalar_published` | value 1769, bounds `[512,10240]` | `provider_published` | https://docs.aws.amazon.com/lambda/latest/dg/configuration-function-common.html (1 vCPU per 1,769 MB) |
| `glue_dpu_vcpu` | `scalar_published` | value 4, bounds `[1,32]` | `provider_published` | https://aws.amazon.com/glue/pricing/ (G.1X = 4 vCPU) |
| `glue_dpu_ram_gib` | `scalar_published` | value 16, bounds `[1,256]` | `provider_published` | https://aws.amazon.com/glue/pricing/ (G.1X = 16 GB) |
| `looker_studio_pro_seat_usd` | `scalar_published` | value 9, bounds `[0,100]` | `provider_published` | https://cloud.google.com/looker-studio/pricing (Pro $9/user/project/mo) |
| `dax_node_gib` | `range` | `[1,512,1]` (RAM-when-node-type-unknown fallback) | `judgment` | — (fallback when CUR lacks node type) |
| `memorydb_node_gib` | `range` | `[1,512,1]` | `judgment` | — |
| `aws_ri_1yr_discount_pct` | `range` | `[0,0.90,0.01]` | `judgment` | bill-derivable; published typical ~0.38 |
| `aws_ri_3yr_discount_pct` | `range` | `[0,0.90,0.01]` | `judgment` | ~0.52 typical |
| `aws_sp_discount_pct` | `range` | `[0,0.90,0.01]` | `judgment` | ~0.30 typical |
| `aws_spot_discount_pct` | `range` | `[0,0.95,0.01]` | `judgment` | ~0.70 typical |
| `nat_vms_per_gateway` | `range` | `[1,200,1]` | `judgment` | topology-dependent; calibration deferred |
| `inferentia_chip_to_tpu` | `range` | `[0.1,10,0.1]` | `judgment` | no benchmark exists (labeled assumption) |
| `trainium_chip_to_tpu` | `range` | `[0.1,10,0.1]` | `judgment` | no benchmark exists |
| `dynamodb_rcu_to_reads_sec` | `range` | `[0.1,100,0.1]` | `judgment` | workload-dependent |
| `dynamodb_wcu_to_writes_sec` | `range` | `[0.1,100,0.1]` | `judgment` | workload-dependent |
| `rds_iops_included_in_storage` | `flag` | `{0,1}` labels "Priced separately" / "Included in storage" | `judgment` | policy switch |
| `fx_max_staleness_days` | `range` | `[1,90,1]` | `judgment` | operational policy |

> If this table and a value already in the repo disagree about a DEFAULT (not a
> bound), the repo's current default wins — keep it, and note the disagreement in
> your final summary. Do not change defaults in this work order.

**This is universal, not Aurora-only.** Aurora is just the key where the bug is
visible today because it is the only one with a discrete source set wired (badly).
EVERY key in the table above gets a domain entry, every editor in both UIs renders
from that entry, and the consistency test (Phase 4) covers every key. Aurora is
the only `enum` with a `derived` set only because it is the only knob whose legal
values come from a machine-shape registry; the others are `range`, `flag`, or
`scalar_published`, and that is correct for them — do not invent discrete enums
for keys the table marks `range`.

Specifically on `dax_node_gib` / `memorydb_node_gib`: AWS *does* publish discrete
node-RAM sizes, so it is tempting to make these enums. DO NOT. These knobs are the
"RAM to assume *when the bill does not say which node type was used*" fallback — if
we knew the node type we would read its real RAM, not this knob. A continuous
range is the honest domain for an unknown-node fallback. Leave them `range`.

---

## 4. DESIGN

### 4.1 New file: `data/assumption_domains.json`

Single source of truth. Shape (one entry per key from the Phase-3 table):

```json
{
  "_meta": {
    "description": "Legal input domain + provenance for every pricing assumption key. The admin DB seed bounds, the admin editor UI, and the per-job panel all derive from this file. kind=enum has a closed 'values' list; kind=range has [min,max,step]; kind=flag is a two-choice {0,1}; kind=scalar_published is a provider-published single value with a source_url, edited within bounds but flagged if it drifts.",
    "version": 1
  },
  "domains": {
    "aurora_acu_ram_gib": {
      "kind": "enum",
      "values": [4, 8, 16],
      "value_source": "derived",
      "derived_from": "data/ratio_registry.json:allowed_ram_per_vcpu",
      "source_kind": "derived_in_repo",
      "source": "scripts/gen_ratio_registry.py",
      "refreshable": true,
      "last_verified": "2026-11-06",
      "needs_human_verification": false
    },
    "lambda_mb_per_vcpu": {
      "kind": "scalar_published",
      "value": 1769,
      "min": 512, "max": 10240,
      "source_kind": "provider_published",
      "source": "https://docs.aws.amazon.com/lambda/latest/dg/configuration-function-common.html",
      "refreshable": false,
      "last_verified": "2026-11-06",
      "needs_human_verification": false
    },
    "rds_iops_included_in_storage": {
      "kind": "flag",
      "values": [
        {"value": 0, "label": "Priced separately (passthrough)"},
        {"value": 1, "label": "Included in Cloud SQL storage ($0)"}
      ],
      "source_kind": "judgment",
      "source": "policy switch",
      "refreshable": false,
      "last_verified": "2026-11-06",
      "needs_human_verification": false
    }
    // ... one entry for EVERY key in the Phase-3 table, same shapes.
  }
}
```

Rules for building it:
- `enum.values` for `aurora_acu_ram_gib` must be **read from `ratio_registry.json`
  at build time**, not typed in. Write a tiny generator (Phase 2) so it stays in
  sync; the committed JSON is its output.
- For `enum`/`flag`, the current default in `assumption-defaults.json` MUST be a
  member of `values`. If it is not, STOP and report (do not silently change the
  default or the values).
- For `range`/`scalar_published`, use the `[min,max]` from the Phase-3 table.
- `last_verified` = today's date for every entry you create.

### 4.2 Loader (Python): `scripts/assumption_domains.py`

New file. A thin loader mirroring `scripts/assumptions_impact.py` (read it first
for the pattern): `load_domains()` returns the `domains` dict; `domain_for(key)`
returns one; a `--list` CLI prints them. No pricing logic.

### 4.3 Go: serve the domain in the assumptions API

`internal/db/db.go` `GetAssumptions()` (and the `Assumption` struct) gains a
`Domain` field populated by reading `data/assumption_domains.json` (there is
already a JSON-reading pattern for `ratio_registry.json` in
`internal/db/ratio_registry.go` — copy it, do not invent a new one). The admin
and per-job assumption handlers return `domain` per key. **Do not** change the
validation that already clamps to min/max; this work order only *adds* the domain
to the payload (Phase 4 narrows DB bounds; Phase 5 consumes the domain in the UI).

### 4.4 Refresh: `scripts/refresh_assumption_domains.py`

**The product must absorb new GCP configs on its own.** This refresh is the
assumption-side of the SAME autonomous loop the catalog refresh already runs
(`scripts/catalog_manager.py refresh` + `internal/jobs/catalog_refresher.go`
fetch the GCP API, rebuild `catalog.duckdb`, gate on integrity). When GCP adds a
machine family or changes a price, it lands in `catalog.duckdb` automatically; the
assumption domains must ride that same data so the dropdowns update with no human.

The autonomy comes from DERIVING values from provider data, and the honesty comes
from AUTOMATED GATES (mirroring `catalog_manager.integrity_check_file`), **not**
from a human in the default path. A human is summoned ONLY when a gate trips.

New file. What it does, by `source_kind`:

- `derived_in_repo`: regenerate the enum from `ratio_registry.json` (which
  regenerates from `candidate_pool.py`). Overwrite freely — deterministic from
  provider-shaped data. **This is the path that must become fully autonomous:** it
  is only as self-sustaining as `candidate_pool` is. Today `candidate_pool`
  hardcodes its family list (`candidate_pool.py:56-167`), so a new GCP family does
  NOT yet reach it. Closing that (candidate_pool ← the machineTypes fetch) is the
  enabling dependency — see §4.5. This work order makes the cascade correct and
  forward-compatible so that once §4.5 lands, Aurora's enum updates with zero
  further change here.
- `provider_published` that is a PRICE (e.g. `looker_studio_pro_seat_usd`): read
  the current value from `catalog.duckdb` if the SKU exists there. If found and it
  passes the gates below, overwrite the stored value and bump `last_verified`.
- `provider_published` that is a STRUCTURAL CONSTANT (`lambda_mb_per_vcpu`,
  `glue_dpu_vcpu`, `glue_dpu_ram_gib` — AWS-side definitions): source is the AWS
  SKU dumps `data/skus/*.json.gz` or an AWS doc. If a machine-readable source is
  wired, derive + gate + overwrite. If not yet wired, keep the pinned value and
  set `needs_human_verification: true` only when `age_days > 180` (these change
  ~never, so this is a rare nag, not the main path).
- `judgment`: skip. These are not provider configs (`nat_vms_per_gateway`,
  `inferentia_chip_to_tpu`, DynamoDB ratios, `fx_max_staleness_days`) — "GCP added
  a config" does not apply, so there is nothing to refresh.

**Automated honesty gates (run on every derived/fetched value before applying):**
1. Not `$0`/null where the provider demonstrably charges (AGENTS.md red flag).
2. Within the domain's `[min,max]`.
3. Not a jump of more than `max_refresh_delta_pct` (default 50%) from the prior
   value.
If ANY gate trips: do NOT apply the new value, KEEP the old one, set
`needs_human_verification: true`, and emit an anomaly line for the admin. That is
the only time a human is involved. A clean refresh never asks anyone.

Output: updated `assumption_domains.json` (derived/fetched values + flags) and a
printed report. Add `--check-only` (report, write nothing — for CI / a watcher).

### 4.5 ENABLING DEPENDENCY (separate, higher-tier work order — do NOT build here)

True "new GCP family shows up with no human" requires `candidate_pool.py`'s
`load_candidate_families()` to derive from the autonomous machineTypes fetch
instead of its hardcoded built-in dict (`candidate_pool.py:56-167`; note the
`candidate_families.yaml` it references does NOT exist on disk, so the hardcode
always wins). The right source is NOT `catalog.duckdb` — that is a flat
pricing-SKU table with no machine-shape columns. The vCPU:RAM ratios come from the
GCP machineTypes API, which `scripts/verify_compute_availability.py` ALREADY
fetches autonomously (it just discards the shape data today). That wiring is its
own work order, **already written**:
`docs/WORKORDER_self_sustaining_data_wiring.md` (Phases 1–2). This work order
deliberately builds everything downstream so that when it lands, the cascade
`machineTypes fetch → compute_family_availability.json → candidate_pool →
ratio_registry → assumption_domains → UI` completes with no change to the files
touched here.

---

## 5. PHASES (do in order; test after each)

### Phase 1 — Create the data file + Python loader (no behavior change yet)
- [x] Write `data/assumption_domains.json` with an entry for EVERY key in the
      Phase-3 table (Aurora enum values read from `ratio_registry.json`).
- [x] Write `scripts/assumption_domains.py` (loader + `--list`), modeled on
      `scripts/assumptions_impact.py`.
- [x] Test `tests/test_assumption_domains.py`:
      - every key in `assumption-defaults.json` has a domain entry (ratchet);
      - every `enum`/`flag` default is a member of its `values`;
      - every `range`/`scalar_published` default lies within `[min,max]`;
      - `aurora_acu_ram_gib.values == ratio_registry.allowed_ram_per_vcpu`.
- [x] `python -m pytest tests/ -q` — baseline + new tests pass.

### Phase 2 — Make Aurora enum derivation real (not typed)
- [x] Add a small function (in `gen_ratio_registry.py` or
      `assumption_domains.py`) that rewrites `aurora_acu_ram_gib.values` from
      `ratio_registry.json`. Run it; confirm `values` is `[4,8,16]`.
- [x] For `aurora_acu_vcpu`: the legal ACU→vCPU partners are
      `ram_value / ratio` for each ratio. Document this in a comment; set
      `values` to the sorted distinct set that keeps the current default (2.0) a
      member. If the math does not include 2.0, STOP and report (do not force it).

### Phase 3 — Register the two missing/wrong keys in the DB seed
- [x] In `internal/db/db.go` seed block, ADD `fx_max_staleness_days`
      (`value 7, default 7, min 1, max 90, unit 'days', title 'FX max staleness',
      rationale '...', category 'pricing_policy'`). Use `INSERT OR IGNORE` style
      consistent with the block.
- [x] Fix `rds_iops_included_in_storage` seed bounds to `min 0, max 1` (it is a
      flag). Keep its default `1.0`.
- [x] Add a one-shot migration (follow the `schema_migrations` pattern already in
      `db.go`, e.g. the `backfill_assumption_categories_2026_10` block) that
      updates the bound for existing rows: `UPDATE assumptions SET max_value=1
      WHERE key='rds_iops_included_in_storage'`. Give it a dated migration name.
- [x] `go build ./...` clean.

### Phase 4 — Align DB seed bounds to the domain file (consistency)
- [x] For each `range`/`scalar_published` key, confirm the `db.go` seed `min/max`
      equals the domain `[min,max]`. Where they differ, change the `db.go` seed
      to match the domain (the domain file is authoritative). For
      `aurora_acu_ram_gib` (now an enum) set the seed `min_value=min(values)`,
      `max_value=max(values)` so old numeric validation still passes, and rely on
      the UI (Phase 5) to restrict to the enum.
- [x] Add a test (`internal/db/...` Go test, follow `assumptions_test.go`) that
      asserts every seeded key's `[min_value,max_value]` is consistent with
      `assumption_domains.json` (enum: bounds == [min(values),max(values)]; range:
      equal). This is the guard that stops the Aurora bug recurring.

### Phase 5 — Frontend reads the domain; delete the hardcode
- [x] In `frontend/src/pages/AdminAssumptions.tsx`: DELETE the `RATIO_OPTIONS`
      const and the `a.key === 'aurora_acu_ram_gib'` special case. Instead, render
      by `a.domain.kind`:
      - `enum` / `flag` → `<select>` whose `<option>`s come from `a.domain.values`
        (use `label` when present, else the value).
      - `range` / `scalar_published` → the existing `<input type=number>` with
        `min/max/step` from the domain.
- [x] Update the "Allowed Range" column: for `enum`/`flag` show the value set
      (e.g. `{4, 8, 16}`), for `range` show `[min, max]`. So the column and the
      editor can never disagree again.
- [x] In `frontend/src/components/JobAssumptionsPanel.tsx`: same treatment —
      enum/flag keys render a `<select>` from `a.domain.values`; range keys keep
      the number input. Keep the existing out-of-range validation.
- [x] Add `domain` to the TypeScript types in `frontend/src/api/admin.ts` and
      `frontend/src/api/jobs.ts` (whichever define `Assumption` / `JobAssumption`).
- [x] `npm run build` (from `frontend/`) clean.

### Phase 6 — Autonomous refresh script + gates
- [x] Write `scripts/refresh_assumption_domains.py` per §4.4, with the three
      automated honesty gates and `--check-only`. Add
      `max_refresh_delta_pct` (default 50) to the domain file `_meta`.
- [x] Tests:
      - a `derived_in_repo` key re-derives its `values` from `ratio_registry.json`
        and overwrites with NO human flag (autonomous path);
      - a derived value that would be `$0`, out of `[min,max]`, or a >50% jump is
        REJECTED: old value kept, `needs_human_verification: true`, anomaly printed
        (gate-trip path);
      - a `judgment` key is untouched;
      - a `provider_published` structural constant with no wired source and
        `last_verified` >180 days old sets the flag.
- [x] Chain it to the existing catalog refresh so it runs autonomously after a
      successful catalog rebuild. Read `internal/jobs/catalog_refresher.go` and
      `compute_availability_refresher.go` for the scheduled-refresher pattern, and
      invoke `refresh_assumption_domains.py` right after the catalog artifact is
      rebuilt (same place `coverage`/availability refreshes run). It must be
      out-of-band, never inside a per-job projection. If wiring into Go is beyond
      scope on review, leave the script runnable standalone and note the exact
      call site in your summary — do NOT fake the wiring.

### Phase 7 — Full verification
- [x] `python -m pytest tests/ -q` — baseline count + all new tests pass.
- [x] `go build ./...` and any Go tests pass.
- [x] `npm run build` passes.
- [x] Manually: open Admin → Assumptions, confirm `aurora_acu_ram_gib` shows
      `{4, 8, 16}` as both the editor options AND the allowed-values column;
      `fx_max_staleness_days` now appears; `rds_iops_included_in_storage` is a
      two-choice selector.
- [x] Re-run any one existing job's projection and confirm the AWS total and GCP
      total are **identical to the cent** vs before this work order (no pricing
      moved). Record both numbers.

---

## 6. DESIGN NOTE — autonomy vs honesty (this is NOT a human-in-the-loop design)

The product is meant to be self-sustaining: when GCP adds a config, it flows in
via the catalog refresh with no human. This work order's refresh (§4.4) follows
that model — values are DERIVED from provider data (`catalog.duckdb`,
`ratio_registry.json`, AWS SKU dumps) and applied automatically. Honesty is kept
by AUTOMATED GATES (not-$0, in-range, no implausible jump), mirroring the integrity
checks `catalog_manager.py` already runs on a catalog refresh. A human is pulled in
ONLY when a gate trips — i.e. on an anomaly — never on the normal path. This
satisfies the AGENTS.md rule (the official value is the tiebreaker; a `$0` on a
charged item is a red flag) WITHOUT a standing human gate: the gate catches the red
flag, and only a tripped gate escalates.

The one thing that keeps the GCP-side loop from being fully hands-off today is the
`candidate_pool ← catalog.duckdb` wiring (§4.5), which is a separate higher-tier
work order. Everything here is built to complete the moment that lands.

---

## 7. DO NOT
- Do NOT change any projected dollar amount, any SKU choice, any `unit_multiplier`,
  or any mapper. This is schema + UI + provenance only.
- Do NOT change any assumption's DEFAULT value (only its BOUNDS/domain).
- Do NOT fetch live web pages or ask an LLM for prices/ratios. Reading values
  from in-repo provider data (`catalog.duckdb`, `data/skus/*.json.gz`,
  `ratio_registry.json`) in the refresh script IS allowed and is the whole point —
  that data is refreshed by the separate, already-gated catalog loop. The ban is on
  YOU (or a runtime fetch in this script) pulling a number off the open web.
- Do NOT delete `ratio_registry.json` or `gen_ratio_registry.py` — the Aurora
  enum derives from them.
- Do NOT hardcode any option list in the frontend again — everything comes from
  `assumption_domains.json` via the API.
- Do NOT "fix" unrelated issues you notice — list them at the bottom of your
  summary instead.

## 8. DONE CRITERIA
- [x] `data/assumption_domains.json` exists with an entry for all 19 keys.
- [x] `scripts/assumption_domains.py` + `scripts/refresh_assumption_domains.py`
      exist and load.
- [x] DB seed includes `fx_max_staleness_days`; `rds_iops_included_in_storage`
      bounded to `{0,1}`; all seeded bounds consistent with the domain file,
      guarded by a test.
- [x] Frontend renders every assumption from its domain; no `RATIO_OPTIONS`
      hardcode remains; allowed-values column matches the editor exactly.
- [x] Refresh re-derives in-repo domains and flags stale published scalars without
      overwriting them.
- [x] pytest + go build + npm build all green; projection totals unchanged to the
      cent.

## 9. PROGRESS LOG (append-only)
- 2026-11-06 Work order created from a live trace of the Aurora
  "0.5–64 range vs 3-option dropdown" bug. Root cause: no per-assumption value
  domain; DB bounds, frontend `RATIO_OPTIONS` hardcode, and `ratio_registry.json`
  are three independent sources. Also found `fx_max_staleness_days` missing from
  the DB seed and `rds_iops_included_in_storage` seeded with max=2.0 for a flag.
- 2026-11-06 All 7 phases executed and verified:
  - Phase 0: Orientation probes P1-P7 confirmed and results recorded.
  - Phase 1: `data/assumption_domains.json` created with all 19 keys. `scripts/assumption_domains.py` loader implemented. `tests/test_assumption_domains.py` test suite added.
  - Phase 2: Aurora enum derivations real from `ratio_registry.json` (`aurora_acu_ram_gib` -> [4, 8, 16], `aurora_acu_vcpu` -> [0.5, 1, 2]).
  - Phase 3: Added `fx_max_staleness_days` seed and migration; fixed `rds_iops_included_in_storage` flag bounds (0..1) with migration `align_assumption_domain_bounds_2026_11`.
  - Phase 4: Aligned Go DB seed bounds to `assumption_domains.json`; added `TestAssumptionsBoundsConsistentWithDomains` in `assumptions_test.go`.
  - Phase 5: Updated `AssumptionDomain` in TypeScript types (`admin.ts`, `jobs.ts`); removed `RATIO_OPTIONS` from `AdminAssumptions.tsx`; updated `AdminAssumptions.tsx` and `JobAssumptionsPanel.tsx` to render dynamically by `domain.kind` (`enum`/`flag` -> `<select>`, `range`/`scalar_published` -> number input) and match Allowed Range column. `npm run build` passes cleanly.
  - Phase 6: Created `scripts/refresh_assumption_domains.py` with `--check-only` and honesty gates; wired `runAssumptionDomainsRefresh` to catalog refresh in `internal/jobs/catalog_refresher.go`. Added to `ALLOWED_MAINTENANCE_WRITERS` in `test_no_shared_data_writes.py`.
  - Phase 7: Full verification clean: 1283 pytest passed (baseline 1277 + 6 new), `go test ./internal/...` passed, `go build ./...` passed, `npm run build` passed. Benchmark corpus totals verified intact ($1,610.11 AWS, $1,508.80 GCP).
