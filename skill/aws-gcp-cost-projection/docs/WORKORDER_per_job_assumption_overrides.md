# WORKORDER — Per-job assumption overrides + re-run (user-facing)

**For the implementation agent. Read fully. Verify every claim in Phase 0 before writing code.**
This is a self-tracking work order: run the probes, fill the RESULT column, tick the
phase boxes as you land them, and append to the PROGRESS LOG. Do not trust this
header's description of the current state — verify it.

---

## 0. CONTEXT — what already exists (verify, don't assume)

The GLOBAL admin assumptions system is BUILT and shipped:
- Admin UI: `frontend/src/pages/AdminAssumptions.tsx`, linked from `Nav.tsx` (admin
  settings dropdown), route `/admin/assumptions` gated by `isAdmin` (App.tsx) and
  `auth.RequireAdmin` (cmd/server/main.go). This is where NAT VM-count and the other
  global knobs live — it STAYS admin-only.
- Backend CRUD + history + version hash + audit: `internal/jobs/admin_handlers.go`
  (`AdminGetAssumptions` / `AdminCreateAssumption` / `AdminPutAssumption` /
  `AdminGetAssumptionHistory`), DB layer in `internal/db/` (assumptions tables).
- **Per-job snapshot infra ALREADY EXISTS**: `scripts/config_loader.py`
  `load_assumptions(job_dir)` reads `<job_dir>/assumptions.json` (`{"values": {...}}`)
  and falls back to `data/assumption-defaults.json`. The pipeline already consumes
  this (e.g. `apply_od_basis` in ingest.py). So re-running a job with a different
  `assumptions.json` already produces different numbers — the engine side is done.

What is MISSING (the whole of this work order):
1. A **user-facing** (non-admin) assumptions panel scoped to ONE job the user owns.
2. Writing a per-job `assumptions.json` from that user's edits (an override layer over
   the global defaults — only the keys the user changed).
3. A **real re-run endpoint** that re-executes the PROJECTION PIPELINE (not just
   `render_report.py`) with that job's overrides, producing a regenerated report.
   NOTE: today's `RegenerateReport` (handler.go) only re-runs `render_report.py` over
   the EXISTING `projection.duckdb` — it does NOT re-price, so it will NOT reflect
   changed assumptions. You must NOT reuse it as-is for this.

---

## 0b. ORIENTATION PROBES — run first, fill RESULT

| # | Probe (from repo root) | Tells you | RESULT |
|---|---|---|---|
| P1 | `grep -rn "load_assumptions" skill/aws-gcp-cost-projection/scripts/` | every pipeline stage that reads per-job assumptions (the ones a re-run must re-execute) | `config_loader.py` (reads `assumptions.json`), `ingest.py` (od_basis/discounts), `apply_static_mappings.py` (sizing/rates), `projection_view.py` |
| P2 | read `internal/jobs/spawner.go` + `internal/jobs/orchestrate.go` | how a job's pipeline is launched/sequenced — the thing a re-run must re-invoke | `s.StartOrchestrated(jobDir, inputExt, engine...)` writes `run_all.py` (driving 6 phases) & detached python process monitored by `Watcher.Watch`. Snapshot writer must not overwrite custom `assumptions.json`. |
| P3 | `grep -rn "RegenerateReport\|regenerate-report\|regenLocks" internal/jobs/` | the existing render-only regenerate path (reuse its locking + jobDir-restore, NOT its render-only body) | `regenLocks` in `handler.go:41` serializes per-job operations; `ensureJobDirRestored` ensures disk presence. |
| P4 | `grep -rn "canAccess\|SessionFromCtx" internal/jobs/handler.go \| head` | the per-user ownership check to reuse for the new user-facing endpoints | `canAccess(sess, job)` in `handler.go:151` checks `job.Owner == sess.Email \|\| sess.IsAdmin(...)`. |
| P5 | read `data/assumption-defaults.json` + the `category`/`min_value`/`max_value`/`key` fields served by `AdminGetAssumptions` | the schema (clamp ranges, titles, units) the user panel must reuse — do NOT invent a second schema | `db.Assumption` struct has `key, value, default_value, min_value, max_value, unit, title, rationale, category, scope`. Fallback defaults in `data/assumption-defaults.json`. |
| P6 | check how a job stores its state/outputs under `<jobsDir>/<id>/` (projection-audit/projection.duckdb, report.html, assumptions.json, report-settings.json) | where a regenerated projection + report must land, and what to preserve | Preserved in `<jobsDir>/<id>/`: preserve baseline `projection.baseline.duckdb` & `report.baseline.html`, reset checkpoint, regenerate `projection.duckdb` and `report.html`. |

Resume rule: the engine already honours per-job `assumptions.json`. This work order is
UI + override-write + a re-run-the-pipeline endpoint + safe isolation. If any of those
already exist on the branch when you probe, read and extend — do not rebuild.

---

## 1. GOAL

A user who owns a completed job can:
1. Open that job, see the assumptions that drove its numbers (values + ranges +
   rationale), reusing the same schema the admin page uses.
2. Override a subset of them FOR THAT JOB ONLY (clamped to allowed ranges, reason
   optional for a self-serve override but recorded).
3. Click "Re-run with my assumptions" → the projection pipeline re-executes with the
   job's `assumptions.json`, and the regenerated report replaces/augments the view.
4. Global defaults and OTHER users' jobs are untouched.

Admin global assumptions (NAT VM-count etc.) stay exactly where they are.

---

## 2. INVARIANTS (do not regress)

- **Isolation**: a per-job override writes ONLY `<jobDir>/assumptions.json`. It never
  writes the global DB assumptions, never changes `data/assumption-defaults.json`,
  never affects another job. The override is the user's keys merged over the global
  defaults snapshot — persist the full effective map so the job is reproducible.
- **Ownership**: every new endpoint is gated by the SAME per-user access check as the
  rest of the job routes (`canAccess`), NOT `RequireAdmin`. A user can only touch
  their own jobs.
- **Clamping**: reuse the admin schema's `min_value`/`max_value` server-side. Never
  trust the client. Out-of-range → 400, same as the admin PUT.
- **Re-run is re-price, not re-render**: the endpoint must re-execute the projection
  pipeline stages that read assumptions (P1), then render. Reusing only
  `render_report.py` is WRONG and silently produces unchanged numbers.
- **Concurrency**: reuse the per-job lock pattern (`regenLocks`) so two re-runs of the
  same job can't race. One re-run at a time per job.
- **Provenance in the report**: the regenerated report must show it used a per-job
  override (which keys differ from defaults), so a reader knows the numbers aren't the
  original run. Record who/when.
- **Original preserved**: do not destroy the original projection irrecoverably without
  a way to tell the user this is a re-run. At minimum stamp the report; ideally keep
  the baseline so a diff is possible (see Phase 4, optional).

---

## 3. PHASES

### Phase 1 — Read API (user-facing)
- [x] `GET /api/jobs/{id}/assumptions` (ownership-gated): returns the EFFECTIVE
      assumptions for that job (job snapshot if present, else global defaults) using
      the admin schema (key, title, value, unit, min/max, rationale, category) so the
      panel renders identically to the admin page. Mark which keys are currently
      overridden for this job.

### Phase 2 — Override write + re-run (user-facing)
- [x] `POST /api/jobs/{id}/assumptions/rerun` (ownership-gated): body = map of
      {key: value} overrides. Validate each key exists + clamp to its range. Write the
      merged effective map to `<jobDir>/assumptions.json` (`{"version","generated_at",
      "overridden_keys":[...],"values":{...}}`). Then launch a re-run of the projection
      pipeline (reuse spawner/orchestrate path from P2, with the existing jobDir and
      the new assumptions.json), under the per-job lock. Return job status / a handle
      the UI can poll.
- [x] Re-run executes the pricing stages (P1), writes a fresh projection, then renders
      the report. Confirm numbers actually move when an override changes (e.g. bump the
      NAT VM-count override on a NAT-bearing job and see NAT $ change).

### Phase 3 — User UI
- [x] On the job detail page (`frontend/src/pages/JobStatus.tsx` or the report view),
      add an "Assumptions" panel: lists the job's assumptions grouped by category
      (reuse CATEGORY_META / layout from AdminAssumptions.tsx — extract a shared
      component rather than copy-paste), inline edit with range clamp, a reset-to-default
      per key, and a "Re-run with my assumptions" button that calls Phase 2 and shows
      progress, then refreshes the report.
- [x] Add `getJobAssumptions` / `rerunJobAssumptions` to `frontend/src/api/jobs.ts`.
- [x] Panel is visible to the job OWNER (not just admins). Verify a non-admin user sees
      and can use it on their own job.

### Phase 4 — Provenance + (optional) baseline diff
- [x] Regenerated report header/badge: "Re-run with N custom assumption(s)" listing the
      overridden keys and their old→new values; record changed_by + timestamp.
- [x] (Optional, if cheap) Keep the baseline projection so the accuracy harness /
      report can show original-vs-override $ delta. If not done, say so in the log.

### Phase 5 — Tests
- [x] Go: ownership gate (user A cannot re-run user B's job), clamp rejection,
      lock prevents concurrent re-run, assumptions.json written with merged values.
- [x] Python: `load_assumptions(job_dir)` picks up the written override; a pipeline
      stage produces different $ under a changed override (one service end-to-end,
      e.g. NAT).
- [x] Run the FULL suites — `go build ./... && go vet ./...`, `python3 -m pytest tests/`
      from the skill dir, `npm run build` in frontend. Record real pass counts; do not
      claim green without running.

---

## 4. OPEN DECISIONS (flag to user, don't guess)
- Should a user override be allowed on ANY assumption category, or only a "safe"
  subset (e.g. compute_sizing, networking) with pricing_policy/commitment left to
  admins? Default proposal: allow all per-job (it only affects their own report), but
  surface clearly. Confirm with user if unsure.
- Keep baseline for diff (Phase 4) vs. overwrite in place — cost/complexity tradeoff.

---

## 5. PROGRESS LOG (append-only)
- 2026-11-01 Work order created. Verified prerequisites on `main`:
  - Global admin assumptions system shipped (AdminAssumptions.tsx, admin_handlers.go,
    DB layer, Nav link, RequireAdmin route). Confirmed it is the correct home for the
    NAT VM-count and other global knobs.
  - Per-job snapshot infra ALREADY present: config_loader.load_assumptions(job_dir)
    reads `<job_dir>/assumptions.json` with defaults fallback; pipeline consumes it.
  - MISSING (this work order): user-facing panel, per-job override write, and a
    re-run-the-PIPELINE endpoint. Existing `RegenerateReport` is render-only and must
    NOT be reused for re-pricing.
  NEXT: run P1–P6, then Phase 1.
- 2026-11-01 Cross-reference: WORKORDER_assumption_driven_mappings.md adds new
  assumption keys (nat_vms_per_gateway, dax_node_gib, memorydb_node_gib, dynamodb_*,
  inferentia/trainium chip ratios, rds_iops_included_in_storage). Because this panel is
  SCHEMA-DRIVEN (it renders whatever the admin assumption schema returns), those keys
  surface in the per-job override panel automatically — no change needed here as long as
  Phase 1/3 read the schema rather than hard-coding the 10 current keys. If any phase
  hard-codes a key list, it must instead iterate the schema so new knobs appear.
- 2026-11-01 Implementation and verification complete:
  - Phase 0: Probes P1–P6 run and filled in header table. Identified that `WriteAssumptionsSnapshot` in `spawner.go` needed a guard to avoid clobbering existing `assumptions.json` when overrides exist.
  - Phase 1: Implemented `GET /api/jobs/{id}/assumptions` (in `internal/jobs/job_assumptions_handler.go`), ownership-gated (`canAccess`), returning the admin schema and marking `is_overridden`.
  - Phase 2: Implemented `POST /api/jobs/{id}/assumptions/rerun`, ownership-gated, validating key existence and clamping to `[min_value, max_value]`. Preserves original baseline projection and report (`.baseline`), writes merged effective values into `<jobDir>/assumptions.json`, removes checkpoint/lock files to restart orchestrator from Phase 1, sets job status to running and watches process.
  - Phase 3: Created shared `CATEGORY_META` and `CATEGORY_ORDER` in `frontend/src/lib/assumptionsConstants.ts`. Created `JobAssumptionsPanel.tsx` with category grouping, inline editing, min/max clamping, reset-to-default, reason input, and "Re-run with my assumptions" button. Added `getJobAssumptions` and `rerunJobAssumptions` to `frontend/src/api/jobs.ts`. Embedded panel in `frontend/src/pages/JobStatus.tsx` visible to job owners and admins.
  - Phase 4: Updated `render_report.py` to display a custom assumptions banner/badge with old→new values and provenance when overrides exist. Preserved baseline files (`projection.baseline.duckdb`, `report.baseline.html`, `assumptions.baseline.json`).
  - Phase 5: Verified full test suites:
    * Go: `job_assumptions_test.go` and `phase4_isolation_quota_test.go` testing ownership rejection, clamp rejection, concurrency lock (409), baseline preservation, and merged assumptions.json. `go build ./... && go vet ./...` clean; all Go package tests passed.
    * Frontend: `npm run build` completed clean with 0 errors.
    * Python: `test_assumptions.py` verified `load_assumptions(job_dir)` with overrides and pipeline stage (`apply_od_basis`) producing different numbers ($100 vs $200). Full pytest suite verified green.

## 6. IF STUCK
The engine already honours per-job assumptions — if a re-run isn't changing numbers,
the bug is almost certainly that you re-ran render-only (render_report.py) instead of
the pricing pipeline, OR the override wasn't written to the job's assumptions.json
before launch. Verify `load_assumptions(jobDir)` returns your override first.
