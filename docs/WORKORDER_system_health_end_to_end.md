# WORKORDER — System Health as a real end-to-end health + data management console

**Audience:** implementation model (mid-tier OK; several decisions pre-made below — follow them, don't re-open).
**Status:** ready to implement. Every path/function/env var below was verified against the repo on 2026-11-05.
**Branch/push:** branch off `main`; push to `origin2/AGY` AND `github-personal/AGY` only — never company `origin`.
**Commit trailer:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
**Builds on:** `docs/WORKORDER_system_health_and_catalog_refresh.md` (already implemented in commit `f7df40c`). This is the follow-up that makes the page *honest and operational* end to end.

---

## 0. What the user asked for (read first)

> "Everything and every data and other things needed for the full pipeline and product should be fully seeable and manageable from the System Health route for my Facets admins. If something isn't there, or some tracking is just a UI gimmick (pseudo), fix it — we need proper industry-level tracking. I should be fully able to manage the data stored: see data and manage data from here. Think end to end — full health management of the tool from this route, working end to end. Also for Gemini we need to make a call to check if it's working or not."

Three hard requirements:
1. **No fake metrics.** Every number/status on `/admin/health` must come from a real source or be removed. Today several are hardcoded or invented (inventory in §1).
2. **Live engine (Gemini/agy) health check.** An actual round-trip to the model, reported with latency + pass/fail, plus a manual "Test engine" button — not a hardcoded "healthy".
3. **See AND manage the data from this route.** Admins must be able to view every critical data store and edit the editable ones (with validation, backup, atomic write, and audit) — without SSHing into the box.

"Industry-level" here means: real probes, honest status (ok/warn/fail/unknown — never a cosmetic green), timestamps on every check, actionable detail text, audit trail on every mutation, and safe writes (validate → backup → atomic swap → audit).

---

## 1. Current state — what is REAL vs PSEUDO (verified)

`internal/jobs/admin_handlers.go` → `AdminHealthSummary` (~line 2551) and `HealthResponse`. After commit `f7df40c`:

| Field | Source today | Verdict |
|---|---|---|
| `catalog.*` (sku_count, service_count, age, stale, status) | `catalog_manager.py check --json`, cached 60s | ✅ REAL (done last work order) |
| `stores[]` + `data_status` | `data_health.go` `CheckDataStores` | ✅ REAL |
| `pipeline.completed_jobs / failed_jobs / failure_rate` | `h.db.GetUsageStats()` | ✅ REAL |
| `pipeline.queue_depth` | `totalJobs - done - failed` (clamped ≥0) | ⚠️ DERIVED — ok but verify it isn't counting ancient stuck jobs |
| `pipeline.running_jobs` | **hardcoded `0`** (`admin_handlers.go` ~2658) | ❌ PSEUDO |
| `engine.concurrency_limit` | **hardcoded `5`** (~2571) | ❌ PSEUDO |
| `engine.status` | **hardcoded `"healthy"`** (~2580) | ❌ PSEUDO |
| `engine.active_engine` / `version` | `GetEngineSettings()` + `spawner.EngineVersion()` (`agy --version`) | ✅ REAL-ish (version can be empty → show "unknown") |
| `tokens.total_tokens_est` | **`doneJobs * 35000`** (~2670) | ❌ PSEUDO (invented constant) |
| `tokens.estimated_cost_usd` | **`doneJobs * 0.05`** (~2671) | ❌ PSEUDO (invented constant) |
| engine **phases** list | static 6-string list; badge now neutral "Phase N" | ⚠️ COSMETIC (acceptable as a static pipeline legend, but must not imply live per-phase health) |

**Nothing today makes a live call to Gemini.** The engine is the `agy` CLI (Antigravity) wrapping Gemini models; auth is `ANTIGRAVITY_API_KEY` (env; `GEMINI_API_KEY` is **ignored** by agy — see `internal/config/config.go` ~line 114). `EngineVersion()` only runs `agy --version` (`internal/jobs/spawner.go` ~249).

**Data stores are read-only today.** `data_health.go` reports status but there is no way to view a store's contents or edit it from the UI. DB-backed config (assumptions, report-settings, integrations) IS editable via existing pages, but file-backed JSON stores under `skill/aws-gcp-cost-projection/data/` are not.

---

## 2. Scope & decisions (pre-made — do not re-open)

- **Engine probe = a real agy round-trip**, bounded and cached. Decision: run `agy --model <active> --print "<tiny prompt>"` with a short timeout (default 30s) in a temp/throwaway dir, measure latency, classify result. Do NOT invent a separate Gemini HTTP client — the product runs everything through agy, so agy is what must be healthy. (If `AGY_PATH`/`agy` is absent, that itself is the finding.)
- **Token/cost KPI**: there is **no real token tracking** in the DB (verified — no token columns, agy writes per-job `agy-internal.log` but nothing aggregates it). Decision: **remove the fake `tokens.total_tokens_est`/`estimated_cost_usd` math.** Replace the KPI card with real job throughput (total jobs analyzed, avg duration if available) OR, if you add real token capture (§A4, optional), show that. Never multiply job count by a made-up constant again.
- **Data management**: file-backed JSON stores get **view + edit** with guardrails (§D). DB-backed config stays where it is but gets **surfaced/linked** from the health route so "manage everything from here" holds. Destructive edits require validation + backup + audit.
- **Permissions**: reuse the existing `sess.IsAdmin(h.adminEmails)` guard on every new endpoint. Mutations also call `h.db.LogAdminAction(...)` (already used by catalog refresh).
- Keep the catalog refresh tracking from `f7df40c` intact.
- Do not touch projection/pricing pipeline logic. Python changes limited to adding read-only probes where noted.

---

## PART A — Kill every pseudo metric

### A1. Engine concurrency (real)
`concurrency_limit` is hardcoded `5`. The real limit is whatever bounds concurrent spawns. Find it: grep for the worker/semaphore that gates `spawner.Start` (search `internal/jobs/` for `semaphore`, `make(chan`, `MaxConcurrent`, `limit`, or the watcher's worker pool in `internal/jobs/watcher.go`). Surface the **actual** configured value. If it's genuinely a constant in code, read it from that constant (one source of truth), not a second literal in the handler. If there is no real limiter, say `concurrency_limit: 0` with a note "unbounded" rather than a fake 5.

### A2. Running jobs (real)
`running_jobs: 0` is hardcoded. Count jobs currently in a running/in-progress state. `GetUsageStats()` already yields totals; either extend it with a `RunningJobs` count (query jobs whose status is running/active — check the job status enum in `internal/db/jobs.go`) or add a dedicated `h.db.CountRunningJobs()`. Wire it into `pipeline.running_jobs`. Recompute `queue_depth` honestly as queued-but-not-started (not `total - done - failed`, which lumps running + queued together) — expose both `running_jobs` and `queue_depth` distinctly.

### A3. Engine status (real)
`engine.status: "healthy"` must be derived from the live probe (§B). Values: `ok` (probe round-tripped), `warn` (binary present but slow / degraded / version unknown), `fail` (binary missing or auth missing or probe failed), `unknown` (probe disabled/not yet run). Never literal "healthy".

### A4. Token/cost KPI (remove the fake)
Delete the `doneJobs * 35000` and `doneJobs * 0.05` lines. Replace `HealthTokens` content with real throughput:
- `total_jobs_analyzed` (real, = done jobs) — keep.
- `avg_job_duration_seconds` if job rows carry start/finish (check `internal/db/jobs.go`); else omit.
- **Optional, only if cheap:** parse the last N jobs' `agy-internal.log` / `agy.log` for a token/usage line and sum. If the format isn't reliably present, do NOT fake it — leave token fields out and relabel the KPI card "Pipeline Throughput". The frontend KPI must not show a dollar figure derived from a constant.

---

## PART B — Live Gemini / agy engine health probe (the explicit ask)

### B1. Probe function (Go, `internal/jobs/spawner.go` or a new `engine_health.go`)
Add `func (s *Spawner) ProbeEngine(ctx context.Context) EngineProbe` returning:
```go
type EngineProbe struct {
    State       string `json:"state"`        // ok | warn | fail | unknown
    Model       string `json:"model"`
    BinaryFound bool   `json:"binary_found"`
    BinaryPath  string `json:"binary_path"`
    Version     string `json:"version"`
    AuthPresent bool   `json:"auth_present"`  // ANTIGRAVITY_API_KEY set (non-empty) — DO NOT return the key
    RoundTrip   bool   `json:"round_trip"`    // a --print call returned output with no error
    LatencyMS   int64  `json:"latency_ms"`
    CheckedAt   string `json:"checked_at"`
    Detail      string `json:"detail"`        // human reason, esp. on warn/fail
}
```
Logic:
1. `BinaryFound` / `BinaryPath`: `exec.LookPath(s.agyBin())`. If absent → `fail`, detail "agy binary not found on PATH (set AGY_PATH)".
2. `Version`: `agy --version` (reuse `EngineVersion()`), short timeout.
3. `AuthPresent`: `os.Getenv("ANTIGRAVITY_API_KEY") != ""`. If empty → `fail`/`warn`, detail "ANTIGRAVITY_API_KEY not set; spawns will fail auth". **Never put the key value in the response or logs.**
4. `RoundTrip`: run `agy --model <active> --print "Reply with the single word: OK"` (use `--dangerously-skip-permissions` like `baseFlags`, and a throwaway temp dir as CWD so it doesn't pollute anything) under `ctx` with a 30s timeout. Success = exit 0 and non-empty stdout. Measure `LatencyMS`. Classify: round-trip ok → `ok`; binary+version ok but round-trip failed/timed out → `fail` with the error tail; round-trip ok but latency > a threshold (e.g. 15s) → `warn` "engine slow".
5. Always set `CheckedAt`.

Cache the probe result ~60s (same pattern as `getCatalogHealthCheck`) so the health page doesn't spawn agy on every load. The manual "Test engine" endpoint (§B2) forces a fresh probe (bypass cache).

> **Cost/safety note:** a `--print "OK"` round-trip is one tiny model call. Acceptable on manual trigger and on the ~60s-cached auto path. Do NOT probe on a tight timer. Honor a kill switch: if env `AGY_HEALTH_PROBE_DISABLED=1`, skip the round-trip and return `state:"unknown", detail:"live probe disabled"` (still report binary/version/auth). Also skip the round-trip automatically when `AuthPresent` is false (no point, and avoids noise).

### B2. Endpoints (Go)
- Fold the cached probe into `AdminHealthSummary` so `engine` carries the real probe fields (merge `EngineProbe` into `HealthEngine`, or add an `engine_probe` block).
- Add `POST /api/admin/engine/test` → `AdminTestEngine` handler (admin-gated): forces a fresh `ProbeEngine`, logs an admin action (`engine_test`), returns the `EngineProbe` JSON. Register in `cmd/server/main.go` near the engine/health routes.

### B3. Frontend (`AdminHealth.tsx` + `admin.ts`)
- `admin.ts`: add `EngineProbe` type + `testEngine(): Promise<EngineProbe>` (POST).
- On the Engine card: show binary found/path, version, auth present (✓/✗ — never the key), last probe state pill, round-trip latency, checked-at. Add a **"Test Engine"** button that calls `testEngine()`, shows a spinner, then the fresh result (green ok + latency / red fail + detail). This is the "make a call to check Gemini is working" the user asked for.

---

## PART C — Credentials & environment dependencies panel (new, real)

Admins need to see at a glance whether the box has what the pipeline needs. Add a **"Environment & Credentials"** section to the health summary. For each, report present/absent/reachable — **never the secret value**:

| Dependency | Check | fail/warn logic |
|---|---|---|
| `ANTIGRAVITY_API_KEY` | env non-empty | fail if missing (engine can't auth) |
| `RESEND_API_KEY` | env non-empty | warn if missing (email notifications off) |
| `agy` binary | `exec.LookPath` | fail if missing |
| Python 3 | `exec.LookPath(python3/python)` | fail if missing (catalog + checks break) |
| duckdb (python module) | `python -c "import duckdb"` quick probe (cached) | fail if import errors (pricer + health checks break) |
| GCP creds for catalog refresh | `GOOGLE_CLOUD_API_KEY`/`GCP_API_KEY` env non-empty | warn if missing (auto-refresh is a no-op) |
| Skill dir writable | stat + is-image-baked (`isImageBaked`) | warn "image-baked (read-only)" or fail if unwritable + not baked |
| Data disk free space | stat the data dir / `h.jobsDir` volume | warn if low (<~1GB), fail if ~0 |

Model it like `data_health.go`: a `type DepHealth struct { Key, Status, Detail string; Present bool }` and a `CheckEnvDependencies()` returning `[]DepHealth` + roll-up. Add to `HealthResponse` as `env []DepHealth` + `env_status`. Fold `env_status` into the top-level `overall` status. Frontend: a compact table with ✓/✗/⚠ pills and detail text.

> Redaction rule (enforce everywhere in this work order): endpoints may report a secret's **presence/length/last-4 at most**, never the value. Add a tiny helper `redactPresence(v string) string` and use it; grep the final diff to be sure no `os.Getenv("*_KEY")` value is ever marshaled into a response or `slog` line.

---

## PART D — See AND manage the data stores from this route

This is the "manage data from here" requirement. Two classes of stores:

### D1. DB-backed config (already editable elsewhere) — surface + link
`assumptions`, `report-settings`, `integrations` already have GET/PUT/history endpoints and pages. On the System Health data panel, for these show a row with current item count + last-modified + a **"Manage →"** link to their existing page (`/admin/assumptions`, `/admin/report-settings`, `/admin/integrations`). No new write path needed — just make them reachable from this console so the admin truly manages "everything from here".

### D2. File-backed JSON stores — new view + edit with guardrails
For the critical JSON files in `skill/aws-gcp-cost-projection/data/` (the manifest already in `data_health.go` §D1 of the prior work order, plus any you add), build a safe editor:

**Read endpoint** — `GET /api/admin/datastores` (list: key, file, status, size, age, count, **editable** bool) and `GET /api/admin/datastores/{key}` → returns the raw file content (pretty-printed JSON) + metadata + its health. Admin-gated. Cap response size (e.g. refuse to inline a file > ~2MB; `catalog.duckdb` and `skus/*.json.gz` are **not** editable/inlinable — mark `editable:false`, view = metadata only).

**Write endpoint** — `PUT /api/admin/datastores/{key}` with the new JSON body. Guardrails, in order:
1. Reject if `{key}` not in the allow-list of editable stores (closed set — never arbitrary paths; prevent path traversal: resolve against the data dir and verify the cleaned path stays inside it).
2. Validate: body must be valid JSON; if a store has a known shape (e.g. `review-config.json` numeric thresholds, `service_map.json` object), run a light schema/sanity check. Reject with a clear message on fail.
3. **Backup**: copy current file to `data/_backups/{key}.{RFC3339}.json` (create dir; keep last ~10 per key, prune older).
4. **Atomic write**: temp file in same dir → `os.Rename` (Windows: remove target first, like `writeRefreshRunLocked`).
5. **Audit**: `h.db.LogAdminAction(sess.Email, "datastore_edit", key, "")` + log size delta.
6. Invalidate any in-process cache that reads this store (e.g. if you cache data health, bust it).
7. Return the new health status for that store so the UI updates immediately.

**Revert endpoint (optional but recommended)** — `POST /api/admin/datastores/{key}/revert` restoring the most recent backup. Same audit + atomic write.

**Frontend** — a "Data Stores" manager on the health route: the existing Data Dependencies table gains a **"View"** action per store opening a panel with a JSON editor (a `<textarea>`/code editor with monospace + validate-on-save). Editable stores show Save (with a confirm dialog — reuse `ConfirmDialog`) + Revert; non-editable (duckdb, gz, DB-backed) show read-only metadata or the "Manage →" link. Show validation errors inline; show a success toast + refreshed status on save.

> **Danger rails:** editing `service_map.json`/`service_specs.json`/`review-config.json` changes live pipeline behavior. The Save confirm dialog must spell that out ("This file drives projections for all future jobs"). Keep the backup/revert so a bad edit is recoverable. Do NOT allow editing `catalog.duckdb`, `CATALOG_META.json` (managed by refresh), or any `*.json.gz`.

---

## PART E — Pipeline & job health, honest

- Replace `running_jobs`/`queue_depth` per §A1–A2.
- Add `last_job_at` (timestamp of most recent job) and `last_failure_at` if cheaply available from `internal/db/jobs.go` — real recency beats a bare rate.
- `halted` already comes from `IsStopAllActive` — keep, and surface it prominently (a red "PIPELINE HALTED" banner when true) since an admin managing the tool must see a global stop immediately.
- The 6-phase list stays as a **static legend** only; relabel the card so it doesn't imply live per-phase health (it's a description of the pipeline, not a live check). If you want per-phase liveness later, that's a separate effort — don't fake it now.

---

## PART F — Overall status, caching, layout

- `overall` status = worst of {engine probe, catalog, data_status, env_status, pipeline (failure_rate high or halted)}. One honest roll-up at the top of the page: green **Operational** / amber **Degraded** / red **Down** / grey **Unknown**, with a one-line reason naming the worst contributor.
- Cache the expensive probes independently (catalog check 60s already; engine probe 60s; duckdb-import probe 5min; env checks are cheap, no cache). The health endpoint must stay responsive (<~1s on a warm cache) — never block on a cold agy round-trip inline; if the engine probe cache is cold, return `state:"unknown"` and let the frontend's "Test Engine" / a background refresh fill it, OR run the cold probe with a hard 30s ctx and a fallback. Prefer: health summary uses last cached probe (or `unknown`); the explicit Test Engine button is the only path that waits for a live round-trip.
- Keep the existing dark/light theming (`--fcp-*` tokens) and card styling. The page may get long — group into sections: **Overall** · **Engine** · **Catalog & Refresh** · **Pipeline & Jobs** · **Data Stores (view/manage)** · **Environment & Credentials**.

---

## PART G — Tests & verification

### Go
- `ProbeEngine`: test with a fake/stub agy (a shell script on PATH via `AGY_PATH` that echoes a version and "OK") → `ok`; missing binary → `fail`; `AGY_HEALTH_PROBE_DISABLED=1` → `unknown` (no round-trip). Do NOT hit real Gemini in tests.
- `CheckEnvDependencies`: set/unset env vars in the test and assert present/absent + redaction (assert the key value never appears in the marshaled JSON).
- Datastore read/write: temp data dir; write valid JSON → backup created + atomic swap + audit called; invalid JSON → rejected, original untouched; path-traversal key (`../foo`) → rejected; non-editable key (`catalog_artifact`) → rejected.
- Register new routes in `admin_panel_testplan_test.go` (table of `{method, path}`) and assert 403 for non-admin / 200 for admin.
- `go build ./... && go test ./...` green.

### Frontend
- `cd frontend && npm run build` clean (tsc + vite). Manual: Test Engine button shows live pass/fail + latency; data store View opens, edit+Save writes and reflects new status, Revert restores; Environment panel shows ✓/✗ with no secret values; Overall banner reflects worst component.

### Python
- Any new read-only probe (e.g. duckdb-import check if you shell out) must not disturb `pytest tests/ -q` — run it, stays green.

---

## Definition of done (checklist)

- [ ] No hardcoded `5` concurrency, `0` running jobs, `"healthy"` engine status, or `*35000`/`*0.05` token math left in `AdminHealthSummary` — grep proves it.
- [ ] `engine.status` derives from a real `ProbeEngine`; **"Test Engine" button makes a live agy→Gemini round-trip** and shows pass/fail + latency; secrets never exposed.
- [ ] `running_jobs` and `queue_depth` are real and distinct; `halted` surfaced as a banner.
- [ ] Environment & Credentials panel shows real present/absent/reachable for agy, ANTIGRAVITY_API_KEY, RESEND_API_KEY, Python, duckdb, GCP creds, disk — values redacted.
- [ ] Every critical data store is **viewable** from the route; editable JSON stores are **editable** with validate → backup → atomic write → audit → revert; duckdb/gz/DB-backed are read-only/linked.
- [ ] DB-backed config (assumptions/report-settings/integrations) reachable via "Manage →" from this console.
- [ ] One honest `overall` roll-up with a named worst contributor; page responsive on warm cache.
- [ ] `go build ./...` + Go tests green; frontend build clean; `pytest tests/ -q` green; new routes in the admin test plan.
- [ ] Branch pushed to `origin2/AGY` and `github-personal/AGY`; commit trailer present.

---

## Gotchas (confirmed from the code)

- **agy, not raw Gemini.** Auth is `ANTIGRAVITY_API_KEY` (env); `GEMINI_API_KEY` is ignored (`config.go` ~114). Probe via the `agy` CLI; respect `AGY_PATH`.
- **Never leak secrets.** Report presence only. Grep the final diff for `Getenv(".*KEY")` reaching a response or log.
- **Don't block the health endpoint on a cold engine probe.** Use cache + `unknown` fallback; only the explicit Test Engine button waits for a live round-trip (30s ctx).
- **Atomic + Windows.** Reuse the temp→remove-target→rename pattern from `writeRefreshRunLocked` for every file write (datastore saves, backups).
- **Path traversal.** Datastore keys are a closed allow-list mapped to fixed filenames; resolve + verify the path stays under the data dir; reject anything else.
- **Image-baked prod is read-only.** `isImageBaked` true → datastore edits can't persist (discarded next deploy). Surface that like catalog refresh does: refuse the write with a clear reason rather than pretending it saved.
- **Keep `f7df40c` intact.** Catalog refresh tracking, data_health manifest, and the redirect all stay; this work order extends them, doesn't replace them.
