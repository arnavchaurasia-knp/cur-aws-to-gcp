# WORK ORDER — Admin UI: pages that render empty, search that misses, and features with no way in

**For an implementing AI with no prior context. Read fully before editing.**
Paths are from the repository root. Line numbers are from commit `3158e48`.

**Scope: the admin surface only** — `frontend/src/App.tsx`,
`frontend/src/components/Nav.tsx`, `frontend/src/api/admin.ts`,
`frontend/src/pages/Admin*.tsx`, `internal/jobs/admin_handlers.go`,
`internal/db/jobs.go`. **No pipeline, pricing or projection code is involved.**

Six defects, ordered by how much they block an admin from doing their job.
Each is independently shippable. Do them in order — A-1 and A-2 together
explain the "I log in as admin and see nothing" report, and fixing A-2 alone
would merely make the failure visible rather than fix it.

---

## 0. First, an honest scope note

The brief that prompted this work order asked to "show many routes which lie
only at backend". After enumerating every route in `cmd/server/main.go`
against every `fetch()` in `frontend/src/`, that is **not** what the evidence
shows. Only **two** backend routes have no caller anywhere in the frontend
(A-5). The larger problem is different, and worse: routes that *are* wired
return data the UI then throws away (A-2), and two complete admin pages exist
with no link to them (A-4).

Do not go hunting for a long list of orphan endpoints. There isn't one. The
list below is the complete, verified set.

---

## A-1 — The UI decides who is an admin differently from the server (CRITICAL)

**This is the "I don't see things in the UI as an admin" bug.**

The frontend gates every admin route and nav link on the *email domain alone*.

`frontend/src/App.tsx:42-49`, duplicated verbatim in
`frontend/src/components/Nav.tsx:7-14`:

```ts
const email = (user?.email || '').toLowerCase()
const isFacetsUser = Boolean(
  email && (
    email.endsWith('@facets.cloud') ||
    email.endsWith('@facets.io') ||
    email.includes('@facets.')
  )
)
```

The server gates the same routes on domain **and role**
(`internal/auth/session.go:38-60`):

```go
func (s *Session) IsAdmin(adminEmails []string) bool {
    if !s.IsFacetsEmployee() { return false }
    if s.Role == "viewer" { return false }   // <-- the frontend has no idea this exists
    if s.Role == "admin"  { return true }
    // ... explicit ADMIN_EMAILS allow-list ...
    return true  // all @facets accounts default to admin
}
```

So a `@facets` account whose role has been set to `viewer` — which is exactly
what `POST /api/admin/users/{email}/role` exists to do, and which
`internal/auth/middleware.go:25-31` re-resolves from the users table on
**every request** — sees the full admin navigation, can open every admin page,
and gets `403 forbidden` from every single API call behind it. Combined with
A-2 those 403s render as empty tables. The admin concludes the product is
broken, and nothing on screen says "you do not have permission".

### Fix

1. Expose the decision to the client. `GET /api/auth/me` (`oauthH.Me`) must
   return the session's resolved `role` and a computed `is_admin`, where
   `is_admin` comes from the *same* `Session.IsAdmin(cfg.AdminEmails)` call the
   middleware uses. There must be exactly one implementation of this
   predicate, in Go.
2. Replace both copies of `isFacetsUser` with that server-supplied value.
3. **Delete the duplication.** `App.tsx` and `Nav.tsx` carry byte-identical
   copies of the predicate. Put the value in the existing user context/prop and
   read it in both places. Two copies of an authorization rule is how they
   drift apart in the first place.
4. If the `/facets` routes need employee-but-not-admin access, keep that as a
   separate server-supplied flag. Do not re-derive it from the email string in
   TypeScript.

### Do NOT

- Do not widen the server's `IsAdmin` to match the frontend. The server is the
  one that is right; the frontend is missing the role check.
- Do not drop or bypass the `viewer` role.
- Do not compute `is_admin` in TypeScript from a `role` string. That moves the
  divergence rather than removing it.

---

## A-2 — 18 of 26 admin API calls turn every error into an empty result (CRITICAL)

`frontend/src/api/admin.ts` exports 26 functions. Eighteen end a failed
request like this:

```ts
if (!res.ok) return []        // or return null, or { jobs: [], total: 0, ... }
```

Exact lines: 27, 95, 118, 149, 169, 198, 233, 248, 254, 291, 306, 312, 318,
333, 382, 388, 405, 411.

A `403` (A-1), a `500` from a failing `h.db`, an expired session, a cold-start
timeout on Render's free tier — every one of them reaches the component as
"there is no data", and the page renders its empty state. **The UI cannot
distinguish "you are not allowed" from "nothing exists yet" from "the database
is down".**

This is the same defect shape found repeatedly in the pipeline this cycle:
*absence encoded indistinguishably from failure.*

### Fix

1. Add one shared helper, e.g. `frontend/src/api/http.ts`:

```ts
export class ApiError extends Error {
  constructor(readonly status: number, message: string) { super(message) }
}

export async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, body || `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}
```

2. Convert all 26 functions in `admin.ts` to use it and **throw** rather than
   return a default.
3. Every `Admin*.tsx` page must render three distinct states: loading, error
   (showing the status, with a Retry control), and genuinely-empty. A `403`
   must say "You do not have admin permission" — never "No jobs found".
4. A `401` should redirect to `/login` rather than render an error; the session
   has simply expired.

### Do NOT

- Do not leave "return a default on failure" anywhere in `admin.ts`, including
  functions this work order does not list by line. The pattern is the bug.
- Do not surface the error only as a toast while the table still renders its
  empty state. The table must not claim the data is empty.

---

## A-3 — Search only searches the page you are already looking at (HIGH)

`AdminListAllPaged` (`internal/jobs/admin_handlers.go:33-70`) accepts `limit`,
`offset`, `status`, `from` and `to`. **It accepts no search term.** Page size
defaults to 50 (`admin_handlers.go:28`).

`frontend/src/pages/AdminJobs.tsx:64` holds a `query` state, and lines
`210-218` and `222-230` filter with `useMemo` over the `jobs` array already in
memory — that is, over the current page of 50 only. The input promises
"Search prospect, owner, ID, status…" (`AdminJobs.tsx:385`).

So searching for a prospect whose job is on page 7 returns "no results". The
feature looks like it works and is wrong, which is worse than it being absent.

`AdminUsers.tsx:14,53` also filters client-side, but that list is
unpaginated, so it is correct there. **Leave `AdminUsers` alone.**

### Fix

1. Add a `q` parameter to `AdminListAllPaged` and plumb it into both
   `h.db.ListAllJobsFiltered(...)` and `h.db.CountAllJobs(...)` in
   `internal/db/jobs.go`, so the total matches the filter and pagination stays
   correct.
2. Match case-insensitively across the fields the placeholder promises:
   prospect, owner email, job ID, status. Use a parameterised
   `LIKE '%' || ? || '%'` — **never** concatenate the term into SQL. It must
   work against both a libSQL/Turso remote and local SQLite.
3. Debounce the input (~300 ms) and send `q` to the server. Remove the
   client-side `useMemo` filter for jobs, so there is one filter rather than
   two that disagree.
4. Do the same for the deleted-jobs list (`AdminJobs.tsx:620`, backed by
   `GET /api/admin/deleted-jobs`), which has the identical problem.
5. Reset `offset` to 0 whenever `q` changes, or page 3 of the old result set
   gets requested against a new one.

---

## A-4 — Two finished admin pages have no way to reach them (HIGH)

`AdminAssumptions` and `AdminReportSettings` are fully built, have working API
clients (`getAssumptions`, `putAssumption`, `getAssumptionHistory`,
`getReportSettings`, `putReportSetting`, `getReportSettingHistory`,
`resetReportSettings`) and are routed at `frontend/src/App.tsx:62-63`.

`frontend/src/components/Nav.tsx:39-44` renders exactly six links: All
Reports, Logs & Engine, Users, Catalog, Analytics, Audit. **Neither page is
among them, and nothing anywhere else in the app links to either.** They are
reachable only by typing the URL.

These pages control the assumptions and report settings every projection uses,
so this is not cosmetic.

### Fix

Add both to the admin navigation. Six links already crowd the bar at `sm:`
width and the mobile branch (`Nav.tsx:47-52`) exposes only two, so do not just
append two more. Group them: keep All Reports, Logs & Engine, Users and
Catalog top-level, and move Analytics, Audit, Assumptions and Report Settings
into a single "Settings" dropdown. `UserMenu` (`Nav.tsx:58`) is an existing
dropdown in this codebase — follow its pattern, including its open/close and
keyboard handling, rather than introducing a second dropdown implementation.

Make the mobile branch reach every admin page too; today it reaches two.

---

## A-5 — Share links can be created but never listed or revoked (MEDIUM, security-relevant)

These are the only two routes in the whole app with no frontend caller:

| route | registered at | handler | client caller |
|---|---|---|---|
| `GET /api/admin/shares` | `cmd/server/main.go:246` | `admin_handlers.go:614` | none |
| `DELETE /api/admin/shares/{token}` | `cmd/server/main.go:247` | `admin_handlers.go:640` | none |

Both handlers work, and `maskToken` (`admin_handlers.go:681`) already masks
all but the first six characters server-side.

Meanwhile `shareJob` **is** wired, so admins can mint public share links to
customer reports. `GET /api/reports/{token}` (`main.go:201`) serves those with
**no authentication** — correctly, that is what a share link is for. But no
screen lists outstanding links and nothing can revoke one. A link sent to the
wrong prospect cannot be withdrawn through the UI.

### Fix

Add a Shares admin page (or a tab alongside Catalog/Settings) listing every
share: masked token, job, prospect, who created it, when, and a Revoke action
calling the existing `DELETE`. Add `listReportShares` and `deleteReportShare`
to `admin.ts` using the `getJSON` helper from A-2. Require a confirmation step
before revoking — it breaks a link someone may be relying on.

Do **not** unmask the full token in the list. It is masked server-side for a
reason, and revoking needs only the identifier the API already returns.

---

## A-6 — The audit log cannot be filtered (MEDIUM)

`AdminAuditLog` (`internal/jobs/admin_handlers.go:379-392`) accepts only
`limit` (default 100) and `offset`, and `listAuditLog` (`admin.ts:145-147`)
sends only those. There is no filter by actor, action or date.

An audit log you can only page through blindly does not answer the question an
audit log exists to answer: who changed this setting, and when.

### Fix

Add `actor`, `action` and `from`/`to` parameters to the handler and to
`h.db.ListAuditLog`, reusing the date parsing already written for
`AdminListAllPaged` (`admin_handlers.go:48-60`) so the two endpoints treat
dates identically. Surface them as filter controls on `AdminAudit.tsx`. The
same parameterised-SQL rule as A-3 applies.

---

## Verification

A unit test on a handler is necessary but **not sufficient**. A-1 exists
precisely because the server and the client disagreed while both, in
isolation, "worked".

1. **Go**: tests that `AdminListAllPaged` honours `q` and that `CountAllJobs`
   returns the count *of the filtered set*; tests for the audit filters.
   `go build ./...` and `go vet ./...` clean. Run `gofmt -l` only on files you
   touched — most of the repo is CRLF and will be listed spuriously.
2. **Frontend**: `npm run build` in `frontend/` clean. It is a Docker build
   stage, so a TypeScript error fails the deploy.
3. **The one that matters — exercise the role path end to end:**
   - Sign in as a `@facets` account, set its role to `viewer` via
     `POST /api/admin/users/{email}/role`, reload, and confirm the admin nav is
     **gone** rather than present-and-broken.
   - Set the role back and confirm every nav item loads real data.
   - Force a `403` on one admin endpoint in DevTools and confirm the page shows
     a permission error, **not** an empty table.
4. **Search**: with more than 50 jobs, search for a prospect you know is not on
   page 1 and confirm it is found.
5. Confirm every admin page is reachable by clicking from the nav alone, at
   both desktop and mobile widths.

---

## Done

- [ ] `/api/auth/me` returns server-computed `is_admin`; both copies of
      `isFacetsUser` are gone, replaced by one shared value.
- [ ] No function in `admin.ts` returns a default on failure; all 26 throw.
- [ ] Every `Admin*.tsx` distinguishes loading / error / empty; `403` says
      permission, `401` redirects to login.
- [ ] Job search and deleted-job search are server-side and parameterised,
      with counts matching the filter and offset reset on change.
- [ ] Assumptions and Report Settings are reachable from the nav at desktop
      and mobile widths.
- [ ] Shares can be listed and revoked from the UI; tokens stay masked.
- [ ] Audit log filterable by actor, action and date range.
