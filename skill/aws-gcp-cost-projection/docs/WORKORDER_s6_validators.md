# WORKORDER — S6 validators: service_contracts / spec_parity / validate_report

> **NEW AGENT:** read `ARCHITECTURE_MASTER_TRACKER.md` §0 first. This is subsystem **S6** (governance/gates). Update the
> tracker §2 (S6) and `AUDIT_built_systems_wiring.md` (C) as you progress.

Status: **IN PROGRESS — D-CON-1 resolved (industry dark-launch); orchestrator hookup + CI tools remain.** Author: Claude (Opus 4.8) · 2026-11-25.

## D-CON-1 RESOLVED — industry shadow / dark-launch pattern (2026-11-25)
The industry way to introduce a validation gate whose value is unmeasured and whose enforcement is risky is to
**dark-launch it in observe-only mode**: it records what it *would* flag (telemetry/annotations), changes nothing,
you measure from real runs, then **promote to enforce or retire** on the data. Never block/route on an unmeasured gate;
never run two gates for the same check.
- `service_contracts.validate_all_contracts` is **already observe-only by nature**: it writes only the annotation
  columns `contract_issues` / `field_availability` and returns a summary — it touches NO mapping/strategy/dollar column.
  So observe-mode is its real behavior, not a new mode to build.
- ☑ **Invariant locked:** `tests/test_service_contracts_observe_only.py` asserts a shadow run leaves every
  mapping/dollar column (`strategy`, `unit_multiplier`, `gcp_sku_*`, `aws_amortized_cost`, …) byte-identical and only
  writes the two annotation columns. If anyone later makes it route/block, this test fails — so **promotion to enforce
  is a deliberate, reviewed change, never an accident.**
- **Measurement = the dark launch itself.** Running it on real jobs emits `contract_failures` + `details_by_service`
  (how many rows it WOULD flag, which critical fields) — that is the overlap-vs-additive data we lacked. No need to
  pre-run locally.
- **Promotion criteria:** after it runs on real jobs, if `contract_failures` is ~0 or purely overlaps existing gates
  (completeness gate / resolve_sku guards / field_state) → **retire / demote to lint**. If it flags a real class those
  miss → consider promoting to routing, with a measured before/after (dollar-adjacent). Default stays observe-only.

Remaining for D-CON-1: **T3-hookup (AGY/Go)** — run `validate_all_contracts` as an observe phase per job and log its
summary to the diagnostics channel. Dollar-neutral by the invariant above; no routing.

## Corrected finding (the audit's "built but not orchestrated = debt" was wrong for 2 of 3)

These three were flagged together, but they are three different kinds of thing:

| Module | What it actually is | Correct home | Verdict |
|---|---|---|---|
| `service_contracts.py` (Phase J) | a **pipeline validation phase**: `validate_contract` returns a `ContractResult` with `blocked_reason` when a *critical field is UNKNOWN*; intends to route the row to the outlier/review flow instead of mapping. **Not wired** (nothing calls `validate_all_contracts` in the orchestrator). | the pipeline, IF we want a pre-mapping contract gate | **DECISION D-CON-1** |
| `spec_parity.py` | a **read-only dev/CI shadow tool** ("writes nothing, changes nothing") comparing spec-dispatch vs legacy `classify_mechanics` over real job DBs, run before/after a classify change. | **CI**, as part of the **S1 classify cutover** — never a pipeline phase. | **not debt — it's a tool**; fold into S1 |
| `validate_report.py` | a **CI/QA golden check** of a rendered report vs the source bill (EDP-net baseline, no inverted CUDs, ratio bounds). | **CI** (or fold into `validate_fix`). | **not debt — it's a tool**; wire into CI |

So S6 is NOT "wire three dormant systems." It's: decide one pipeline gate (service_contracts), and move two offline
tools into CI where they belong. "Built but not in the orchestrator" is the CORRECT state for a shadow-comparison and a
golden-report check — they are not production phases.

## DECISION D-CON-1 — does the Phase-J contract gate run in the pipeline, and what does a block do?
`service_contracts` checks that each row's **critical fields** are known before mapping; if not, it blocks with a
reason. Open questions to settle before wiring anything:
1. **Overlap / additive value.** Is this additive over what's ALREADY enforced — the completeness gate
   (`service_usage_catalog` + spec_dispatch), `resolve_sku`'s dimensional/component guards, and `field_state`? If those
   already catch "critical field missing," the contract gate may be redundant → **demote to an opt-in lint / delete**.
   If it catches a real class they miss (e.g. "service X needs instance_type and we have none"), it's additive → wire.
   **Action:** sweep real jobs — how many rows would `validate_all_contracts` block that today map anyway? That number
   decides additive-vs-redundant.
2. **On-fail behavior.** The module says "route to the outlier flow instead of mapping," but its own comments also say
   failures "don't block mapping" (emit assumptions). Pick one: (a) route blocked rows to review/outlier, (b) annotate
   only (confidence cap, no routing change), (c) passthrough. (a) is **dollar/behavior-adjacent** → must be measured on
   real jobs with before/after; (b) is dollar-neutral.
3. **Placement.** If wired, it runs as a phase BEFORE mapping (Phase J, per its design). Confirm the orchestrator slot.

Recommendation: **measure overlap first (Q1)**. If the block-count on real jobs is ~0, demote service_contracts to an
opt-in lint or delete it (it's then redundant with existing gates). If non-trivial, wire it as **annotate-only (2b)**
first (dollar-neutral), and only consider routing (2a) with a measured before/after. Do not wire routing blind.

## Tasks
- [ ] T1 (spec_parity) — move to CI as the S1-cutover shadow check; document it in `WORKORDER_spec_dispatch_cutover.md`
  as the before/after gate. No pipeline change. Mark it "tool, CI" in the audit (not debt).
- [ ] T2 (validate_report) — wire into CI against a committed report+bill fixture, or fold its 3 checks into
  `validate_fix`. Mark "tool, CI".
- [ ] T3 (service_contracts) — resolve D-CON-1: first the overlap sweep (Q1), then wire annotate-only / demote / delete
  per the result. Parity-locked; routing changes (if any) need real-job before/after.
- [ ] T4 — flip S6 in the tracker + audit to the resolved state; retro in `TODO_backlog`.

## Guardrails
- `service_contracts` routing (2a) can change which rows map vs get flagged → dollar-adjacent; measure, don't assume.
- spec_parity / validate_report need real job DBs / a report+bill fixture to run — they belong in CI with fixtures, not
  the per-job orchestrator.
