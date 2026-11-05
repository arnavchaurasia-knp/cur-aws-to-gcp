# WORKORDER — System Health v3: fixes found in verification

**For:** a lower-tier implementation model, in a fresh conversation.
**Written:** 2026-11-05, after verifying `docs/WORKORDER_system_health_v3_console.md` end to end (builds, all tests, and a live browser walkthrough).
**Size:** small — 6 focused fixes, mostly 1–20 lines each. Do them in order.
**Branch / push:** commit on `main`; then `git push origin2 main` AND `git push github-personal main`. Never push `origin` (company repo). Never force-push. (Note: local `main` already has an unpushed commit `4654d7c` — your push will include it; that is expected.)
**Commit trailer:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Context (read first)
The System Health page (`/admin/health`) was rebuilt in v3. Verification showed it works: the status headline, "Needs attention" list, live AI test, Settings & Secrets, and byte-for-byte data-file saving all behave correctly. Six small problems remain. The most important one (Fix 1) breaks the v3 promise that **"every status on the page comes from the issues list, so nothing can contradict anything else."**

Key files:
- Backend: `internal/jobs/system_health.go` (health endpoint, issue builders `build*Issues`, `deriveOverallHealth`), `internal/jobs/catalog_refresher.go`, tests in `internal/jobs/system_health_test.go`.
- Frontend: `frontend/src/pages/health/EngineTab.tsx`, `frontend/src/pages/health/HealthPage.tsx`, `frontend/src/pages/health/StatusBadge.tsx`.

Status vocabulary (don't change): component/overall status is `ok | warn | fail | unknown`; issue severity is `critical | warning | info`.

**Do not** change any JSON field names (the frontend depends on them), bump `HealthSchemaVersion`, or touch pricing/pipeline code.

---

## Fix 1 — Component cards must take their status from their own issues (MOST IMPORTANT)

**Bug (seen live):** the Overview card **"Settings & Secrets" shows "Needs attention"**, but the "Needs attention" list contains nothing for Settings — only an *info* tip ("AGY_API_KEY is set but the app never reads it").
**Cause:** each component function computes its status with its own if/else ladder, separate from the issue builder. In `settingsHealth()` (`system_health.go` ~line 822–834) the ladder says "ignored vars present → `warn`", while `buildSettingsIssues` correctly rates that case as `info` when the right variable (`ANTIGRAVITY_API_KEY`) is already set. Two calculations → they disagree. The same pattern exists in `catalogHealth` (~480), `jobsHealth` (~644), `dataHealth` (~739) and `runtimeHealth` (~1004) — they happen to agree today, but can drift.

**Fix:** derive every component's status from that component's issues with one shared helper. Add to `system_health.go`:
```go
// componentStatus is the ONLY way a component's status is decided:
// any critical issue → fail, any warning → warn, otherwise ok — except a
// component that reports "unknown" (e.g. AI engine not verified yet) keeps
// "unknown" when it has no critical/warning issue.
func componentStatus(base string, issues []HealthIssue) string {
	status := "ok"
	for _, iss := range issues {
		switch iss.Severity {
		case "critical":
			return "fail"
		case "warning":
			status = "warn"
		}
	}
	if status == "ok" && base == "unknown" {
		return "unknown"
	}
	return status
}
```
Then in **each** of `engineHealth`, `catalogHealth`, `jobsHealth`, `dataHealth`, `settingsHealth`, `runtimeHealth`: build the issues first, then set `X.Status = componentStatus(X.Status, issues)` right before returning. Keep the existing ladders only for choosing the **summary sentence**, not the status.

Also fix the Settings summary so it doesn't sound like a problem when there is only a tip. In `settingsHealth()` replace the ladder's ignored-vars branch:
- remove `len(ignored) > 0` from the `warn` condition;
- after the ladder, if status is still `"ok"` and `len(ignored) > 0`, set `summary = fmt.Sprintf("Settings are fine. %d unused variable(s) found — see tips.", len(ignored))`.

**Engine note:** `engineHealth` already sets `fail` from `InspectEngine` and `warn/fail` from the last test, and those produce matching issues (`engine.cli_missing`, `engine.key_missing`, `engine.test_failed`, `engine.slow`), so applying `componentStatus` there must not change today's results — the test below proves it.

**Tests** (`system_health_test.go`):
- `TestComponentStatus`: table — no issues + base "ok" → ok; no issues + base "unknown" → unknown; one info → ok; one warning → warn; warning + critical → fail; info + base "unknown" → unknown.
- `TestSettingsHealth_IgnoredVarOnlyIsOK`: with `t.Setenv("AGY_API_KEY","x")` and `t.Setenv("ANTIGRAVITY_API_KEY","y")`, call the settings path (or `buildSettingsIssues` + `componentStatus`) and assert status `ok` and the issue severity `info`.

---

## Fix 2 — "Test Failed" shown for a test that succeeded slowly

**Bug (seen live):** Gemini answered in 26.3 s → backend state `warn` ("The model answered, but slowly"). The result box in the AI Engine tab says **"Test Failed"** in a red box.
**Where:** `frontend/src/pages/health/EngineTab.tsx` ~lines 165–176.
**Fix:** map the label and colour by state:
| `testResult.state` | Label | Box colour classes |
|---|---|---|
| `ok` | `Test passed` | the existing emerald classes |
| `warn` | `Answered, but slowly` | amber: `bg-amber-50 dark:bg-amber-950/30 border-amber-200 text-amber-900 dark:text-amber-200` |
| `unknown` | `Test not run` | gray: `bg-gray-50 dark:bg-gray-800 border-[var(--fcp-border-subtle)] text-[color:var(--fcp-text-secondary)]` |
| `fail` / anything else | `Test failed` | the existing rose classes |

Implement as a small lookup object in the component, not nested ternaries. Also format latency as seconds: `(${(testResult.latency_ms/1000).toFixed(1)}s)` and only when `latency_ms > 0`. Apply the same seconds formatting in the Test History table's Latency column.

---

## Fix 3 — Catalog refresh failure shows a raw exit code

**Bug (seen live):** issue detail reads *"exit status 1 No GOOGLE_CLOUD_API_KEY is set — that is the usual cause."* — raw `exit status 1`, no punctuation between the two parts.
**Causes:**
1. `catalog_refresher.go` `runCatalogRefreshNow` stores `Reason: err.Error()` (→ `"exit status 1"`) for non-timeout failures.
2. `buildCatalogIssues` (`system_health.go` ~550–557) concatenates that with a space.

**Fix A — better reason at the source** (`catalog_refresher.go`, the `if err != nil` branch, non-timeout case): set
```go
reason = fmt.Sprintf("The refresh script stopped with an error (exit code %d).", exitCode)
if msg := lastMeaningfulLine(logTail); msg != "" {
	reason += " Last message: " + msg
}
```
and add:
```go
// lastMeaningfulLine returns the last log line that is not a separator
// (=====, -----) or blank, trimmed to 200 characters.
func lastMeaningfulLine(lines []string) string {
	for i := len(lines) - 1; i >= 0; i-- {
		l := strings.TrimSpace(lines[i])
		if l == "" || strings.Trim(l, "=-") == "" {
			continue
		}
		if len(l) > 200 {
			l = l[:200] + "…"
		}
		return l
	}
	return ""
}
```
(Ensure `fmt` and `strings` are imported.) For this machine the reason becomes: *"The refresh script stopped with an error (exit code 1). Last message: No GCP credentials (set GOOGLE_CLOUD_API_KEY). Nothing fetched."*

**Fix B — clear issue text** (`buildCatalogIssues`): replace the detail-building block with:
```go
detail := cat.LastRun.Reason
if !cat.APIKeyPresent {
	detail = "Prices couldn't be downloaded because no Google Cloud pricing API key is set."
} else if detail == "" {
	detail = "The refresh stopped with an error. Open the Pricing Catalog tab to see the log."
}
```
And set `Fix` per case: no key → `"Add GOOGLE_CLOUD_API_KEY in Settings & Secrets, then refresh the catalog."`; key present → `"Open the Pricing Catalog tab, read the log of the last run, then refresh again."` (actions stay as they are).

**Test:** `TestLastMeaningfulLine` (separators/blank skipped, long line trimmed) and extend `TestSystemHealth_StaleCatalog_ActionPointsToAPIKeyWhenMissing` (or add a test) asserting the refresh-failed detail with no key contains `"no Google Cloud pricing API key"` and does **not** contain `"exit status"`.

Old history entries keep their old reason text — that's fine; don't rewrite history files.

---

## Fix 4 — Headline shows a tip's title when everything is fine

**Bug:** when the only issues are *info* tips, overall status is `ok` but `deriveOverallHealth` (`system_health.go` ~1046) sets `summary = issues[0].Title`, e.g. headline "Everything is working" with the sentence "The AI engine hasn't been verified recently" underneath.
**Fix:** in `deriveOverallHealth`, after computing status:
```go
if status == "ok" {
	tips := len(issues)
	if tips == 0 {
		return status, "Everything is working."
	}
	return status, fmt.Sprintf("Everything is working. %d tip(s) below.", tips)
}
```
Otherwise keep the existing behaviour (first issue's title + "(and N more)" over non-info issues). Since issues are sorted critical → warning → info, `issues[0]` is never an info item when status isn't ok.
**Test:** update/add `TestDeriveOverallHealth`: only info issues → status ok, summary starts with "Everything is working."; one warning + one info → summary equals the warning's title (no "(and N more)").

---

## Fix 5 — Leftover technical wording in the UI

1. `frontend/src/pages/health/EngineTab.tsx` ~line 63 prints `(source: {engine.model_source})` → raw values like `report_settings`. Replace with a mapping:
   - `report_settings` → `set in Report Settings`
   - `env` → `from the AGY_MODEL server setting`
   - `default` → `built-in default`
   - anything else → `{engine.model_source}`
2. `frontend/src/pages/health/HealthPage.tsx` line 221: `Loading system health telemetry...` → `Checking the system…`
3. Search the whole `frontend/src/pages/health/` folder (case-insensitive) for these words in user-visible strings and reword in plain English: `telemetry`, `liveness`, `cardinality`, `probe`, `artifact`, `round-trip`. Variable/field names in code are fine; only change text a user sees (JSX text, labels, titles, placeholders). Example: "Live test probe is turned off" → "Live test is turned off".

---

## Fix 6 — Add a check for "any Google account can sign in" (recommended)

**Why:** the local `.env` has `ALLOWED_DOMAINS=*` with `ALLOW_ANY_DOMAIN=true` (the server logs *"ALLOWED_DOMAINS=* is active — any Google account can authenticate"*). Fine on a laptop, dangerous on a shared server — and the health page doesn't mention it today.
**Fix:** in `buildSettingsIssues` add (pass `allowedDomains := os.Getenv("ALLOWED_DOMAINS")` in from `settingsHealth`, like `appBaseURL` is passed today, so the builder stays pure):
- Condition: any comma-separated entry of `ALLOWED_DOMAINS`, trimmed, equals `*`.
- If `APP_BASE_URL` host is `localhost`/`127.0.0.1` (reuse the same local-host test the `settings.dev_bypass` rule uses) → severity `info`; otherwise `critical`.
- `ID: "settings.any_domain"`, `Area: "settings"`, `Title: "Any Google account can sign in"`, `Detail: "ALLOWED_DOMAINS is set to * , so sign-in is not limited to your company's domain."`, `Fix: "Set ALLOWED_DOMAINS to your domain (e.g. facets.cloud) in the server's .env and restart."`, no action.
**Test:** table test — `*` + `http://localhost:8080` → info; `*` + `https://app.example.com` → critical; `facets.cloud` → no issue.

---

## Verify (all must pass before committing)
1. `go build ./... && go vet ./internal/jobs/ && go test ./internal/... ./cmd/server/ -count=1`
   - Known pre-existing Windows flake: `TestPhase4_IsolationAndPreferencesAndQuotas` can fail during temp-dir cleanup ("being used by another process"). Re-run it alone: `go test ./internal/jobs/ -run TestPhase4 -count=1`. Not your bug. Also note `internal/jobs/phase4_isolation_quota_test.go` already has uncommitted edits from someone else — don't revert or include them unless they are yours.
2. `cd frontend && npm run build` — zero TypeScript errors.
3. **Restart the backend** (the running one won't have your Go changes): stop whatever listens on :8080 (`netstat -ano | findstr :8080`, then `Stop-Process -Id <pid> -Force` in PowerShell) and run `go run ./cmd/server` from the repo root. Confirm `curl -s http://localhost:8080/api/admin/health` returns JSON (`{"error":...}` 401), not HTML.
4. In the browser (`http://localhost:5173/admin/health`, sign in as an admin — on localhost DEV_AUTH_BYPASS accepts any password for an `ADMIN_EMAILS` address, or ask the user to sign in), confirm:
   - Overview: the **Settings & Secrets card is "Healthy"** when its only item is the AGY_API_KEY tip; every card's badge matches what the "Needs attention" list shows for that area.
   - The catalog refresh-failed item reads in plain English with no "exit status".
   - AI Engine tab: model source reads "set in Report Settings"; run one test — a slow success shows the amber "Answered, but slowly", not "Test failed".
   - With only tips present (e.g. after a fast successful engine test and if the catalog is fresh), the headline sentence is "Everything is working. N tip(s) below." If the catalog is still stale on this machine, just verify Fix 4 via its unit test.
   - The "Any Google account can sign in" item appears as a tip (info) on localhost.
5. Commit (message e.g. `fix(health): derive card status from issues, clearer engine/catalog wording, any-domain check`) + trailer; push to both personal remotes. Report what you verified, with screenshots, and anything you couldn't verify.

## Don'ts
- Don't add another place that computes a status — always go through `componentStatus` / `deriveOverallHealth`.
- Don't log or display secret values.
- Don't rename JSON fields or bump the schema version.
- Don't touch the data-file save path (it is correct and verified byte-for-byte).
