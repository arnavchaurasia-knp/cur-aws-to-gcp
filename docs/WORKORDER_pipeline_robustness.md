# WORKORDER — Pipeline Robustness (industry-grade fix + architecture)

**Companion to:** [`docs/ARCHITECTURE_pipeline_robustness.md`](ARCHITECTURE_pipeline_robustness.md) (principles P1–P5 + the gate→repair→retry contract). That doc is the *why/what*; this is the *how/do-it*.
**Status:** WO-0 DONE (shipped). WO-1…WO-5 OPEN, priority-ordered.
**Audience:** implementation model. Every task below states exact files, steps, acceptance tests, and risk. Do tasks in order; each is independently shippable and test-gated.
**Origin:** [[project_review_retry_idle_wait]] — a 339-row bill's Review phase took 17 min to emit 3 correct vetoes; ~13 min was an agy agent idling on a leaked background task after a gate forced an avoidable LLM retry.

---

## Ground rules (apply to every task)

1. **Source of truth for rates/units/policy:** the provider's official pricing page wins over catalog/code/notes (see `AGENTS.md`). Not in scope here, but don't regress it.
2. **P1 is the spine:** if a gate violation is mechanically repairable, repair it with a script *before* the LLM. Never add an LLM call to fix something a rule can fix.
3. **Test-gate everything:** no task is "done" until its acceptance tests pass AND `python -m pytest tests/ -q` (skill) + `go build ./... && go vet ./... && go test ./internal/jobs/ ./internal/db/ -count=1` are green.
4. **Commit discipline:** one task per commit, Conventional Commits, on `main`, push to `origin2`. Stage only the files the task touches (the tree has pre-existing CRLF phantom-modified files — never `git add -A`).
5. **Editing the orchestrator:** `internal/jobs/orchestrate.go` embeds the pipeline driver as a Python string with `%%`-escaped format verbs. After any edit, **render + py_compile** it (procedure in Appendix A) before committing — a stray `%` or unbalanced quote compiles in Go but crashes every job at runtime.

---

## WO-0 — DONE (shipped, do not redo)

Recorded so the implementer builds on it rather than re-deriving it.

| Item | Commit | What |
|---|---|---|
| P2 passthrough SSOT | `8c2089c` | `config_loader._VALUE_SENTINEL_PASSTHROUGH_KEYS`; `load_assumption_passthroughs` now = explicit toggles ∪ value-sentinels (`value ≤ 0`); `is_passthrough_enabled` keeps caller `value` authoritative via `_load_explicit_passthrough_toggles`. |
| P3 advisory schema-adapt | `8c2089c` | `advisory_reviewer.py` introspects `gcp_projection` columns; sources `gcp_sku_name`/`gcp_od_cost` from where they actually live. |
| Liveness | `8c2089c` | `db.TouchJob` (running-only) called from `watcher.watchUntilDead` activity loop → `updated_at` tracks phase progress. |
| P1 gate repair contract | `f0797be` | `phaseSpec.RepairScripts`; orchestrator runs them on gate failure *before* the LLM retry and skips the retry if the gate then passes; `no_illegal_passthroughs` declares `[passthrough_enforcer]`. Regression test `test_value_sentinel_passthrough_stamped_on_pdf_row_and_gate_passes`. |

---

## WO-1 — agy task lifecycle & time budgets (P4) — **highest priority, open**

The dominant remaining risk. ~13 of the 17 incident minutes were `root agent idle; waiting up to 45m0s for 1 background task(s)`: a sub-second `find-sku.sh --help` was backgrounded during Mapping, never reaped, and a *later* agy invocation blocked on its stale completion notification up to the `--print-timeout` (45m).

Key facts (verified):
- Each phase is a **separate** one-shot agy call: `run_agy` → `subprocess.run([AGY, "--dangerously-skip-permissions","--model",MODEL,"--print-timeout",PRINT_TIMEOUT,"--log-file",…,"--add-dir",JOB_DIR,"-p",prompt])` (`orchestrate.go`). `PRINT_TIMEOUT="45m"`.
- The leaked task affected a *different* invocation than the one that spawned it ⇒ agy persists background tasks in a **shared per-user store**, not per-invocation. So reaping cannot be done purely inside one `run_agy`.
- `watcher.go` kills only on **no-activity** (`staleTimeout = config.AGYTimeoutMinutes·min`, 5 s poll). There is **no whole-job wall-clock ceiling** (also flagged in `WORKORDER_round2_risk_remediation.md`).

### WO-1a — Investigate agy's background-task surface (spike, do first)
**Goal:** know exactly what levers exist before changing behavior. Deliver findings as a short section appended to this file.
**Steps:**
1. Run `agy --help` (path: `C:\Users\arnav\AppData\Local\agy\bin\agy.exe`; `ELECTRON_RUN_AS_NODE=1`). Capture flags for: disabling background/async tasks, an idle/background wait timeout distinct from `--print-timeout`, and the task-store location.
2. Locate the shared task store (likely under `~/.gemini/…` / antigravity dirs). Determine whether a per-invocation isolated store is possible (e.g. an env var or `--add-dir`-style scratch).
3. Reproduce minimally: one `agy -p` call that backgrounds a trivial command, then a second `agy -p` call, and observe whether the second idles on the first's task.
**Acceptance:** findings documented with the exact flag(s)/env/store path. If agy exposes a background-wait timeout or a no-background flag, WO-1c uses it; if not, WO-1c falls back to the watcher ceiling (WO-1d) as the guarantee.
**Risk:** low (read-only investigation).

### WO-1b — Stop backgrounding fast helpers (prompt + wrapper)
**Problem:** `find-sku.sh` (and peers) are sub-second; nothing should background them.
**Files:** `skill/aws-gcp-cost-projection/phases/*.md` (prompt contract); optionally `scripts/find-sku.sh`.
**Steps:**
1. Add one line to the shared phase-prompt preamble: *"Run helper scripts (find-sku.sh, etc.) synchronously and wait for output. Do NOT background them or use async/background task execution."*
2. Belt-and-suspenders: if WO-1a shows agy auto-backgrounds on a heuristic (e.g. perceived long-running), make `find-sku.sh` print a fast-exit marker / ensure `--help` returns immediately so no heuristic trips.
**Acceptance:** a Mapping-phase run shows no `Tool is running as a background task` line for `find-sku.sh` in `token_usage_breakdown_*.csv` / `agy.log`. (Prompts are advisory — this reduces incidence; WO-1d is the hard guarantee.)
**Risk:** low. Prompt-only + a help-path tweak.

### WO-1c — Decouple the idle-wait from the work timeout
**Problem:** the "wait for background task" ceiling is the 45-minute *print*-timeout, which exists for legitimately long model turns — wrong budget for "a leftover task nobody tracks."
**Files:** `internal/jobs/orchestrate.go` (`run_agy` args / `PRINT_TIMEOUT`), `internal/config` (new knob).
**Steps (choose per WO-1a):**
- *If agy exposes a background/idle timeout flag:* pass it (e.g. a few minutes) on every `run_agy`. Add `config.AGYBackgroundWaitMinutes` (default small) and thread it through `StartOrchestrated` env like `TOTAL_PHASES`/`PRINT_TIMEOUT`.
- *If not:* leave `--print-timeout` as-is and rely on WO-1d for the hard ceiling; record in this file that agy has no finer knob.
**Acceptance:** with a deliberately-leaked background task, a downstream phase no longer blocks for >N min (N = the new budget), proven by `[LLM:END] elapsed` on a lightweight phase.
**Risk:** medium — don't set it so low that a genuinely long model turn is cut. Keep `--print-timeout` for *work*; the new knob is only for *idle-on-task*.

### WO-1d — Whole-job wall-clock ceiling (the agy-independent guarantee)
**Problem:** no end-to-end budget; a pathological phase can run to 45 min × phases.
**Files:** `internal/jobs/watcher.go` (owns PID liveness + stale-kill), `internal/config`.
**Design:** add `config.JobMaxWallClockMinutes` (generous default, e.g. 60–90, admin-tunable via existing settings plumbing). In `watchUntilDead`, track job start (first PID observation) and, each poll tick, if `now - jobStart > ceiling`, `killGroup(pid)`, write `failure.txt` with a clear reason, and let `finalize` mark it failed (distinct message from the stale-kill so operators can tell a budget-blowout from a hang). Ensure checkpoint/resume semantics are respected (a resumed job restarts its own clock, or the ceiling is per-attempt — decide and document).
**Acceptance:** a Go test (table/clock-injected) asserting a job exceeding the ceiling is killed and marked failed with the budget reason; `go test ./internal/jobs/ -run Watcher`. Verify a normal run well under the ceiling is untouched.
**Risk:** medium — must not false-kill legitimately long enterprise bills. Set the default from the slowest real job in the 75-job corpus + margin; make it admin-configurable.

**WO-1 exit criteria:** a leaked/slow background task can no longer inflate a job by >N minutes; there is a hard whole-job ceiling; normal jobs are unaffected. Update `ARCHITECTURE_…md` §5/§7 to mark P4 done.

---

## WO-2 — `projection_columns()` schema contract (P3)

**Problem:** `advisory_reviewer` is now schema-adaptive, but every other reader of `gcp_projection`/`aws_li_to_gcp_li` still hard-codes columns and will `BinderException` on the next view change. Make the contract central and loud.
**Files:** new `skill/aws-gcp-cost-projection/scripts/schema_contract.py` (or extend an existing data-access module); callers across `scripts/*.py`.
**Design:**
1. `projection_columns(con) -> set[str]` and `mapping_columns(con) -> set[str]`, each introspecting once (`PRAGMA table_info`) and cached per-connection.
2. A declared **canonical column set** for `gcp_projection` (the documented contract), versioned next to the view DDL. A helper `require_columns(con, table, needed)` raises one clear error naming the missing columns and the table — so drift fails loudly in *one* place.
3. Migrate readers that hard-code `gcp_projection` columns onto `projection_columns()`/adaptive sourcing (grep: `FROM gcp_projection`, `JOIN gcp_projection`).
**Acceptance:** a test that drops a column from a synthetic `gcp_projection` and asserts `require_columns` raises a message naming it (not a raw binder error); existing readers still pass. `pytest tests/ -q`.
**Risk:** low–medium (mechanical, but touches several readers — do it reader-by-reader).

---

## WO-3 — Gate-repair audit (P1 generalization)

**Problem:** only `no_illegal_passthroughs` has `RepairScripts`. Other gates may still escalate mechanically-fixable violations to the LLM.
**Files:** `internal/jobs/orchestrate.go` (each `phaseSpec` with `CheckSQL`); `scripts/validate_fix.py` (existing AUTOFIX rules to reuse).
**Steps:**
1. Enumerate every `CheckName`/`CheckSQL` gate. For each, classify its violations: **mechanical** (rule can fix), **partial**, or **judgment** (needs LLM).
2. For mechanical/partial gates, declare `RepairScripts` running the deterministic fixer *before* the LLM retry. `validate_fix.py` already holds autofix rules for the echo-bug (`gcp_service` = AWS name), map-with-no-rate downgrade, and detail-less-row passthrough — factor the relevant repair into a small script (or a `validate_fix --repair-only <gate>` mode) and wire it as `RepairScripts`.
3. For judgment gates, add a one-line comment stating why the LLM retry is the right mechanism (so the absence of `RepairScripts` is intentional, not an oversight).
4. Preserve `mapping_coverage`'s **post-retry** `ensure_full_coverage` backstop (model-first is correct there) — document the pre-retry vs post-retry distinction on each gate.
**Acceptance:** for each mechanical gate, a test like WO-0's: construct a violating DB, run the repair, assert the gate goes `>0 → 0` with no LLM. Render+py_compile the orchestrator.
**Risk:** medium — don't let a repair mask a *real* data gap that should surface. A repair must only stamp/normalize what is provably correct; anything ambiguous still goes to the LLM or fails loudly.

---

## WO-4 — Passthrough / is_workload resolution consolidation (P2 finish)

**Problem:** P2 is fixed for passthrough *enablement*, but related derived facts (`is_workload`, charge-type routing) still have multiple deciders per the existing backlog ([[project_classify_mechanics_fixes]] NEW-3 "is_workload unify", [[project_generic_mechanic_overclaim]]).
**Files:** `scripts/classify_mechanics.py`, `scripts/passthrough_enforcer.py`, `config_loader.py`.
**Steps:** fold `is_workload` and passthrough-reason resolution into single resolvers reused by classifier, enforcer, and validator; delete the divergent inline checks. Cross-reference and close the relevant items in `WORKORDER_classify_mechanics_fixes.md`.
**Acceptance:** one resolver per fact; tests asserting classifier/enforcer/validator agree on a shared corpus row-set. `pytest tests/ -q`.
**Risk:** medium–high (touches the hot classification path) — stage behind tests; this is the "stop the whack-a-mole" item, do it deliberately.

---

## WO-5 — Observability for the contract (P4/P5 supporting)

**Problem:** the incident was only diagnosable by hand-reading `agy.log`/`phase_checkpoint.json`. Make "deterministic repair fired / LLM retry fired / idle-wait occurred" first-class signals.
**Files:** `scripts/diagnostics.py` emitters; `orchestrate.go` repair/retry branches; `internal/metrics`.
**Steps:**
1. Emit a structured diagnostic on each path: `gate_repaired_deterministically{gate,phase}`, `gate_escalated_to_llm{gate,phase,residual}`, `phase_idle_wait_seconds{phase}`.
2. Add Go metrics counters (`metrics.GateRepaired`, `metrics.GateLLMRetry`) alongside the existing job metrics.
3. Surface per-phase "repaired vs retried" in the job log digest the watcher already persists.
**Acceptance:** a run that hits the repair path emits `gate_repaired_deterministically` and increments the counter; visible in `run_diagnostics.json` / digest.
**Risk:** low.

---

## Verification matrix (run before each commit)

| Layer | Command |
|---|---|
| Skill unit/integration | `cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q` |
| Go build/vet | `go build ./... && go vet ./internal/jobs/ ./internal/db/` |
| Go unit | `go test ./internal/jobs/ ./internal/db/ -count=1` |
| Orchestrator render+compile | Appendix A |
| End-to-end (when touching the pipeline) | run a bill with an Inferentia/Spot row; confirm `gate … repaired deterministically — skipping LLM retry` in `agy.log` and **no** `-retry` LLM phase; Review elapsed in seconds, not minutes |

## Sequencing & risk

```
WO-1a (spike) ─▶ WO-1b ─▶ WO-1c ─▶ WO-1d      (P4: the real win; WO-1d is the hard guarantee)
WO-2 ─▶ (independent, any time)                (P3: loud schema contract)
WO-3 ─▶ (after WO-0 pattern is understood)     (P1: generalize repair to all gates)
WO-4 ─▶ (deliberate, hot path)                 (P2: finish SSOT; stops whack-a-mole)
WO-5 ─▶ (cheap, do alongside WO-1/WO-3)        (observability)
```
Highest value / lowest risk first: **WO-1d** (hard ceiling, agy-independent) and **WO-5** (observability) can land immediately and de-risk everything else. **WO-4** is the most invasive — schedule it when the classifier path has test cover.

---

## Appendix A — render + py_compile the orchestrator

```powershell
# from repo root (bash tool). Writes a temp test that dumps the rendered driver, compiles it, removes it.
```
```bash
cat > internal/jobs/zz_render_dump_test.go <<'EOF'
package jobs
import ("os";"testing")
func TestZZDumpRendered(t *testing.T){
 s,err:=RenderOrchestratorForTest(".csv"); if err!=nil{t.Fatal(err)}
 if err:=os.WriteFile("orch_rendered_dump.py",[]byte(s),0644);err!=nil{t.Fatal(err)}
}
EOF
go test ./internal/jobs/ -run TestZZDumpRendered -count=1
rm -f internal/jobs/zz_render_dump_test.go
python -c "import py_compile; py_compile.compile(r'internal/jobs/orch_rendered_dump.py', doraise=True); print('PY_COMPILE OK')"
rm -f internal/jobs/orch_rendered_dump.py internal/jobs/__pycache__/orch_rendered_dump*.pyc
```
`PY_COMPILE OK` is mandatory before committing any `orchestrate.go` change. To also assert a phase's wiring, decode `PHASES` (base64 in the rendered file) and inspect `repair_scripts`/`check_sql` as in the WO-0 verification.
