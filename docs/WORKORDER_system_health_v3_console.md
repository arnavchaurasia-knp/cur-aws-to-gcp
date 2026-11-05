# WORKORDER — System Health v3: a console a colleague can understand and operate

**For:** a lower-tier implementation model, in a fresh conversation.
**Written:** 2026-11-05 by the orchestrator, after a design pass over the whole codebase.
**Status:** foundation DONE and tested (Part 1). Everything in Parts 2–6 is TO DO, in order.
**Branch / push:** commit on `main`; push with `git push origin2 main` AND `git push github-personal main`. Never push `origin` (company repo). Never force-push.
**Commit trailer:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

Read Part 0 fully before touching code. Do the parts in order. After each part, run its "Check" commands and do not continue while anything is red.

---

## Part 0 — Why this exists and what "done" looks like

### What the user said
> "I don't know how you are writing things… give an option to add environment variables… Our goal is to deliver a world-class product where my colleagues can understand and make changes fully in the app."

### What is wrong with today's `/admin/health` page (observed in screenshots + code)
1. **It contradicts itself.** The banner said "DEGRADED" while its subtitle said "everything operating within target parameters"; the engine card said "Healthy" while the panel next to it said "Key Missing". Root cause: status is computed in several places (banner ladder, card, panel) that drift apart.
2. **Jargon.** "Reasoning Engine Liveness", "Catalog Cardinality", "Binary Path: Resolved at runtime". A colleague cannot tell what to do.
3. **Problems are not actionable.** A red pill with no "why it matters" and no "how to fix" button.
4. **Env vars can't be managed.** No way to see the full configuration or set an API key from the app. Separately, `SETTINGS_ENCRYPTION_KEY` is not set on this machine, so even the Integrations page cannot save secrets.
5. **Data-file editor corrupts files.** `AdminGetDataStore`/`AdminPutDataStore` (`internal/jobs/datastore_handlers.go`) round-trip JSON through a Go `map`, which **re-sorts every key alphabetically** on view and on save. `service_map.json` says *"First match wins, so order specific → general"*; `service_specs.json` says charge types are tested *"in order, FIRST MATCH WINS"*; `review-config.json` keeps `_comment_*` keys next to the values they explain. Saving from the UI can silently change pricing behaviour. **This is the most important correctness fix in this work order.**
6. **Stale backend is invisible.** The user's backend was an old build while the frontend was new; the page showed placeholders that looked like real data. Nothing told them "restart the server".
7. **Frontend error messages show raw JSON.** In `frontend/src/api/admin.ts`, `updateDataStore` and `triggerCatalogRefresh` do `try { … throw new Error(errObj.error) } catch { throw new Error(text) }` — the `catch` swallows the clean message and the admin sees `{"error":"..."}`.

### Facts about this machine (verified; do not "fix" them, just make the UI show them correctly)
- `.env` sets `ANTIGRAVITY_API_KEY` (non-empty). It ALSO sets `AGY_API_KEY`, which **no code reads** — the UI must flag it.
- `.env` does NOT set `GOOGLE_CLOUD_API_KEY` → that is why "Refresh catalog" fails with `exit status 1`.
- `.env` does NOT set `SETTINGS_ENCRYPTION_KEY` → Part 2 auto-provisions one.
- `DEV_AUTH_BYPASS` is set in `.env` → on localhost you can sign in with any email from `ADMIN_EMAILS` and any non-empty password (used for verification in Part 6).
- The engine is the `agy` CLI wrapping Gemini; auth env var is `ANTIGRAVITY_API_KEY` (see `internal/config/config.go` ~line 114).

### Definition of done (the whole work order)
- One page, `/admin/health`, with tabs **Overview · AI Engine · Pricing Catalog · Jobs · Data Files · Settings & Secrets · Guide**, deep-linkable via `?tab=`.
- A **"Needs attention"** list at the top. Every item: plain-English title, why it matters, how to fix, and a fix button that does the thing.
- Overall status is **derived from that list** (never computed separately), so banner, cards and list can't disagree.
- **Settings & Secrets** shows every env var the app reads, grouped and explained; safe operational ones can be set/cleared/tested in-app (encrypted, audited, live — no restart); notification ones link to Integrations; security-critical ones are read-only with "change in .env and restart".
- Data files are viewed and saved **byte-for-byte**; backups listed with restore-any-version.
- The page warns when the backend is older than the frontend.
- `docs/SYSTEM_HEALTH.md` lets a new colleague understand and extend the page.
- `go build ./...`, `go test ./...`, `npm run build`, `pytest` all green; verified in a real browser.

---

## Part 1 — ALREADY DONE (do not redo; read so you know what exists)

All of this compiles and is covered by passing tests (`go test ./internal/envmgr/ ./internal/db/ ./internal/jobs/ ./cmd/server/`).

| File | What it is |
|---|---|
| `internal/envmgr/registry.go` (new) | **The single registry of every env var the app reads**, with `Title`, `Category`, plain-English `Description`, `Secret`, `Mode` (`managed` / `integration` / `deployment`), `Required`, `Aliases`, `Options`, `Example`, `ManageURL`, `Testable`, `Note`. Also `IgnoredNames()` (e.g. `AGY_API_KEY` → use `ANTIGRAVITY_API_KEY`), `Validate(v, value)`, `Preview(v, value)` (secrets → `••••` + last 4; deployment secrets → `••••` only), `Categories()`. Header comment explains how colleagues add a variable. |
| `internal/envmgr/envmgr.go` (new) | Runtime state: `CaptureBaseline()` (snapshot of startup env), `ServerValue(v)`, `Effective(v)`, `HasOverride(key)`, `OverrideCount()`, `ApplyOverride(key, value)` (os.Setenv + hook, managed vars only), `ClearOverride(key)` (restores startup value or unsets), `RecordLoadError/LoadError`, `RegisterApplyHook(key, fn)`, `IgnoredPresent()`. |
| `internal/envmgr/envmgr_test.go` (new) | Registry well-formedness; security-critical vars can never be `managed`; validation; preview never leaks; override lifecycle restores server value; unregistered keys (e.g. `PATH`) rejected. |
| `internal/db/db.go` | New table `runtime_env` (same shape/encryption as `integration_settings`). |
| `internal/db/runtime_env.go` (new) | `SetRuntimeEnv(key, value, isSecret, by)` (secrets AES-GCM encrypted; returns `ErrNoEncryptionKey` if no key), `DeleteRuntimeEnv`, `LoadRuntimeEnv()` (decrypted, server-side only, per-row `Err` when undecryptable), `ListRuntimeEnv()` (who/when metadata). |
| `internal/db/settings_key.go` (new) | `EnsureEncryptionKey(dataDir)`: uses `SETTINGS_ENCRYPTION_KEY` if valid; else loads/creates `<dataDir>/settings-encryption.key` (0600, never overwrites) and exports it to the env. `EncryptionKeyInfo()` → `(source, path, problem)` where source ∈ `env|file|generated|none`. A malformed env key is NOT replaced. |
| `internal/db/runtime_env_test.go` (new) | Encryption at rest, round trip, refusal without key, key reuse across restart, env preferred, malformed env rejected. |
| `internal/db/health_queries.go` (new) | `ListRecentJobsByStatus(status, limit) []JobBrief`, `LastJobUpdateAt(status)`, `RecentOutcomes(window) (done, failed)` (use for failure rate — recent window, not all-time). |
| `internal/logger/logger.go` | Log level is a `slog.LevelVar`; `logger.ApplyLevelFromEnv()` changes it live. |
| `internal/jobs/spawner.go` | `agyBin()` reads `AGY_PATH` from the **live** env first, so an in-app change applies to the next job. |
| `internal/jobs/engine_health.go` (rewritten) | `InspectEngine(ctx)` — cheap, no model call (CLI found, version cached 5 min, key present). `ProbeEngine(ctx, true)` — live one-word prompt, 30 s timeout, plain-English `Detail`. `activeModel()` now returns `(model, source)` with source ∈ `report_settings|env|default`. Test history: `appendEngineHistory(dataDir, EngineTestRecord)` / `loadEngineHistory(dataDir)` → `<dataDir>/health/engine_tests.json`, newest first, max 20. Shared helper `atomicWriteFile(path, data)`. |
| `internal/jobs/engine_health_test.go` | Updated + new tests (inspect never calls model; live `AGY_PATH` wins; history capped/newest-first). |
| `internal/jobs/env_health.go` (rewritten) | Now **runtime tools only**: `CheckRuntimeDependencies(skillDir, jobsDir)` → Python, DuckDB (cached 5 min), data folder writable, disk space. Each `DepHealth` has `Title`, `Description`, `Fix`. Credentials moved to the envmgr registry (shown once, in Settings). |
| `internal/jobs/catalog_refresher.go` | Every finished refresh (success/failed/skipped) is appended to `data/catalog_refresh_history.json` (max 15, log tail trimmed to 15 lines). `GetCatalogRefreshHistory()`. |
| `internal/jobs/data_health.go` | `storeManifest` gives each data file a `Title` + `Description` (taken from each file's own `_meta`). `StoreHealth` now has `title`, `description`; `Editable` comes from `editableDatastores`. |
| `internal/jobs/admin_handlers.go` | **Old** `AdminHealthSummary` still lives here (temporarily switched to call `CheckRuntimeDependencies`). Part 3 replaces it. |
| `.gitignore` | Ignores `catalog_refresh_history.json`, `settings-encryption.key`. |

**Not yet wired:** nothing calls `CaptureBaseline`, `EnsureEncryptionKey`, the stored overrides, the hooks, the history files, or the new queries. That is Part 2.

**Known flaky test (pre-existing, not yours):** `TestPhase4_IsolationAndPreferencesAndQuotas` sometimes fails on Windows with *"The process cannot access the file because it is being used by another process"* during TempDir cleanup. Re-run it alone (`go test ./internal/jobs/ -run TestPhase4 -count=1`); it passes. Don't "fix" it as part of this work.

---

## Part 2 — Backend: wire the foundation + Settings & Secrets API

### 2.1 Shared helpers — create `internal/jobs/health_helpers.go`
```go
package jobs

import (
	"encoding/json"
	"net/http"

	"github.com/facets/cur-web/internal/auth"
)

// adminOnly returns the session if the caller is an admin; otherwise it writes
// a JSON 401/403 and returns nil.
func (h *Handler) adminOnly(w http.ResponseWriter, r *http.Request) *auth.Session {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		writeJSONError(w, http.StatusUnauthorized, "Please sign in again.")
		return nil
	}
	if !sess.IsAdmin(h.adminEmails) {
		writeJSONError(w, http.StatusForbidden, "Only admins can do this.")
		return nil
	}
	return sess
}

// writeJSONError writes {"error": msg}. Messages are shown to admins as-is:
// write plain sentences that say what to do.
func writeJSONError(w http.ResponseWriter, status int, msg string) {
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set(contentTypeHeader, contentTypeJSON)
	_ = json.NewEncoder(w).Encode(v)
}
```
Use `adminOnly` + `writeJSONError` in every handler you write or touch in this work order.

### 2.2 Server info — top of the new `internal/jobs/system_health.go` (file is completed in Part 3)
```go
var (
	serverStartedAt = time.Now().UTC()
	serverBuildSHA  = "dev"
	serverBuildTime = "unknown"
)

// SetServerInfo is called once from main with the -ldflags build info.
func SetServerInfo(sha, buildTime string) { serverBuildSHA, serverBuildTime = sha, buildTime }

// HealthSchemaVersion: bump whenever the /api/admin/health response shape
// changes, and bump EXPECTED_HEALTH_SCHEMA in the frontend in the same commit.
const HealthSchemaVersion = 3
```

### 2.3 Apply stored overrides at startup — create `internal/jobs/env_handlers.go` (first function)
```go
// ApplyStoredEnvOverrides applies every saved Settings & Secrets override to
// the process env. Call once in main after the DB opens. Never logs values.
func ApplyStoredEnvOverrides(d *db.DB) {
	vals, err := d.LoadRuntimeEnv()
	if err != nil {
		slog.Warn("could not load saved settings overrides", "err", err)
		return
	}
	applied := 0
	for _, it := range vals {
		v, ok := envmgr.Lookup(it.Key)
		if !ok || v.Mode != envmgr.ModeManaged {
			slog.Warn("ignoring saved override for a variable that is no longer editable", "key", it.Key)
			continue
		}
		if it.Err != nil {
			envmgr.RecordLoadError(it.Key, "The saved value can't be decrypted — the encryption key changed since it was saved. Enter it again.")
			continue
		}
		if err := envmgr.ApplyOverride(it.Key, it.Value); err == nil {
			applied++
		}
	}
	slog.Info("applied saved settings overrides", "count", applied)
}
```

### 2.4 Wire `cmd/server/main.go`
Exact order matters. Edit `main()`:
1. Immediately after the four `godotenv.Load(...)` lines and **before** `logger.Init()`:
   ```go
   envmgr.CaptureBaseline()
   ```
2. Immediately after `cfg, err := config.LoadFromEnv()` succeeds:
   ```go
   if src, kerr := db.EnsureEncryptionKey(cfg.DataDir); kerr != nil {
       slog.Warn("saved secrets can't be encrypted", "err", kerr)
   } else {
       slog.Info("encryption key for saved secrets ready", "source", src)
   }
   ```
3. Right after `buildSHA, buildTime = resolveBuildInfo(...)`: `jobs.SetServerInfo(buildSHA, buildTime)`.
4. Right after `database, err := db.Open(...)` succeeds (and the `defer database.Close()`):
   ```go
   envmgr.RegisterApplyHook("LOG_LEVEL", logger.ApplyLevelFromEnv)
   envmgr.RegisterApplyHook("AGY_PATH", jobs.InvalidateEngineProbeCache)
   jobs.ApplyStoredEnvOverrides(database)
   ```
5. Add the import `"github.com/facets/cur-web/internal/envmgr"`.

### 2.5 Settings & Secrets API — rest of `internal/jobs/env_handlers.go`

**Response types** (JSON field names are the contract with the frontend — do not rename):
```go
type envValueView struct {
	Set     bool   `json:"set"`
	Preview string `json:"preview,omitempty"`  // from envmgr.Preview — NEVER the raw secret
	FromKey string `json:"from_key,omitempty"` // when an alias supplied the value
}
type envOverrideView struct {
	Set       bool   `json:"set"`
	Preview   string `json:"preview,omitempty"`
	UpdatedBy string `json:"updated_by,omitempty"`
	UpdatedAt string `json:"updated_at,omitempty"`
	LoadError string `json:"load_error,omitempty"`
}
type envEffectiveView struct {
	Set     bool   `json:"set"`
	Preview string `json:"preview,omitempty"`
	Source  string `json:"source"` // "app" | "server" | "integrations" | "none"
}
type envVarView struct {
	Key         string           `json:"key"`
	Title       string           `json:"title"`
	Category    string           `json:"category"`
	Description string           `json:"description"`
	Note        string           `json:"note,omitempty"`
	Secret      bool             `json:"secret"`
	Mode        string           `json:"mode"` // managed | integration | deployment
	Required    bool             `json:"required"`
	Options     []string         `json:"options,omitempty"`
	Example     string           `json:"example,omitempty"`
	ManageURL   string           `json:"manage_url,omitempty"`
	Testable    bool             `json:"testable"`
	Server      envValueView     `json:"server"`
	Override    envOverrideView  `json:"override"`
	Effective   envEffectiveView `json:"effective"`
}
type envListResponse struct {
	Encryption struct {
		Available bool   `json:"available"`
		Source    string `json:"source"` // env | file | generated | none
		KeyPath   string `json:"key_path,omitempty"`
		Problem   string `json:"problem,omitempty"`
	} `json:"encryption"`
	Categories     []string             `json:"categories"`
	Vars           []envVarView         `json:"vars"`
	IgnoredPresent []envmgr.IgnoredVar  `json:"ignored_present"`
}
```

**`func (h *Handler) envVarView(v envmgr.Var, meta map[string]db.RuntimeEnvEntry) envVarView`** — build one row:
- `Server`: `val, from, ok := envmgr.ServerValue(v)` → `Set: ok, Preview: envmgr.Preview(v, val), FromKey: from` (set `FromKey` only if `from != v.Key`).
- `Override` (managed only): `Set: envmgr.HasOverride(v.Key)`; if set, `Preview: envmgr.Preview(v, os.Getenv(v.Key))`, `UpdatedBy/UpdatedAt` from `meta[v.Key]`. Always set `LoadError: envmgr.LoadError(v.Key)`.
- `Effective`:
  - integration mode: resolve with existing resolvers — `RESEND_API_KEY` → `h.emailCfg().APIKey`, `RESEND_FROM` → `h.emailCfg().From`, `SLACK_WEBHOOK_URL` → `h.slackURL()`; `Source: "integrations"` if non-empty else `"none"`.
  - otherwise `val, _, ok := envmgr.Effective(v)`; `Source`: `"app"` if override set, else `"server"` if ok, else `"none"`.
- Never put a raw value in any field. The only allowed representation of a secret is `envmgr.Preview`.

**Handlers** (register routes in 2.6):

1. `AdminListEnv` — `GET /api/admin/env`
   - `sess := h.adminOnly(w, r)`; return if nil.
   - `meta, _ := h.db.ListRuntimeEnv()`.
   - Encryption: `src, path, problem := db.EncryptionKeyInfo()`; `Available: db.EncryptionAvailable()`.
   - `Categories: envmgr.Categories()`, `Vars`: every `envmgr.All()` through `envVarView`, `IgnoredPresent: envmgr.IgnoredPresent()` (use `[]envmgr.IgnoredVar{}` not nil so JSON is `[]`).
   - **Special case — the `SETTINGS_ENCRYPTION_KEY` row:** set `Effective.Set = db.EncryptionAvailable()`, `Effective.Source = "server"` when the key source is `env`, otherwise `"none"`, and leave `Preview` empty. The frontend explains the real situation from the top-level `encryption` object (env / auto-created file / missing), not from this row.

2. `AdminSetEnv` — `PUT /api/admin/env/{key}`, body `{"value": "..."}` (cap body at 16 KB with `io.LimitReader`).
   - Unknown key → 404 `"Unknown setting."`
   - `Mode == integration` → 400 `"Change this on the Integrations page."`; `deployment` → 400 `"This one can only be changed in the server's .env file (then restart) — changing it from a web page could lock everyone out."`
   - `envmgr.Validate(v, value)` → 400 with `err.Error()`.
   - If `v.Secret && !db.EncryptionAvailable()` → 409 `"Secrets can't be saved yet: the server has no encryption key. Check the 'Encryption key' row below."`
   - `h.db.SetRuntimeEnv(key, value, v.Secret, sess.Email)` → 500 on error (message: `"Couldn't save the setting."`; log the error, never the value).
   - `envmgr.ApplyOverride(key, value)`.
   - `h.db.LogAdminAction(sess.Email, "env_set", key, "")` — **details must be empty; never log the value.**
   - Respond with the updated `envVarView`.

3. `AdminClearEnv` — `DELETE /api/admin/env/{key}`
   - Managed only (same 400s as above). `h.db.DeleteRuntimeEnv(key)`; `envmgr.ClearOverride(key)`; audit `"env_clear"`; respond with updated view.

4. `AdminTestEnv` — `POST /api/admin/env/{key}/test` → `{"ok": bool, "status": "ok|warn|fail", "detail": "...", "latency_ms": n}`
   - `ANTIGRAVITY_API_KEY`: `p := h.spawner.ProbeEngine(r.Context(), true)`; also `appendEngineHistory(filepath.Dir(h.jobsDir), EngineTestRecord{EngineProbe: p, Actor: sess.Email})`; map `p.State`/`p.Detail`/`p.LatencyMS`.
   - `AGY_PATH`: `p := h.spawner.InspectEngine(r.Context())`; ok if `p.BinaryFound` → detail `"Found agy at <path> (version <v>)."` else fail with `p.Detail`.
   - `GOOGLE_CLOUD_API_KEY`: HTTP GET `https://cloudbilling.googleapis.com/v1/services?pageSize=1` with header **`X-Goog-Api-Key: <key>`** (NOT a `?key=` query param — Go's `url.Error` prints the URL, which would leak the key into logs/responses). 10 s timeout. 200 → ok `"Google accepted the key (Cloud Billing API reachable)."`. Non-200 → parse `{"error":{"message":...}}` and return fail with that message (it never contains the key). Network error → fail `"Couldn't reach Google: " + err.Error()` (safe because the key is in a header).
     - Key to test: `os.Getenv("GOOGLE_CLOUD_API_KEY")`, else `os.Getenv("GCP_API_KEY")`; if empty → fail `"No key is set."`.
   - Any other key → 400 `"This setting has no test."`
   - Audit `"env_test"` with details `"status=<status>"`.

### 2.6 Routes — `cmd/server/main.go`, inside the admin route group next to `/api/admin/health`
```go
r.Get("/api/admin/env", jobHandler.AdminListEnv)
r.Put("/api/admin/env/{key}", jobHandler.AdminSetEnv)
r.Delete("/api/admin/env/{key}", jobHandler.AdminClearEnv)
r.Post("/api/admin/env/{key}/test", jobHandler.AdminTestEnv)
r.Get("/api/admin/datastores/{key}/backups", jobHandler.AdminListDataStoreBackups)   // Part 3.3
r.Post("/api/admin/datastores/{key}/restore", jobHandler.AdminRestoreDataStoreBackup) // Part 3.3
```
Also add the same lines to the router and the `{method, path}` table in `internal/jobs/admin_panel_testplan_test.go` (that test asserts 403 for non-admins).

### 2.7 Tests — `internal/jobs/env_handlers_test.go`
Use the setup pattern in `datastore_handlers_test.go` (`db.Open(":memory:")`, `ctxWithSession`). Before each test: `envmgr.ResetForTest()`, `t.Setenv("SETTINGS_ENCRYPTION_KEY", base64.StdEncoding.EncodeToString(make([]byte, 32)))`, `envmgr.CaptureBaseline()`.
- List: response JSON **never contains** a secret you `t.Setenv` (marshal and `assert.NotContains`). `AGY_API_KEY` set → appears in `ignored_present`.
- Set `LOG_LEVEL=debug` → 200, `os.Getenv("LOG_LEVEL")=="debug"`, audit row `env_set` with empty details.
- Set a deployment var (`SESSION_SECRET`) → 400; set an unknown key (`PATH`) → 404.
- Set secret with key unset (`t.Setenv("SETTINGS_ENCRYPTION_KEY","")`) → 409.
- Clear restores the startup value.
- Test endpoint for `GOOGLE_CLOUD_API_KEY`: point the client at an `httptest.Server` (make the base URL a package var `cloudBillingBaseURL` so tests can override it) and assert the key arrives in `X-Goog-Api-Key` and never in the URL.

**Check:** `go build ./... && go test ./internal/... ./cmd/server/`

---

## Part 3 — Backend: one honest health endpoint + data-file fixes

### 3.1 Delete the old health code
In `internal/jobs/admin_handlers.go` delete everything from the comment line `// System Health Handlers (Phase 3)` (and the `// ───` line above it) down to — but **not** including — the `// ───` line above `// Report Override Handlers (Phase 4)`. That removes: `HealthResponse`, `HealthEngine`, `HealthCatalog`, `HealthTokens`, `HealthPipeline`, `catalogHealthJSON`, the catalog cache vars, `getCatalogHealthCheck`, `AdminHealthSummary`, `AdminTestEngine`. You re-create the ones still needed in `system_health.go`. Re-run `go build ./...` — the only errors should be the missing symbols you are about to add.

### 3.2 Create `internal/jobs/system_health.go`
Contains: the server-info block from 2.2, `catalogHealthJSON` + `getCatalogHealthCheck` (copy them unchanged from the deleted code, but add a `Bad []struct{Service, Status string; Issues []string}` field under `Source` with json tags `bad`, `service`, `status`, `issues`), and the following.

**Response contract (JSON names are the frontend contract):**
```go
type HealthIssue struct {
	ID       string       `json:"id"`       // stable, e.g. "engine.key_missing", "data.service_map"
	Severity string       `json:"severity"` // critical | warning | info
	Area     string       `json:"area"`     // engine | catalog | jobs | data | settings | runtime
	Title    string       `json:"title"`    // what's wrong, one line, plain English
	Detail   string       `json:"detail"`   // why it matters
	Fix      string       `json:"fix"`      // what to do
	Action   *IssueAction `json:"action,omitempty"`
}
type IssueAction struct {
	Kind   string `json:"kind"`   // test_engine | refresh_catalog | edit_env | open_store | open_tab | link
	Target string `json:"target,omitempty"` // env key / store key / tab id / path
	Label  string `json:"label"`  // button text, e.g. "Add the key"
}

type HealthResponse struct {
	SchemaVersion int           `json:"schema_version"` // HealthSchemaVersion
	CheckedAt     string        `json:"checked_at"`
	Status        string        `json:"status"`  // ok | warn | fail — DERIVED from Issues
	Summary       string        `json:"summary"` // one sentence
	Issues        []HealthIssue `json:"issues"`  // sorted critical → warning → info
	Engine        EngineHealth  `json:"engine"`
	Catalog       CatalogHealth `json:"catalog"`
	Jobs          JobsHealth    `json:"jobs"`
	Data          DataHealth    `json:"data"`
	Settings      SettingsHealth `json:"settings"`
	Runtime       RuntimeHealth `json:"runtime"`
	Server        ServerInfo    `json:"server"`
}

type EngineHealth struct {
	Status        string             `json:"status"`  // ok | warn | fail | unknown
	Summary       string             `json:"summary"`
	Model         string             `json:"model"`
	ModelSource   string             `json:"model_source"` // report_settings | env | default
	CLIFound      bool               `json:"cli_found"`
	CLIPath       string             `json:"cli_path"`
	CLIVersion    string             `json:"cli_version"`
	KeyPresent    bool               `json:"key_present"`
	KeySource     string             `json:"key_source"` // app | server | none
	VerifiedBy    string             `json:"verified_by,omitempty"` // test | job
	LastTest      *EngineTestRecord  `json:"last_test,omitempty"`
	History       []EngineTestRecord `json:"history"`
	LastSuccessfulJobAt string       `json:"last_successful_job_at,omitempty"`
	LiveTestDisabled bool            `json:"live_test_disabled"`
}

type CatalogHealth struct {
	Status         string              `json:"status"`
	Summary        string              `json:"summary"`
	SKUCount       int                 `json:"sku_count"`
	ServiceCount   int                 `json:"service_count"`
	FetchedAt      string              `json:"fetched_at"`
	AgeDays        int                 `json:"age_days"`
	StaleAfterDays int                 `json:"stale_after_days"` // catalogStaleDays()
	Stale          bool                `json:"stale"`
	ArtifactOK     bool                `json:"artifact_ok"`
	SourceTotal    int                 `json:"source_total"`
	SourceProblems []string            `json:"source_problems"` // "service: STATUS — issue"
	ImageBaked     bool                `json:"image_baked"`
	APIKeyPresent  bool                `json:"api_key_present"`
	LastRun        CatalogRefreshRun   `json:"last_run"` // GetCatalogRefreshStatus()
	History        []CatalogRefreshRun `json:"history"`  // GetCatalogRefreshHistory()
}

type JobsHealth struct {
	Status           string         `json:"status"`
	Summary          string         `json:"summary"`
	Running          int            `json:"running"`
	Queued           int            `json:"queued"`
	CompletedTotal   int            `json:"completed_total"`
	FailedTotal      int            `json:"failed_total"`
	RecentWindow     int            `json:"recent_window"`      // 20
	RecentFailed     int            `json:"recent_failed"`
	RecentFailurePct float64        `json:"recent_failure_pct"` // over the window
	AvgDurationSec   float64        `json:"avg_duration_sec"`
	ConcurrencyLimit int            `json:"concurrency_limit"`  // from GetQuotaSettings, default 2
	Halted           bool           `json:"halted"`
	LastSuccessAt    string         `json:"last_success_at,omitempty"`
	LastFailureAt    string         `json:"last_failure_at,omitempty"`
	RecentFailures   []db.JobBrief  `json:"recent_failures"` // 5 newest, Error truncated to 300 chars
}

type DataHealth struct {
	Status  string        `json:"status"`
	Summary string        `json:"summary"`
	Stores  []StoreHealth `json:"stores"`
}

type SettingsHealth struct {
	Status            string `json:"status"`
	Summary           string `json:"summary"`
	EncryptionSource  string `json:"encryption_source"`
	OverrideCount     int    `json:"override_count"`
	MissingRequired   []string `json:"missing_required"` // keys of Required vars not effectively set
	IgnoredPresent    []envmgr.IgnoredVar `json:"ignored_present"`
	NotificationsOn   bool   `json:"notifications_on"` // email or slack configured
}

type RuntimeHealth struct {
	Status  string      `json:"status"`
	Summary string      `json:"summary"`
	Checks  []DepHealth `json:"checks"` // CheckRuntimeDependencies
}

type ServerInfo struct {
	SchemaVersion int    `json:"schema_version"`
	BuildSHA      string `json:"build_sha"`
	BuildTime     string `json:"build_time"`
	StartedAt     string `json:"started_at"`
	UptimeSec     int64  `json:"uptime_sec"`
	GoVersion     string `json:"go_version"` // runtime.Version()
	OS            string `json:"os"`         // runtime.GOOS + "/" + runtime.GOARCH
	PID           int    `json:"pid"`
	DataDir       string `json:"data_dir"`   // filepath.Dir(h.jobsDir)
	SkillDir      string `json:"skill_dir"`
	ImageBaked    bool   `json:"image_baked"`
}
```
Always return empty slices (`[]T{}`), never `nil`, so the frontend can `.map` safely.

**Build each component in its own small function** (`h.engineHealth(ctx)`, `h.catalogHealth()`, `h.jobsHealth()`, `h.dataHealth()`, `h.settingsHealth()`, `h.runtimeHealth()`), each returning `(Component, []HealthIssue)`. `AdminHealthSummary` just calls them, concatenates issues, sorts, derives status. Keep every function under ~60 lines.

**Engine status rules** (`dataDir := filepath.Dir(h.jobsDir)`):
1. `p := h.spawner.InspectEngine(ctx)`; fill CLI/key fields; `KeySource`: `"app"` if `envmgr.HasOverride("ANTIGRAVITY_API_KEY")`, `"server"` if key present, else `"none"`.
2. If `p.State == "fail"` → status `fail`, summary `p.Detail`.
3. Else `hist := loadEngineHistory(dataDir)`; if `len(hist)>0` and the newest `CheckedAt` is < 24 h old → status = that record's `State`, `VerifiedBy="test"`, summary = its `Detail` prefixed with "Last test: ".
4. Else if `h.db.LastJobUpdateAt("done")` is < 24 h old → status `ok`, `VerifiedBy="job"`, summary `"A projection job completed successfully <relative time> ago."`
5. Else status `unknown`, summary `"Not verified recently — run a test."`
- Note SQLite `updated_at` is `"2006-01-02 15:04:05"` UTC; parse with that layout.

**Overall:** `fail` if any `critical`; `warn` if any `warning`; else `ok`. `Summary`: no issues → `"Everything is working."`; else first issue's `Title`, plus `" (and N more)"` when more than one non-info issue.

**Issue rules — implement exactly these** (titles/fixes are final copy; keep them):

| ID | Condition | Sev | Title | Fix | Action |
|---|---|---|---|---|---|
| `engine.cli_missing` | `!p.BinaryFound` | critical | The AI engine tool (agy) isn't installed or can't be found | Install agy on the server, or set its full path in Settings & Secrets → agy tool location. | `edit_env AGY_PATH` "Set agy location" |
| `engine.key_missing` | CLI ok, `!p.KeyPresent` | critical | The AI engine API key is missing | Add ANTIGRAVITY_API_KEY in Settings & Secrets. Every projection job fails without it. If `AGY_API_KEY`/`GEMINI_API_KEY` is set, append: "AGY_API_KEY is set, but the app never reads that name." | `edit_env ANTIGRAVITY_API_KEY` "Add the key" |
| `engine.test_failed` | last test < 24 h and `State=="fail"` | critical | The last AI engine test failed | Detail = test detail. Fix: check the key and network, then run the test again. | `test_engine` "Test again" |
| `engine.slow` | last test < 24 h and `State=="warn"` | warning | The AI engine is responding slowly | Jobs will take longer; usually temporary. Re-test later. | `test_engine` "Test again" |
| `engine.unverified` | status `unknown` and not disabled | info | The AI engine hasn't been verified recently | Run a quick test (uses a tiny amount of quota). | `test_engine` "Run test" |
| `catalog.artifact` | `!ArtifactOK` | critical | The pricing catalog database is missing or broken | Projections can't price anything. Run a catalog refresh. | `refresh_catalog` "Refresh catalog" (if no API key → `edit_env GOOGLE_CLOUD_API_KEY` "Add pricing API key") |
| `catalog.stale` | `Stale` | warning | GCP prices are N days old (refresh after M days) | Refresh the catalog. Needs the Google Cloud pricing API key. | same as above |
| `catalog.refresh_failed` | `LastRun.State=="failed"` | warning | The last catalog refresh failed | Detail: LastRun.Reason. If `!APIKeyPresent`: "No GOOGLE_CLOUD_API_KEY is set — that is the usual cause." | key missing → `edit_env GOOGLE_CLOUD_API_KEY`, else `open_tab catalog` "See the log" |
| `catalog.source` | `len(SourceProblems)>0` | warning | N price files are incomplete | Refresh the catalog to re-download them. | `refresh_catalog` |
| `catalog.image_baked` | `ImageBaked` | info | Catalog is built into the server image | It refreshes on each deploy, not from this page. | none |
| `jobs.halted` | `Halted` | critical | All job processing is paused (STOP ALL is on) | New uploads won't run until it's resumed. | `link /admin` "Open All Projections" |
| `jobs.failing` | window ≥ 4 finished and `RecentFailurePct > 25` | critical | N of the last M jobs failed | Open the failures below to see the error. | `open_tab jobs` "See failures" |
| `jobs.some_failing` | window ≥ 10 and pct > 5 | warning | (same pattern) | (same) | `open_tab jobs` |
| `data.<key>` | store `fail` | critical | "<Title>" is missing or unreadable | Detail = store Detail. Fix: restore a backup or redeploy the file. | `open_store <key>` "Open file" |
| `data.<key>` | store `warn` | warning | "<Title>" needs a look | Detail = store Detail. | `open_store <key>` |
| `settings.missing.<KEY>` | Required managed var not set (skip ANTIGRAVITY — covered by engine) | critical | "<Title>" is not set | Var description. | `edit_env <KEY>` |
| `settings.load_error.<KEY>` | `envmgr.LoadError(key) != ""` | warning | The saved "<Title>" can't be read | Re-enter it (the encryption key changed). | `edit_env <KEY>` "Re-enter" |
| `settings.ignored.<KEY>` | ignored var present | warning if its `UseInstead` is not set, else info | <KEY> is set but the app never reads it | Use <UseInstead> instead (Settings & Secrets), then remove <KEY> from .env. | `edit_env <UseInstead>` |
| `settings.encryption_none` | `!db.EncryptionAvailable()` | warning | Secrets can't be saved in the app | Problem text from `EncryptionKeyInfo`. Set SETTINGS_ENCRYPTION_KEY (generate: `openssl rand -base64 32`) in .env and restart. | `open_tab settings` |
| `settings.encryption_on_disk` | source `file`/`generated` AND `APP_BASE_URL` starts with `https://` | warning | Saved secrets depend on a key file on this server's disk | If the disk is replaced on redeploy, saved secrets are lost. Set SETTINGS_ENCRYPTION_KEY in the deployment. | `open_tab settings` |
| `settings.dev_bypass` | `DEV_AUTH_BYPASS=="true"` AND `APP_BASE_URL` host is not `localhost`/`127.0.0.1` | critical | Anyone can sign in with any password | DEV_AUTH_BYPASS is on for a non-local server. Remove it from the deployment and restart. | none |
| `settings.notifications_off` | email and slack both unconfigured | info | Job notifications are off | Set up email or Slack on the Integrations page. | `link /admin/integrations` |
| `runtime.<key>` | runtime check `fail` / `warn` | critical / warning | "<Title>" — <Detail> | `DepHealth.Fix` | none |

Sort: critical, warning, info; within a severity keep the order of the table.

**Handler:** `AdminHealthSummary` (`GET /api/admin/health`, same route name — keep it registered as today) and `AdminTestEngine` (`POST /api/admin/engine/test`): `p := h.spawner.ProbeEngine(ctx, true)`, `appendEngineHistory(dataDir, EngineTestRecord{EngineProbe: p, Actor: sess.Email})`, audit `engine_test` with `"state=<s> latency=<ms>ms"`, respond `p`.

**Performance rule:** the health endpoint must never call the model and must stay < 1 s on a warm cache. The only slow call is `getCatalogHealthCheck` (cached 60 s) — keep that cache.

### 3.3 Fix the data-file editor (`internal/jobs/datastore_handlers.go`)
1. **View raw.** In `AdminGetDataStore`, delete the `json.Unmarshal` → `json.MarshalIndent` block. Set `Content: string(contentBytes)` exactly as read. Count items with a separate `json.Unmarshal` into `any` (only for the count). Add `Title`/`Description` from `storeManifest` to `DataStoreDetailDTO` (`json:"title"`, `json:"description"`).
2. **Save raw.** In `AdminPutDataStore`, replace "unmarshal → MarshalIndent → write" with:
   ```go
   raw := unwrapContent(bodyBytes)           // see below
   if err := validateStoreJSON(key, raw); err != nil { writeJSONError(w, 400, err.Error()); return }
   if len(raw) == 0 || raw[len(raw)-1] != '\n' { raw = append(raw, '\n') }
   // backup (existing code) → atomicWriteFile(fullPath, raw) → audit → respond
   ```
   ```go
   // unwrapContent accepts either the raw file, or {"content": "<raw file>"}
   // (what the frontend sends). Only an object with exactly one key "content"
   // holding a JSON string is treated as the wrapper.
   func unwrapContent(body []byte) []byte {
   	var m map[string]json.RawMessage
   	if json.Unmarshal(body, &m) == nil && len(m) == 1 {
   		if c, ok := m["content"]; ok {
   			var s string
   			if json.Unmarshal(c, &s) == nil {
   				return []byte(s)
   			}
   		}
   	}
   	return body
   }

   // validateStoreJSON checks syntax (with line/column on error) and the
   // top-level shape. It never re-encodes, so key order and formatting survive.
   func validateStoreJSON(key string, raw []byte) error {
   	var v any
   	if err := json.Unmarshal(raw, &v); err != nil {
   		if se, ok := err.(*json.SyntaxError); ok {
   			line, col := lineCol(raw, se.Offset)
   			return fmt.Errorf("This isn't valid JSON — %s (line %d, column %d).", se.Error(), line, col)
   		}
   		return fmt.Errorf("This isn't valid JSON — %s.", err.Error())
   	}
   	switch key {
   	case "service_map", "review_config", "fx_rates", "service_specs", "assumption_defaults", "pricing_matrix", "refresh_store":
   		if _, ok := v.(map[string]any); !ok {
   			return fmt.Errorf("The file must be a JSON object ({ … }) at the top level.")
   		}
   	}
   	return nil
   }

   func lineCol(b []byte, off int64) (int, int) {
   	line, col := 1, 1
   	for i := int64(0); i < off && i < int64(len(b)); i++ {
   		if b[i] == '\n' { line++; col = 1 } else { col++ }
   	}
   	return line, col
   }
   ```
   (All seven editable files are objects at the top level — verified.)
3. **Backups list + restore.**
   - `AdminListDataStoreBackups` — `GET /api/admin/datastores/{key}/backups` → `{"backups":[{"name","created_at","size_bytes","kind"}]}` newest first; `kind` = `"pre-revert"` if the name contains `.pre-revert.`, `"pre-restore"` if `.pre-restore.`, else `"before-edit"`. `created_at` from file mod time (RFC3339).
   - `AdminRestoreDataStoreBackup` — `POST /api/admin/datastores/{key}/restore` body `{"name": "<file name>"}`. Validate: key editable; `filepath.Base(name) == name`; `strings.HasPrefix(name, key+".")`; `strings.HasSuffix(name, ".json")`; file exists in `_backups`; its content passes `validateStoreJSON`. Back up the current file as `<key>.<ts>.pre-restore.json`, `atomicWriteFile`, audit `datastore_restore` with the backup name, respond with the refreshed `StoreHealth` (same shape as PUT).
   - Keep `AdminRevertDataStore` (restore newest) working.
4. Switch every error in this file to `writeJSONError` with plain sentences (e.g. `"This file is read-only here."`, `"That backup doesn't exist."`), and the admin checks to `h.adminOnly`.

### 3.4 Tests
- `datastore_handlers_test.go` — **add** `TestAdminDataStore_SavePreservesKeyOrderAndFormatting`: seed `{"zeta": 1, "_comment_a": "x", "alpha": 2}` with 4-space indent; PUT the same text with `"zeta": 9`; assert the file on disk equals the sent text byte-for-byte (+ trailing newline), i.e. `zeta` still before `alpha`. Add a GET test asserting `Content` equals the raw file. Add backup-list and restore tests incl. rejecting `name="../x.json"` and a name for another key. Update the existing tests only where error wording changed (they check `"invalid JSON"` → now `"isn't valid JSON"`; `"read-only or unrecognized"` → your new message).
- `system_health_test.go` — table-driven tests for the issue builder functions (make them pure: take inputs, return issues) covering at least: key missing → `engine.key_missing` critical + overall `fail`; ignored `AGY_API_KEY` with `ANTIGRAVITY_API_KEY` set → info only; stale catalog with no API key → action `edit_env GOOGLE_CLOUD_API_KEY`; no issues → status `ok`, summary "Everything is working."; health JSON contains `"schema_version":3`.

**Check:** `go build ./... && go test ./internal/... ./cmd/server/`

---

## Part 4 — Frontend: rebuild the page as clear, small components

Stack: React + TypeScript + Vite + Tailwind; theme via CSS variables `--fcp-*` (see existing classes in `frontend/src/pages/AdminHealth.tsx` — copy the card/border/text classes, do not invent colours). Shared: `Layout`, `BackButton`, `ConfirmDialog` (props: `open, onOpenChange, title, description, confirmLabel, destructive, loading, requireTypedWord?, onConfirm`), `useTitle`.

### 4.1 Fix API error handling — `frontend/src/api/http.ts`
Add (keep `getJSON` but make it use the same parsing):
```ts
async function readError(res: Response): Promise<ApiError> {
  const text = await res.text().catch(() => '')
  let msg = text || `${res.status} ${res.statusText}`
  try {
    const j = JSON.parse(text)
    if (j && typeof j.error === 'string') msg = j.error
    else if (j && typeof j.reason === 'string') msg = j.reason
  } catch { /* not JSON — keep text */ }
  if (text.trimStart().startsWith('<')) {
    msg = 'The server answered with a web page instead of data. The backend is probably not running or is an older build — restart it.'
  }
  return new ApiError(res.status, msg)
}

export async function sendJSON<T>(method: 'POST' | 'PUT' | 'DELETE', url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw await readError(res)
  const ct = res.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    if (res.status === 204) return undefined as T
    throw new ApiError(res.status, 'The server answered with a web page instead of data. Restart the backend.')
  }
  return res.json() as Promise<T>
}
```
Change `getJSON` to `if (!res.ok) throw await readError(res)` and apply the same content-type check. Then fix `triggerCatalogRefresh` and `updateDataStore` in `admin.ts` to use `sendJSON` (removes the throw-inside-try bug).

### 4.2 New API module — `frontend/src/api/health.ts`
Mirror every Go struct from Parts 2.5 and 3.2 as TypeScript interfaces **with the same field names**, plus:
```ts
export const EXPECTED_HEALTH_SCHEMA = 3 // must equal HealthSchemaVersion in system_health.go
export const getHealth = () => getJSON<HealthResponse>('/api/admin/health')
export const testEngine = () => sendJSON<EngineProbe>('POST', '/api/admin/engine/test')
export const getEnv = () => getJSON<EnvListResponse>('/api/admin/env')
export const setEnv = (key: string, value: string) => sendJSON<EnvVarView>('PUT', `/api/admin/env/${encodeURIComponent(key)}`, { value })
export const clearEnv = (key: string) => sendJSON<EnvVarView>('DELETE', `/api/admin/env/${encodeURIComponent(key)}`)
export const testEnv = (key: string) => sendJSON<EnvTestResult>('POST', `/api/admin/env/${encodeURIComponent(key)}/test`)
export const listBackups = (key: string) => getJSON<{ backups: BackupInfo[] }>(`/api/admin/datastores/${encodeURIComponent(key)}/backups`)
export const restoreBackup = (key: string, name: string) => sendJSON<{ message: string; store: StoreHealth }>('POST', `/api/admin/datastores/${encodeURIComponent(key)}/restore`, { name })
```
Re-export/move `getCatalogRefreshStatus`, `triggerCatalogRefresh`, `getDataStore`, `updateDataStore`, `revertDataStore` here. Delete the old `HealthSummary`, `EngineProbe`, `DepHealth` types from `admin.ts` once nothing imports them (keep `getCatalogStatus` — `AdminIntegrations.tsx` uses it).

### 4.3 File layout — `frontend/src/pages/health/`
| File | Responsibility (keep each < ~250 lines) |
|---|---|
| `HealthPage.tsx` | Container: loads health (and env when the Settings tab opens), tab state from `?tab=` via `useSearchParams` (`overview` default), auto-refresh every 30 s while `document.visibilityState === 'visible'` (toggle; remember choice in `localStorage` inside try/catch), dispatches issue actions. |
| `format.ts` | `relativeTime(iso)` ("3 min ago", handles SQLite `"YYYY-MM-DD HH:MM:SS"` as UTC), `formatBytes`, `formatDuration(sec)`. |
| `StatusBadge.tsx` | One component for every status. Map: `ok`→"Healthy" (emerald), `warn`→"Needs attention" (amber), `fail`→"Not working" (rose), `unknown`→"Not checked yet" (gray). Use these words everywhere — nowhere else may print raw `ok/warn/fail`. |
| `StatusHero.tsx` | Big overall state: `ok` "Everything is working" / `warn` "Needs attention" / `fail` "Something is broken"; `health.summary`; "Checked <relative> · Refresh" button; auto-refresh toggle; small server line "Backend started <relative> · build <sha>". |
| `ServerMismatchBanner.tsx` | Red banner when `health.schema_version !== EXPECTED_HEALTH_SCHEMA`: "This page is newer than the backend it's talking to. Restart the backend (`go run ./cmd/server`) or redeploy — until then some checks are missing or wrong." |
| `IssuesList.tsx` | "Needs attention (N)" — critical & warning cards expanded (title, detail, **What to do:** fix, action button); info items collapsed under "N tips". Empty → "Nothing needs attention." |
| `OverviewTab.tsx` | Six cards: AI engine, Pricing catalog, Jobs, Data files, Settings & secrets, Server tools — each: name, `StatusBadge`, component `summary`, "Open →" (switches tab). |
| `EngineTab.tsx` | Sections: **What this is** ("The AI engine is the agy tool calling Google's Gemini model. Every projection uses it to map AWS line items to GCP."); rows: Model (+ "Change in Report Settings" link → `/admin/report-settings`), agy tool (found/path/version), API key (present? source "set in this app"/"from server .env"/"missing" + "Manage" → Settings tab focused on the key), Verified by; **Test AI engine** button with explanation ("Sends a one-word prompt; takes 5–30 s; uses a tiny amount of quota") and live result; **Test history** table (time, result badge, latency, who, detail). Disabled-state note when `live_test_disabled`. |
| `CatalogTab.tsx` | **What this is**; facts (SKUs, services, fetched relative, stale threshold, price files OK x/y, problems list); **Refresh catalog** button — reuse the existing polling logic from today's `AdminHealth.tsx` (status every 2 s, elapsed timer, stop on terminal state); if `!api_key_present` show an inline warning with "Add pricing API key" (→ Settings, key `GOOGLE_CLOUD_API_KEY`) and keep the button enabled (the run will explain the failure); image-baked note; **Refresh history** table with expandable log tail per run. |
| `JobsTab.tsx` | Tiles: Running, Queued, Concurrency limit (+ link to Report Settings), Recent failure rate ("N of last M"), Avg duration, Last success; halted banner with link to `/admin`; **Recent failures** list → each row links to `/jobs/<id>` and `/jobs/<id>/logs`, shows prospect, owner, relative time, error (first 300 chars, expandable). |
| `DataFilesTab.tsx` | Table: Title (+ file name small), Status badge, Items, Size, Last changed, Action ("Open"). Description shown under the title. DB-backed rows (from `GET /api/admin/datastores`) show "Manage →" link instead. |
| `StoreEditor.tsx` | Modal/drawer: title + description; **warning box** for `service_map`/`service_specs`/`pricing_matrix`/`review_config`: "Changes here affect every future projection. Rules are checked top to bottom — order matters."; monospace `<textarea>` with the raw content; live "Valid JSON ✓ / ✗ <message>" indicator (`JSON.parse` in a `useMemo`); buttons: **Save** (ConfirmDialog: "A backup of the current version is kept; you can restore it from the history below."), **Discard changes**, **Download** (Blob of the current text, filename = file name); **Version history**: list from `listBackups` with time, kind, size, "Restore" (ConfirmDialog). No "Format" button — reformatting would change the file's layout. Read-only stores (catalog files) show facts only. |
| `SettingsTab.tsx` | Top card **Encryption key for saved secrets**: source explained — `env`: "Set by the server environment ✓"; `file`/`generated`: "Created automatically and stored at <key_path>. Fine for a laptop; on servers whose disk is replaced at deploy, set SETTINGS_ENCRYPTION_KEY instead."; `none`: problem text + how to fix. **Ignored variables** warning box when `ignored_present` non-empty. Then one section per category (in `categories` order): rows with Title, key in monospace, description (+ note), status (Set / Not set / Overridden in app, with previews), and actions by mode — `managed`: **Edit**/**Add**, **Remove override** (only if override set; ConfirmDialog "Go back to the server's value"), **Test** (if `testable`); `integration`: "Manage on Integrations →"; `deployment`: "Read-only — change in the server's .env and restart" (tooltip/expandable "Why?": "Changing sign-in, session or storage settings from a web page could lock everyone out."). A header button **Add a setting** opens a picker listing managed vars that are currently not set. A filter box (by key/title). |
| `EnvVarEditor.tsx` | Modal: title, key, description, note; input (`type="password"` with show/hide toggle for secrets; `<select>` when `options`); placeholder = `example`; inline validation mirroring `envmgr.Validate` (non-empty, single line, no surrounding spaces, ≥ 8 chars for secrets) — server remains the authority; "Applies immediately — no restart needed." Save → `setEnv` → show result + offer "Test now" if testable. Never pre-fill a secret's value (only show its preview as a hint). |
| `GuideTab.tsx` | Plain-English guide: what each status word means; what each tab is for; "When something is red" checklist; where settings live (Settings & Secrets vs Integrations vs .env); "Page looks wrong? Restart the backend"; link to `docs/SYSTEM_HEALTH.md` (show the repo path as text). |

`frontend/src/pages/AdminHealth.tsx` becomes a thin wrapper: `export { HealthPage as AdminHealth } from './health/HealthPage'` — or update the import in `App.tsx`; keep the route `/admin/health`.

### 4.4 Issue action dispatch (in `HealthPage.tsx`)
| `action.kind` | Behaviour |
|---|---|
| `test_engine` | switch to `engine` tab and start the test |
| `refresh_catalog` | switch to `catalog` tab and start the refresh |
| `edit_env` | switch to `settings` tab and open `EnvVarEditor` for `target` |
| `open_store` | switch to `data` tab and open `StoreEditor` for `target` |
| `open_tab` | switch to tab `target` (`overview|engine|catalog|jobs|data|settings|guide`) |
| `link` | `navigate(target)` |

### 4.5 Copy rules (non-negotiable — this is the user's main complaint)
- No jargon: never "liveness", "cardinality", "telemetry", "artifact", "round-trip", "probe" in UI text. Say "AI engine", "number of prices", "test", "price list database".
- Every section starts with one "What this is" sentence.
- Every warn/fail shows **what to do**. Every error message from the server is shown verbatim (it is already written for humans).
- Times are relative ("12 min ago") with the absolute time in a `title` tooltip.
- Never render a secret. Only `preview` strings from the API.
- Both light and dark themes must look right (use the `--fcp-*` tokens and the `dark:` variants already used in this file).

**Check:** `cd frontend && npm run build` (tsc + vite) — zero errors.

---

## Part 5 — Docs for colleagues: `docs/SYSTEM_HEALTH.md`
Sections (write for a new engineer, ~200–300 lines):
1. **What the page is for** — one paragraph.
2. **How status is decided** — issues → overall status; the severity rules; the issue table from 3.2 (copy it).
3. **Where the code lives** — backend files (system_health.go, engine_health.go, env_health.go, data_health.go, datastore_handlers.go, env_handlers.go, catalog_refresher.go, internal/envmgr, internal/db/runtime_env.go, settings_key.go), frontend `pages/health/*`.
4. **Runtime state files** — `<data dir>/health/engine_tests.json`, `<data dir>/settings-encryption.key`, `skill/.../data/catalog_refresh_status.json`, `catalog_refresh_history.json`, `data/_backups/` (all gitignored).
5. **How to add…** — a new check (component function + issue rule + test), a new env var (one registry entry; choose the mode carefully; why arbitrary vars are not allowed), a new data file (CheckDataStores + storeManifest + editableDatastores).
6. **API reference** — every endpoint from Parts 2–3 with request/response examples (secrets shown as `••••abcd`).
7. **Troubleshooting** — "page shows old/missing data → restart backend"; "catalog refresh fails → GOOGLE_CLOUD_API_KEY"; "secret can't be read → re-enter (key changed)"; "AGY_API_KEY does nothing → use ANTIGRAVITY_API_KEY".
8. **Security model** — admin-only endpoints, values never returned or logged, AES-GCM at rest, audit actions (`env_set`, `env_clear`, `env_test`, `engine_test`, `datastore_edit`, `datastore_restore`, `datastore_revert`).

---

## Part 6 — Verify end to end (do not skip; this is where the last attempt failed)

1. `go build ./... && go test ./...` — green (re-run the known flaky test alone if it trips).
2. `cd frontend && npm run build` — green.
3. `cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q` — green.
4. **Restart the backend** — the running server does not pick up Go changes. Find and stop whatever listens on 8080 (`netstat -ano | findstr :8080` → `Stop-Process -Id <pid>`), then start it from the repo root: `go run ./cmd/server`. Confirm `curl -s http://localhost:8080/api/admin/health` returns `{"error":...}` JSON (401), **not** HTML.
5. Open `http://localhost:5173/admin/health` (Vite dev server; start it with `npm run dev --prefix frontend` if not running). Sign in on localhost with an email listed in `ADMIN_EMAILS` and any password (DEV_AUTH_BYPASS is on locally) — or ask the user to sign in.
6. Walk every tab and confirm with screenshots:
   - Overview: no contradictions; with this machine's config expect an issue for `AGY_API_KEY` being ignored (info, since ANTIGRAVITY is set) and a catalog warning (stale and/or last refresh failed, with the "Add pricing API key" action because GOOGLE_CLOUD_API_KEY is absent).
   - AI Engine: key present = "from server .env"; press **Test AI engine** once; result appears in history and survives a page reload.
   - Settings: set `LOG_LEVEL` to `debug` → shows "Overridden in app"; audit trail shows `env_set` with no value; remove the override → back to server value. **Do not overwrite the real ANTIGRAVITY_API_KEY.** Only test `GOOGLE_CLOUD_API_KEY` if the user provides one.
   - Data Files: open `review-config.json`, change one number, save, then confirm on disk that **only that number changed** (`git diff skill/aws-gcp-cost-projection/data/review-config.json` shows a one-line diff, keys not reordered). Restore the backup from Version history and confirm `git diff` is empty again.
   - Stale-server banner: temporarily set `EXPECTED_HEALTH_SCHEMA = 99` in dev, see the banner, set it back.
   - Dark and light themes both readable.
7. Commit on `main` with a clear message + trailer, push to both personal remotes (see header). Report to the user: what changed, what you verified (with screenshots), anything not verified.

---

## Pitfalls (read twice)
- **Never re-encode user JSON** (no `MarshalIndent`, no `JSON.stringify(JSON.parse(x))` before saving). Validate, then write the original bytes.
- **Never log or return secret values.** Audit `details` for env actions is always empty. Use `X-Goog-Api-Key` header, not `?key=`.
- **Only registry variables can be set.** Don't add a free-text "any variable" form — it would let a typo or stolen session change how the server runs programs (PATH, PYTHONPATH…).
- **Status comes only from issues.** Don't add another status ladder anywhere (that's how the page contradicted itself).
- **Restart the backend after Go changes** before judging the UI.
- Windows: use `atomicWriteFile` (already handles rename-over-existing). Paths in the UI may contain backslashes — render them in monospace, don't parse them.
- Keep `/api/admin/catalog/status` (used by `AdminIntegrations.tsx`) and `/api/admin/catalog/refresh-status` (polling) working.
- Don't touch pricing/pipeline Python, mappers or `classify_mechanics`.
