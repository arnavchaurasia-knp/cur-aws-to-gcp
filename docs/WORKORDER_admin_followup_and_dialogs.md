# WORK ORDER — Follow-up: fix the regressions in the admin access work, and replace native `confirm()`

**For an implementing AI with no prior context. Read fully before editing.**
Paths are from the repository root. Line numbers are from the **uncommitted
working tree** on top of commit `bedf58b` — the state the previous pass left
behind. If a line has moved, match on the quoted code, not the number.

This follows `docs/WORKORDER_admin_errors_and_access.md`. That work was mostly
done correctly: the role-ordering lockout is genuinely fixed, `ADMIN_EMAILS`
works as a break-glass path, all 18 error-swallowing returns are gone, and
`AdminJobs` got thorough per-endpoint error handling. **Do not revisit those.**

What follows is what that pass broke or left undone.

**Part A is blocking — the frontend does not currently build, so nothing can
deploy.** Do Part A before Part B.

---

# PART A — Regressions and gaps

## A-1 — The frontend build fails (BLOCKING)

```
src/components/Nav.tsx(9,9): error TS6133: 'isFacetsEmployee' is declared but its value is never read.
```

`npm run build` is a Docker build stage (`Dockerfile:11`), so this fails the
image build. Nothing ships until it is fixed.

It is not just an unused variable — it marks a real inconsistency.
`frontend/src/components/Nav.tsx:9` computes:

```ts
const isFacetsEmployee = Boolean(user?.is_facets_employee)
```

…but the link to `/facets` is inside the `isAdmin` block
(`Nav.tsx:34`):

```tsx
<Link to="/facets" className={navItemClass('/facets')}>Logs &amp; Engine</Link>
```

while `frontend/src/App.tsx` routes that page on `isFacetsEmployee`. So a
Facets employee holding the `viewer` role can open `/facets` by typing the URL
but is shown no link to it.

### Fix

Gate the `/facets` link — in **both** the desktop block and the `sm:hidden`
mobile block (`Nav.tsx:39-43`) — on `isFacetsEmployee`, and keep the admin
links on `isAdmin`. The two groups have different audiences; render them as
two conditionals rather than one.

Do **not** fix the build by deleting the variable or adding `void
isFacetsEmployee`. That would silence the compiler and keep the wrong
behaviour.

---

## A-2 — Three admin pages now hang on "Loading…" forever (BLOCKING for users)

`admin.ts` now throws on failure, which is correct. But only `AdminJobs` and
`AdminUsers` were updated to catch. Three pages call throwing functions with
no handler at all:

| page | call | line |
|---|---|---|
| `AdminCatalog.tsx` | `await Promise.all([getCatalogStatus(), getNotifyStatus()])` inside `refresh()`, invoked by a bare `useEffect(() => { refresh() }, [])` | 22-27 |
| `AdminAnalytics.tsx` | `getAnalytics().then(setStats)` | 14 |
| `AdminAudit.tsx` | `listAuditLog({ limit: PAGE_SIZE, offset }).then(setEntries)` | 16 |

All three use `null` as the "still loading" sentinel and render
`{x === null && <p>Loading…</p>}`. When the promise rejects the setter never
runs, so state stays `null` and **the page shows "Loading…" indefinitely**,
with an unhandled promise rejection in the console.

`AdminAudit` is a *regression*: it previously received `[]` and rendered "No
admin actions logged yet." It now spins forever.

`AdminCatalog` is the worst case — `getCatalogStatus` and `getNotifyStatus`
are the exact two endpoints observed returning 403 in the production log that
prompted this whole effort.

### Fix

1. Extract the error handling `AdminJobs.tsx:77` already implements
   (`handleApiError(err, endpoint)`) into a shared module —
   `frontend/src/components/adminError.ts` or similar — and have `AdminJobs`
   import it rather than keeping a private copy. One implementation.
2. Give each of the three pages an explicit `error` state alongside its
   existing data state, set it in a `.catch`, and render three **distinct**
   states: loading, error, empty. A `403` must read as a permission problem;
   a `401` should redirect to `/login`.
3. Never leave the loading sentinel as the failure state. If a page uses
   `null` to mean "loading", a rejected promise must move it out of `null`.
4. Keep the `Nav` rendered in the error state so the admin can navigate away.

### Verification

For each of the three pages, force the request to fail (DevTools request
blocking, or sign in as a `viewer`) and confirm the page shows an error, not
a spinner. A page that still says "Loading…" after a failed request has not
been fixed.

---

## A-3 — The role migration undoes demotions on every restart (HIGH)

`internal/db/db.go:192` runs inside `Open()`:

```sql
UPDATE users
SET role = 'admin', updated_at = datetime('now')
WHERE role = 'viewer'
  AND (email LIKE '%@facets.cloud' OR email LIKE '%@facets.io' OR email LIKE '%@facets.%')
```

`Open()` runs on **every** process start. The statement is idempotent in the
sense that re-running changes nothing new, but it is not one-shot: it
re-promotes *any* Facets-domain user sitting at `viewer`, including someone an
admin deliberately demoted five minutes earlier. On a platform that restarts
containers routinely, demotion of a Facets employee never sticks.

Since `IsAdmin` treats a Facets account as admin unless the role is explicitly
`viewer`, demoting to `viewer` is the *only* way to restrict a Facets
employee — and this statement cancels it.

The original work order said "one-shot" without saying how to make it so.
That was an under-specification; this is the correction.

### Fix

Guard it with a persisted marker so it runs exactly once per database. Create
a small `schema_migrations` table (`name TEXT PRIMARY KEY, applied_at TEXT`)
if the codebase has no migration bookkeeping yet, record the repair under a
stable name such as `repair_facets_viewer_roles_2026_10`, and skip the UPDATE
when the row exists. Insert the marker in the **same transaction** as the
UPDATE, so a crash between the two cannot leave it half-applied.

Do **not** key the guard on "are there any viewer rows left" — that is the
same not-one-shot behaviour with extra steps.

---

## A-4 — The notification warning prints an env var name as the subsystem (MEDIUM)

`internal/config/config.go:129`:

```go
log.Printf("WARNING: %s is not set — %s notifications disabled", k,
    strings.TrimSuffix(strings.ToLower(k), "_API_KEY"))
```

`strings.ToLower(k)` lowercases first, then `TrimSuffix` is given the
**uppercase** `"_API_KEY"`, which can never match. The output is:

```
WARNING: RESEND_API_KEY is not set — resend_api_key notifications disabled
WARNING: RESEND_FROM is not set — resend_from notifications disabled
WARNING: SLACK_WEBHOOK_URL is not set — slack_webhook_url notifications disabled
```

Three lines, each naming an environment variable where a subsystem name
belongs. This is worse than the original it replaced.

### Fix

Drop the string-munging. Emit one line per subsystem, naming the variables
that subsystem is missing:

```
email notifications disabled (RESEND_API_KEY, RESEND_FROM unset)
Slack notifications disabled (SLACK_WEBHOOK_URL unset)
```

Log them through the structured logger at `WARN` level, not `log.Printf` with
`"WARNING:"` inside the message text — the rest of the service emits
`{"level":"WARN",...}`, and level-based filtering cannot see a string prefix.

---

## A-5 — The lockout fix has no tests (HIGH)

`Session.IsAdmin` and `UpsertUserSeen` have **no test coverage at all**
(`internal/auth/session_test.go` and `internal/auth/middleware_test.go` do not
reference either). The bug that locked every admin out of production is
currently pinned by nothing.

### Fix

Add these four. The second one is the point of the exercise:

1. `UpsertUserSeen(email, "admin")` creates the row with role `admin`, and a
   second call with `"viewer"` does **not** change the existing row — the
   documented "never touches an existing row's role" guarantee.
2. **A Facets account is still admin on its second login.** Drive the same
   path the handler does: resolve role, upsert, resolve again. A single-login
   test passes against the original broken code and proves nothing — the bug
   only appeared from the second login onward.
3. An address in `ADMIN_EMAILS` whose stored role is `viewer` resolves as
   admin (the break-glass path).
4. A non-Facets address is not an admin regardless of role or `ADMIN_EMAILS`.

Each test must fail if the specific guard it covers is reverted. Verify that
by actually reverting the guard, running the test, and restoring it.

---

## A-6 — A deployment comment was deleted (LOW, but restore it)

The previous pass removed this from `internal/config/config.go`:

```go
// ANTIGRAVITY_API_KEY is intentionally NOT in the required list. It must be
// set in the host environment (VM-wide on prod, shell on dev) so the
// spawned agy subprocess inherits it via os.Environ(). GEMINI_API_KEY is
// ignored by agy — use ANTIGRAVITY_API_KEY.
```

It documents why a required-looking variable is absent from the required list,
and that a plausible-looking alternative is ignored. Nobody reconstructs that
from the code. Restore it near the `required` slice.

---

## A-7 — Minor cleanups

- `frontend/src/api/http.ts:19` — `getJSONOrThrow` is a dead alias that just
  calls `getJSON`. Delete it; nothing imports it.
- `internal/config/config.go` — the file now ends without a trailing newline.
  Restore it.
- `internal/config/config.go:138` — the `SESSION_SECRET` check decodes the
  value twice (once for the error check, once for the length). Decode once and
  reuse.
- The `ALLOWED_DOMAINS=*` guard was relocated, not extended. Add the `WARN`
  the original work order asked for, so a wildcard is visible in the log
  rather than passing silently once `ALLOW_ANY_DOMAIN=true` is set.

---

## A-8 — Deployment ordering (read before shipping Part A)

`internal/config/config.go:138` now **refuses to start** when `SESSION_SECRET`
is not valid base64. The current production value is not valid base64 — the
live log shows `"SESSION_SECRET is not valid base64; using raw bytes as AES
key"` and `decoded_len: 55`.

This check is correct and should stay. But it means:

> **Rotate `SESSION_SECRET` in the Render dashboard to `openssl rand -base64 32`
> BEFORE deploying this change.** Deploying first takes the service down on
> boot.

State this in the PR description so whoever merges sees it.

---

# PART B — Replace native `confirm()` with a proper dialog

## B-1 — What is actually there

There are **zero** `alert()` calls in the codebase. The pattern to replace is
**nine `window.confirm()` calls**, and `sonner` is already installed, mounted
(`App.tsx:17`, `<Toaster … />`) and used for notifications in four files.

So this is not "add toasts". Toasts already exist and are used correctly for
*outcomes*. The gap is that **decisions** still use a blocking browser dialog.

**Do not replace `confirm()` with a toast.** A toast is non-blocking and
returns nothing; `confirm()` blocks and returns a boolean the caller branches
on. Swapping one for the other would let destructive actions run without an
answer. A confirmation needs a modal dialog.

### The nine sites

| file:line | action |
|---|---|
| `components/JobList.tsx:43` | Delete report for a prospect |
| `pages/AdminJobs.tsx:155` | EMERGENCY stop-all (first confirm) |
| `pages/AdminJobs.tsx:156` | EMERGENCY stop-all (second, double-confirm) |
| `pages/AdminJobs.tsx:203` | Cancel this job |
| `pages/AdminJobs.tsx:218` | Archive & delete job |
| `pages/AdminJobs.tsx:270` | Revoke share link |
| `pages/AdminReportSettings.tsx:96` | Reset all report settings |
| `pages/JobStatus.tsx:309` | Delete this job run |
| `pages/JobStatus.tsx:322` | Stop this estimation run |

## B-2 — What to build

`package.json` currently has only `react`, `react-dom`, `react-markdown`,
`react-router-dom`, `recharts` and `sonner`. There is no dialog primitive.

Add **`@radix-ui/react-alert-dialog`** — the accessible primitive that
shadcn/ui's AlertDialog wraps, and the de-facto standard for this in the React
ecosystem. Pin an exact version. It gives focus trapping, focus restore on
close, `Escape` to dismiss, scroll locking and correct ARIA roles, all of
which a hand-rolled `<div>` modal gets wrong.

Build **one** `ConfirmDialog` component and use it at all nine sites. It must
take: title, body, confirm label, a `destructive` flag for styling, and an
`onConfirm` callback. Style it with the existing Tailwind tokens so it matches
the app's dark theme — match the surfaces already used in `Nav.tsx`
(`bg-[#0a0a0f]`, `border-white/10`) and the accent `#00C2BB`.

Keep the division of labour: **dialog asks, toast reports.** After the user
confirms, the existing `toast.success` / `toast.error` calls stay exactly as
they are.

## B-3 — Specific behaviour to preserve

- **The double-confirm at `AdminJobs.tsx:155-156` exists on purpose.** It
  guards halting every running job across the host. Do not collapse it to a
  single plain dialog. Replace it with **one** dialog that requires typed
  confirmation — the user types `STOP` to enable the confirm button. That is
  the industry pattern for irreversible global actions and is stronger than
  two dialogs people click through.
- Every dialog's confirm button must be disabled while the action is in
  flight, so it cannot be double-submitted. Several of these call endpoints
  that are not idempotent.
- Keep each existing message's specifics — the prospect name, the "moved to
  Deleted Jobs archive" reassurance, the "Anyone with this URL will see 404"
  consequence. Those are good copy; they just need a better container.
- The destructive confirm button is the *secondary* visual action; Cancel
  takes focus on open, so `Enter` does not fire a deletion.

## B-4 — Do NOT

- Do not leave any `window.confirm` / `confirm(` behind. Verify with
  `grep -rnE "\b(alert|confirm|prompt)\(" frontend/src/` returning nothing.
- Do not add a second toast library, or a second dialog implementation. One of
  each.
- Do not use a toast with "Undo" instead of a confirmation for deletes. That
  is a valid pattern in general, but these actions hit the server immediately
  and several cannot be undone.
- Do not change what any of the nine actions *do*. This is a presentation
  change only.

---

## Verification for the whole work order

1. `npm run build` in `frontend/` **clean** — this is the gate A-1 exists for.
2. `go build ./...`, `go vet ./...`, `go test ./...` all clean, including the
   four new tests from A-5. Run `gofmt -l` only on files you touched — most of
   the repo is CRLF and will be listed spuriously.
3. Sign in as an admin and confirm every admin page loads real data.
4. Force a failure on `AdminCatalog`, `AdminAnalytics` and `AdminAudit` and
   confirm each shows an error rather than a permanent "Loading…".
5. Restart the service twice with a Facets user demoted to `viewer`, and
   confirm the demotion survives both restarts (A-3).
6. Exercise all nine confirmation dialogs: cancel, confirm, `Escape`, and
   `Enter` on open. Confirm `Enter` does not trigger a destructive action.
7. `grep -rnE "\b(alert|confirm|prompt)\(" frontend/src/` returns nothing.

---

## Done

- [ ] `npm run build` clean; `/facets` link gated on `is_facets_employee` in
      both desktop and mobile nav.
- [ ] `AdminCatalog`, `AdminAnalytics` and `AdminAudit` render loading, error
      and empty as distinct states, using the shared error helper extracted
      from `AdminJobs`.
- [ ] The role repair runs once per database, recorded in a migrations table,
      and a demotion survives restarts.
- [ ] One accurate `WARN` line per notification subsystem, through the
      structured logger.
- [ ] Four tests covering `IsAdmin` and `UpsertUserSeen`, including the
      second-login case, each verified to fail when its guard is reverted.
- [ ] `ANTIGRAVITY_API_KEY` comment restored; `getJSONOrThrow` deleted;
      trailing newline restored; secret decoded once; `ALLOWED_DOMAINS=*`
      logs a WARN.
- [ ] PR description states that `SESSION_SECRET` must be rotated in Render
      **before** this deploys.
- [ ] All nine `confirm()` sites replaced by one `ConfirmDialog`; stop-all uses
      typed confirmation; no `alert`/`confirm`/`prompt` left in the codebase.
