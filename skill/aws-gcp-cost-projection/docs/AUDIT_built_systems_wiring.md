# AUDIT — built systems & their wiring status

> **NEW AGENT:** read `ARCHITECTURE_MASTER_TRACKER.md` §0 first (working rules + the one-engine map). This audit is the
> evidence behind §2 of that tracker. When you WIRE/DELETE a system here, flip its status in the tracker's §2 too.

Status: **OPEN — audit complete, decisions pending.** Author: Claude (Opus 4.8) · 2026-11-20.
Trigger: the unit-conversion finding (`conversion_factor` built but never called) is not unique — several "system"
modules were built to unify or gate the pipeline and then **never wired in**, so the ad-hoc code they were meant to
replace still stands and the system rots. This audit finds all of them. Feeds
[[project_unit_conversion_engine]] (`WORKORDER_unit_conversion_engine.md`).

> **The pattern:** a module is written as the *single source of truth* for some capability (unit conversion, catalog
> queries, spec-comparison, a validation phase). It passes its own tests. But nothing in the pipeline imports it or the
> orchestrator never runs it — so callers keep doing the thing inline, three different ways, and the "system" is dead
> weight that looks like safety. Every fix that doesn't route through the intended system deepens this.

---

## Method (how "wired" was measured)

Two wiring modes exist; the metric must respect both or it over-flags:
- **PHASE-CLI**: invoked by the Go orchestrator as `scripts/X.py` (a pipeline phase). Its functions are called by its
  own `main()`, so "0 import-callers" is expected and fine.
- **LIBRARY**: imported by other scripts. Here, a public function with 0 import-callers = genuinely bypassed API.

Measured across all 94 `scripts/*.py`: import graph (who imports whom), the orchestrator's `scripts/*.py` invocation
set (37 phase scripts), `main()` presence, and test-only imports. Numbers are a 2026-11-20 snapshot.

---

## Findings

### A. Partially-wired capability — bypassed by inline code  🔴 (route fixes here)

| System | Built to be | Reality | Evidence |
|---|---|---|---|
| `unit_equivalence` | the unit-conversion engine | `conversion_factor()` has **0 callers**; 230 hand-coded multipliers across 45 mechanics do the job instead; only `compatible()` is wired (as a guard). | covered by `WORKORDER_unit_conversion_engine.md` |

### B. Unification systems built then bypassed — duplication still stands  🔴

| System | Built to be | Reality | Decision |
|---|---|---|---|
| `spec_match` (85 ln) | "one shared definition of 'does this GCP candidate meet/exceed the AWS spec'" | Canonical absolute rule; `reconcile_capacity` locked to it via `test_spec_match_parity`; `auto_review` clarified as shape/burstable review (D-SPEC-1 resolved). See `WORKORDER_spec_match_consolidation.md`. | ✅ LOCKED & CONSOLIDATED |
| `sku_catalog` / `catalog_db` | "**single source of truth** for querying `catalog.duckdb`" | Consolidated into `catalog_db` (Layer-0 repository): 1 cached read-only connection + list-all (`query_skus`) and `all_tiers`. All dollar-path readers (`apply_rates`, `incremental_rerate`, `catalog_manager`, `check_cud_coverage`) migrated. Ratchet `test_single_catalog_connection` locked. See `WORKORDER_sku_catalog_consolidation.md`. | ✅ LOCKED & CONSOLIDATED |
| `candidate_tier` (159 ln) | "Phase L: candidate tiering between hard-constraint filtering and scoring" | **0 callers, no CLI, not orchestrated.** `candidate_pool` (the scorer) runs without it. | ☐ wire into candidate_pool, or delete |

### C. Validation / review systems built, not orchestrated  🟡 (have CLI, pipeline never runs them)

| System | Built to be | Reality | Decision |
|---|---|---|---|
| `service_contracts` (410 ln) | "Phase J: every service has a contract of required fields; validate BEFORE mapping" | has `main()` + tests; not in the orchestrator. **D-CON-1 RESOLVED** (industry dark-launch): it's observe-only by nature (writes only contract_issues/field_availability, no mapping/dollar column); invariant locked by test_service_contracts_observe_only. Measurement = the dark launch; promote to routing or retire on real-job data. Remaining: observe-phase hookup (Go). | ◐ observe-mode locked; orchestrator hookup + promote/retire pending data |
| `spec_parity` (197 ln) | shadow-compare spec-dispatch vs legacy classify_mechanics over real jobs | **CORRECTED 2026-11-25: it's an offline dev/CI TOOL, not pipeline debt** — "not orchestrated" is correct. Home = CI for the S1 classify cutover. | ◐ reclassified tool → CI (fold into S1) |
| `validate_report` (118 ln) | golden-test a rendered report against the source bill (EDP-net baseline, etc.) | **CORRECTED 2026-11-25: offline CI/QA TOOL, not pipeline debt.** Home = CI (or fold its 3 checks into validate_fix). | ◐ reclassified tool → CI |
| `equivalence_review` (273 ln) | review queue for equivalence claims ranked by $ exposure | part of the review/refinement layer; **not orchestrated.** | ☐ confirm intended (manual/admin) or wire |

### D. Standalone admin/debug tools — dormant by design  ⚪ (likely fine, confirm)

`explain_row`, `job_inspect`, `compare_runs`, `gcp_sweep`, `weekly_catalog_refresh` — all have `main()`, none
orchestrated. These read like manual/cron tools, not pipeline systems. Action: confirm each is intentionally
manual (and that `weekly_catalog_refresh` is actually on a schedule), then leave.

### E. Not a problem (resolved on inspection)

- `family_mapper` (68 ln) — a **compat shim** re-exporting consolidated compute logic from `apply_static_mappings`
  for back-compat with tests (AR-3). Intentional. Leave.
- `candidate_pool` — IS wired (orchestrator phase + imported by `config_loader`/`gen_ratio_registry`); its "dormant"
  public funcs are its own CLI's internals. Fine.

---

## Why this matters for every future fix

The user's instinct is correct: **when a system exists for a capability, a fix must route through it, not inline a
4th copy.** Concretely, going forward:
- catalog reads → `sku_catalog` (not ad-hoc `duckdb.connect`), once it's adopted;
- spec/capacity comparison → `spec_match` (not a new inline check);
- unit multipliers → the unit engine (`WORKORDER_unit_conversion_engine.md`);
- required-field checks → `service_contracts`, if adopted as a phase.

Until each system is either **wired or deleted**, it's a trap: it looks like the right place but isn't actually load-
bearing, so fixes silently bypass it.

---

## Decision tracker  `(0 / 10 decided)`

Each needs an owner decision: **WIRE** (make it load-bearing), **DELETE** (remove + record why), or **CONFIRM-TOOL**
(intentionally manual).

| # | System | Category | Decision | Done |
|---|---|---|---|---|
| 1 | unit_equivalence (conversion) | A | WIRE (via unit workorder) | ☑ |
| 2 | spec_match | B | WIRE (canonical rule + parity locked) | ☑ |
| 3 | sku_catalog / catalog_db | B | WIRE (catalog_db SSOT + ratchet locked) | ☑ |
| 4 | candidate_tier | B | ? | ☐ |
| 5 | service_contracts | C | ? | ☐ |
| 6 | spec_parity | C | ? | ☐ |
| 7 | validate_report | C | ? | ☐ |
| 8 | equivalence_review | C | ? | ☐ |
| 9 | admin tools (D, ×5) | D | CONFIRM-TOOL | ☐ |
| 10 | catalog-read SSOT adoption (sweep ad-hoc `duckdb.connect`) | B | WIRE (all readers migrated to catalog_db) | ☑ |

Recommended order: 2 and 3 first (highest duplication leverage — `spec_match` kills 3 copies, `sku_catalog` unifies
every catalog read), then the unit engine (1), then the Phase-J/L decisions (4, 5), then CI wiring (6, 7), then the
rest.
