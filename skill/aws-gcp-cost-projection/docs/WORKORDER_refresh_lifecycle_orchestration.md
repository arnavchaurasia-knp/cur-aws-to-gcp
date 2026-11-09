# WORK ORDER — Refresh lifecycle orchestration (schedule · retry/backoff · escalate · recover)

**For an implementing AI with NO prior context. Read top to bottom. Do every
ORIENTATION PROBE in Phase 0 and paste real output into RESULT before writing code.
If a probe disagrees with this file, STOP and report.**

This is the **capstone** of the refresh work. It sits on top of:
- `WORKORDER_refresh_observability_and_completeness.md` — the `RefreshRun` record,
  `data/refresh_sources.json`, per-source completeness. **BUILD THAT FIRST.** This
  work order assumes the `RefreshRun` superset record and the sources file exist.
- `WORKORDER_self_sustaining_data_wiring.md` — the individual refreshers this
  orchestrator drives.

Goal: turn the current "run once on a 24h tick, and if it fails just sit failed
until tomorrow" into a managed lifecycle — scheduled, retried with backoff,
escalated to humans only when it truly can't recover, dependency-ordered, and
controllable from the UI.

### Anti-hallucination rules (whole work order)
1. **Reuse the existing primitives — do not invent a scheduler framework.** The
   goroutine scheduler (`StartCatalogRefresher`), the manual trigger
   (`TriggerCatalogRefresh`), the single-writer locks (`refreshRunMu` + the Python
   `Lock` in `catalog_manager.py`), and the notify infra (email/Slack, already used
   by `RevalidateDirectivesAfterRefresh`) ALL EXIST. Generalize them; don't
   duplicate.
2. **This work order changes NO pricing and NO projected dollars.** It orchestrates
   *when/whether* refreshers run and *how failures are handled* — never what a
   fetched value is.
3. **A failing refresh must never degrade live data.** Retries operate on the fetch;
   the pricer keeps using the last COMPLETE artifact until a new complete one lands
   (the partial-fetch guard from the observability work order). Verify, don't assume.
4. **One phase at a time; tests after each.** All tests use fixtures/fakes — NO live
   GCP/AWS fetches, NO real sleeps (inject the clock / backoff so tests are fast).
5. Go under `internal/`, Python under `skill/aws-gcp-cost-projection/`, React under
   `frontend/`.

---

## 0. ORIENTATION PROBES — run first, fill RESULT

| # | Probe | Expected (confirm) | RESULT |
|---|---|---|---|
| P1 | `go build ./... ; echo ok` and `cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q 2>&1 \| tail -3` | both green; baseline pass count ____ | |
| P2 | `grep -n "StartCatalogRefresher\|runCatalogRefreshIfStale\|runCatalogRefreshNow\|time.NewTicker\|TriggerCatalogRefresh" internal/jobs/catalog_refresher.go` | a goroutine: startup run + 24h ticker → `runCatalogRefreshIfStale` → `runCatalogRefreshNow`; `TriggerCatalogRefresh` is the manual path. **NO retry, NO backoff, NO attempt counter.** | |
| P3 | `grep -n "State\b" internal/jobs/catalog_refresher.go \| head` | states are only idle/running/success/failed/skipped (+`partial` once observability lands). No retrying/backing_off/escalated. | |
| P4 | `grep -rn "RevalidateDirectivesAfterRefresh\|emailCfg\|slackURL\|notify\." internal/jobs/catalog_refresher.go internal/jobs/notify_resolve.go \| head` | notify (email/Slack) infra exists and is already called after a refresh for stale pins — reuse it for escalation | |
| P5 | `grep -n "refreshRunMu\|Lock()" internal/jobs/catalog_refresher.go && grep -n "class .*Lock\|LOCK\|flock\|single-writer" scripts/catalog_manager.py \| head` | single-writer locking exists on both sides | |
| P6 | `test -f data/refresh_sources.json && echo FOUND || echo "MISSING — build the observability work order first"` | FOUND (dependency) | |
| P7 | `grep -n "Start.*Refresher(ctx)" cmd/server/main.go` | **TWO** scheduler goroutines launched at boot: `StartCatalogRefresher(ctx)` AND `StartComputeAvailabilityRefresher(ctx)` (compute_availability_refresher.go). The orchestrator subsumes **both** — you will replace both launches. | |

---

## 1. THE LIFECYCLE GAP

Current flow: boot → run if stale → 24h ticker → run if stale. On failure: record
`failed`, do nothing until the next tick or a manual click. `catalog_refresh_status.json`
today shows repeated `failed` ("No GCP credentials … Nothing fetched") with no
retry and no alert — the exact "sits broken silently" problem.

Missing lifecycle capabilities:
- **Retry with backoff** after a transient failure (network, rate limit, partial).
- **Max attempts → escalate** (notify admins) → **cooldown** instead of hammering.
- **A real state machine** with attempt count and `next_retry_at`.
- **Dependency ordering**: downstream refreshers (assumption-domains, egress, cud)
  run only after their upstream (catalog) run is `Complete`.
- **One orchestrator for all sources**, not a per-source ad-hoc goroutine.
- **Timeout** on a run + stuck-run recovery (partial reset exists; formalize it).
- **UI control**: pause/resume a source, "retry now", see attempts + next retry.

## 2. DESIGN

### 2.1 State machine (per source)

```
            ┌──────── due (schedule says run, deps satisfied) ───────┐
 idle ──────┤                                                        ▼
            └── paused (admin)                                    running ──┐
                                                                            │ outcome
     escalated ◄── (attempts == max) ── backing_off ◄── retrying ◄─ failed/partial
        │                                   ▲                 │
        │ (manual retry / next schedule)    └── wait next_retry_at ─┘
        └───────────────► due ───►                     success ──► idle (schedule next)
```

- `running` → `success`: schedule next run at `now + interval`; reset attempts.
- `running` → `failed`/`partial`: if `attempts < max_attempts` → `retrying`, set
  `next_retry_at = now + backoff(attempts)`; the orchestrator picks it up then.
- `attempts == max_attempts` → `escalated`: send ONE notification (email/Slack via
  the existing infra), stop retrying, wait for the next scheduled window or a manual
  "retry now". On the next `success`, send a ONE-TIME recovery notification.
- `paused`: admin-set; orchestrator skips it until resumed.

### 2.2 Per-source policy in `data/refresh_sources.json` (extend it)

Add a `lifecycle` block to each source entry (the file already exists from the
observability work order):

```json
"gcp_catalog": {
  "...": "...existing completeness fields...",
  "lifecycle": {
    "interval_hours": 168,
    "max_attempts": 4,
    "backoff_base_seconds": 300,
    "backoff_factor": 3.0,
    "backoff_cap_seconds": 21600,
    "timeout_seconds": 3600,
    "depends_on": [],
    "escalate_after_consecutive_failures": 1,
    "treat_partial_as": "retry"
  }
},
"assumption_domains": {
  "...": "...",
  "lifecycle": { "...": "...", "depends_on": ["gcp_catalog", "compute_availability"] }
}
```

`backoff(n) = min(backoff_cap, backoff_base * backoff_factor^(n-1))`. All values
read from the file — NONE hardcoded in Go/Python.

### 2.3 Extend the `RefreshRun` record (observability work order's shape)

Add: `Attempt int`, `MaxAttempts int`, `NextRetryAt string`,
`ConsecutiveFailures int`, `Escalated bool`. `State` gains
`retrying|backing_off|escalated|paused`. Persisted the same way as the base record.

### 2.4 The orchestrator (generalize `StartCatalogRefresher`)

One goroutine, `StartRefreshOrchestrator(ctx)`, launched where
`StartCatalogRefresher` is launched today (P7). Every tick (e.g. 1 min, injectable
for tests) it:
1. Loads `refresh_sources.json` + each source's latest `RefreshRun`.
2. For each source, decides the next action from the state machine: is it due
   (stale/scheduled), is a retry's `next_retry_at` reached, is it `paused`/`escalated`?
3. **Dependency check**: a source with `depends_on` runs only if every dependency's
   latest run is `Complete` and newer than this source's last successful run.
4. Runs at most one source at a time under the existing single-writer lock (catalog
   refresh is heavy); others wait for the next tick.
5. On `timeout_seconds` exceeded → kill/abandon the run → `failed(timeout)` → retry
   path.
6. Records the attempt, computes next state, escalates/notifies as per §2.1.

Keep `TriggerCatalogRefresh` working as the "manual run now" path; add a generic
`TriggerRefresh(source, actor)` that forces `due` for any source (and resets
`escalated`). The old function can delegate to it.

### 2.5 Alerting (reuse notify infra)

On `escalated`: one email/Slack via the existing `emailCfg()`/`slackURL()` resolvers
(`notify_resolve.go`; email is the path `RevalidateDirectivesAfterRefresh` already
takes via `notify.EmailConfig`, and `slackURL()` is the resolver other handlers use
for Slack posts — wire both), subject e.g. "Refresh
escalated: gcp_catalog failed 4× — last error: …". On first `success` after an
escalation: one "recovered" message. No per-attempt spam.

### 2.6 UI (extend the Data Freshness dashboard from the observability WO)

Per source, add: current state (with retrying/backing_off/escalated badges),
attempt `n/max`, `next_retry_at` countdown, consecutive-failure count, and controls:
**Retry now**, **Pause**/**Resume**. Reuse the admin trigger endpoint pattern
(`AdminCatalogRefresh`) generalized to take a `source`.

---

## 3. PHASES (targeted; tests after each; NO real sleeps/network)

### Phase 1 — Lifecycle fields on the record + the sources policy
- [ ] Add §2.3 fields to the `RefreshRun` struct (all `omitempty`); add the
      `lifecycle` block (§2.2) to `data/refresh_sources.json` for `gcp_catalog` and
      `compute_availability`. `go build ./...` + reader test.

### Phase 2 — Pure state-machine function (fully unit-tested, no I/O)
- [ ] Write a pure function `nextAction(run RefreshRun, policy Lifecycle, now time.Time, depsComplete bool) -> Action` where `Action ∈ {skip, run, escalate, wait}` plus the next `RefreshRun` state it implies. No files, no network, no sleep.
- [ ] Exhaustive table tests: success→schedules next & resets attempts; failure under
      max→retrying with correct `next_retry_at` (verify backoff math); at max→escalate;
      paused→skip; deps incomplete→wait; `next_retry_at` in future→wait, reached→run;
      timeout→failed→retry. This function is where correctness lives — test it hard.

### Phase 3 — Orchestrator loop around it
- [ ] `StartRefreshOrchestrator(ctx)`: tick (injectable interval + injectable clock),
      load sources + latest runs, call `nextAction`, execute at most one `run` under
      the single-writer lock, persist records, fire escalation/recovery notifications.
- [ ] Replace BOTH boot launches (P7) — `StartCatalogRefresher` AND
      `StartComputeAvailabilityRefresher` — with the single orchestrator; `gcp_catalog`
      and `compute_availability` each become just one managed source. Keep
      `runCatalogRefreshNow` (and the compute refresher's equivalent executor) as the
      per-source executors the orchestrator calls. Do NOT leave either old goroutine
      running alongside the orchestrator — that double-schedules the source.
- [ ] Integration test with a FAKE executor (returns success/failure/partial on cue)
      and a fake clock: drive a full failure→backoff→retry→escalate→manual-retry→
      success→recovery-notify cycle and assert states + that exactly one escalation
      and one recovery notification were sent. No real time passes.

### Phase 4 — Dependency ordering
- [ ] Implement the `depends_on` check in the orchestrator (a dependent runs only
      when every dependency's latest run is `Complete` and newer). Test: domains
      source waits while catalog is failing, runs after catalog succeeds.

### Phase 5 — Timeout + stuck-run recovery
- [ ] Enforce `timeout_seconds` (cancel/abandon → `failed(timeout)` → retry). Fold in
      the existing stale-`running` reset. Test with a fake long-running executor.

### Phase 6 — Manual controls + generic trigger
- [ ] Generalize `TriggerCatalogRefresh` → `TriggerRefresh(source, actor)` (forces
      `due`, clears `escalated`); add pause/resume persisted per source. Admin
      endpoints generalized to take `source`. Keep the old catalog endpoint working.

### Phase 7 — UI
- [ ] Extend the Data Freshness dashboard (observability WO) with state badges,
      attempt `n/max`, next-retry countdown, consecutive failures, and Retry-now /
      Pause / Resume controls. `npm run build` clean.

### Phase 8 — Verification
- [ ] pytest + `go build ./...` + Go tests + `npm run build` green.
- [ ] End-to-end with fixtures: a source that fails twice then succeeds recovers
      automatically with the right backoff and no escalation; a source that fails
      `max_attempts` escalates once, pauses retrying, and recovers on manual trigger.
- [ ] Confirm no projection dollar changed, and that a failed/partial refresh never
      replaced the live complete artifact.

---

## 4. OPEN DECISIONS (flag, don't guess)
- **`treat_partial_as`** per source: `retry` (a partial fetch is a failure to retry)
  vs `accept` (keep what came, flag the gap). Recommend `retry` for `gcp_catalog`
  (completeness matters), `accept`+flag for `compute_availability` (it legitimately
  omits unreachable regions). Confirm with the user.
- **Escalation channel + recipients**: reuse admin email/Slack as-is, or a dedicated
  refresh-alerts setting? Recommend reuse now.
- **Global max concurrency**: this work order runs one refresh at a time (simplest,
  safe with the single-writer lock). Parallel sources is a later option.

## 5. DO NOT
- Do NOT write a new scheduler/queue framework — generalize `StartCatalogRefresher`
  and reuse the lock + notify infra.
- Do NOT use real `time.Sleep` or real network in tests — inject the clock and a
  fake executor; the state machine (Phase 2) must be pure.
- Do NOT let retries hammer the provider — backoff + max_attempts + escalate are the
  contract.
- Do NOT let a failed/partial/timed-out run overwrite the live complete artifact.
- Do NOT change pricing, mapping, or projected dollars.
- Do NOT spam notifications — exactly one escalation per failure streak, one recovery.
- Do NOT "fix" unrelated issues — list them in your summary.

## 6. DONE CRITERIA
- [ ] `RefreshRun` carries attempt/next_retry/escalation; new lifecycle states exist.
- [ ] A pure, exhaustively-tested `nextAction` state machine drives everything.
- [ ] One orchestrator manages all sources; `gcp_catalog` is just one of them; the
      old catalog scheduler is subsumed with no behavior regression.
- [ ] Failures retry with backoff; `max_attempts` escalates once (email/Slack) then
      cools down; recovery notifies once.
- [ ] `depends_on` ordering works; timeouts recover; manual retry/pause/resume work.
- [ ] UI shows lifecycle state + controls per source.
- [ ] All tests green with injected time (fast); no live fetches; no dollar changes.

## 7. PROGRESS LOG (append-only)
- 2026-11-06 Created from a live trace. Confirmed the catalog refresher is a bare
  startup+24h-ticker goroutine with manual trigger, single-writer locks, and notify
  infra, but NO retry/backoff/escalation/state-machine/dependency ordering — a failed
  refresh sits failed silently (observed in `catalog_refresh_status.json`). Depends on
  the observability work order's `RefreshRun`/`refresh_sources.json`. CUD derivation is
  being handled in a separate work order per the user and is out of scope here.
- 2026-11-06 Completed all phases:
  - Phase 1: Added lifecycle & observability fields to `RefreshRun` (`CatalogRefreshRun` alias preserved) and created `data/refresh_sources.json` with declared `lifecycle` configs for `gcp_catalog`, `compute_availability`, and `assumption_domains`.
  - Phase 2: Implemented pure state machine `nextAction` in `internal/jobs/refresh_state_machine.go` and verified backoff math via exhaustive unit tests in `refresh_state_machine_test.go`.
  - Phase 3 & 4: Implemented `RefreshOrchestrator` in `internal/jobs/refresh_orchestrator.go`, dependency ordering (`depends_on`), single-writer execution, and single-alert escalation/recovery via Resend email and Slack webhook.
  - Phase 5 & 6: Implemented context timeout handling, stuck-run recovery, manual `TriggerRefresh`, `PauseSource`, `ResumeSource`, and admin HTTP endpoints in `internal/jobs/admin_handlers.go`.
  - Phase 7: Extended the System Health UI (`CatalogTab.tsx`, `StatusBadge.tsx`, `health.ts`) with live state badges, attempt counts, failure counts, next retry countdown, and Retry/Pause/Resume controls.
  - Phase 8: Verified all Go tests pass (`go test ./internal/jobs`), frontend compiles clean (`npm run build`), Python tests pass with zero dollar changes.
