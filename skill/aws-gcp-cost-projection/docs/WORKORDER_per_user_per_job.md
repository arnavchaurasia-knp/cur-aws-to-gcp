# WORK ORDER — Per-user, per-job: logs, engine, access, exports

**You are implementing this and you have never seen this codebase before.**
**Read sections 0, 1 and 2 completely before you touch any file.**

This is a large work order. It is split into **5 phases**. Each phase leaves
the system working and is independently testable.

> **Do one phase. Verify it. Commit it. Then start the next.**
> Do not begin a phase before the previous one's verification passes.

---

## 0. Orientation — what this system is

A web app that takes an AWS bill and estimates what the same workload would
cost on Google Cloud.

| part | language | where |
|---|---|---|
| web server | Go | `internal/`, `cmd/server/` |
| pipeline | Python | `skill/aws-gcp-cost-projection/scripts/` |
| web UI | React + TypeScript | `frontend/src/` |

A **job** is one bill being processed. Each job gets a directory on disk. The
Go server writes config files into it, then runs Python scripts with that
directory as the working directory. The pipeline calls an LLM (the binary is
called `agy`) for the parts it cannot decide deterministically.

### Vocabulary used in this document

- **owner** — the user whose email created the job (`jobs.owner`)
- **admin** — a user who passes `Session.IsAdmin()`
- **facets employee** — an email ending `@facets.cloud` / `@facets.io`
- **engine** — the LLM model the pipeline uses (today: one global default)
- **job directory** — on-disk working area; **it is deleted on redeploy**

---

## 1. What is actually wrong — the audit

I checked each complaint against the code. **Two are exactly right, one is a
product change, and one is already built.** Read this section before planning:
building something that already exists is the most expensive mistake available
here.

### 1.1 CONFIRMED — logs are not per-user or per-job

`FacetsLogs` (`internal/jobs/facets.go:92`) begins:

```go
if !requireFacetsEmployee(w, r) { return }
```

Three consequences:

1. **Any @facets employee can read any job's log**, including jobs they do not
   own. There is no ownership check.
2. **A normal user cannot read their own job's log at all.** Not one line.
3. It serves **only `agy.log`**, the last 200 lines, with no filtering, no
   phases, no levels, and nothing from `debug.log` or `diagnostics.jsonl`.

So "logs are not per user per job" is correct and is Phase 1.

### 1.2 CONFIRMED — the engine is global, not per job

`SpawnerConfig.AGYModel` (`internal/jobs/spawner.go:18`) is **one value for the
whole server**, defaulting to `config.DefaultAGYModel`. It is:

- not selectable per job
- not chosen by the user
- **not recorded against the job**

That last one is the real damage. If you change the model, every past
projection silently loses its provenance — you cannot tell which engine
produced which number, so you cannot explain why last month's estimate differs
from this month's. That is Phase 2.

### 1.3 PRODUCT CHANGE — admin is restricted to @facets accounts

`Session.IsAdmin()` begins:

```go
// Admin is strictly restricted to @facets accounts.
if !s.IsFacetsEmployee() { return false }
```

This is deliberate and currently correct. **The requested change is that a
facets admin should be able to grant admin to a non-facets user.** That is a
legitimate product decision, but it removes a safety rail, so Phase 3 replaces
the blanket rule with an explicit, audited, non-escalating grant rather than
simply deleting the check.

### 1.4 ALREADY BUILT — CSV and Excel export exist

**Do not build this.** It is there:

- API: `GET /api/jobs/{id}/download?format=csv` and `?format=xlsx`
  (`internal/jobs/handler.go`, `case "xlsx", "csv":`)
- Implementation: `skill/aws-gcp-cost-projection/scripts/export_report.py`,
  which emits the service table, an instance sheet and a discounts sheet
- UI: `frontend/src/pages/JobStatus.tsx` lines **542** and **547** already
  render both links

If a user cannot find it, the problem is **discoverability**, not absence.
Phase 5 improves that and adds the formats that genuinely are missing.
**Open those lines and look before changing anything.**

### 1.5 VERIFIED SOUND — per-job access control

`canAccess(sess, job)` returns `job.Owner == sess.Email || sess.IsAdmin(...)`
and is called from **12 handlers**. Job-level access is correctly enforced.
**Do not rewrite it.** Phase 1 extends it to logs; it does not replace it.

---

## 2. THE RULES — read twice

### Rule 1 — "Not yours" and "does not exist" look identical
Every access failure returns **404 with the same body**, never 403 with a
different message. A different response for "exists but not yours" tells an
attacker which job IDs are real.

The one exception is an explicitly shared report link, which is public by
design.

### Rule 2 — Logs can contain customer data
Job logs quote bill contents: account IDs, resource ARNs, spend. Treat them
like the bill itself. Never widen who can read them, never put them in an
email or a notification body, never log them to the server's own stdout.

### Rule 3 — The job directory is not durable
It is deleted on every redeploy. Anything that must survive goes in the
database. A screen reading only from disk shows "nothing wrong" for every old
job, which is a lie. When data is gone, **say it is gone** — do not render that
as "clean".

### Rule 4 — Never break an existing projection's numbers
Nothing in this work order changes how a cost is calculated. If you find
yourself editing a SQL sum, a rate, or a mapping, stop — you are outside this
work order.

### Rule 5 — Default closed
Every new setting's default is the safe value. A new permission defaults to
off. A new log stream defaults to the narrowest audience.

### Rule 6 — Nobody can escalate themselves
No endpoint may grant a permission to the caller. Granting admin to yourself,
or to someone who can then grant it back to you, must be impossible.

---

# PHASE 1 — Per-user, per-job logs

**Goal: a user can read the logs of their own jobs, and only their own. An
admin can read any. The view is genuinely useful, not 200 raw lines.**

## 1.1 Replace the audience check

In `internal/jobs/facets.go`, `FacetsLogs` currently checks
`requireFacetsEmployee`. Replace that with the **same ownership check every
other job endpoint uses**:

```go
sess := auth.SessionFromCtx(r.Context())
job, err := h.db.GetJob(id)
if err != nil || job == nil || !h.canAccess(sess, job) {
    http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
    return
}
```

Note the ordering: validate the id, load the job, check access, **then** touch
the filesystem. Never read a file path built from a parameter you have not
authorised.

Keep the existing `isValidJobID` check and the `filepath.Clean` + prefix guard
— those prevent path traversal and are correct.

**Move the route.** `/api/facets/jobs/{id}/logs` is now wrong: it is no longer
a facets-only panel. Register it as `/api/jobs/{id}/logs` beside the other
per-job routes. Keep the old path working as an alias for one release so
nothing breaks, with a comment saying when to delete it.

## 1.2 Serve more than one stream

Today only `agy.log` is exposed. A job directory holds several useful files:

| stream | file | what it is |
|---|---|---|
| `engine` | `agy.log` | the LLM's own output |
| `pipeline` | `projection-audit/debug.log` | every Python script's DEBUG log |
| `diagnostics` | `projection-audit/diagnostics.jsonl` | structured findings (level, code, message, rows, usd) |

Accept `?stream=engine|pipeline|diagnostics`, defaulting to `pipeline`.
Return `{"available": false}` with **HTTP 200** for a stream whose file is
absent — never 404, so the UI can distinguish "no such stream" from "request
failed" (Rule 3).

## 1.3 Make the pipeline log actually readable

Raw tail is not a feature. Parse `debug.log` lines into objects. The format is
set by `scripts/log_utils.py` — **open that file and read the formatter before
writing a parser**, rather than guessing the layout.

Return per line: timestamp, level, logger/script name, message. Support:

- `?level=debug|info|warning|error` — minimum level, default `info`
- `?script=ingest.py` — filter to one script
- `?q=<substring>` — case-insensitive text search
- `?limit=` (default 200, max 2000) and `?offset=` for paging

**A line your parser does not understand is still returned**, as level
`unknown` with the raw text. Never drop a line because it did not match — the
unparseable line is often the interesting one.

## 1.4 Keep a summary after the directory dies

Job directories vanish on redeploy (Rule 3). At job completion, store a
compact digest in the database so history stays meaningful:

```sql
CREATE TABLE IF NOT EXISTS job_log_digest (
    job_id        TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    error_count   INTEGER NOT NULL DEFAULT 0,
    warn_count    INTEGER NOT NULL DEFAULT 0,
    first_error   TEXT,
    last_lines    TEXT,          -- last ~50 lines, for post-mortem
    recorded_at   DATETIME DEFAULT (datetime('now')),
    PRIMARY KEY (job_id, run_id)
);
```

Write it where the job is marked complete — `internal/jobs/watcher.go`, near
where the report's presence decides success (search `resolveReportPath`).
**If the files are missing, log one line and carry on.** A job must never fail
because its logs could not be summarised.

When the directory is gone, serve the digest and set
`"source": "digest", "available": true, "truncated": true` so the UI can say
*"full logs expired; showing the saved summary"*.

## 1.5 The UI

On the job page, a **Logs** tab for the owner (and admins):

- stream selector: Pipeline / Engine / Diagnostics
- level filter, script filter, search box
- auto-refresh every 5s **while the job is running**, off when it is finished
- a **Copy** button and a **Download full log** link
- when showing a digest: a clear banner saying so

Keep the existing facets panel working — it just uses the new endpoint now.

## 1.6 Phase 1 tests

1. owner reads own job's logs → 200
2. **non-owner, non-admin reads another user's logs → 404, identical body to a
   nonexistent job id** (Rule 1)
3. admin reads any job's logs → 200
4. a facets employee who is *not* owner and *not* admin → **404** (this is the
   behaviour change; assert it explicitly)
5. `?stream=diagnostics` returns parsed records; a missing file returns
   `available:false` with status 200
6. `?level=error` returns only errors; an unparseable line still appears with
   level `unknown`
7. after the job directory is deleted, the endpoint still returns the digest
   with `source: "digest"`

---

# PHASE 2 — Engine as a first-class, per-job value

**Goal: every job records which engine produced it; an admin chooses the
default and which engines are allowed; the value is visible in the report.**

## 2.1 Record it (do this first, on its own)

Even before anyone can choose, **start recording what was used**. This is the
highest-value half-hour in this work order: without it, no past projection can
be explained.

Add to the `jobs` table:

```sql
ALTER TABLE jobs ADD COLUMN engine TEXT;          -- model alias actually used
ALTER TABLE jobs ADD COLUMN engine_version TEXT;  -- agy/binary version if available
```

Follow the existing migration pattern in `internal/db/db.go` — the file already
uses `ALTER TABLE ... ADD COLUMN` guarded against "duplicate column" errors.
**Copy that pattern; do not invent a migration system.**

Populate both in the spawner at the moment the engine is chosen
(`internal/jobs/spawner.go`, where `model` is resolved). Write them to the job
row before the pipeline starts.

Show the engine in: the job detail page, the report footer (beside the existing
settings version), and the diagnostics summary.

## 2.2 Make it configurable by an admin

Reuse the **report-settings pattern**, which already exists and works
end-to-end. Open these and copy them:

- `internal/db/report_settings.go` — table, get/update/history/version
- `internal/jobs/admin_handlers.go` — `AdminGetReportSettings` and friends
- `frontend/src/pages/AdminReportSettings.tsx` — the page

Add an `engine_settings` concept with two values:

| key | type | meaning |
|---|---|---|
| `default_engine` | enum | the model used when the user does not choose |
| `allowed_engines` | string | comma-separated list the user may pick from |

**Validate on write**: `default_engine` must be in `allowed_engines`. Reject
the change otherwise, with a message saying so. An admin must not be able to
set a default nobody is allowed to use.

## 2.3 Let the user choose per job

On the upload page, an **Engine** dropdown populated from `allowed_engines`,
defaulting to `default_engine`. Pass it with the upload. The server must
**re-validate** against `allowed_engines` — never trust the browser (Rule 4 of
§2 applies: the browser is not an authority).

If the submitted engine is not allowed, **fail the upload with a clear
message**. Do not silently substitute the default: a user who asked for one
engine and got another, with no notice, cannot interpret their own results.

## 2.4 Phase 2 tests

1. a completed job has non-empty `engine`
2. the report footer shows the engine
3. setting `default_engine` to a value outside `allowed_engines` is rejected
4. an upload naming a disallowed engine is rejected with a clear error, and no
   job is created
5. an upload with no engine uses `default_engine`
6. two jobs run with different engines record different values

---

# PHASE 3 — A facets admin can grant admin to a non-facets user

**Goal: make the grant possible, explicit, audited, and impossible to abuse.**

## 3.1 Understand what you are removing

`Session.IsAdmin()` currently refuses any non-facets account outright. That one
line is the only thing preventing an external account from holding admin. You
are replacing a blanket rule with a **narrower, explicit** one — not deleting
it.

## 3.2 The new rule

Add a column:

```sql
ALTER TABLE users ADD COLUMN admin_granted_by TEXT;   -- email of the facets admin who granted
ALTER TABLE users ADD COLUMN admin_granted_at DATETIME;
```

`IsAdmin()` becomes:

> A user is an admin if **either** they are a facets employee with role
> `admin`, **or** they have role `admin` *and* a non-empty `admin_granted_by`.

So a non-facets admin exists only when a specific facets admin deliberately
created them, and the record says who did it.

**`IsAdmin` must not query the database.** It operates on the session. The role
is already resolved per request by the auth middleware (`getRoleFunc`); extend
that same lookup to also fetch `admin_granted_by` so the session carries it.
Read `internal/auth/middleware.go` to see how the per-request role resolution
works before changing it.

## 3.3 The guardrails — all of these are required

1. **Only a facets admin may grant admin to a non-facets user.** A non-facets
   admin calling the same endpoint for someone else is refused. This stops a
   chain of external accounts promoting each other (Rule 6).
2. **Nobody may change their own role.** Reject when target == caller, always,
   even for facets admins. An admin who wants to step down asks another admin.
3. **Every grant and revoke writes an audit row** via `LogAdminAction`,
   recording granter, target, and before/after role.
4. **The grant is visible.** The Users page shows non-facets admins distinctly
   — "admin (granted by alice@facets.cloud, 2026-10-28)" — not a bare "admin".
   Someone reviewing the list must see instantly which admins are external.
5. **Revoking clears both columns**, not just the role, so a later re-promotion
   is a fresh, separately audited decision.
6. **The last facets admin cannot be demoted.** If demoting would leave zero
   facets admins, refuse with a clear message. Otherwise one mistake locks
   everyone out permanently.

## 3.4 Phase 3 tests

1. facets admin grants admin to `x@gmail.com` → succeeds; `IsAdmin` true on
   the next request
2. the granted non-facets admin tries to grant admin to `y@gmail.com` → refused
3. any admin tries to change their own role → refused
4. revoke clears role, `admin_granted_by` and `admin_granted_at`
5. demoting the last facets admin → refused with a clear message
6. the users list marks a non-facets admin with its granter and date
7. every grant and revoke appears in the admin audit log

---

# PHASE 4 — Per-user, per-job everywhere else

**Goal: close the remaining places where "per user" is assumed rather than
enforced, and make per-job state visible.**

Work through this list. For each, **check first** — some may already be
correct, and §1.4 is a warning about building what exists.

1. **Audit every handler that takes a job id.** List them
   (`grep -n 'chi.URLParam(r, "id")' internal/jobs/*.go`) and confirm each one
   calls `canAccess` **before** touching the filesystem or the pipeline. Report
   any that do not. Expect most to be fine — 12 already call it.
2. **`/api/facets/*`** — these are facets-wide by design. Confirm each is
   deliberately facets-wide rather than accidentally so, and that none of them
   exposes another user's bill contents. Document the decision per endpoint in
   your summary.
3. **Per-user job quotas.** Add `max_jobs_per_day` and
   `max_concurrent_jobs` as admin settings (default 20 and 2). Enforce at
   upload with a clear 429 naming the limit and when it resets. Admins exempt.
4. **Per-user preferences**: default engine, default export format, email
   notification on job completion. Store per user; fall back to the global
   default when unset.
5. **"My activity"** on the job page: who created it, when, which engine,
   which assumption and report-settings versions, how long each phase took.
   All of this is already recorded somewhere — this is surfacing, not
   computing.

---

# PHASE 5 — Exports that are actually useful

**Read §1.4 first. CSV and XLSX already exist. Do not rebuild them.**

## 5.1 Make them discoverable
They are rendered at `frontend/src/pages/JobStatus.tsx:542` and `:547`. Check
whether they are hidden behind a job state or buried visually. Put all export
options behind one clear **Export** control listing every format with a
one-line description.

## 5.2 Add the formats that are genuinely missing

| format | why |
|---|---|
| **JSON** | lets a customer or their tooling consume the projection programmatically. Same data as the CSV, stable key names, documented. |
| **Line-item CSV** | the current CSV is service-level. A full per-line-item export — AWS line, mapped GCP SKU, multiplier, projected cost, passthrough reason — is what someone checking our work needs. |

Both come from `scripts/export_report.py`. Extend it with a `--format json`
and a `--level line-item` flag rather than writing a second exporter.

## 5.3 Make exports obey the report settings

The report-settings feature lets an admin hide columns and sections. An export
that ignores those settings contradicts the report it came from. Pass the
job's `report-settings.json` into `export_report.py` and apply the same column
choices.

**Keep totals complete regardless** — the same rule the report follows: hiding
a column never changes a total, and a filtered row set states what it hid.

## 5.4 Phase 5 tests

1. every advertised format downloads successfully for a completed job
2. the JSON export parses and its totals equal the HTML report's totals
3. the line-item export's rows sum to the service-level export's totals
4. a column hidden by report settings is absent from the CSV, and the totals
   are unchanged
5. exporting a job you do not own → 404

---

## 6. Verification for every phase

```bash
cd /c/Users/arnav/Downloads/cur-web-AGY && go build ./... && go test ./internal/...
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
```

Record the baseline counts before you start. **Today: 1,110 Python tests, all
Go packages passing.**

After each phase, also confirm:
- a projection's **numbers are unchanged** — render a report before and after
  and compare the headline totals
- no endpoint returns 403 where it should return 404 (Rule 1)

---

## 7. Do NOT

- Do **not** rebuild CSV/XLSX export (§1.4).
- Do **not** rewrite `canAccess` (§1.5) — extend its use.
- Do **not** return 403 for "not yours" (Rule 1).
- Do **not** let logs reach a non-owner, a notification, or server stdout
  (Rule 2).
- Do **not** silently substitute an engine the user did not choose (§2.3).
- Do **not** let any endpoint grant a permission to its caller (Rule 6).
- Do **not** allow the last facets admin to be demoted (§3.3.6).
- Do **not** change any pricing, mapping or total anywhere in this work order.
- Do **not** start a phase before the previous phase's tests pass.

---

## 8. Done

- [ ] **Phase 1** logs scoped by `canAccess`; three streams; parsed, filterable,
      paged; digest survives directory loss; UI tab; 7 tests
- [ ] **Phase 2** engine recorded on every job; admin default + allow-list;
      per-job selection, server-validated; shown in report footer; 6 tests
- [ ] **Phase 3** facets admin can grant admin to a non-facets user, with all
      six guardrails; 7 tests
- [ ] **Phase 4** every job-id handler audited; quotas; per-user preferences;
      activity panel
- [ ] **Phase 5** exports discoverable; JSON + line-item added; settings
      honoured; 5 tests
- [ ] Both suites clean after every phase; projection numbers unchanged
- [ ] Summary states, per phase, what was built, what was found already
      correct, and anything noticed but not fixed
