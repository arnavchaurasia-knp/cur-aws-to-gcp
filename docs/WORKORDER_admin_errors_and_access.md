# WORK ORDER — Admin access lockout and error handling

**For an implementing AI with no prior context. Read fully before editing.**
Paths are from the repository root. Line numbers are from commit `3a2c4cb`.

**Scope: authorization and error handling.** `internal/auth/oauth.go`,
`internal/auth/session.go`, `internal/auth/middleware.go`,
`internal/db/users.go`, `internal/db/db.go`, `frontend/src/api/admin.ts`,
`frontend/src/App.tsx`, `frontend/src/components/Nav.tsx`.

The *presentation* of admin pages — navigation, search, tables, empty states —
is a separate work order: `docs/WORKORDER_admin_ui.md`. Do **E-1 first**: it is
a live production lockout, and the UI work cannot be tested until an admin can
actually reach an admin page.

**No pipeline, pricing or projection code is involved.**

---

## E-1 — Every admin is permanently locked out, with no recovery path (CRITICAL, live)

### Confirmed in production

From the Render log for the running service:

```
"http","method":"GET","path":"/api/auth/me","status":200
"http","method":"GET","path":"/api/facets/jobs","status":200
"http","method":"GET","path":"/api/admin/catalog/status","status":403
"http","method":"GET","path":"/api/admin/notify/status","status":403
```

The session is valid and is recognised as a Facets employee (`/api/facets/jobs`
is gated on `IsFacetsEmployee`), yet every `/api/admin/*` call returns 403.

### The mechanism

`internal/auth/oauth.go:454-464` (and the same sequence at `:210-220`):

```go
role := "viewer"
if isFacets { role = "admin" }                    // 1. correctly computes admin
if h.users != nil {
    if r, err := h.users.GetUserRole(info.Email); err == nil && r != "" {
        role = r                                  // 2. overwrites it from the DB
    }
    _ = h.users.UpsertUserSeen(info.Email)        // 3. inserts the row as 'viewer'
}
```

`UpsertUserSeen` (`internal/db/users.go:85-95`) hardcodes the inserted role and
documents that it never changes an existing one:

```go
// ... `viewer`-role row on first sight and bumping updated_at on every
// subsequent login. Never touches an existing row's role — promotion/
// demotion only happens via SetUserRole.
`INSERT INTO users (email, role) VALUES (?, 'viewer') ...`
```

The column default agrees (`internal/db/db.go:37`:
`role TEXT NOT NULL DEFAULT 'viewer'`).

So:

| login | `GetUserRole` returns | resulting session role |
|---|---|---|
| 1st | `""` (no row yet) | `admin` — works |
| 2nd and every one after | `viewer` (written by step 3) | `viewer` — 403 everywhere |

`internal/auth/middleware.go:25-31` re-resolves the role from the users table
on **every request**, so even the first session degrades as soon as the row
exists.

### Why there is no way out

`Session.IsAdmin` (`internal/auth/session.go:38-60`) short-circuits on
`viewer` *before* consulting the `ADMIN_EMAILS` allow-list:

```go
if !s.IsFacetsEmployee() { return false }
if s.Role == "viewer" { return false }   // line 46 — returns before...
if s.Role == "admin"  { return true }
for _, a := range adminEmails {          // line 53 — ...this ever runs
    if a == email { return true }
}
return true
```

So setting `ADMIN_EMAILS` does not help. And the only promotion route,
`POST /api/admin/users/{email}/role`, is itself behind `RequireAdmin`. Once
every row says `viewer`, **no one can promote anyone through the product.**
Recovery today requires a direct write to the `users` table.

### Fix

Three changes, all required:

1. **Seed the row with the role the login already computed.** Replace
   `UpsertUserSeen(email)` with a variant taking the computed default role
   (`UpsertUserSeen(email, defaultRole)`), so a Facets account's first row is
   written as `admin` rather than `viewer`. Keep the "never change an existing
   row" guarantee — only the INSERT branch uses the default.

2. **Restore the `ADMIN_EMAILS` escape hatch.** Move the allow-list check
   *above* the `viewer` short-circuit in `IsAdmin`, so an address explicitly
   listed in `ADMIN_EMAILS` is an admin regardless of the stored role. This is
   the break-glass path; without it, a bad role write is unrecoverable without
   database access.

3. **Repair the existing rows.** Add a one-shot idempotent migration that
   promotes existing Facets-domain rows still sitting at `viewer` to `admin`.
   Run it at startup like the other schema steps in `internal/db/db.go`. Match
   the domain exactly as `IsFacetsEmployee` does — do not invent a second
   domain test (see E-2).

### Do NOT

- Do not make `IsAdmin` ignore `viewer` entirely. An explicitly demoted user
  must stay demoted; the bug is that `viewer` is assigned by accident, not that
  the role exists.
- Do not fix this by setting `ADMIN_EMAILS` in Render and calling it done. With
  the current ordering that env var has no effect, and the next person to log
  in will still be written as `viewer`.
- Do not drop or recreate the `users` table.

### Verification

1. A Go test that logs the same account in **twice** and asserts the session is
   still admin the second time. A single-login test passes against the broken
   code and is worthless here — the bug only appears on the second login.
2. A test that an address in `ADMIN_EMAILS` whose stored role is `viewer`
   resolves as admin.
3. A test that a non-Facets address with role `viewer` is still **not** admin.
4. After deploying: sign in, confirm `/api/admin/catalog/status` returns 200,
   sign out, sign in again, and confirm it still returns 200.

---

## E-2 — The UI and the server decide "admin" by different rules (CRITICAL)

The frontend gates every admin route and nav link on the email domain alone.
`frontend/src/App.tsx:42-49`, duplicated byte-for-byte in
`frontend/src/components/Nav.tsx:7-14`:

```ts
const isFacetsUser = Boolean(
  email && (
    email.endsWith('@facets.cloud') ||
    email.endsWith('@facets.io') ||
    email.includes('@facets.')
  )
)
```

The server additionally requires a role (`session.go:38-60`). The frontend has
no concept of a role at all. That is why the production log shows a user
browsing admin pages while every call behind them 403s: the nav renders, the
page mounts, the data never arrives.

Even after E-1, this stays wrong — a deliberately demoted `viewer` would still
see the full admin navigation.

### Fix

1. `GET /api/auth/me` must return the session's resolved `role` and a computed
   `is_admin`, where `is_admin` comes from the *same*
   `Session.IsAdmin(cfg.AdminEmails)` call the middleware uses. One
   implementation of the predicate, in Go.
2. Replace both copies of `isFacetsUser` with that server-supplied value, read
   from one shared place. Two copies of an authorization rule is how they drift.
3. If `/facets` routes need employee-but-not-admin access, return that as a
   separate server-computed flag. Do not re-derive it from the email string in
   TypeScript.

### Do NOT

- Do not compute `is_admin` in TypeScript from the `role` string. That moves
  the divergence rather than removing it.
- Do not relax the server to match the client. The server is the correct side.

---

## E-3 — 18 of 26 admin API calls turn every error into an empty result (CRITICAL)

`frontend/src/api/admin.ts` exports 26 functions. Eighteen end a failed request
like this:

```ts
if (!res.ok) return []        // or return null, or { jobs: [], total: 0, ... }
```

Exact lines: 27, 95, 118, 149, 169, 198, 233, 248, 254, 291, 306, 312, 318,
333, 382, 388, 405, 411.

A 403 (E-1/E-2), a 500 from a failing `h.db`, an expired session, or a
cold-start timeout on Render's free tier all reach the component as "there is
no data", and the page renders its empty state.

**This is what turned a total admin lockout into a silent one.** The 403s were
in the server log the whole time; the UI showed empty tables. The product could
not distinguish "you are not allowed" from "nothing exists yet" from "the
database is down".

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
3. Map statuses deliberately at the page level: `401` redirects to `/login`
   (the session expired); `403` renders a permission message naming the
   endpoint; everything else renders the status with a Retry control.

How those three states should *look* is specified in
`docs/WORKORDER_admin_ui.md` (U-1). This work order owns getting the error to
the component; that one owns rendering it.

### Do NOT

- Do not leave "return a default on failure" anywhere in `admin.ts`, including
  functions not listed above by line. The pattern is the bug.
- Do not show the error only as a toast while the table still renders its empty
  state. The table must not claim the data is empty.

---

## E-4 — Production is accepting any Google account (HIGH, live)

The running service logs:

```
"level":"INFO","msg":"allowed domains","domains":"*"
```

`ALLOWED_DOMAINS=*` means every Google account on the internet passes the
domain check. The startup guard that refuses `*` is satisfied because
`ALLOW_ANY_DOMAIN=true` is also set.

Non-Facets accounts are not admins, so this is not *by itself* an admin
breach — but it grants authenticated access to the product and whatever a
signed-in non-admin can reach, to anyone at all.

### Fix

This is a configuration change in the Render dashboard, not a code change:
set `ALLOWED_DOMAINS=facets.cloud` (plus any customer domain deliberately
invited) and remove `ALLOW_ANY_DOMAIN`.

Then add a startup `WARN` — not `INFO` — when `ALLOWED_DOMAINS` is `*`, saying
plainly that any Google account can sign in. It is currently logged at `INFO`
alongside routine startup lines, which is why it went unnoticed.

---

## E-5 — The session key in production is the one that was published (CRITICAL, live)

The running service logs:

```
"level":"WARN","msg":"SESSION_SECRET is not valid base64; using raw bytes as AES key"
"level":"WARN","msg":"SESSION_SECRET decodes to more than 32 bytes; truncating to 32 for AES-256","decoded_len":55
```

`decoded_len: 55` identifies it as the placeholder string that was committed in
`render.yaml` in a public repository from 2026-10-13 until commit `d41b2c0`.
That commit stopped it being published but could not rotate it. **It is still
the live key.**

It is the AES key for session cookies, so anyone who read the repository can
forge a cookie for any address. Combined with E-4, that is a complete
authentication bypass.

### Fix

Dashboard action, no code change: set `SESSION_SECRET` in Render to the output
of `openssl rand -base64 32`. Every user is signed out once; that is the only
side effect.

Then make this detectable in code: the two `WARN` lines above are correct but
easy to miss. Fail startup outright when `SESSION_SECRET` is not valid base64
**and** the service is not in dev-bypass mode — a production key should never
be a raw ASCII string, and the current code silently accepts one.

---

## E-6 — Three identical warnings for one condition (LOW)

Startup logs:

```
"WARNING: RESEND_API_KEY is not set — email/Slack notifications disabled"
"WARNING: RESEND_FROM is not set — email/Slack notifications disabled"
"WARNING: SLACK_WEBHOOK_URL is not set — email/Slack notifications disabled"
```

Three lines, one fact, and each claims both email *and* Slack are disabled when
each variable governs only part of that. Emit one line per subsystem stating
what is actually off, e.g. `email notifications disabled (RESEND_API_KEY,
RESEND_FROM unset)` and `Slack notifications disabled (SLACK_WEBHOOK_URL
unset)`. Also log them at `WARN` level rather than `INFO` with "WARNING:"
inside the message string, so level-based filtering works.

---

## Verification

1. **Go**: `go build ./...` and `go vet ./...` clean; the E-1 tests above, the
   E-2 `/api/auth/me` shape test. Run `gofmt -l` only on files you touched —
   most of the repo is CRLF and will be listed spuriously.
2. **Frontend**: `npm run build` in `frontend/` clean. It is a Docker build
   stage, so a TypeScript error fails the deploy.
3. **The one that matters — the second login.** E-1 is invisible to any test
   that signs in once. Sign in, sign out, sign in again, and confirm an
   `/api/admin/*` call still returns 200. Then demote the account to `viewer`
   and confirm the admin nav disappears rather than rendering and failing.
4. **Check the server log, not just the screen.** The whole reason this
   shipped is that the UI looked merely empty while the log showed 403s.

---

## Done

- [ ] A Facets account is still admin on its second and subsequent logins.
- [ ] `ADMIN_EMAILS` works as a break-glass path regardless of stored role.
- [ ] Existing `viewer` rows for Facets accounts repaired by an idempotent
      startup migration.
- [ ] `/api/auth/me` returns server-computed `is_admin`; both copies of
      `isFacetsUser` are gone.
- [ ] No function in `admin.ts` returns a default on failure; all 26 throw,
      with 401 and 403 handled distinctly.
- [ ] `ALLOWED_DOMAINS` no longer `*` in production; `*` logs at WARN.
- [ ] `SESSION_SECRET` rotated in Render; startup refuses a non-base64 key
      outside dev-bypass mode.
- [ ] Notification warnings: one accurate line per subsystem, at WARN level.
