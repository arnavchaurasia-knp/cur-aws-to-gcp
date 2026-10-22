# WORK ORDER — Make the pipeline's diagnostics visible (Job Health)

**You are implementing this and you have never seen this codebase before.
Read sections 0 and 1 before touching any file.**

---

## 0. The situation

The pipeline **already produces good structured diagnostics**. Nobody can see
them.

What exists today, all working:

| piece | file | what it does |
|---|---|---|
| `diag(level, code, source, message, rows, usd, detail)` | `scripts/diagnostics.py` | appends one JSON record per finding to `<job>/projection-audit/diagnostics.jsonl` |
| `read_all(job_dir)` | same file | reads those records back |
| `run_diag_summary.py` | `scripts/` | aggregates them into `<job>/run_diagnostics.json`; already runs as the last step of the pipeline |
| `debug.log` | `scripts/log_utils.py` | full DEBUG-level text log per job |

A real example the pipeline emits today and nobody sees:

```
[diag] warn unit_price_outlier src=ingest.py rows=1 usd=$6,409.41 ::
  1 line item(s) priced inconsistently with other lines on the same meter.
  Largest: file line 453, meter APS5-Lambda-GB-Second-ARM, unit price 0.0133334
  vs 1.33334e-05 on 4 other line(s) of that meter (1000x), cost $6,409.41.
```

**The Go API serves none of it and the React frontend reads none of it.** When
a projection looks wrong, the person looking at it has no way to learn that
ingest already explained why.

### So there are exactly two gaps

1. **Nothing surfaces the diagnostics** — no endpoint, no screen (Parts B & C).
2. **Only 7 scripts emit them.** Everything else logs unstructured text into
   `debug.log`, which nobody opens (Part D).

**You are not building a logging system. One exists and it is good.** You are
connecting it to a screen and widening its coverage.

---

## 1. Rules

### Rule 1 — This is internal, admin-only
Diagnostics describe our pipeline's uncertainty. They are **never** shown to a
customer and never put in the HTML report. Admin routes only, same middleware
as the other admin endpoints.

### Rule 2 — Job directories disappear; the database does not
Job directories live on ephemeral storage and **are lost on every redeploy**.
A diagnostics screen that reads only from the job directory will be empty for
every older job, and will look like "no problems" rather than "data gone".

So the **summary** must be copied into the durable database when the job
finishes (Part A). The full record list can stay on disk — if it is gone, say
so explicitly.

### Rule 3 — "No diagnostics" and "diagnostics missing" are different
- file present, zero records → **"No issues recorded for this run."**
- file absent → **"Diagnostics are not available for this run (job files expired)."**

Never render the second as the first. That distinction is the entire point of
the feature.

### Rule 4 — Never fail a job because of diagnostics
`diag()` never raises, and `run_diag_summary.py` always exits 0. Keep it that
way. A reporting step must never become a gate.

### Rule 5 — Do not change what the pipeline computes
Adding a `diag()` call records something that already happened. If you find
yourself changing a mapping, a rate or a total, you have gone outside this
work order.

---

## 2. PART A — Persist the summary (Go)

`run_diag_summary.py` already writes `<jobDir>/run_diagnostics.json`:

```json
{
  "workload_usd": 10738.09,
  "error_usd": 0.0,
  "error_pct_of_workload": 0.0,
  "error_count": 0,
  "warn_count": 3,
  "checks_not_evaluated": 0,
  "by_code": {
    "unit_price_outlier": { "level": "warn", "count": 1, "rows": 1,
                            "usd": 6409.41, "sources": ["ingest.py"] }
  }
}
```

**A1.** Add a table in `internal/db/db.go`, beside the existing ones:

```sql
CREATE TABLE IF NOT EXISTS job_diagnostics (
    job_id       TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    summary_json TEXT NOT NULL,   -- the file above, verbatim
    error_count  INTEGER NOT NULL DEFAULT 0,
    warn_count   INTEGER NOT NULL DEFAULT 0,
    error_usd    REAL    NOT NULL DEFAULT 0,
    workload_usd REAL    NOT NULL DEFAULT 0,
    recorded_at  DATETIME DEFAULT (datetime('now')),
    PRIMARY KEY (job_id, run_id)
);
```

The counts are duplicated out of the JSON so a job list can show a badge
without parsing every blob.

**A2.** Add `func (d *DB) SaveJobDiagnostics(jobID, runID string, summary []byte) error`
and `func (d *DB) GetJobDiagnostics(jobID, runID string) (*JobDiagnostics, error)`.
Copy the style of the neighbouring DB functions.

**A3.** Call `SaveJobDiagnostics` when a job finishes. Find where the job is
marked complete — `internal/jobs/watcher.go` decides success by the report's
presence (search `resolveReportPath`). Immediately after that, read
`<jobDir>/run_diagnostics.json` and save it.

**If the file is missing, log one line and carry on.** A job must never fail
because its diagnostics could not be stored.

---

## 3. PART B — Serve them (Go)

Add two admin endpoints in `cmd/server/main.go`, **inside the same admin-only
block** as the other `/api/admin/...` routes:

```go
r.Get("/api/admin/jobs/{id}/diagnostics", jobHandler.AdminJobDiagnostics)
r.Get("/api/admin/jobs/{id}/diagnostics/records", jobHandler.AdminJobDiagnosticRecords)
```

**`AdminJobDiagnostics`** returns the summary:
1. read it from `job_diagnostics` (durable, works forever)
2. if absent, fall back to reading `<jobDir>/run_diagnostics.json`
3. if both absent, return `{"available": false}` — **HTTP 200, not 404**, so
   the UI can tell "not available" from "request failed"

**`AdminJobDiagnosticRecords`** returns the individual records by reading
`<jobDir>/projection-audit/diagnostics.jsonl` and parsing one JSON object per
line. Skip malformed lines rather than failing the request. Cap the response
at the most recent 500 records. If the directory is gone, return
`{"available": false, "records": []}`.

**Do not** add a third endpoint for `debug.log`. It is large and
unstructured; the existing download path is enough.

---

## 4. PART C — Show them (React)

Add a **Job Health** panel on the admin job detail view (find where admin job
actions such as Cancel and Delete are rendered and put it beside them).

Add the API client functions to `frontend/src/api/admin.ts`, copying the style
of the functions already there.

The panel shows:

**A one-line verdict at the top**, from the summary:
- `error_count > 0` → red: *"3 errors affecting $4,210 (4.1% of workload)"*
- else `warn_count > 0` → amber: *"3 warnings — review before sending"*
- else → green: *"No issues recorded for this run."*
- summary unavailable → grey: *"Diagnostics are not available for this run (job files expired)."*

**A table from `by_code`**, sorted by `usd` descending:

| code | level | count | rows | $ exposure | source |
|---|---|---|---|---|---|
| unit_price_outlier | warn | 1 | 1 | $6,409.41 | ingest.py |

**Expanding a row** calls the records endpoint and lists the individual
messages for that code, including the `detail` object — that is where the
file line number and resource id live, and it is the thing that makes a
problem findable in the customer's bill.

Keep the wording neutral and factual. These are observations about a bill and
about our coverage, not apologies.

---

## 5. PART D — Widen coverage

Only 7 scripts emit diagnostics today: `ingest.py`, `apply_rates.py`,
`apply_static_mappings.py`, `ensure_full_coverage.py`, `fix_storage_misroute.py`,
`outlier_gate.py`, `validate_fix.py`.

Add a `diag()` call at each point below. **Each one records a real failure
that has actually happened in this pipeline and was invisible.** Use the
existing import style: `from diagnostics import diag, WARN, ERROR`.

| script | when to emit | code | include |
|---|---|---|---|
| `classify_mechanics.py` | rows land in the `misc` group | `unmapped_misc_spend` | row count + $ |
| `merge_mappings.py` | a group's mapping file is empty **but** the catalog has rows in that group | `empty_group_mappings` | group name, catalog row count, $ |
| `apply_commitment_ignores.py` | it claims 0 rows while `mechanic_group='commitment_discount'` is non-empty | `commitment_rows_unclaimed` | row count + $ |
| `reconcile_capacity.py` | rows skipped because `instance_vcpus` is NULL | `capacity_check_skipped` | row count + $ |
| `render_report.py` | the materiality filter dropped rows | `materiality_dropped` | row count + $ + % of bill |

> The `empty_group_mappings` and `commitment_rows_unclaimed` cases are not
> hypothetical. A mapper silently wrote `[]` over a valid mappings file and
> $850 of commitment fees went unmapped on two bills — counted on the AWS side,
> zero on the GCP side. It was invisible for exactly as long as nobody was
> looking. These two calls are the highest-value items in this table.

**Rule: a `diag()` call reports, it never changes behaviour.** Do not add
`continue`, `return` or `raise` beside one.

---

## 6. Verification

```bash
cd /c/Users/arnav/Downloads/cur-web-AGY && go build ./... && go test ./internal/...
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
```

- Run a job end to end. Confirm `run_diagnostics.json` exists **and** a
  `job_diagnostics` row was written.
- `GET /api/admin/jobs/{id}/diagnostics` returns that summary.
- Delete the job directory, call it again → the summary **still returns** from
  the database; the records endpoint returns `available: false`.
- A job with zero findings shows the green "No issues recorded" state, **not**
  the grey "not available" one.
- A non-admin gets 403 on both endpoints.
- Feed in a bill with a 1000x unit-price line; confirm `unit_price_outlier`
  appears in the panel **with the file line number visible** when expanded.
- Confirm no job fails because diagnostics were missing: delete
  `run_diagnostics.json` mid-run and check the job still completes.

### Tests
1. `SaveJobDiagnostics` then `GetJobDiagnostics` round-trips.
2. Saving twice for the same (job, run) replaces rather than duplicates.
3. The summary endpoint returns `available: false`, status 200, when nothing exists.
4. The records endpoint skips a malformed JSONL line instead of failing.
5. Python: each new `diag()` call site emits the expected `code` on a fixture
   that triggers it.

---

## 7. Do NOT

- Do **not** build a new logging framework. `diag()` exists.
- Do **not** show diagnostics in the customer HTML report or on any
  non-admin route.
- Do **not** return 404 when diagnostics are absent — the UI must distinguish
  "none recorded" from "files expired" (Rule 3).
- Do **not** let a diagnostics failure fail a job (Rule 4).
- Do **not** change pipeline behaviour while adding `diag()` calls (Rule 5).
- Do **not** stream `debug.log` through the API.

---

## 8. Done

- [ ] `job_diagnostics` table; summary saved on job completion; missing file is non-fatal
- [ ] Two admin endpoints; `available: false` instead of 404; records capped and malformation-tolerant
- [ ] Job Health panel: verdict line, `by_code` table, expandable records showing `detail`
- [ ] 5 new `diag()` call sites, none of which change behaviour
- [ ] 5 tests pass; Go and Python suites clean
- [ ] Verified that a lost job directory still shows the summary, and that
      "no issues" and "not available" render differently
