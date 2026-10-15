# Work order: Global, authoritative cancel / kill-switch

Hand this to the implementation model as-is. Goal: a **global cancel that reliably stops job runs and keeps them stopped** — not scoped to one job id, not limited to processes the server happens to be tracking, not defeated by any phase, retry, or restart path. There are two levels, built on one primitive:

- **Per-job cancel** — the authoritative primitive: stop one run and keep it stopped.
- **Global cancel (kill-switch)** — one action that stops **everything**: every non-terminal job, plus any orphaned agy/orchestrator processes the server lost track of, plus a drain flag so nothing resumes until an operator clears it.

Today's cancel is neither global nor authoritative: it kills one agent and stamps `cancelled`, but the in-process watcher sees the dead PID and *resumes* that job, flipping it back to `running` and spawning a fresh agent. Cancel must win that race, every time, and a global stop must sweep the whole machine — including strays like a hung Antigravity Electron process whose job row was already cancelled.

---

## §0 Global cancel (the headline capability)

A single operator action that **halts all orchestration on this host**:

1. **Cancel every live run.** `POST /api/admin/jobs/cancel-all` → load `ListNonTerminalJobs()` ([internal/db/jobs.go:76](../internal/db/jobs.go)) and run the per-job `cancelJob()` primitive (§1–§4 below) on each, in parallel. Return a summary: `{cancelled: [...ids], already_terminal: [...], errors: [...]}`.
2. **A persistent drain flag** — `<DataDir>/STOP_ALL` sentinel file (and/or a `system_state` DB singleton). While set:
   - the **spawner refuses to `Start`** any job (new or retry) — returns a clear "orchestration halted" error;
   - the **startup orphan reaper** ([cmd/server/main.go:90](../cmd/server/main.go)) attaches **no** watchers and resumes nothing;
   - every **watcher `finalize`** bails to the cancel path (§2);
   - the **orchestrator** exits at its next phase-boundary check (§7).
   It is cleared only by an explicit `POST /api/admin/jobs/resume-all` (or deleting the sentinel), so a global stop survives a server restart and genuinely stops the machine, not just the current second.
3. **Sweep orphan processes — "not limited to things."** The DB only knows jobs it tracks; a global stop must also reap agy/Antigravity/`run_all.py` processes that are detached, PID-reused, or belong to already-cancelled jobs (exactly the hung strays seen in practice). Implement `reapOrphanAgents()`: enumerate processes whose command line matches the agent signature (`Antigravity*.exe --dangerously-skip-permissions`, `python ... run_all.py`, and their language_server children) and `killGroup` any whose job id is terminal/unknown. Scope it to this product's processes only (match on the job-dir path and agent flags) so it never touches unrelated apps. Log every PID it reaps.
4. **UI:** a guarded **"Stop all runs"** control in the admin view (double-confirm, since it's fleet-wide), plus a banner when the drain flag is set and a **"Resume orchestration"** action to clear it.

The three pieces compose: `cancel-all` stops known jobs, the drain flag stops future/restart resumes, and the orphan sweep stops what the DB never knew about. Together that is a true global cancel.

---

Today's cancel is not authoritative: it kills the agent and stamps `cancelled`, but the in-process watcher sees the dead PID and *resumes* the job, flipping it back to `running` and spawning a fresh agent. The per-job primitive below is what `cancel-all` calls; it must win that race, every time.

## The bug, concretely (verified in this codebase)

1. `AdminCancelJob` ([internal/jobs/admin_handlers.go:80](../internal/jobs/admin_handlers.go)) does: `killGroup(job.AgentPID)` → `UpdateJobCancelled(id)`. Correct as far as it goes.
2. But a `Watcher.finalize` goroutine ([internal/jobs/watcher.go:126](../internal/jobs/watcher.go)) is blocked on `watchUntilDead(AgentPID)`. When the cancel kills that PID, `finalize` wakes up, finds no `report.html`, no `failure.txt`, `attempts < maxAttempts`, and takes the **retry path** ([watcher.go:258](../internal/jobs/watcher.go)): `IncrementJobAttempts` → `UpdateJobRunning` → `spawner.Start(...)` → `w.Watch(newPID)`. The job is running again under a new PID, and the DB `cancelled` is overwritten with `running`.
3. Net effect observed: cancel returns `200 {"status":"cancelled"}`, then ~1–10s later the job is `running` with a new `agent_pid`. The only thing that made it stick was planting a `failure.txt`, which `finalize` honors as a no-retry stop ([watcher.go:189](../internal/jobs/watcher.go)) — a workaround, not a feature.

The root cause: **there is no cancel signal that the resume paths check.** `finalize` re-reads the job (`GetJob`, [watcher.go:130](../internal/jobs/watcher.go)) but never inspects its status before retrying.

## Design: a cancel signal that every resume path is required to honor

Introduce an explicit, durable **cancel sentinel** and make every spawn/resume decision gate on it. Two layers of defense (belt and suspenders), because the watcher goroutine and the HTTP handler race:

### 1. Durable sentinel (survives process restarts)
- On cancel, write `<jobDir>/cancelled` (a sentinel file, like the existing `failure.txt` convention) **before** killing the process group. Content: a short reason + ISO timestamp + canceller identity.
- Add a helper `IsCancelled(jobDir) bool` next to `readFailureReason` ([watcher.go:283](../internal/jobs/watcher.go)).
- Also set DB status via `UpdateJobCancelled` (already exists, [internal/db/jobs.go:171](../internal/db/jobs.go)). DB is the source of truth for the UI/reaper; the file is the source of truth for the in-flight watcher goroutine (no DB round-trip race, works even if the DB write lands after the watcher wakes).

### 2. Make `finalize` cancel-aware (the critical fix)
In `Watcher.finalize`, immediately after `GetJob` ([watcher.go:130](../internal/jobs/watcher.go)), **before the success/failure/retry branches**:
```
if job.Status == "cancelled" || IsCancelled(jobDir) {
    // Authoritative stop. Do not retry, do not mark done/failed.
    killGroup(job.AgentPID)              // reap any survivors
    _ = os.Remove(filepath.Join(jobDir, "orchestrator.lock"))
    if job.Status != "cancelled" { _ = w.db.UpdateJobCancelled(jobID) }
    log.Printf("watcher %s: cancel observed — not resuming", jobID)
    return
}
```
This closes the retry race for the already-attached watcher.

### 3. Make the cancel handler authoritative and tree-killing
Rework `AdminCancelJob` (and add a non-admin owner variant — see §5) to, in order:
1. `IsValidJobID` + load job (keep current guards).
2. Write the `cancelled` sentinel file first (so any watcher that wakes mid-cancel sees it).
3. `UpdateJobCancelled(id)` in the DB.
4. `killGroup(job.AgentPID)` — must kill the **whole tree**, not just the recorded PID (see §4).
5. Remove `<jobDir>/orchestrator.lock`.
6. Idempotent: cancelling an already-terminal job returns `200` with current status, no-op on processes.

### 4. `killGroup` must kill the full process tree (Windows)
The recorded `AgentPID` is the Python `run_all.py` orchestrator; it spawns the Antigravity/agy Electron process, which spawns `language_server.exe` and other children. Verify `killGroup` ([internal/jobs/](../internal/jobs/) — find its definition) terminates the tree, not just the parent. On Windows use `taskkill /PID <pid> /T /F` (the `/T` kills descendants). On Linux, kill the process group (negative PGID) or walk children. Add a test that a child survives a bare parent-kill but not `killGroup`.

### 5. Expose it as a real feature (API + UI)
- **Endpoint:** keep `POST /api/admin/jobs/{id}/cancel`; add `POST /api/jobs/{id}/cancel` for the **job owner** (same logic, owner-scoped auth — mirror the ownership check used by `Retry`/`Refine` in [cmd/server/main.go:152](../cmd/server/main.go)). Both funnel into one `cancelJob(ctx, id)` service method so behavior can't drift.
- **UI:** a **Cancel / Stop** button on any job in a non-terminal state (`pending`/`running`), on the job detail page (`frontend/src/...` — the page polling `/api/jobs/{id}/progress`). On click: confirm dialog → POST cancel → optimistic `cancelled` state → stop the progress poller. Show `cancelled` as a distinct terminal state (not `failed`) in the status chip.

### 6. Reaper on restart (verify, likely already safe)
`ListNonTerminalJobs` returns only `pending`/`running` ([internal/db/jobs.go:79](../internal/db/jobs.go)), so the startup orphan reaper ([cmd/server/main.go:90](../cmd/server/main.go)) already skips `cancelled` jobs — a cancelled job will not be re-attached/resumed after a server restart. **Add a regression test** asserting a `cancelled` job is not returned by `ListNonTerminalJobs` and not resumed by the reaper. Also confirm the sentinel file makes it belt-and-suspenders: even if a future change reintroduces cancelled jobs into the reaper, `finalize`'s §2 guard stops the resume.

### 7. Concurrency / edge cases to handle
- **Cancel during phase transition:** the orchestrator may be between phases (no agy PID alive momentarily). The sentinel file + DB status mean the next `finalize` still bails. Also have the **orchestrator itself** check for the `cancelled` sentinel at each phase boundary (orchestrate.go run loop) and exit cleanly (`sys.exit(0)`, leave partial data) if present — so a mid-phase cancel doesn't run a whole extra phase before stopping.
- **Cancel vs. success finalize:** if a report was written in the same tick, define precedence. Recommended: **cancel wins** if the sentinel predates the report write; otherwise honor the completed report. Simplest correct rule: the §2 guard runs first, so an observed cancel always stops — acceptable, document it.
- **Double cancel / cancel after done:** idempotent no-op, `200`.
- **PID reuse:** before `killGroup`, confirm the PID is actually this job's process (the code already pairs `pidAlive` checks; keep that).

### 8. Status semantics
`cancelled` is its own terminal state, distinct from `failed`. Don't reuse the `failure.txt` path for cancels (that marks `failed` and muddies metrics). Keep `JobsFailed`/`JobsDone` counters clean; add a `JobsCancelled` metric.

## Acceptance tests

Per-job primitive:
1. **Race test (the headline):** start a fake job with a watcher attached and `attempts < maxAttempts`; call cancel; assert within N seconds the job is `cancelled`, stays `cancelled` for ≥30s, `agent_pid` process tree is dead, and **no new PID** was spawned (`attempts` does not increment).
2. **Tree kill:** parent spawns a child; `killGroup(parent)` leaves no child.
3. **Reaper:** a `cancelled` job survives a simulated server restart without being resumed.
4. **Mid-phase cancel:** orchestrator sees the sentinel at a phase boundary and exits 0 without starting the next phase.
5. **Idempotency:** cancel an already-`cancelled` and an already-`done` job → `200`, no process action.
6. **Owner vs admin:** owner can cancel own job; non-owner non-admin gets `403`.

Global layer:
7. **Cancel-all:** three running jobs → one `cancel-all` → all three `cancelled`, all trees dead, none resumed; summary lists all three.
8. **Drain flag blocks spawn:** with `STOP_ALL` set, `spawner.Start` (fresh and retry) is refused; after `resume-all`, Start works again.
9. **Drain survives restart:** set `STOP_ALL`, simulate restart → reaper attaches no watchers, resumes nothing; clear flag → normal startup resumes as before.
10. **Orphan sweep:** spawn an agent-signature process whose job row is `cancelled`/absent → `reapOrphanAgents()` kills it; a non-matching unrelated process is left untouched.

## API
- `POST /api/jobs/{id}/cancel` — owner-scoped per-job cancel.
- `POST /api/admin/jobs/{id}/cancel` — admin per-job cancel (existing, reworked to use `cancelJob`).
- `POST /api/admin/jobs/cancel-all` — global cancel of all non-terminal jobs + orphan sweep; sets `STOP_ALL`.
- `POST /api/admin/jobs/resume-all` — clears `STOP_ALL`, re-enables spawning.
- `GET  /api/admin/system/state` — reports whether orchestration is halted (for the UI banner).

## Files in play
- `internal/jobs/watcher.go` — §2 guard in `finalize`; `IsCancelled` helper; honor `STOP_ALL`.
- `internal/jobs/admin_handlers.go` — rework `AdminCancelJob`; add shared `cancelJob` primitive; add `CancelAll`, `ResumeAll`, `reapOrphanAgents`, `SystemState` handlers.
- `internal/jobs/handler.go` + `cmd/server/main.go` — owner `POST /api/jobs/{id}/cancel`; wire the new admin routes.
- `internal/jobs/spawner.go` — refuse `Start` while `STOP_ALL` is set.
- `internal/jobs/` (killGroup definition) — tree kill on Windows/Linux; add `reapOrphanAgents` process-signature sweep (scoped to this product only).
- `internal/config/config.go` — `StopAllPath()` helper (`DataDir + "/STOP_ALL"`), alongside `JobsDir()`/`DBPath()`.
- `cmd/server/main.go` — reaper honors `STOP_ALL` (attach nothing when set).
- `internal/db/jobs.go` — confirm `UpdateJobCancelled`; add `JobsCancelled` metric; reaper test.
- `internal/jobs/orchestrate.go` — phase-boundary check for both the per-job `cancelled` sentinel and global `STOP_ALL` → clean exit.
- `frontend/src/...` — per-job Cancel button + `cancelled` chip + stop poller; admin "Stop all runs" (double-confirm), halted banner, "Resume orchestration".

## Out of scope
- Deleting job data on cancel (keep the input + partial projection; `AdminDeleteJob` at [cmd/server/main.go:165](../cmd/server/main.go) already covers hard delete separately).
- Pause/resume semantics. Cancel is terminal only.
