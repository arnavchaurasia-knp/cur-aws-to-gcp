# WORKORDER — Remove Pricing Catalog page, make System Health real & trackable

**Audience:** implementation model (lower-tier is fine — this is mechanical + wiring, no design decisions left open).
**Status:** ready to implement. Every file path, function name, and endpoint below was verified against the repo on 2026-11-05.
**Branch/push rule:** work on a branch off `main`. Push to `origin2/AGY` AND `github-personal/AGY` only — never company `origin`.
**Commit trailer:** end commits with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## 0. Why this work exists (context — read before coding)

This app projects AWS bills onto GCP pricing. An admin panel exposes operational pages. Two of those pages overlap:

- **`/admin/catalog`** ("Pricing Catalog" / `AdminCatalog.tsx`) — a thin page showing only catalog age + a "Refresh now" button.
- **`/admin/health`** ("System Health & Telemetry" / `AdminHealth.tsx`) — a richer page that ALREADY shows catalog SKU count, service count, vintage, staleness, AND a "Trigger Catalog Refresh" button, plus a link back to `/admin/catalog`.

The catalog page is redundant. **The user wants it removed and its one useful action (refresh) folded entirely into System Health.**

Beyond dedup, the real problem: **System Health currently lies.** It reports health that is partly hardcoded and partly cosmetic:

- Engine phases are rendered with a static green **"Active"** badge each — never actually checked (`AdminHealth.tsx` ~line 175).
- Catalog SKU/service counts fall back to **hardcoded constants** `totalSKUs := 90335`, `totalServices := 108`, `vintage := "2026-10-23T16:16:20Z"` (`internal/jobs/admin_handlers.go` ~line 2501) and status strings are literally set to `"healthy"` (~line 2529) regardless of real state.
- Catalog **refresh is fire-and-forget**: `TriggerCatalogRefresh()` does `go runCatalogRefreshNow()` and returns immediately (`internal/jobs/catalog_refresher.go` ~line 183). The subprocess's stdout/stderr go to the server log and **nothing** — no success flag, no exit code, no progress, no "last refresh result" — is ever surfaced back to the admin. The frontend just waits 4 seconds and re-polls freshness, which does not actually tell you whether the refresh succeeded, is still running, or failed.
- Nothing checks the health of the **other data stores** the pipeline depends on. There are ~45 JSON data files under `skill/aws-gcp-cost-projection/data/` (service_map.json, service_specs.json, pricing_matrix.json, the raw catalog source `data/skus/*.json.gz`, the compiled `data/catalog.duckdb`, fx_rates.json, etc.). If one is missing, corrupt, empty, or stale, projections silently degrade and no one sees it.

**Goal of this work order, in the user's words:** "remove the pricing catalog page and add the refresh catalog thing in system health only. Also I should be able to fully track and see my catalog refresh going on successfully, and system health should also properly track and check the health of data we're storing for different things. Other issues should be fully trackable with the correct logic that are dependencies of our project."

So the deliverable is three things:
1. **Delete** the Pricing Catalog page and all references to it.
2. **Make catalog refresh fully trackable** — real run state (idle/running/success/failed), start/finish time, exit code, a tail of the output, surfaced live in System Health with polling.
3. **Make System Health check real data health** — per-store integrity (exists / parses / non-empty / age / row counts) for the catalog AND the other critical data dependencies, with honest pass/warn/fail status instead of hardcoded "healthy".

---

## 1. Repo orientation (where everything lives)

### Frontend (React + TypeScript + Vite + Tailwind, dark/light themed via CSS vars)
- `frontend/src/App.tsx` — routes. Catalog route at line ~117, health route at ~115.
- `frontend/src/pages/AdminCatalog.tsx` — **the page to delete.**
- `frontend/src/pages/AdminHealth.tsx` — **the page to expand.**
- `frontend/src/components/Nav.tsx` — top nav has a "Catalog" link (lines ~87 and ~204–205, desktop + mobile).
- `frontend/src/components/Sidebar.tsx` — sidebar has `{ to: '/admin/catalog', label: 'Pricing Catalog', ... }` (line ~67) and `CatalogIcon` (line ~286).
- `frontend/src/api/admin.ts` — API client. `CatalogStatus` + `getCatalogStatus` + `triggerCatalogRefresh` (lines ~132–152); `HealthSummary` + `getAdminHealth` (lines ~754–788).

### Backend (Go, chi router)
- `cmd/server/main.go` — routes registered ~line 346 (`/api/admin/catalog/status`), ~347 (`/api/admin/catalog/refresh`), ~388 (`/api/admin/health`). Also `loadCatalogVintage` ~line 546.
- `internal/jobs/admin_handlers.go` — handlers: `AdminCatalogStatus` (~483), `AdminCatalogRefresh` (~488), `AdminHealthSummary` (~2466). The health struct types `HealthEngine`/`HealthCatalog`/`HealthPipeline` are in this package (search for them).
- `internal/jobs/catalog_refresher.go` — the refresh engine: `StartCatalogRefresher`, `catalogAge`, `runCatalogRefreshNow`, `GetCatalogStatus`, `TriggerCatalogRefresh`, `catalogStaleDays`, `isImageBaked`.
- `cmd/server/health_test.go`, `internal/jobs/catalog_refresher_test.go`, `internal/jobs/admin_panel_testplan_test.go` — existing tests that reference these routes/functions. **Keep them green.**

### Python pipeline / data
- `skill/aws-gcp-cost-projection/scripts/catalog_manager.py` — the catalog tool. Modes: `check` (read-only deep integrity — per-service source status OK/MISSING/CORRUPT/EMPTY/DEGRADED + artifact sanity on `catalog.duckdb` + age), `refresh` (fetch → integrity gate → rebuild → update `CATALOG_META.json`), `coverage`. **`check` already computes everything we need — it just prints human text instead of JSON.**
- `skill/aws-gcp-cost-projection/data/` — ~45 JSON stores + `catalog.duckdb` + `skus/*.json.gz` + `CATALOG_META.json` + `review-config.json` (holds `catalog_refresh_after_days: 7`).

The Go server resolves the skill dir via `skill.ResolveDir()`. The catalog is **image-baked** when skill dir is under `/app` (prod container) — in that case runtime refresh is a deliberate no-op; `TriggerCatalogRefresh` already returns `(false, reason)` for it. **Preserve that behavior and surface the reason.**

---

## 2. Scope boundaries (do NOT touch)

- Do not change pipeline projection logic, mappers, pricers, or any `scripts/*.py` other than adding a `--json` output path to `catalog_manager.py` (Part C) and, optionally, a tiny new read-only `datahealth` subcommand (Part C, option 2 — pick ONE).
- Do not change auth/admin-gating. Reuse the existing `sess.IsAdmin(h.adminEmails)` guard pattern already in `AdminHealthSummary`.
- Do not re-enable the gcloud live-fetch hang path (`AGY_ALLOW_GCLOUD_LIVE_FETCH` stays off).
- Keep all existing tests passing; add new ones where noted.

---

## PART A — Remove the Pricing Catalog page

1. **Delete** `frontend/src/pages/AdminCatalog.tsx`.
2. `frontend/src/App.tsx`:
   - Remove the import `import { AdminCatalog } from './pages/AdminCatalog'` (~line 9).
   - Remove the route `<Route path="/admin/catalog" ... />` (~line 117).
   - **Add a redirect** so any old bookmark/link lands on health: `<Route path="/admin/catalog" element={<Navigate to="/admin/health" replace />} />`. (Follow the existing redirect pattern at lines ~126–127.)
3. `frontend/src/components/Nav.tsx`: remove both "Catalog" links (desktop ~line 87, mobile ~lines 204–205).
4. `frontend/src/components/Sidebar.tsx`: remove the `/admin/catalog` nav item (~line 67). Leave `CatalogIcon` only if still referenced elsewhere; if now unused, delete it (~line 286) to avoid an unused-symbol lint error.
5. `frontend/src/pages/AdminHealth.tsx`: remove the `<Link to="/admin/catalog">View detailed catalog browser →</Link>` (~lines 233–238) — the page it pointed to is gone. Everything that link offered now lives on this page (Parts B/D).
6. `frontend/src/api/admin.ts`: `getCatalogStatus` / `CatalogStatus` become unused after this. **Keep `triggerCatalogRefresh`** (health page uses it). You may keep `getCatalogStatus`/`CatalogStatus` if any test imports them; otherwise remove to avoid dead-export lint. Check with a grep before deleting.

> **Backend note:** You MAY keep the `/api/admin/catalog/status` and `/api/admin/catalog/refresh` endpoints (other tests hit them, and refresh is still used). Do not delete the Go routes/handlers. Only the *frontend page* goes away.

Verify no remaining references:
```bash
grep -rn "AdminCatalog\|/admin/catalog\|getCatalogStatus" frontend/src
```
Only the redirect route should remain.

---

## PART B — Make catalog refresh fully trackable (the core ask)

Today the refresh is a detached goroutine with no observable result. Give it a persisted status record and a polling endpoint.

### B1. Persist refresh run state (Go, `internal/jobs/catalog_refresher.go`)

Add a status file written to the skill `data/` dir: `data/catalog_refresh_status.json`. Shape:

```json
{
  "state": "idle | running | success | failed | skipped",
  "started_at": "2026-11-05T12:00:00Z",
  "finished_at": "2026-11-05T12:01:30Z",
  "exit_code": 0,
  "trigger": "manual | auto",
  "actor": "admin@email",
  "reason": "",                     // populated on skipped/failed (e.g. image-baked)
  "log_tail": ["...last ~40 lines of combined stdout/stderr..."],
  "duration_seconds": 90
}
```

Implementation:
- Add `type CatalogRefreshRun struct { ... }` with those JSON tags.
- Add `func readRefreshRun() CatalogRefreshRun` (returns `{state:"idle"}` if file absent/corrupt) and `func writeRefreshRun(run CatalogRefreshRun)` (atomic write: write temp file in same dir, `os.Rename`). Guard concurrent writes with a `sync.Mutex` package-level var.
- Refactor `runCatalogRefreshNow()`:
  - Before launching: write `{state:"running", started_at: now, trigger, actor}`.
  - Capture subprocess output into a **ring buffer / bounded slice** (last ~40 lines) in addition to the server log — do NOT just send to `os.Stdout`. Use a `bytes.Buffer` (or an `io.MultiWriter(os.Stdout, &buf)`) and split into lines at the end.
  - On completion: write `{state: success|failed, finished_at, exit_code, duration_seconds, log_tail}`. Deadline-exceeded → `failed` with reason `"timed out after 2m"`.
- `TriggerCatalogRefresh()`:
  - On image-baked refusal, ALSO write `{state:"skipped", reason:<the image-baked reason>, trigger:"manual"}` so the UI can show *why* nothing ran (today it only returns the string in the HTTP body; persisting it means the status panel is consistent after a reload).
  - Change signature to accept the actor email + trigger so it can be stamped: `TriggerCatalogRefresh(actor string) (bool, string)`. Update the one caller (`AdminCatalogRefresh` in `admin_handlers.go` ~line 490) to pass `sess.Email`. The auto-refresher (`runCatalogRefreshIfStale`) calls `runCatalogRefreshNow` directly with `trigger:"auto"`, `actor:"system"`.
- Add a guard so two refreshes can't run at once: if `readRefreshRun().state == "running"` and started < timeout ago, `TriggerCatalogRefresh` returns `(false, "a refresh is already running")`.

### B2. Status endpoint (Go)

- Add handler `func (h *Handler) AdminCatalogRefreshStatus(w http.ResponseWriter, r *http.Request)` in `admin_handlers.go` — admin-gated (copy the guard block from `AdminHealthSummary`), returns `readRefreshRun()` as JSON.
- Register in `cmd/server/main.go` next to the other catalog routes (~line 347): `r.Get("/api/admin/catalog/refresh-status", jobHandler.AdminCatalogRefreshStatus)`.

### B3. Frontend polling (`frontend/src/api/admin.ts` + `AdminHealth.tsx`)

- `admin.ts`: add
  ```ts
  export interface CatalogRefreshRun {
    state: 'idle' | 'running' | 'success' | 'failed' | 'skipped'
    started_at?: string; finished_at?: string; exit_code?: number
    trigger?: string; actor?: string; reason?: string
    log_tail?: string[]; duration_seconds?: number
  }
  export async function getCatalogRefreshStatus(): Promise<CatalogRefreshRun> {
    return getJSON<CatalogRefreshRun>('/api/admin/catalog/refresh-status')
  }
  ```
- `AdminHealth.tsx`: replace the current fire-and-wait-4s logic (`handleRefreshCatalog`, ~lines 38–53) with real tracking:
  - On "Trigger Catalog Refresh": call `triggerCatalogRefresh()`, then start polling `getCatalogRefreshStatus()` every ~2s.
  - Render a live status block: a spinner + elapsed time while `state === 'running'`; a green check + duration + "last refreshed Nd ago" on `success`; a red banner with `reason`/exit_code + the `log_tail` (collapsible `<pre>`) on `failed`; an amber "skipped" note with `reason` on `skipped` (image-baked case).
  - Stop polling when state is terminal (`success`/`failed`/`skipped`), then call `loadData()` once to refresh the catalog KPI cards.
  - On mount, call `getCatalogRefreshStatus()` once so a refresh started in another tab (or the auto-refresher) is reflected, and resume polling if it's already `running`.
  - Clear intervals on unmount.

**Acceptance:** an admin clicks Trigger, sees "running… 0:05, 0:07…", then either a green success with duration + log tail, or a red failure with the exit code and the last ~40 lines of output. Reloading the page mid-refresh still shows "running". Image-baked prod shows an honest "skipped — baked into image" instead of a fake success.

---

## PART C — Real catalog data health (replace hardcoded values)

`AdminHealthSummary` (`admin_handlers.go` ~2500) must stop using hardcoded `90335`/`108`/`"healthy"`. It already reads `CATALOG_META.json` for counts — good — but it should also reflect **source + artifact integrity** that `catalog_manager.py check` computes.

Pick **ONE** of these two wiring strategies (option 1 preferred — least new surface):

### Option 1 (preferred): add `--json` to `catalog_manager.py check`
- In `catalog_manager.py`, `cmd_check(p)` already builds `results` (per-service `{status,count,priced,issues,age_days}`), `artifact_ok`, `is_stale`, and age. Add a `--json` flag (argparse) that, instead of the text table, does `json.dump` of a summary:
  ```json
  {
    "generated_at": "...Z",
    "age_days": 13, "stale": true,
    "artifact_ok": true,
    "source": { "total": 108, "ok": 106, "problems": 2,
                "bad": [{"service":"...","status":"DEGRADED","issues":["..."]}] },
    "sku_count": 90335, "service_count": 108,
    "overall": "ok | warn | fail"
  }
  ```
  `overall`: `fail` if artifact missing/empty or any source MISSING/CORRUPT/EMPTY; `warn` if stale or any DEGRADED; else `ok`. Print ONLY the JSON on stdout in `--json` mode (no banner) so Go can parse it.
- Go: in `AdminHealthSummary`, run `python catalog_manager.py check --json --skill-dir <dir>` with a short timeout (~20s, use `exec.CommandContext` like `runCatalogRefreshNow` does), parse it, and populate `HealthCatalog` from the real values. If the subprocess fails/times out, fall back to the current `CATALOG_META.json`-only read and mark status `"unknown"` (NOT `"healthy"`). **Remove the hardcoded `90335`/`108`/`"healthy"` constants** — keep a `CATALOG_META.json` read as the only fallback, and if even that is missing, report zeros + `"unknown"`.

> Caching: `check --json` touches the duckdb + gzip files and may take a second or two. Cache the parsed result in-process for ~60s (a package var `{at time.Time; val catalogHealthJSON}` under a mutex) so hitting the health page repeatedly doesn't re-scan every time.

### Option 2 (alternative): compute source/artifact counts directly in Go
Only if you'd rather not shell out: open `catalog.duckdb` read-only (the repo already depends on a duckdb driver — check `go.mod`) and count `skus`/`tiered_rates`; stat `data/skus/*.json.gz` for presence/size. More code, no Python dependency at request time. **Do not do both.**

Either way, `HealthCatalog.Status` must be derived, never literal `"healthy"`.

---

## PART D — "Data dependencies" health panel (the broader ask)

The user wants *all* critical data dependencies trackable, "with the correct logic," not just the catalog. Add a **Data Stores Health** section to `AdminHealthSummary` + the health page.

### D1. Define the dependency manifest (Go)
In `admin_handlers.go` (or a small new file `internal/jobs/data_health.go`), declare the critical stores with the check each needs. Resolve paths under `skill.ResolveDir()/data/`:

| key | file | check type | warn/fail logic |
|---|---|---|---|
| catalog_artifact | `catalog.duckdb` | duckdb tables non-empty | fail if missing/empty (reuse Part C) |
| catalog_source | `skus/*.json.gz` | glob present + each >0 bytes | fail if dir empty; warn if any 0-byte |
| service_map | `service_map.json` | exists + valid JSON + non-empty object | fail if missing/corrupt/empty |
| service_specs | `service_specs.json` | exists + valid JSON + N specs | fail if missing/corrupt; warn if specs < expected floor |
| pricing_matrix | `pricing_matrix.json` | exists + valid JSON | fail if missing/corrupt |
| fx_rates | `fx_rates.json` | exists + valid JSON + age | warn if stale (> N days — fx moves) |
| review_config | `review-config.json` | exists + valid JSON | fail if missing/corrupt |
| refresh_store | `refresh_store.json` | exists + valid JSON | warn if missing |
| assumption_defaults | `assumption-defaults.json` | exists + valid JSON | fail if missing/corrupt |

(Use this as the seed set; it's fine to add a few more of the ~45 files if clearly pipeline-critical, but keep the list intentional — not every file.)

Implement one generic checker:
```go
type StoreHealth struct {
    Key string `json:"key"`; File string `json:"file"`
    Status string `json:"status"`      // ok | warn | fail | unknown
    Detail string `json:"detail"`      // human reason
    SizeBytes int64 `json:"size_bytes"`
    AgeDays int `json:"age_days"`       // -1 if n/a
    Count int `json:"count"`            // rows/keys where meaningful, else 0
}
```
A helper `checkJSONStore(path string, mode ...)` that: stats the file (missing → `fail`), reads+`json.Unmarshal` into `interface{}` (parse error → `fail` "corrupt JSON"), checks emptiness (empty object/array → `fail` or `warn` per the table), computes age from mtime, and counts top-level keys/array length for `Count`. Special-case `catalog_source` (glob) and `catalog_artifact` (duckdb/Part C).

### D2. Extend the health response (Go)
- Add `Stores []StoreHealth` to the health summary struct and a derived top-level `DataStatus string` (`fail` if any store fails, else `warn` if any warns, else `ok`).
- Also set the overall `status` field (currently just a string) honestly: worst of {catalog, pipeline failure rate high, any data store fail}.
- **Fix the cosmetic engine phases:** the per-phase green "Active" badge is fake. Either (a) drop the per-phase badge entirely and show phases as a plain list, or (b) keep a badge but base it on something real (e.g. engine `status`). Do NOT keep a hardcoded "Active" on every phase.

### D3. Frontend panel (`AdminHealth.tsx` + `admin.ts`)
- `admin.ts`: extend `HealthSummary` with `stores: StoreHealth[]` and `data_status: string` (+ the `StoreHealth` interface).
- `AdminHealth.tsx`: add a **"Data Dependencies"** card: a compact table/grid, one row per store, with a colored status pill (green ok / amber warn / red fail — reuse `catalogStalenessClass`-style helpers and the existing CSS vars), the file name, count, age, and the `detail` text on warn/fail. Put a roll-up pill (`data_status`) in the top KPI row (replace or sit beside one of the four existing KPI cards).
- Use the existing theme tokens (`--fcp-*`) and card styling already in the file — match it, don't invent new colors.

---

## PART E — Tests & verification

### Go
- `internal/jobs/catalog_refresher_test.go`: add tests for `readRefreshRun`/`writeRefreshRun` round-trip (temp dir), and that `TriggerCatalogRefresh` on an image-baked dir writes a `skipped` status with reason and returns `(false, …)`.
- Add a test for `checkJSONStore`: a valid file → `ok`; a corrupt file → `fail`; a missing file → `fail`; an empty `{}` → `warn`/`fail` per table.
- `cmd/server/health_test.go` + `admin_panel_testplan_test.go`: register the new `/api/admin/catalog/refresh-status` route in the test router and assert 200-for-admin / 403-for-non-admin, matching the existing table-driven style (~lines 144–145). Keep existing assertions green; if you removed hardcoded catalog counts, update any test asserting `90335`/`108` to assert "> 0 and matches META" instead.
- `go build ./... && go test ./...` must pass.

### Python
- If Option 1: `python skill/aws-gcp-cost-projection/scripts/catalog_manager.py check --json --skill-dir skill/aws-gcp-cost-projection` prints parseable JSON and nothing else; exit code unchanged (0 healthy / 1 problems / 2 config). Run the existing suite: `cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q` — must stay green.

### Frontend
- `cd frontend && npm run build` (or the repo's typecheck/lint script) — no TS errors, no unused-import lint from the deletions.
- Manual (dev server via the project's run skill): visit `/admin/catalog` → redirects to `/admin/health`. On `/admin/health`: Data Dependencies panel renders with real pills; Trigger Catalog Refresh shows live running→success/failed with a log tail; nav/sidebar no longer list Catalog.

---

## 6. Definition of done (checklist)

- [ ] `AdminCatalog.tsx` deleted; `/admin/catalog` redirects to `/admin/health`; no dangling imports/links/icons; `grep -rn "/admin/catalog\|AdminCatalog" frontend/src` shows only the redirect.
- [ ] Catalog refresh writes `data/catalog_refresh_status.json` with state/exit/duration/log_tail; `/api/admin/catalog/refresh-status` serves it; image-baked prod records an honest `skipped` with reason.
- [ ] System Health polls refresh live (running → success/failed) with elapsed time and a log tail, and reflects an in-progress refresh after a page reload.
- [ ] No hardcoded `90335`/`108`/`"healthy"` left in `AdminHealthSummary`; catalog health derives from `catalog_manager.py check --json` (or Option 2) with a safe fallback → `"unknown"`, cached ~60s.
- [ ] Data Dependencies panel shows per-store ok/warn/fail for the manifest in §D1 with correct logic (missing/corrupt/empty/stale), plus a roll-up `data_status`.
- [ ] Engine phases no longer show a fake "Active" badge.
- [ ] `go build ./... && go test ./...` green; `pytest tests/ -q` green; frontend build clean.
- [ ] Branch pushed to `origin2/AGY` and `github-personal/AGY`; commit trailer present.

---

## 7. Gotchas (confirmed from the code)

- **Windows:** the Go server + Python run on Windows here. `catalog_manager.py` already reconfigures stdout/stderr to UTF-8 on win32. When shelling out from Go, prefer `python3` then fall back to `python` (the existing `runCatalogRefreshNow` already does this LookPath dance — copy it).
- **Image-baked:** never claim a refresh ran when `isImageBaked(skillDir)` is true. The existing code handles the refusal; your job is to make the *status record* reflect it so the UI is consistent after reload.
- **Don't block the request thread:** the health endpoint shelling out to `check --json` must have a timeout and a cache, or the admin page will hang when the duckdb is large (90k SKUs). 20s timeout + 60s cache.
- **Atomic writes:** write the status JSON to a temp file in the same directory then `os.Rename` — a half-written status file read by a concurrent poll would break the UI.
- **Keep the backend catalog endpoints.** Only the frontend page is removed; `admin_panel_testplan_test.go` still exercises `/api/admin/catalog/status` and `/refresh`.
- **`catalog_manager.py check` is already the source of truth** for per-service integrity — reuse it, do not re-implement its OK/MISSING/CORRUPT/EMPTY/DEGRADED logic in Go.
