# Pipeline Robustness Architecture

**Status:** living design. Principles P1–P5 are the contract; §7 tracks current → target.
**Execution plan:** [`docs/WORKORDER_pipeline_robustness.md`](WORKORDER_pipeline_robustness.md) — task-by-task (WO-1…WO-5) with files, steps, acceptance tests.
**Owner subsystem:** the 6‑phase projection pipeline (`internal/jobs/orchestrate.go` + `skill/aws-gcp-cost-projection/scripts/*`).
**Origin:** diagnosed 2026‑11‑26 from a robustness run where a 339‑row bill's *Review* phase took 17 min to emit 3 (correct) veto decisions. See [[project_review_retry_idle_wait]].

---

## 0. Read this first

The projection pipeline is a chain of deterministic scripts with an LLM (agy) invoked once per phase. It is already well‑factored (checkpoints, soft/hard scripts, per‑phase gates). The robustness defects we hit were **not** random bugs — each was the same shape: *a deterministic invariant was left to be discovered/repaired by an expensive, unbounded mechanism.* This doc names the principles that prevent that class, then maps the current code onto them.

Three symptoms, one disease:

| Symptom (incident) | Immediate cause | Architectural gap |
|---|---|---|
| Review ran 17 min for 3 vetoes; ~13 min was an agy agent idling "waiting up to 45m0s for 1 background task" | A gate forced an LLM *Review‑retry*; that retry inherited a leaked background task and blocked on it | **P4** no bounded task/time lifecycle; **P1** a deterministic fix escalated to the LLM |
| The retry fired at all | An Inferentia passthrough row reached `no_illegal_passthroughs` as `passthrough_reason='unclassified'` | **P1/P2** the deterministic stamper silently no‑op'd because passthrough state had two divergent sources of truth |
| `advisory_reviewer.py` crashed every run (0 findings) | It `SELECT`ed columns the `gcp_projection` *view* does not expose | **P3** schema assumptions hard‑coded per query, failing loudly nowhere and silently everywhere |

---

## 1. Principles (the design spine)

**P1 — Deterministic invariants are enforced deterministically. The LLM decides only genuine ambiguity.**
If a violation can be repaired by a rule (stamp a reason, collapse components, synthesize a CUD alias), a *script* repairs it. The LLM is reserved for decisions that genuinely need judgment (which SKU, confirm/override/veto). Escalating a mechanical fix to the model is the single most expensive mistake in the pipeline: it costs tokens, latency, and — because the model runs under a 45‑minute print‑timeout — tail‑risk wall‑clock.

**P2 — One source of truth per derived fact.**
"Is this row an operator‑forced passthrough?" must have exactly one resolver. Two resolvers that *mostly* agree will diverge on an edge (empty `usage_type`, a value sentinel vs a toggle) and the divergence is invisible until a gate fires downstream.

**P3 — Schema access is a loud contract, not a per‑query assumption.**
Every reader that hard‑codes column names against a table/view it does not own is a latent `BinderException` the day that shape drifts. Column presence is introspected once and sourced adaptively; absence is handled, not crashed.

**P4 — Everything that can wait has a budget.**
Every phase, every agy invocation, every background task, and the job as a whole has an explicit time ceiling and a defined reaping point. "Wait up to 45m for a task nobody is tracking" is never the default.

**P5 — Gates are pure checks over post‑repair state.**
Deterministic repair (AUTOFIX) runs first; gates then *verify*. A gate must never be the thing that triggers the repair of a mechanically‑fixable condition — by the time a gate runs, that condition should already be impossible.

---

## 2. The gate → repair → retry contract (P1, P5)

### 2.1 How it works today
Each `phaseSpec` (`orchestrate.go`) may declare `CheckName`/`CheckSQL`: an integer gate run after the phase's agy call (0 = healthy). On failure the orchestrator ran a **targeted LLM retry** (`run_agy(... "-retry")`), re‑ran post‑LLM scripts, and re‑checked. Only `mapping_coverage` had a deterministic backstop (`ensure_full_coverage.py`) — and deliberately *after* the LLM, because there the model should attempt real mappings first.

Every other gate, including `no_illegal_passthroughs`, had **no deterministic repair** — so a mechanically‑fixable violation (a missing `passthrough_reason` stamp) spent a full LLM retry. That retry is where the 13‑minute idle‑wait lived.

### 2.2 The generalized contract (implemented)
`phaseSpec` now carries `RepairScripts []string`. The orchestrator's gate‑failure path is:

```
gate fails (v > 0)
  └─ if RepairScripts declared:
        run them (deterministic, no LLM)
        re‑evaluate gate
          ├─ 0  → done. checkpoint + return. NO LLM retry, NO idle‑wait.
          └─ >0 → fall through with the reduced count
  └─ targeted LLM retry (only for what repair could not resolve)
  └─ post‑LLM scripts + re‑check
  └─ gate‑specific post‑retry backstop (e.g. mapping_coverage → ensure_full_coverage)
  └─ still failing → CRITICAL phase stops the job; non‑critical warns and ships
```

`no_illegal_passthroughs` declares `RepairScripts: ["?scripts/passthrough_enforcer.py $DB"]`. A forced passthrough that reaches the gate unstamped is now stamped deterministically and the gate passes **without** an LLM call.

**Design rule going forward:** a new gate must answer "is every violation of this gate mechanically repairable?" — if yes, it declares `RepairScripts` and the LLM retry is dead code for it; if partly, repair shrinks the LLM's work to the ambiguous residue; if no (needs judgment), it relies on the LLM retry and says so in a comment. `pre‑retry` repair (authoritative rules) vs `post‑retry` backstop (last‑resort, model‑first) is an explicit, per‑gate choice.

---

## 3. Passthrough‑state resolution (P2)

### 3.1 The divergence that caused the retry
Two functions in `config_loader.py` answered "is key K a passthrough?":
- `load_assumption_passthroughs()` read only an explicit `"passthrough"` toggle object — **absent** from `assumption-defaults.json` — so it returned `∅` for value‑sentinel keys.
- `is_passthrough_enabled()` additionally honored the numeric **value sentinel** (`inferentia_chip_to_tpu <= 0 ⇒ passthrough`).

`passthrough_enforcer.py` used the *first*. So with the committed default `inferentia_chip_to_tpu=0.0`, the enforcer's enabled set was empty and it no‑op'd — the row stayed `unclassified` and tripped the gate.

### 3.2 The model (implemented)
`_VALUE_SENTINEL_PASSTHROUGH_KEYS` is the single declared set of value‑sentinel keys. `load_assumption_passthroughs()` is now the **one** resolver of the *effective* set = explicit toggles ∪ `{k : value(k) ≤ 0}`. `is_passthrough_enabled()` is layered on it and keeps a caller‑supplied `value` authoritative (so callers that already hold the live value aren't overridden by a loaded default). Toggle‑only loading is factored into `_load_explicit_passthrough_toggles()` so the two notions never re‑diverge.

**Invariant (locked by test):** a value‑sentinel passthrough on a PDF‑style row (empty `usage_type`, signal only in `operation`) is stamped `operator_assumption` + `[passthrough: forced via assumption …]`, and `no_illegal_passthroughs` returns 0. (`tests/test_passthrough_enforcer.py::test_value_sentinel_passthrough_stamped_on_pdf_row_and_gate_passes`.)

### 3.3 Why the gate only ever flagged this one row
The gate SQL flags passthroughs **only** for services with direct GCP equivalents (EC2/EBS/RDS/Aurora/ElastiCache/S3/LB) and excludes notes containing `forced via assumption`. MSK and GuardDuty are legitimately passthrough and not in that list, so they never tripped it — confirming the gate's scope is intentional and the fix is targeted, not a broadening of what counts as "illegal".

---

## 4. Schema access contract (P3)

`gcp_projection` is a **view** derived from `aws_li_to_gcp_li` + rate CTEs; it exposes `gcp_sku_id` and `gcp_projected_cost` but **not** `gcp_sku_name` (that lives on the mapping table) or a separate `gcp_od_cost`. `advisory_reviewer.py` hard‑coded `p.gcp_sku_name`/`p.gcp_od_cost` → `BinderException` on every real run.

**Interim (implemented):** the query is schema‑adaptive — it introspects `gcp_projection`'s columns and sources each field from where it actually is (`p.gcp_sku_name` if present, else a de‑duplicated join on `aws_li_to_gcp_li`, else `NULL`; `gcp_od_cost` if present, else alias `gcp_projected_cost`). This mirrors the existing `has_aws_od` pattern and works against both the real view and synthetic test fixtures.

**Target:** one `projection_columns()` / schema‑contract helper that every reader of `gcp_projection` / `aws_li_to_gcp_li` goes through, so a column rename fails in one typed place with a clear message instead of as scattered binder errors. The view's canonical column set becomes a documented contract, versioned with the view DDL.

---

## 5. Agent & task lifecycle, time budgets (P4) — the open frontier

This is the only remaining piece of the incident **not yet fixed** (Bug A). Captured here because P1/P5 above make it *rare*, not *impossible*.

Observed: during Mapping the agent backgrounded `find‑sku.sh --help` (a sub‑second helper). It was tracked as an outstanding background task but never reaped at the phase boundary. The next phase's agent went idle and blocked on `root agent idle; waiting up to 45m0s for 1 background task(s)` — the wait ceiling is the agy `--print-timeout` (`PRINT_TIMEOUT=45m`, `orchestrate.go`). It resolved in ~13 min only when a stale completion notification arrived.

Target design:
1. **Reap at phase boundaries.** No background task survives the phase that spawned it; the orchestrator (or the phase prompt contract) drains/kills outstanding tasks before the next phase starts. A fast helper like `find‑sku.sh` should never be backgrounded in the first place.
2. **Decouple the idle‑wait from the work timeout.** A 45‑minute *print*-timeout (generous, for a legitimately long model turn) must not also be the ceiling for "wait on a leftover task." The idle‑for‑background‑task wait gets its own short budget.
3. **Whole‑job wall‑clock ceiling.** There is today no end‑to‑end budget (noted in `WORKORDER_round2_risk_remediation.md`). Add one, with the watcher as the enforcement point (it already owns PID liveness + stale‑kill in `watcher.go`).

Until (1)–(3) land, P1 is the mitigation: with deterministic repair in front of the gate, the *Review‑retry that triggered the idle‑wait no longer fires* for this (dominant) cause — so the blast radius is cut even though the idle‑wait mechanism itself remains.

---

## 6. Where each principle is enforced

| Principle | Enforcement point | Locked by |
|---|---|---|
| P1 deterministic‑first | `phaseSpec.RepairScripts` + orchestrator gate path | render + py_compile tests; §2.2 |
| P2 one passthrough resolver | `config_loader._VALUE_SENTINEL_PASSTHROUGH_KEYS`, `load_assumption_passthroughs` | `test_passthrough_enforcer`, `test_config_loader_passthrough` |
| P3 schema contract | `advisory_reviewer` introspection (interim); `projection_columns()` (target) | `test_advisory_reviewer` |
| P4 budgets/lifecycle | watcher + orchestrator (target) | — (open) |
| P5 gates verify post‑repair | AUTOFIX→GATES ordering in `validate_fix.py`; repair‑before‑retry in orchestrator | validator tests |

---

## 7. Current → Target → Migration

**Done (commit on `main`, 2026‑11‑26):**
- P1: `RepairScripts` contract + `no_illegal_passthroughs` deterministic repair before LLM retry.
- P2: unified passthrough resolution; value‑sentinel keys honored; regression test.
- P3: `advisory_reviewer` schema‑adaptive query.
- (adjacent) job `updated_at` liveness via `db.TouchJob` from the watcher activity loop.

**Open, in priority order:**
1. **P4 task reaping + idle‑wait budget** (Bug A) — highest remaining leverage; deeper agy/orchestration work. Reap background tasks at phase boundaries; stop backgrounding fast helpers; give the idle‑wait its own short ceiling.
2. **P4 whole‑job wall‑clock ceiling** — watcher‑enforced; see `WORKORDER_round2_risk_remediation.md`.
3. **P3 `projection_columns()` contract helper** — fold every `gcp_projection`/`aws_li_to_gcp_li` reader onto one introspected schema contract.
4. **P1 audit of remaining gates** — for each `CheckSQL` gate, classify violations as mechanically‑repairable / partly / judgment, and declare `RepairScripts` (or document why not). Candidates first: echo‑bug, no‑rate downgrade (both already have AUTOFIX in `validate_fix.py` — move the same rules in front of any orchestrator gate that can trip on them).

**Migration stance:** additive and test‑gated. Each gate gains `RepairScripts` independently; the passthrough resolver and schema helper are drop‑in; the P4 lifecycle work is the one item that touches the hot orchestrator loop and lands behind its own tests.

---

## 8. Design rules for new work (the one‑paragraph version)

Put the rule in a script, not a prompt. Give every gate a deterministic repair unless its violations genuinely need judgment, and run that repair *before* the model. Resolve each derived fact (is‑passthrough, is‑workload, charge‑type) in exactly one function. Read a table you don't own through an introspected contract, never a hard‑coded column list. Give every wait a budget and every background task an owner that reaps it. Then a gate is only ever a *check* — and a 3‑veto review takes 3 minutes, not 17.
