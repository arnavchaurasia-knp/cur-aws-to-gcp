# ARCHITECTURE MASTER TRACKER — the one engine, its subsystems, and what's wired

Status: **LIVING DOC.** Author: Claude (Opus 4.8) · opened 2026-11-20. This is the **single index** for the
architecture / systems work. If you are picking this up in a new session, read §0 first.

---

## 0. FOR A NEW AGENT — START HERE (read before touching anything)

You are working on an **AWS→GCP cost-projection pipeline** (`skill/aws-gcp-cost-projection/`). The pipeline takes an
AWS bill (CUR/PDF), classifies each line, maps it to a GCP SKU, applies a rate, and renders a projected bill.

**The core problem this tracker exists to fix:** over time, many *systems* were built — each meant to be the single
source of truth for one capability (unit conversion, catalog queries, spec comparison, validation phases). Several were
**never wired in**, so callers still do the thing inline, 2–4 different ways, and the "system" rots while looking like
safety. See `AUDIT_built_systems_wiring.md` for the proof. **The goal is ONE coherent engine with one wired SSOT per
capability**, not 94 modules and 125 work orders that partially overlap.

**Non-negotiable working rules (from the user, memory, and AGENTS.md):**
1. **Official page is the tiebreaker.** Every rate/unit/free-tier/billing fact is verified against the provider's
   OFFICIAL pricing page / billing API before you pin it (`cloud.google.com/...`, `aws.amazon.com/...`). The bundled
   catalog and code comments are NOT sources of truth. A `$0` catalog rate on a thing the provider charges for is a
   red flag. JS-rendered pages: use the in-app browser or `bxw` (`reference_bxw_browser_tool`).
2. **Route fixes through the intended system — never inline a 4th copy.** Before adding an inline multiplier, catalog
   query, spec check, or passthrough, check §2 for the SSOT that already owns it.
3. **Git:** commit on `main`, push `git push origin2 main:AGY` (origin2/AGY is the single source of truth). Never
   company origin, never touch credentials. Attribution: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
   (Avoid backticks in `git commit -F -` heredocs — the shell eats them.)
4. **GCP family defaults:** N4D / C4D / C4A, never legacy N2D/C2D AMD as a starting point.
5. **Prove no silent dollar moves.** Any change that alters a `unit_multiplier`, rate, or target must list before/after
   with the official-source cite; otherwise dollars must be unchanged. Full suite stays green (`pytest -q`).
6. **Keep this tracker current.** When you finish or advance any initiative in §3, update its row (status + %) and the
   subsystem status in §2 in the SAME commit. That is how the next session knows where things stand.

---

## 1. North star — ONE engine, nine subsystems

Every row flows through the same spine. Each stage has exactly one SSOT module that owns it; everything else calls that
module. The multiplier/target/rate are **derived**, never hand-picked per mechanic.

```
bill row
  → [S1 Identity]      what service + charge-type is this?        (product_code → spec_dispatch)
  → [S2 Catalog SSOT]  one way to read catalog.duckdb             (sku_catalog)
  → [S3 Target]        which GCP SKU meets/exceeds the spec?      (resolve_sku + spec_match + candidate_pool)
  → [S4 Unit]          multiplier = physical × granularity × semantic   (unit engine)
  → [S5 Pricing]       rate (OD-vs-OD), CUD, currency              (apply_rates)
  → [S6 Governance]    gates: unit/contract/passthrough/outlier    (validate_fix + contracts)
  → [S7 State]         provenance, row/field state, no prose-as-state   (row_state / field_state)
  → [S8 Output]        report rendered from state only             (render_report / render_notes)
  → [S9 Self-heal]     refresh learned facts + advisory review      (refresh loop + advisory_reviewer)
```

---

## 2. Subsystem register — the SSOT per capability + wiring status

Status legend: 🟢 wired & load-bearing · 🟡 partially wired / bypassed in places · 🔴 built but NOT wired · ⚪ unknown (verify).
"Route fixes here" = where new code for that capability MUST go.

| # | Subsystem | Intended SSOT module(s) | Status | Governing docs | Route fixes here |
|---|---|---|---|---|---|
| S1 | Identity / classification | `aws_normalizer` (product_code) → `spec_dispatch` + `service_specs.json`; legacy `classify_mechanics` RULES being retired | 🟡 | `project_layer1_product_code`, `SERVICE_SPECS_DESIGN.md`, `WORKORDER_spec_dispatch_cutover.md`, `WORKORDER_complete_service_registry.md` | add a charge-type to `service_specs.json`, not a new regex |
| S2 | Catalog SSOT | `sku_catalog` (meant to be the only `catalog.duckdb` reader) | 🔴 **0 importers** — reads done ad-hoc everywhere | `AUDIT_built_systems_wiring.md` (B) | migrate reads to `sku_catalog`; stop `duckdb.connect` in mappers |
| S3 | Target selection | `resolve_sku` + `spec_match` (canonical adequacy) + `candidate_pool` | 🟢 `resolve_sku` wired; `spec_match` canonical SSOT & parity locked to `reconcile_capacity`; `auto_review` clarified | `AUDIT_built_systems_wiring.md` (B), `WORKORDER_spec_match_consolidation.md` (100%), `WORKORDER_best_fit_target_selector.md`, `WORKORDER_deterministic_sku_select.md`, `WORKORDER_regional_vs_multiregion_targets.md` | use `spec_match` for spec comparison; `resolve_sku(prefer_single_region=…)` |
| S4 | Unit / quantity | `unit_equivalence` (`conversion_factor`, `derive_multiplier`) + `unit_dimensions.json` | 🟢 engine wired, generated dimensions, L1 physical × L2 granularity × L3 semantic guard | `WORKORDER_unit_conversion_engine.md` (100%), `WORKORDER_unit_dimensions.md`, `DESIGN_conversion_contracts.md`, `WORKORDER_allowed_targets_and_unit_parser.md` | derive multiplier via the engine after resolve_sku |
| S5 | Pricing / rate | `apply_rates` + `resolve_sku` + `synthetic_skus` + `currency_utils` | 🟢 (OD-vs-OD decided & done) | `project_pricer_review_decisions`, `WORKORDER_pricer_architecture.md`, `WORKORDER_od_vs_od_sp_ri.md`, `project_pricing_and_cutover_decisions` | pin rates with official cite; add synthetic SKUs via `synthetic_skus` |
| S6 | Governance / gates | `validate_fix` (phase) + `service_contracts` (Phase J) + `passthrough_enforcer` + `outlier_gate` + `spec_parity` + `validate_report` | 🟡 validate_fix wired; **`service_contracts` 🔴, `spec_parity` 🔴, `validate_report` 🔴 (not orchestrated)** | `AUDIT_built_systems_wiring.md` (C), `project_passthrough_reason_defect_default`, `WORKORDER_passthrough_guard_tier_redesign.md` | add gates to `validate_fix`; declare passthrough_reason from the enum |
| S7 | State / provenance | `row_state` + `field_state` + provenance spine | 🟢 (projection_note isolation done) | `project_projection_note_isolation`, `DESIGN_provenance_spine.md`, `DESIGN_row_state_registry.md`, `DESIGN_field_registry_and_schema_mapping.md` | write to row/field state; never read prose notes for decisions |
| S8 | Output / report | `render_report` + `render_notes` | 🟢 | `DESIGN_report_refinement.md`, `project_projection_note_isolation` | render from state; notes are output-only |
| S9 | Self-heal / review | refresh loop + `advisory_reviewer` + `equivalence_review` | 🟡 refresh partial; `equivalence_review` 🔴 not orchestrated | `project_refresh_and_reviewer_layer`, `WORKORDER_reviewer_refresh_loop.md`, `WORKORDER_operationalize_refresh_loop.md` | feed learned facts to the refresh file, not inline |

**The 🔴/🟡 rows are the backlog of "built but not wired" — consolidating them IS the "one scalable system" the user
wants.** S2, S3 (spec_match), S4, S6 are the highest-leverage because each kills live duplication.

---

## 3. Active architecture initiatives — tracker

| Initiative | Doc | Status | % | Next step |
|---|---|---|---|---|
| Built-systems wiring audit | `AUDIT_built_systems_wiring.md` | decisions pending | — | decide WIRE/DELETE per row (10 rows) |
| Unit-conversion engine (S4) | `WORKORDER_unit_conversion_engine.md` | done | 100% | — |
| `spec_match` consolidation (S3) | `WORKORDER_spec_match_consolidation.md` | done | 100% | — |
| `sku_catalog` SSOT adoption (S2) | `AUDIT…` B | not started | 0% | migrate ad-hoc `duckdb.connect` reads |
| spec-dispatch cutover (S1) | `WORKORDER_spec_dispatch_cutover.md` | in progress | — | delete classify_mechanics RULES |
| Per-service modelling + gate | `WORKORDER_per_service_full_modelling.md` | 26/33 services | ~79% | next service (eks/ecs) |
| passthrough_reason declarations | `WORKORDER_passthrough_reason_declarations.md` | done | 100% | — |

(Keep this table in sync; it is the quick-status a new session reads first.)

---

## 4. The consolidation thesis (why one system, not 94 modules)

The pipeline is sound where a subsystem is wired (S5 pricing, S7 state, S8 output) and fragile where a subsystem was
built but bypassed (S2, S3-spec_match, S4, S6-contracts). Every bug I keep hand-fixing (io1/io2 IOPS→throughput,
Fargate ephemeral-as-memory, multi-region 2× traps) is a symptom of a capability being done inline instead of through
its SSOT. **The fix is not more work orders — it is to finish wiring each subsystem's SSOT and delete the inline
copies**, so there is one scalable/maintainable/reliable path per capability. This tracker is the map for that; the
individual work orders are the steps.

Consolidation order (highest duplication-kill first): **S2 sku_catalog → S3 spec_match → S4 unit engine → S6
service_contracts/validators → S1 classify cutover.** Each is independently shippable, keeps the suite green, and moves
no dollars unless an official-sourced change is listed.

---

## 5. How to update this tracker (protocol)
- Advancing an initiative → update its row in §3 (status + %) and the affected subsystem status in §2, same commit.
- Finishing/wiring/deleting a system → flip its §2 status and note it in `AUDIT_built_systems_wiring.md`'s tracker.
- New architecture doc → add it to the right §2 subsystem's "Governing docs".
- This file + `AUDIT_built_systems_wiring.md` + `WORKORDER_unit_conversion_engine.md` are the three entry points a new
  session should read, in that order.
