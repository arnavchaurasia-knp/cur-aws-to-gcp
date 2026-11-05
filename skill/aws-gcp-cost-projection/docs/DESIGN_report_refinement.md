# DESIGN — Conversational Report Refinement (the Directive Layer)

Status: proposal. Author seat: senior eng. Goal: let a **user** (not a dev) refine a
finished projection in natural language — "use N2D for these ARM boxes", "Intel stays
Intel", "make this row passthrough", "this mapping is wrong", "price these in europe-west1,
fall back to global if absent" — have AGY (the LLM) turn that into a precise, auditable
change, ask a follow-up when it needs one, and re-project deterministically.

---

## 1. The one decision up front: overlay, not a second pipeline

**Do NOT build a parallel architecture.** A second pipeline would duplicate ingest,
classification, rating, gates, and reporting, and the two would drift. Instead add **one
thin layer** — the **Directive Layer** — that sits between deterministic mapping and
rate-fill, and reuses everything downstream (resolver, guards, gates, incremental_rerate,
report).

Why this is also the *cleaner* move: the refinement behaviour already exists today, but
**scattered across four+ ad-hoc mechanisms**, each with its own JSON, its own LLM slice,
its own apply script:

| Today (fragmented) | What it is |
|---|---|
| `review_fixes.json` → `apply_review_fixes.py` (Phase 3) | LLM confirm/override/veto per `aws_li_key` |
| `outlier_fixes.json` → `apply_outlier_fixes.py` (Phase 5) | same, for outliers |
| `<job>/assumptions.json` (+ `.baseline.json`) + `job_assumptions_handler.go` | per-job assumption overrides + rerun |
| `architecture-notes.json` → `apply_architecture_notes.py` | disclosures |
| `commitment_ignores` → `apply_commitment_ignores.py` | forced ignores |
| `RunType: "refinement"` (already in the Go job model) | a rerun triggered by a change |

These are **five implementations of one idea**: "a scoped, user/LLM-authored override that
re-shapes the projection and triggers a rerun." The design collapses them into a single
**Directive Ledger** with one schema, one resolver, one apply phase, one validation gate.
That is the net simplification — fewer moving parts after, not more.

---

## 2. Core model: directives as event-sourced, declarative overrides

A **directive** is one typed, scoped intent. Think CSS (`selector → declaration`) or a
Terraform override: *what rows* + *what to do*. The user's English becomes **data**, never
a direct mutation of the projection. Determinism is preserved: **baseline projection +
ordered directive ledger ⇒ the same refined projection every time** (event sourcing).

### 2.1 Storage
Per job, next to the existing snapshots:
- `<job_dir>/directives.json` — the ordered ledger (append-only; edits are new versions).
- `<job_dir>/refinement_thread.json` — the conversation state (messages + pending
  clarifications) so AGY can resume and ask follow-ups.
- Baseline is already captured (`assumptions.baseline.json` pattern) — generalize to a
  `baseline` marker so "remove all my changes" = replay with an empty ledger.

### 2.2 Directive schema (one shape for every kind of tweak)
```jsonc
{
  "id": "d7",                      // stable, ordered
  "created_at": "...", "author": "user|agy",
  "source_utterance": "use N2D for the ARM app servers",   // verbatim, for audit
  "selector": {                   // WHICH rows — any combination, AND-ed
    "service": "ec2",             // canonical_service
    "arch": "arm64",              // derived field
    "instance_family": ["m7g","c7g"],
    "usage_type_re": "...",       // escape hatch, still structured
    "region": "ap-south-1",
    "account_id": "...", "tag": {"team":"payments"},
    "aws_li_keys": ["..."]        // explicit pin — highest precedence
  },
  "action": {                     // WHAT to do — exactly one verb
    "type": "set_target_family",  // see taxonomy §3
    "value": "N2D",
    "params": {"region_policy":"nearest_then_global"}
  },
  "scope_estimate": {"rows": 42, "aws_spend": 5123.40},  // shown before commit
  "status": "proposed|confirmed|applied|superseded",
  "confidence": 0.82,
  "needs_clarification": null      // or a question object (§5)
}
```

### 2.3 Resolver (deterministic, the heart of "cleaner")
One function resolves the ledger → per-row effective overrides:
- **Specificity order** (most-specific wins): explicit `aws_li_key` pin > tag/account >
  family+arch > service > global. Mirrors the spec-dispatch identity-first philosophy.
- **Recency breaks ties**: later directive wins over earlier of equal specificity.
- **One verb per field**: two directives both setting `target_family` on the same row →
  later wins, earlier marked `superseded` (visible, not silently dropped).
Output: each row carries `applied_directives: [ids]` → full provenance in the report.

---

## 3. Directive taxonomy — every tweak the user can ask for

Enumerating "all possibilities" as the user asked. Each maps to **one action verb**; AGY's
only job is to pick the verb + fill the selector.

| User says (natural language) | action.type | Notes / reuses |
|---|---|---|
| "Use N2D for these ARM servers" / "Intel→Intel" | `set_target_family` | validated against `compute_family_availability.json` + region |
| "Use a smaller/bigger shape", "custom 4vCPU/16GB" | `set_target_shape` | goes through `resolve_sku` existence check |
| "This should be passthrough" | `set_strategy=passthrough` | reuses passthrough_reasons |
| "This should be mapped, not ignored" / "don't drop this" | `set_strategy=map` / `include_row` | |
| "This mapping is wrong, it's really X" | `set_target_service` | revalidated via allowed_targets enum |
| "Ignore this / it's not a workload" | `set_strategy=ignore` / `set_is_workload` | |
| "Treat this credit as workload" | `set_is_workload=true` | existing credit-rows workorder |
| "Price these in europe-west1" | `set_region` | |
| "If not in that region, use nearest / global" | `region_policy` param | `nearest_then_global \| global \| fail` |
| "Assume 8 VMs per NAT gateway", "ACU=2vCPU+4GB" | `set_assumption` | **this is just the existing assumptions.json, now one directive type** |
| "Use CUD / 3-yr commit / net my Savings Plan" | `set_pricing_basis` | OD-vs-OD default stays unless overridden |
| "Apply this to the whole service, not just this row" | widens `selector` | re-scope, not a new verb |
| "Add a note: finance approved this" | `annotate` | disclosure, like architecture-notes |
| "Undo that" / "reset assumptions" | `revoke(id)` / empty ledger | event-sourced ⇒ trivial |

New services/verbs extend the table, not the architecture.

---

## 4. Flow: where it plugs into the existing pipeline

```
 (unchanged deterministic baseline)                       (new, reused on every refine)
 ingest → classify/map → [BASELINE PROJECTION] ─┐
                                                 ├─►  Directive Layer (overlay apply)
 user chats with AGY ──► translate ──► directives.json ──► resolver ──► per-row overrides
                                                 │                         │
                                                 ▼                         ▼
                              VALIDATION GATE (reuse: llm_override_guard,  apply as a single
                              allowed_targets, resolve_sku-exists,         overlay phase that
                              ratio-group clamp, catalog-in-region)        rewrites aws_li_to_gcp_li
                                                 │                         │
                                                 ▼                         ▼
                                   incremental_rerate (only touched rows) → gates → report
```

- **Trigger:** reuse the existing `RunType:"refinement"` rerun path
  (`job_assumptions_handler.go` already writes a snapshot and launches a rerun — generalize
  it from "assumptions" to "directives").
- **Apply point:** a single new phase `apply_directives.py` that *replaces and subsumes*
  `apply_review_fixes.py` + `apply_outlier_fixes.py` + `apply_commitment_ignores.py` +
  the assumptions merge. It runs after baseline mapping, before rate-fill.
- **Incremental:** only rows whose `applied_directives` changed get re-rated
  (`incremental_rerate.py` already does exactly this once `gcp_sku_id` changes).
- **Idempotent & reversible:** rerun from baseline + ledger; never mutate in place.

---

## 5. AGY's role — translator + clarifier, never mutator

AGY converts English → **proposed** directive(s). It has three hard rules:

1. **Schema-constrained output.** AGY returns a directive object (or several), not prose
   and not SQL. Free-form LLM mutation of the projection is forbidden — the same discipline
   that `llm_override_guard.py` + allowed_targets already enforce.
2. **Validate before commit.** A proposed directive is checked against reality *before* the
   user is asked to confirm: does `N2D` exist in `ap-south-1` (`compute_family_availability`)?
   is the target an allowed GCP service for that canonical_service? does `resolve_sku` find a
   priced SKU? If not, AGY doesn't silently proceed — it asks (§ follow-ups).
3. **Confirm with impact.** Before applying, show `scope_estimate` ("this touches 42 rows,
   $5,123/mo") so the user sees blast radius. Big-blast or low-confidence directives require
   an explicit confirm.

### Follow-up questions (the "hey, which region?" loop)
When a directive is under-specified or fails validation, AGY emits a **clarification** into
`refinement_thread.json` instead of guessing:
```jsonc
"needs_clarification": {
  "reason": "region_absent",
  "question": "N2D isn't offered in ap-south-1. Use the nearest region (asia-south2), the global price, or keep the current mapping?",
  "options": ["nearest", "global", "keep"],     // resolvable to a region_policy
  "blocking": true
}
```
The thread persists, so the conversation resumes across sessions. Answering it fills the
param and flips the directive `proposed → confirmed`. **AGY asks at most what it needs** —
if the user already said "use global if absent", no question fires.

---

## 6. Provenance, audit, safety (reuse, don't reinvent)

- **Per-row provenance:** `applied_directives` column → the report renders "Changed by your
  request #7: *use N2D for ARM*" with the verbatim utterance. Fits the existing
  provenance-spine.
- **Audit trail:** the append-only ledger IS the audit log — who changed what, when, from
  which sentence, with what blast radius. Nothing silent.
- **Reversible:** revoke a directive or reset to baseline → replay → gone.
- **Guardrails still run after overlay:** ratio-group clamp (already in `config_loader`),
  allowed_targets enum, SKU-exists, outlier gate. **A user directive cannot produce an
  un-sellable SKU or an absurd ratio silently** — it surfaces the same warning path a
  bad auto-mapping would, and AGY relays it ("heads up, that makes the row 9x AWS — keep?").

---

## 7. Why this is MAANG-standard, not bespoke

- **CQRS / event sourcing:** NL intent → typed command; ordered, replayable ledger; state
  is a fold over events. Reproducible, time-travelable, auditable.
- **Human-in-the-loop LLM with schema-constrained tool output + a validation gate** before
  any side effect — the current best practice for LLMs that change financial numbers.
- **Declarative, specificity-ordered overrides** (CSS/Terraform/policy-as-code), with a
  deterministic resolver — no imperative per-tweak code.
- **Idempotent incremental recompute** — already present (`incremental_rerate`), just wired
  to a general trigger.
- **One seam, many verbs** — extensibility lives in a data table (taxonomy §3), not in new
  services.

---

## 8. Build plan (phased, each shippable)

1. **Unify the schema.** Define `directives.json` + the resolver; make `apply_directives.py`
   read it and reproduce today's behaviour by *translating* the existing `review_fixes.json`,
   `outlier_fixes.json`, assumptions, and commitment-ignores into directives. Ship behind a
   flag; prove byte-identical projections on the pinned corpus → then delete the four old
   apply scripts. (This is pure refactor — de-risks everything after.)
2. **Resolver + provenance column + report rendering** of `applied_directives`.
3. **AGY translator**: NL → proposed directive, schema-constrained, with validation gate and
   `scope_estimate`. Start with the 5 highest-value verbs (family, strategy, target_service,
   region, assumption).
4. **Follow-up loop**: `refinement_thread.json`, clarification objects, resume.
5. **Reversibility UX**: revoke / reset-to-baseline, directive history in the report.
6. Extend the taxonomy to the long tail; add per-directive confirm thresholds.

Phase 1 is the keystone: it makes the system *cleaner today* (four mechanisms → one) even
before any conversational feature ships, so the refactor pays for itself immediately.

---

## 9. Open questions for product
- Confirm-threshold policy: which directives auto-apply vs require explicit confirm (by
  blast radius $ and by confidence)?
- Directive sharing: are directives per-user or per-job-shared across a team?
- Precedence of a user directive vs a later catalog refresh that invalidates it (pin vs
  re-evaluate)?
