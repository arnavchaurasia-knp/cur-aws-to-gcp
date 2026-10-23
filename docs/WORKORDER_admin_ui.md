# WORK ORDER — Admin UI: navigation, search, and how the data is shown

**For an implementing AI with no prior context. Read fully before editing.**
Paths are from the repository root. Line numbers are from commit `3a2c4cb`.

**Scope: the admin presentation layer.** `frontend/src/components/Nav.tsx`,
`frontend/src/App.tsx`, `frontend/src/pages/Admin*.tsx`, plus the specific
backend query changes that search and the jobs table require
(`internal/jobs/admin_handlers.go`, `internal/db/jobs.go`).

Authorization and error handling are a **separate work order**:
`docs/WORKORDER_admin_errors_and_access.md`. **Do that one first.** Admins are
currently locked out of every admin endpoint in production (403 on everything),
so none of the work below can be seen, let alone tested, until it is fixed.
Where this work order needs an error state, it describes how it should *look*;
getting the error to the component is the other one's job.

**No pipeline, pricing or projection code is involved.**

---

## U-1 — Every admin page has one visual state where it needs three (HIGH)

Once the API layer throws instead of returning `[]`
(`WORKORDER_admin_errors_and_access.md` E-3), every `Admin*.tsx` page needs to
render three *distinct* states. Today each has effectively one, because a
failure and an empty result arrive identically.

Required, on each of `AdminJobs`, `AdminUsers`, `AdminCatalog`,
`AdminAnalytics`, `AdminAudit`, `AdminAssumptions`, `AdminReportSettings`:

| state | what the user sees |
|---|---|
| loading | a skeleton of the table/panel, not a spinner that replaces the page |
| error | what failed, the status, and a Retry control that re-runs that one request |
| empty | a message that says the set is genuinely empty, and why that might be |

Specifics that matter:

- A `403` must read as a permission problem naming what was refused — never
  "No jobs found". A table claiming emptiness when the server refused the
  request is the single defect that hid a total production lockout.
- A `401` should not render an error at all; route to `/login`.
- Retry must retry the failed request only. Do not reload the page.
- Keep the surrounding chrome (nav, filters, tabs) visible in the error state
  so the admin can navigate away or change a filter. A full-page error that
  replaces the nav traps them.

Build this once as a shared component (e.g. `AdminPanelState`) and use it on
all seven pages, rather than seven hand-rolled variants.

---

## U-2 — Search silently searches only the page you are looking at (HIGH)

`AdminListAllPaged` (`internal/jobs/admin_handlers.go:33-70`) accepts `limit`,
`offset`, `status`, `from` and `to`. **It accepts no search term.** Page size
is 50 (`admin_handlers.go:28`).

`frontend/src/pages/AdminJobs.tsx:64` holds a `query` state, and lines
`210-218` and `222-230` filter with `useMemo` over the `jobs` array already in
memory — the current page of 50 only. The input promises
`"Search prospect, owner, ID, status…"` (`AdminJobs.tsx:385`).

So a prospect whose job is on page 7 returns "no results". The feature looks
like it works and quietly lies, which is worse than not having it.

`AdminUsers.tsx:14,53` also filters client-side, but that list is unpaginated,
so it is correct there. **Leave `AdminUsers` alone.**

### Fix

1. Add a `q` parameter to `AdminListAllPaged`, plumbed into **both**
   `h.db.ListAllJobsFiltered(...)` and `h.db.CountAllJobs(...)`
   (`internal/db/jobs.go`), so the total matches the filter and pagination
   stays honest. A filtered list with an unfiltered count produces pages that
   are empty for no visible reason.
2. Match case-insensitively on prospect, owner email, job ID and status —
   the fields the placeholder already promises. Use a parameterised
   `LIKE '%' || ? || '%'`. **Never** concatenate the term into SQL. It must
   work against both a libSQL/Turso remote and local SQLite.
3. Debounce input ~300 ms, send `q` to the server, and delete the client-side
   `useMemo` filter for jobs so there is one filter rather than two that
   disagree.
4. Apply the same to the deleted-jobs list (`AdminJobs.tsx:620`, backed by
   `GET /api/admin/deleted-jobs`), which has the identical defect.
5. Reset `offset` to 0 whenever `q` changes, or page 3 of the previous result
   set is requested against a new one.
6. While a search is active, say what is being shown: "12 results for 'acme'"
   rather than an unlabelled filtered table.

---

## U-3 — The jobs table omits the product's own headline number (HIGH)

`AdminJobs` renders these columns: **When, Prospect, Status, AWS Spend,
Exports, Actions.**

Two problems:

**a) No GCP projection.** This is an AWS→GCP cost-projection product and its
main admin list shows the AWS side only. The projected GCP cost and the
AWS→GCP ratio are the numbers the whole pipeline exists to produce, and an
admin scanning for a bad run (a 3x over-projection, say) cannot see them.

This needs a backend change: `gcp_od` lives on `RunResult`
(`frontend/src/api/jobs.ts:13`), not on the `Job` row
(`jobs.ts:3-4` — `id, owner, prospect, status, aws_spend, error, created_at`).
Extend `ListAllJobsFiltered` to join each job's latest run and return
`gcp_od` alongside `aws_spend`, then add **GCP (OD)** and **Ratio** columns.

Show the ratio to two significant figures and make an implausible one visually
obvious (e.g. outside 0.5x–1.5x). An admin should be able to spot a broken run
by scanning the list — that is the point of the list.

**b) No Owner column.** `owner` is already on the `Job` type and is already
shown in the *deleted*-jobs table, but not in the main one — while the search
box offers to search by owner. You can search a field you cannot see. Add it.

---

## U-4 — No sorting anywhere, and pagination is prev/next only (MEDIUM)

No admin table supports clicking a column to sort. The only `sort` call in the
admin pages is an internal grouping at `AdminJobs.tsx:259`.

Pagination offers only Previous/Next (`AdminJobs.tsx:596-604`) with a
hardcoded page size, no way to jump to a page, and no way to show more rows.
With a page size of 50 and no sorting, finding the largest bill or the oldest
failure means paging through everything by hand.

### Fix

1. Add server-side sorting to `AdminListAllPaged`: a `sort` parameter
   (`created_at`, `aws_spend`, `prospect`, `status`) and a `dir` (`asc`/`desc`),
   with `created_at desc` as today's default. **Validate both against an
   allow-list** and map to column names in Go — never interpolate a client
   string into `ORDER BY`.
2. Make the headers clickable with a visible sort indicator, and keep the
   active sort in the URL alongside `q` so a filtered, sorted view can be
   shared or reloaded.
3. Add a page-size control (50/100/200 — the handler already caps `limit` at
   500) and show "Page 2 of 7 · 331 jobs" rather than bare arrows.
4. Sorting must be server-side. Sorting the current page client-side would
   reproduce exactly the U-2 bug in a new place.

---

## U-5 — Two finished admin pages have no way to reach them (HIGH)

`AdminAssumptions` and `AdminReportSettings` are fully built, have working API
clients (`getAssumptions`, `putAssumption`, `getAssumptionHistory`,
`getReportSettings`, `putReportSetting`, `getReportSettingHistory`,
`resetReportSettings`) and are routed at `frontend/src/App.tsx:62-63`.

`frontend/src/components/Nav.tsx:39-44` renders exactly six links — All
Reports, Logs & Engine, Users, Catalog, Analytics, Audit. **Neither page is
among them, and nothing anywhere else in the app links to either.** They are
reachable only by typing the URL.

They control the assumptions and report settings every projection uses, so this
is not cosmetic.

### Fix

Six links already crowd the bar at `sm:` width and the mobile branch
(`Nav.tsx:47-52`) exposes only two, so do not simply append two more. Restructure:

- Top level: **All Reports**, **Logs & Engine**, **Users**, **Catalog**
- Under one "Settings" dropdown: **Analytics**, **Audit**, **Assumptions**,
  **Report Settings**, and **Shares** (U-6)

`UserMenu` (`Nav.tsx:58`) is an existing dropdown in this codebase — follow its
open/close and keyboard handling rather than adding a second implementation.

The mobile branch must reach every admin page; today it reaches two.

---

## U-6 — Share links can be created but never seen or revoked (MEDIUM)

`shareJob` is wired, so admins mint public share links to customer reports, and
`GET /api/reports/{token}` (`cmd/server/main.go:201`) serves them with **no
authentication** — correctly; that is what a share link is for.

But these two routes are the only ones in the entire app with no frontend
caller at all:

| route | registered | handler |
|---|---|---|
| `GET /api/admin/shares` | `cmd/server/main.go:246` | `internal/jobs/admin_handlers.go:614` |
| `DELETE /api/admin/shares/{token}` | `cmd/server/main.go:247` | `internal/jobs/admin_handlers.go:640` |

Both work. There is simply no screen. A link sent to the wrong prospect cannot
be withdrawn through the product.

### Fix

Add a Shares page under the Settings dropdown listing every share: masked
token, job, prospect, who created it, when, and a Revoke action calling the
existing `DELETE`. Require a confirmation step — revoking breaks a link
someone may be using.

Do **not** display the full token. `maskToken`
(`internal/jobs/admin_handlers.go:681`) masks all but the first six characters
server-side for a reason, and revoking needs only the identifier the API
already returns.

---

## U-7 — The audit log cannot be filtered (MEDIUM)

`AdminAuditLog` (`internal/jobs/admin_handlers.go:379-392`) accepts only
`limit` (default 100) and `offset`; `listAuditLog` (`admin.ts:145-147`) sends
only those. There is no filter by actor, action or date.

An audit log you can only page through blindly does not answer the question an
audit log exists to answer: who changed this setting, and when.

### Fix

Add `actor`, `action` and `from`/`to` to the handler and to
`h.db.ListAuditLog`, reusing the date parsing already written for
`AdminListAllPaged` (`admin_handlers.go:48-60`) so both endpoints treat dates
identically. Surface them as filter controls on `AdminAudit.tsx`, with the
same parameterised-SQL rule as U-2.

---

## Verification

1. **Frontend**: `npm run build` in `frontend/` clean. It is a Docker build
   stage, so a TypeScript error fails the deploy.
2. **Go**: tests that `q` and `sort`/`dir` are honoured, that an invalid `sort`
   value is rejected rather than interpolated, and that `CountAllJobs` returns
   the count *of the filtered set*. `go build ./...` and `go vet ./...` clean.
   Run `gofmt -l` only on files you touched — most of the repo is CRLF and
   will be listed spuriously.
3. **Search, against real volume**: with more than 50 jobs, search for a
   prospect you know is not on page 1 and confirm it is found, that the result
   count matches, and that paging within the filtered set works.
4. **Reachability**: confirm every admin page can be reached by clicking from
   the nav alone, at both desktop and mobile widths, with no typed URLs.
5. **States**: force a `403` and a `500` on one endpoint in DevTools and
   confirm the page shows a permission error and a server error respectively,
   each distinct from the genuinely-empty state, with the nav still usable.

---

## Done

- [ ] All seven admin pages render loading / error / empty as distinct states
      via one shared component; 403 reads as permission, 401 redirects.
- [ ] Job and deleted-job search are server-side and parameterised, counts
      match the filter, offset resets on change, result count shown.
- [ ] Jobs table shows Owner, GCP (OD) and Ratio, with implausible ratios
      visually flagged.
- [ ] Server-side sorting on allow-listed columns, with sort and query in the
      URL; page-size control and a real pagination summary.
- [ ] Assumptions, Report Settings and Shares reachable from the nav at
      desktop and mobile widths.
- [ ] Shares can be listed and revoked from the UI; tokens stay masked.
- [ ] Audit log filterable by actor, action and date range.
