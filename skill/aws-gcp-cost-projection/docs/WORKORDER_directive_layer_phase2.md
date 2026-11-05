# WORKORDER — Directive Layer, Phase 2: conversational refinement + UI reconciliation

Parent design: `docs/DESIGN_report_refinement.md`. Depends on Phase 1
(`WORKORDER_directive_layer_phase1.md`) being merged: one directive schema
(`directives.py`), resolver (`directive_resolver.py`), single apply phase
(`apply_directives.py`), `applied_directives` provenance column (registered in
`row_state_registry.json`), parity-gated.

Phase 2 makes the layer **usable by a user**: AGY turns natural language into directives,
asks follow-ups when it must, the report shows what changed and lets the user revert — and
the **manual-override UI that is being built in parallel is folded into the same ledger** so
we do not end Phase 1's unification only to re-fragment.

Baseline: `pytest tests/` green (1256), `go test ./internal/...` green. Every step keeps
them green.

---

## 0. FIRST — reconcile the parallel override mechanism (do before anything else)

A second override path has appeared and **must converge with the directive layer or the
whole Phase-1 unification is undone**:

| Parallel artifact | What it is today |
|---|---|
| `frontend/src/pages/ReportOverride.tsx` | "Review & Overrides" UI — per-row suppress / set GCP target / override rate / pin key-val / note |
| `internal/db/overrides.go` | `job_line_item_overrides` table + `LineItemOverride{Suppressed, GCPTarget, OverrideRate, PinnedKey/Val, Note}` |
| `scripts/apply_overrides.py` | reads `<job>/job_overrides.json`, writes the projection |

Its fields map 1:1 onto directive verbs — so it is a **directive producer**, not a third
engine:

| UI override field | Directive action |
|---|---|
| `Suppressed = true` | `set_strategy = ignore` |
| `GCPTarget = "Cloud SQL"` | `set_target_service` |
| `OverrideRate = X` | `set_pricing_basis`/rate pin (Phase-3 rate verb; until then carry as a pinned override directive, see §0.2) |
| `PinnedKey/PinnedVal` | generic `set_field` directive (selector = this row) |
| `Note` | `annotate` |

### 0.1 Convert the UI path to emit directives
- The Go handler that today writes `job_line_item_overrides` / `job_overrides.json` instead
  (or additionally) appends a directive to `<job>/directives.json` via a small writer
  (mirror `job_assumptions_handler.go`). Selector pins the exact `aws_li_key` (highest
  specificity — a manual per-row override must always win, design §2.3).
- `apply_overrides.py` is **absorbed into `apply_directives.py`**: add a
  `directives_from_legacy.py` branch that reads any existing `job_overrides.json` /
  `job_line_item_overrides` rows and yields directives, so in-flight jobs keep working.
  Then delete `apply_overrides.py` and drop its orchestrate step once no job writes
  `job_overrides.json`.
- Keep `job_line_item_overrides` as the **UI's read/edit store** if convenient, but it is a
  projection of the ledger, not a second source of truth: the ledger is authoritative and
  is what `apply_directives.py` consumes.

### 0.2 Parity gate for the merge
Extend `tests/test_directive_parity.py` (or a sibling) so a job carrying a
`job_overrides.json` produces an identical projection whether applied via the old
`apply_overrides.py` or via the directive path. No cutover until green.

**Deliverable of §0:** exactly one apply phase (`apply_directives.py`) and one source of
truth (the ledger). The manual UI and the (coming) chat UI both write directives.

---

## 1. AGY translator: natural language → proposed directive  (`scripts/directive_translator.py` — NEW)

Turns a user utterance + job context into **proposed** directives. AGY proposes; it never
mutates the projection (design §5).

- Input: `{utterance, job_dir, optional selected_row_keys}`.
- Build a tight LLM slice: the candidate rows (from the selection or a keyword match over
  `aws_li_catalog`), their canonical_service / product / usage_type / current
  mapping, and the **allowed action verbs + selector fields** (schema-constrained).
  Reuse the slice-building discipline in `llm_guidance.py`; do not hand the LLM the whole
  bill.
- Output: a list of `Directive` objects with `status="proposed"`, `author="agy"`,
  `source_utterance` = verbatim text, and a `confidence`. **Schema-constrained**: the model
  returns directive JSON only; anything failing `directives.validate_directive()` is
  rejected and retried/surfaced, never applied (reuse `llm_override_guard.py`).
- Phase-2 verb set = the Phase-1 verbs only: `set_target_service`, `set_strategy`,
  `set_is_workload`, `set_assumption`, `annotate`, plus the generic `set_field` for a
  pinned row. **New verbs (`set_target_family`, `set_target_shape`, `set_region`,
  `region_policy`, `set_pricing_basis`) are Phase 3** — if the user asks for one, AGY
  records the intent and returns a "not yet supported" clarification rather than guessing.

Tests (`tests/test_directive_translator.py`, NEW, mock the LLM): a clean utterance →
valid proposed directive; an out-of-schema model reply → rejected, nothing applied; an
utterance naming a Phase-3 verb → graceful "not yet supported".

---

## 2. Validation-before-confirm + scope estimate  (extend translator + `directives.py`)

Before a proposed directive is shown to the user, validate it against reality and compute
blast radius (design §5):
- **Existence/compatibility checks**, reusing what exists: target is an allowed GCP service
  for that `canonical_service` (service_map allowed-targets), and resolves to a priced SKU
  (`resolve_sku`); assumption value passes the ratio-group clamp (`config_loader`).
- **`scope_estimate`**: run the resolver's selector against the job's rows (read-only) to
  fill `{rows, aws_spend}`. This is a dry-run of §Phase-1 resolve, no writes.
- A directive that fails validation does not get `confirmed`; it becomes a **clarification**
  (§3), e.g. "target X isn't offered for this service — did you mean Y?"

Tests: scope_estimate counts match a known corpus job; an invalid target becomes a
clarification, not a confirmed directive.

---

## 3. Follow-up / clarification thread  (`<job>/refinement_thread.json` + `scripts/refinement_thread.py` — NEW)

Persist the conversation so AGY can ask and resume (design §5).
- `refinement_thread.json`: ordered messages (`user`/`agy`), and open **clarification**
  objects `{reason, question, options, blocking, directive_ref}`.
- AGY emits a clarification instead of guessing when a directive is under-specified or fails
  validation (missing region, ambiguous scope, unpriced target, Phase-3 verb). It asks
  **only what it needs** — if the user pre-answered ("use global if absent"), no question
  fires.
- Answering a clarification fills the param and flips the directive `proposed → confirmed`;
  the thread persists across sessions so a half-finished refinement resumes.
- `blocking=true` means the directive can't apply until answered; non-blocking clarifications
  (e.g. "FYI this is 9x AWS, keep?") apply on default-yes with the warning recorded.

Tests: an under-specified utterance yields a blocking clarification; answering it produces a
confirmed directive; a pre-answered utterance yields no clarification.

---

## 4. Apply + re-project trigger (reuse Phase-1 + existing rerun)

- Confirmed directives already flow through `apply_directives.py` (Phase 1). Phase 2 only
  wires the **trigger**: a confirmed refinement writes the ledger and launches the existing
  `RunType:"refinement"` rerun (generalize `job_assumptions_handler.go`, as Phase-1 §4
  began — it should snapshot `directives.json`, the superset, not only assumptions).
- Only touched rows re-rate (`incremental_rerate.py`). Guards/gates run after overlay
  (ratio clamp, allowed-targets, outlier) so a user directive can't silently produce an
  un-sellable SKU — AGY relays any warning back into the thread.

Go tests: a directive-triggered rerun reruns and the projection reflects the directive;
extend `internal/jobs/job_assumptions_test.go` / `duckdb_test.go` (the `refinement`
run-type case already exists).

---

## 5. Report shows what changed + lets the user revert  (render + export)

This is the Phase-1 §7 deferred rendering, now due.
- In `render_report.py` + `export_report.py`, read `applied_directives` and show, per
  affected row, "Changed by your request: *<source_utterance>*" with the directive id.
  Mirror the license-split symmetry pattern so HTML/PDF and CSV/XLSX disclose identically.
- A **refinement summary** block: N rows changed, $ delta vs baseline, list of directives
  with their verbatim utterances — the audit trail, human-readable.
- Reversibility entry point (minimal in Phase 2; full UX is Phase 5): a "revoke" that marks
  a directive `superseded` and reruns → row returns to baseline. The event-sourced ledger
  makes this a delete-and-replay, not an inverse-patch.

Tests: `tests/test_export_report.py` / a render test assert an affected row carries its
directive provenance in both outputs; revoke-then-rerun restores the baseline value.

---

## 6. UI: one surface for both manual and conversational refinement

`ReportOverride.tsx` becomes the single refinement surface:
- Keep the per-row manual controls (they now emit directives, §0).
- Add a chat box bound to the job (`refinement_thread.json`) for the AGY path: user types
  English → proposed directives shown with `scope_estimate` and a Confirm button →
  follow-up questions rendered inline → Apply triggers the rerun.
- Show the applied-directives list with per-directive revoke.
Backend: extend `internal/mcp/tools.go` / the job handlers with endpoints to
`propose` (calls translator), `confirm`, `answer_clarification`, `revoke`. No new store —
all state is the ledger + thread files.

---

## 7. Done criteria
- Exactly one apply phase and one source of truth; `apply_overrides.py` deleted, UI emits
  directives, merge parity green.
- AGY translator: NL → schema-validated proposed directives, with scope_estimate, mocked in
  tests; nothing applied without passing `validate_directive` + reality checks.
- Clarification thread persists and resumes; AGY asks only what it needs.
- Confirmed directive reruns via the existing refinement run-type; only touched rows rerate;
  guards still fire.
- Report (HTML + CSV/XLSX) discloses per-row provenance + a refinement summary; revoke
  restores baseline.
- Full `pytest tests/` + `go test ./internal/...` green.

## 8. Explicitly out of scope → Phase 3+
New preference verbs — `set_target_family` ("N2D for these ARM boxes", "Intel→Intel"),
`set_target_shape`, `set_region` + `region_policy` ("global if absent"), `set_pricing_basis`
(CUD / SP-netting) — and the full reversibility/history UX (Phase 5) and long-tail taxonomy
(Phase 6). Phase 2 ships the conversational loop and the unified surface on the **existing**
verbs; the richer preferences ride the same rails next.
