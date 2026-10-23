# WORK ORDER — Going live: the standard routes, settings and protections this app is missing

**For an implementing AI with no prior context. Read §0 and §1 first.**

This app currently runs for a small internal audience. Putting it on GCP for
real users raises the bar. This work order lists what a public deployment is
expected to have, split into **P0 (do before launch)**, **P1 (soon after)** and
**P2 (when there are real users)**.

**Do P0 completely before anything else.** Two P0 items are denial-of-service
vectors and one is an access-control default.

---

## 0. What already exists — do not rebuild these

The audit found these already correct:

| thing | state |
|---|---|
| `/health` | unauthenticated, for load-balancer and container probes — correct |
| `/debug/vars` (expvar) | **behind auth** — correctly not public |
| Session cookies | `HttpOnly`, `Secure` (env-aware), `SameSite=Lax` — correct |
| Admin RBAC | `RequireAdmin` middleware + `Session.IsAdmin`, @facets-restricted |
| Admin audit log | real, written by the admin handlers |
| Panic recovery | `middleware.Recoverer` is installed |

**This app handles customers' AWS billing data.** That is commercially
sensitive — spend, service mix, account IDs, sometimes resource ARNs. Several
items below matter more here than they would for a generic CRUD app.

---

## 1. Rules

1. **Do not weaken an existing control to make something work.** If a change
   requires loosening auth, stop and report it.
2. **Default closed.** A new setting's default must be the safe value, not the
   convenient one.
3. **Never log secrets or customer cost data.** Request logs get method, path,
   status, duration, request id, user — not bodies, not query strings that may
   carry tokens.
4. **Every new endpoint states its auth.** Public, authenticated, or admin —
   written in a comment above the route, and matching the block it sits in.

---

# P0 — Before launch

## P0.1 There is no hard upload size limit (DoS / disk exhaustion)

`internal/jobs/handler.go:144` does:

```go
r.ParseMultipartForm(10 << 20)
```

That is **not a size cap**. It is the in-memory buffer size — anything larger
spills to **disk**, with no limit. A single large upload can fill the
container's disk, and the containers here are small.

### Fix
Wrap the body before parsing:

```go
const maxUploadBytes = 64 << 20  // 64 MiB — raise only with a measured reason
r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)
if err := r.ParseMultipartForm(10 << 20); err != nil {
    // MaxBytesReader surfaces as an error here; return 413, not 500
}
```

Return **413 Payload Too Large** with a clear message naming the limit. Check
what the largest legitimate bill in the corpus is before choosing the number —
one is ~9 MB — and leave real headroom.

Also set `Server.ReadHeaderTimeout` and `ReadTimeout` where the HTTP server is
constructed if they are not already set; a slow-body client otherwise holds a
connection indefinitely.

## P0.2 There is no rate limiting anywhere

No endpoint is rate limited — a repo search for rate-limiting middleware finds
nothing. The three that matter:

| endpoint | why |
|---|---|
| `GET /api/auth/login` + callback | login abuse, OAuth cost |
| `POST /api/jobs` (upload) | each job spawns a pipeline — the expensive one |
| `GET /api/reports/{token}` | public, unauthenticated, enumerable |

### Fix
Add a simple per-IP (and per-session where there is one) limiter — a token
bucket in memory is fine for one instance; if you run more than one, note in
your summary that it is per-instance.

Suggested starting limits: login 10/min/IP, upload 5/hour/session,
shared report 60/min/IP. Return **429** with `Retry-After`.

**Do not rate-limit `/health`** — probes will fail and the service will be
marked down.

## P0.3 `ALLOWED_DOMAINS` defaults to `google.com`

`internal/config/config.go:26`:

```go
var DefaultAllowedDomains = []string{"google.com", "facets.cloud"}
```

`google.com` as a login allow-list entry is far broader than intended. Worse,
a deployment setting `ALLOWED_DOMAINS=*` would let **anyone with a Google
account** sign in and upload bills.

### Fix
1. Change the default to `facets.cloud` only.
2. **Reject `*` outright** at startup with a clear fatal error, unless an
   explicit `ALLOW_ANY_DOMAIN=true` is also set. A wildcard must be a decision
   someone typed on purpose, not a value that quietly works.
3. Log the effective allow-list at startup so it is visible in deploy logs.

## P0.4 No security headers

No CSP, HSTS, `X-Content-Type-Options`, `X-Frame-Options` or `Referrer-Policy`
anywhere. The app serves customer cost reports as HTML.

### Fix
Add one middleware, applied globally:

```
Strict-Transport-Security: max-age=31536000; includeSubDomains   (HTTPS only)
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'
```

**Check the generated report HTML against the CSP before enabling it** — the
reports use inline styles, which is why `style-src` allows `unsafe-inline`
above. If they also use inline `<script>`, either hash it or keep scripts out
of the report rather than weakening the policy to `unsafe-inline` for scripts.

Set HSTS only when serving over HTTPS, or local development breaks.

## P0.5 No `/version`

There is `/health` but nothing reports what is deployed. When a projection
looks wrong, the first question is "which build produced it".

### Fix
Add `GET /version` (public, no secrets): git SHA, build time, and the GCP
catalog vintage already available to the health handler. Inject the SHA at
build time via `-ldflags`. Also include it in the report footer and in the
diagnostics summary.

---

# P1 — Soon after launch

## P1.1 Users have no settings page at all

There is `/api/auth/me` and nothing else. Standard, and expected by users:

```
GET    /api/me                  profile: email, name, role, created, last seen
GET    /api/me/sessions         active sessions: created, last used, IP, user agent
DELETE /api/me/sessions         "sign out everywhere" — the important one
GET    /api/me/notifications    per-user preferences
PUT    /api/me/notifications    job complete / job failed, email and/or Slack
```

**"Sign out everywhere" is the one that matters.** Someone who thinks their
laptop was compromised has no way to invalidate sessions today. It also
dovetails with the role-change defect in
`WORKORDER_admin_gaps_and_features.md` §1.1 — both need session invalidation,
so **build that mechanism once and use it for both.**

## P1.2 No data export or deletion

The app stores customers' AWS billing data. There is a job delete, but no way
for a user to export or erase everything about them.

```
POST   /api/me/export       queue an export; returns a download when ready
DELETE /api/me              delete account + owned jobs + uploaded bills
```

Deletion must remove: `jobs` rows, job directories, `report_shares` (so links
die), and uploaded inputs. Keep audit-log entries — they are a security record
— but strip the email to `deleted-user-<id>`.

**Make deletion a two-step confirmation** and write it to the admin audit log.

## P1.3 No retention policy for uploaded bills

Bills and job directories accumulate indefinitely. For data you do not need,
the safest state is not stored.

### Build
A configurable retention window (default 180 days) and a scheduled sweep that
deletes job directories and inputs past it, leaving the summary row and
`job_diagnostics` so history still lists. Show the policy on the upload page:
*"Uploaded bills are deleted after 180 days."*

## P1.4 No request IDs in logs

When a user reports a problem there is no way to find their request. Add a
request-id middleware (chi has one), put the id in every log line and in the
response headers, and show it on the UI error screen so a user can quote it.

---

# P2 — When there are real users

- **`/readyz`** separate from `/health`: ready only when the DB answers and the
  GCP catalog is loaded. Point the load balancer at `/readyz`, probes at
  `/health`.
- **Terms and privacy pages**, linked from login and upload. Needed the moment
  a non-employee uploads a bill.
- **Per-user or per-org quotas** — jobs per day, concurrent jobs.
- **Error reporting** (Sentry or GCP Error Reporting) rather than reading
  container logs.
- **Graceful shutdown** — stop accepting, finish in-flight jobs, then exit, so
  a deploy does not kill a running projection.

---

## 2. Verification

```bash
cd /c/Users/arnav/Downloads/cur-web-AGY && go build ./... && go test ./internal/...
```

Record the baseline test count first. Per item:

- **P0.1** — a 100 MB upload returns **413**, not 500, and leaves no file on
  disk. Confirm disk usage does not grow.
- **P0.2** — exceeding a limit returns 429 with `Retry-After`; `/health` is
  never limited.
- **P0.3** — `ALLOWED_DOMAINS=*` refuses to start without
  `ALLOW_ANY_DOMAIN=true`; the effective list is logged at startup.
- **P0.4** — headers present on every response; **load a generated report and
  confirm the browser console shows no CSP violations.**
- **P0.5** — `/version` returns the SHA; it matches the report footer.
- **P1.1** — "sign out everywhere" makes an existing session fail on its next
  request.
- **P1.2** — after deletion, the user's jobs, directories and share links are
  gone and shared links 404.

### Tests
1. Oversized upload → 413, nothing written.
2. Rate limiter → 429 after N, resets after the window.
3. `ALLOWED_DOMAINS=*` → startup fails without the explicit override.
4. Security headers present on an API response and on a report response.
5. Session invalidation → existing session refused on next request.
6. Account deletion → jobs, directories and shares removed; audit entry email
   anonymised but the entry retained.

---

## 3. Do NOT

- Do **not** rate-limit `/health`.
- Do **not** expose `/debug/vars` publicly — it is correctly behind auth today.
- Do **not** set HSTS on plain HTTP.
- Do **not** add `'unsafe-inline'` to `script-src` to make a report render —
  fix the report instead.
- Do **not** delete audit-log rows during account deletion; anonymise instead.
- Do **not** raise the upload cap to make a large bill work without first
  measuring that bill and saying so.
- Do **not** start P1 before P0 is done — two of those are DoS vectors.

---

## 4. Done

- [ ] P0.1 hard upload cap + 413 + server timeouts
- [ ] P0.2 rate limits on login, upload and shared reports; `/health` exempt
- [ ] P0.3 `ALLOWED_DOMAINS` default narrowed; `*` requires explicit override;
      effective list logged
- [ ] P0.4 security headers; report verified CSP-clean
- [ ] P0.5 `/version` with the build SHA, also in the report footer
- [ ] P1.1 `/api/me`, sessions list, **sign out everywhere** (shared mechanism
      with the role-change fix), notification preferences
- [ ] P1.2 export + account deletion, audit anonymised not deleted
- [ ] P1.3 retention window + sweep + stated on the upload page
- [ ] P1.4 request ids in logs, headers and the UI error screen
- [ ] 6 tests pass; Go suite clean
- [ ] Summary lists what shipped, what was deferred, and anything noticed but
      not fixed
