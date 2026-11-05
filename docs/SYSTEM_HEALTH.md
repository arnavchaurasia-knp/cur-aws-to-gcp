# System Health & Diagnostic Console (v3)

## 1. What the Page is For
The System Health console (`/admin/health`) is the central operational control panel for the AWS-to-GCP Cost Projection engine. It allows engineers and operators to monitor engine readiness, inspect GCP pricing catalog vintage, track queue throughput, edit order-sensitive mapping files with byte-level preservation, and configure environment variables and secrets safely in-app without server restarts or key leakage.

---

## 2. How Status is Decided
System status is derived strictly from a unified list of detected issues (`HealthIssue`). There are no independent status ladders or conflicting card evaluations:

- **Overall Status:**
  - `fail` ("Not working") if any issue has severity `critical`.
  - `warn` ("Needs attention") if no critical issues exist, but one or more issues have severity `warning`.
  - `ok` ("Healthy") when only `info` tips or no issues are detected.
- **Summary Line:**
  - When issues exist, the title of the first issue is displayed (e.g. *"The AI engine API key is missing"*), followed by `(and N more)` if multiple non-info issues are active.
  - When zero issues exist, the summary displays *"Everything is working."*

### Unified Issue Rules

| ID | Condition | Severity | Title | Fix | Action |
|---|---|---|---|---|---|
| `engine.cli_missing` | `!p.BinaryFound` | critical | The AI engine tool (agy) isn't installed or can't be found | Install agy on the server, or set its full path in Settings & Secrets → agy tool location. | `edit_env AGY_PATH` |
| `engine.key_missing` | CLI found, `!p.KeyPresent` | critical | The AI engine API key is missing | Add ANTIGRAVITY_API_KEY in Settings & Secrets. Every projection job fails without it. | `edit_env ANTIGRAVITY_API_KEY` |
| `engine.test_failed` | Last test < 24h and failed | critical | The last AI engine test failed | Check the key and network, then run the test again. | `test_engine` |
| `engine.slow` | Last test < 24h and warning | warning | The AI engine is responding slowly | Jobs will take longer; usually temporary. Re-test later. | `test_engine` |
| `engine.unverified` | Status unknown and not disabled | info | The AI engine hasn't been verified recently | Run a quick test (uses a tiny amount of quota). | `test_engine` |
| `catalog.artifact` | `catalog.duckdb` missing/broken | critical | The pricing catalog database is missing or broken | Projections can't price anything. Run a catalog refresh. | `refresh_catalog` |
| `catalog.stale` | Price age > stale threshold | warning | GCP prices are N days old (refresh after M days) | Refresh the catalog. Needs the Google Cloud pricing API key. | `refresh_catalog` |
| `catalog.refresh_failed`| Last refresh run failed | warning | The last catalog refresh failed | Check the pricing API key and network connectivity. | `refresh_catalog` or `edit_env` |
| `catalog.source` | Incomplete source JSONs | warning | N price files are incomplete | Refresh the catalog to re-download them. | `refresh_catalog` |
| `catalog.image_baked` | Skill dir inside `/app` | info | Catalog is built into the server image | It refreshes on each deploy, not from this page. | none |
| `jobs.halted` | `STOP_ALL` active | critical | All job processing is paused (STOP ALL is on) | New uploads won't run until it's resumed. | `link /admin` |
| `jobs.failing` | Last window >= 4 & failure > 25%| critical | N of the last M jobs failed | Open the failures below to see the error. | `open_tab jobs` |
| `jobs.some_failing` | Last window >= 10 & failure > 5% | warning | N of the last M jobs failed | Open the failures below to see the error. | `open_tab jobs` |
| `data.<key>` | Store integrity `fail` | critical | "<Title>" is missing or unreadable | Restore a backup or redeploy the file. | `open_store <key>` |
| `data.<key>` | Store integrity `warn` | warning | "<Title>" needs a look | Check configuration and syntax. | `open_store <key>` |
| `settings.missing.<KEY>`| Required managed var unset | critical | "<Title>" is not set | Set this variable in Settings & Secrets. | `edit_env <KEY>` |
| `settings.load_error.<KEY>`| Undecryptable saved value | warning | The saved "<Title>" can't be read | Re-enter it (the encryption key changed). | `edit_env <KEY>` |
| `settings.ignored.<KEY>`| Ignored env var present | info / warning | <KEY> is set but the app never reads it | Use <UseInstead> instead, then remove <KEY> from .env. | `edit_env <UseInstead>` |
| `settings.encryption_none`| No key available | warning | Secrets can't be saved in the app | Set SETTINGS_ENCRYPTION_KEY in .env and restart. | `open_tab settings` |
| `settings.encryption_on_disk`| Auto-generated file on HTTPS server | warning | Saved secrets depend on a key file on this server's disk | Set SETTINGS_ENCRYPTION_KEY in the deployment configuration. | `open_tab settings` |
| `settings.dev_bypass` | Bypass active on non-localhost | critical | Anyone can sign in with any password | Remove DEV_AUTH_BYPASS from the deployment and restart. | none |
| `settings.notifications_off`| Email & Slack unconfigured | info | Job notifications are off | Set up email or Slack on the Integrations page. | `link /admin/integrations` |
| `runtime.<key>` | Host tool/disk fail or warn | critical / warning | "<Title>" — <Detail> | Refer to specific dependency fix instructions. | none |

---

## 3. Where the Code Lives

### Backend Architecture
- `internal/jobs/system_health.go`: Single honest `/api/admin/health` endpoint, `HealthResponse` schema v3, and pure issue-builder logic.
- `internal/jobs/engine_health.go`: Inspects `agy` CLI binary, version caching (5 min), and live prompt probes with history recording.
- `internal/jobs/env_health.go`: Host dependency checks (Python 3, DuckDB binary, disk space, folder permissions).
- `internal/jobs/data_health.go`: Monitored store definitions (`storeManifest`) and filesystem integrity checks.
- `internal/jobs/datastore_handlers.go`: Raw byte-for-byte read/write handlers (`AdminGetDataStore`, `AdminPutDataStore`, `AdminListDataStoreBackups`, `AdminRestoreDataStoreBackup`, `AdminRevertDataStore`).
- `internal/jobs/env_handlers.go`: Managed environment variable handlers (`AdminListEnv`, `AdminSetEnv`, `AdminClearEnv`, `AdminTestEnv`).
- `internal/jobs/catalog_refresher.go`: Pricing catalog refresh lifecycle, history tracking, and stale detection.
- `internal/envmgr/`: The central registry of every environment variable the app recognizes, validation, baseline capture, and redaction logic.
- `internal/db/runtime_env.go`: Encrypted database storage for runtime settings overrides.
- `internal/db/settings_key.go`: Automatic provisioning and validation of the 256-bit AES-GCM encryption key.

### Frontend Architecture
- `frontend/src/api/health.ts`: TypeScript contracts, schema v3 verification constant (`EXPECTED_HEALTH_SCHEMA = 3`), and typed API clients.
- `frontend/src/api/http.ts`: Error message extraction avoiding raw JSON leakage; typed `sendJSON` client.
- `frontend/src/pages/health/`:
  - `HealthPage.tsx`: Tab navigation coordinator (`?tab=`), auto-refresh interval (30s), action dispatcher.
  - `StatusHero.tsx`: High-visibility health banner and server build indicator.
  - `StatusBadge.tsx`: Uniform status badges (`Healthy`, `Needs attention`, `Not working`, `Not checked yet`).
  - `IssuesList.tsx`: Actionable issue list with expandable details and fix actions.
  - `OverviewTab.tsx`: 6 summary cards linking directly into subsystem tabs.
  - `EngineTab.tsx`: Model info, `agy` path, live prompt testing, and test history.
  - `CatalogTab.tsx`: Pricing catalog status, vintage metrics, and live refresh runner with polling.
  - `JobsTab.tsx`: Queue metrics, concurrency limits, and expandable recent failure cards.
  - `DataFilesTab.tsx`: Monitored data files table.
  - `StoreEditor.tsx`: Raw-byte Monaco/textarea editor with live JSON validation and backup restoration.
  - `SettingsTab.tsx`: Grouped settings, encryption diagnostics, in-app overrides, and quick-test buttons.
  - `EnvVarEditor.tsx`: Modal editor for individual variables with client-side validation.
  - `GuideTab.tsx`: Colleague reference guide and checklists.

---

## 4. Runtime State Files
The following files are maintained in the data directory and are git-ignored:
- `<dataDir>/health/engine_tests.json`: Rolling history of the last 20 AI engine live tests.
- `<dataDir>/settings-encryption.key`: 32-byte auto-generated encryption key used when `SETTINGS_ENCRYPTION_KEY` is not provided in `.env`.
- `<skillDir>/data/catalog_refresh_status.json`: State of the active or latest catalog refresh operation.
- `<skillDir>/data/catalog_refresh_history.json`: Rolling history of the last 15 catalog refresh runs.
- `<skillDir>/data/_backups/`: Automatic timestamped backups of edited datastore files (`<key>.<ts>.json`, `<key>.<ts>.pre-revert.json`, `<key>.<ts>.pre-restore.json`).

---

## 5. How to Add New Functionality

### Adding a New Health Check
1. In `system_health.go`, add your check to the relevant component builder (e.g. `engineHealth`, `catalogHealth`, etc.).
2. Define a corresponding `HealthIssue` rule with a unique ID, plain-English title, explanation, and an `IssueAction` if an in-app fix is possible.
3. Add a unit test case in `system_health_test.go` asserting that your check triggers the expected issue and overall status.

### Adding a New Environment Variable
1. In `internal/envmgr/registry.go`, add an entry to the `knownVars` list with `Title`, `Category`, `Description`, `Mode`, and `Secret`.
2. Choose the `Mode` carefully:
   - `ModeManaged`: Safe operational variables that admins may change at runtime (e.g. log levels, API keys).
   - `ModeIntegration`: Notification credentials configured via the Integrations page.
   - `ModeDeployment`: Security-critical settings (e.g. `SESSION_SECRET`, `PORT`, `DATA_DIR`) that can only be set in `.env` to prevent administrator lockouts.
3. If the variable supports live testing, implement the test probe in `internal/jobs/env_handlers.go` (`AdminTestEnv`).

### Adding a New Monitored Data File
1. In `internal/jobs/data_health.go`, add an entry to `storeManifest` describing the file.
2. In `CheckDataStores()`, add the filesystem check verifying file presence and readability.
3. If the file is user-editable, add its key and filename to `editableDatastores` in `internal/jobs/datastore_handlers.go`.

---

## 6. API Reference

All endpoints require an authenticated admin session (`adminOnly`).

### `GET /api/admin/health`
Returns the unified health response.
```json
{
  "schema_version": 3,
  "checked_at": "2026-11-05T17:00:00Z",
  "status": "ok",
  "summary": "Everything is working.",
  "issues": [],
  "engine": {
    "status": "ok",
    "model": "gemini-3.8-flash",
    "cli_found": true
  },
  "server": {
    "schema_version": 3,
    "build_sha": "a1b2c3d4",
    "uptime_sec": 3600
  }
}
```

### `GET /api/admin/env`
Lists all recognized environment variables and their effective values.
```json
{
  "encryption": {
    "available": true,
    "source": "file",
    "key_path": "/path/to/settings-encryption.key"
  },
  "categories": ["AI Engine", "Pricing Catalog", "Logging & Debugging"],
  "vars": [
    {
      "key": "ANTIGRAVITY_API_KEY",
      "title": "AI Engine API Key",
      "secret": true,
      "mode": "managed",
      "effective": {
        "set": true,
        "preview": "••••1234",
        "source": "server"
      }
    }
  ],
  "ignored_present": []
}
```

### `PUT /api/admin/env/{key}`
Sets an in-app override for a managed variable. Body: `{"value": "..."}`.

### `DELETE /api/admin/env/{key}`
Removes an in-app override, reverting to the server's environment baseline.

### `POST /api/admin/env/{key}/test`
Executes an on-demand connectivity test for `ANTIGRAVITY_API_KEY`, `AGY_PATH`, or `GOOGLE_CLOUD_API_KEY`.

### `GET /api/admin/datastores/{key}`
Returns the raw content and metadata for a datastore file.

### `PUT /api/admin/datastores/{key}`
Saves updated content byte-for-byte. A backup of the prior version is stored automatically.

### `GET /api/admin/datastores/{key}/backups`
Returns all available backups for the given datastore.

### `POST /api/admin/datastores/{key}/restore`
Restores a specific backup file by name. Body: `{"name": "service_map.2026-11-05T12-00-00Z.json"}`.

---

## 7. Troubleshooting

- **Page shows outdated data or schema mismatch:**
  Restart the backend server (`go run ./cmd/server`). The backend binary must be rebuilt to reflect schema changes.
- **Catalog refresh fails immediately:**
  Verify that `GOOGLE_CLOUD_API_KEY` is set in Settings & Secrets or `.env`. Google Cloud Pricing API requires a valid API key.
- **Saved secret cannot be decrypted:**
  The encryption key changed since the secret was stored. Re-enter the secret value in Settings & Secrets.
- **`AGY_API_KEY` is set but engine reports missing key:**
  The application only reads `ANTIGRAVITY_API_KEY`. Add your key as `ANTIGRAVITY_API_KEY` in Settings & Secrets.

---

## 8. Security Model
- **Strict Role-Based Access:** All endpoints require an admin role checked via `h.adminOnly`.
- **Zero Secret Exposure:** Raw secrets are never returned in JSON responses or written to log files. Only sanitized previews (e.g. `••••1234`) are rendered.
- **Header-Based Secret Transmission:** Outbound API tests (e.g. Google Cloud Pricing) pass keys via HTTP headers (`X-Goog-Api-Key`) rather than query parameters, preventing URL leakage in error dumps.
- **AES-GCM Encryption at Rest:** In-app secrets are encrypted with standard AES-256-GCM before persisting to SQLite.
- **Comprehensive Audit Trail:** All administrative operations log an audit record (`env_set`, `env_clear`, `env_test`, `engine_test`, `datastore_edit`, `datastore_restore`, `datastore_revert`) with sanitized details.
