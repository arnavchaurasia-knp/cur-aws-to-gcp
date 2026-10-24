# WORK ORDER — Open items: deploy verifiability, HSTS, and the remaining admin UI

**For an implementing AI with no prior context. Read fully before editing.**
Paths are from the repository root. Line numbers are from commit `0fff21a`.

This is the standing list of what is still open after the admin access and
dialog work landed. Everything here was verified against the working tree and
the live service on 2026-10-24 — none of it is speculative.

**Already done, do not revisit:** the admin role lockout and its tests, the
`admin.ts` error plumbing, the three-state rendering on `AdminCatalog` /
`AdminAnalytics` / `AdminAudit`, the shares API wiring, and the
`ConfirmDialog` replacement of all nine `confirm()` calls.

Do **Part A first**. It is small, and A-1 is what makes every later deploy
verifiable — without it you cannot tell which commit is running.

---

# PART A — You cannot currently tell what is deployed

## A-1 — `build_sha` is permanently `"dev"` (HIGH, do this first)

`cmd/server/main.go:417-420` declares:

```go
// buildSHA and buildTime are injected at build time via -ldflags. Degrade to
// ...
var (
    buildSHA  = "dev"
    ...
)
```

and `/version` serves it (`main.go:409`). But `Dockerfile:23` builds with:

```dockerfile
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /app/cur-web ./cmd/server
```

**There is no `-ldflags`.** Nothing ever injects the values, so production
reports `{"build_sha":"dev","build_time":"unknown"}` forever. The feature is
plumbed end-to-end and dead at the one point that matters.

This is not cosmetic. It is why verifying the HSTS fix (A-2) stalled: the only
observable difference between two deploys was the very header under test, so
checking whether the fix shipped was circular. Any future "is my change live?"
question has the same problem.

### Fix

1. Add build args to the backend stage and pass them through `-ldflags`:

```dockerfile
ARG BUILD_SHA=dev
ARG BUILD_TIME=unknown
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
    -ldflags="-X main.buildSHA=${BUILD_SHA} -X main.buildTime=${BUILD_TIME}" \
    -o /app/cur-web ./cmd/server
```

   Confirm the `-X` paths match the real package and variable names — if the
   vars are not in `package main`, the path differs and `-X` silently does
   nothing rather than erroring. **Verify by reading `/version`, not by
   reading the Dockerfile.**

2. Render exposes the deploying commit as `RENDER_GIT_COMMIT`. Wire it in
   `render.yaml` so the value is real in production, not just locally.

3. As a fallback for when the arg is absent, read `RENDER_GIT_COMMIT` from the
   environment at startup when `buildSHA` is still `"dev"`. A deploy that
   cannot name itself is the problem being fixed; do not leave a path where it
   stays anonymous.

### Verification

Deploy, then `curl https://cur-web-br12.onrender.com/version` and confirm
`build_sha` matches the commit you deployed. A Dockerfile that looks correct
is not evidence — `-X` against a wrong symbol path fails silently.

---

## A-2 — HSTS is still not sent in production (HIGH)

Commit `3158e48` changed `securityHeaders` (`cmd/server/main.go:~451`) to:

```go
if r.TLS != nil || r.Header.Get("X-Forwarded-Proto") == "https" {
    w.Header().Set("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
}
```

The reasoning is sound — Render terminates TLS at its edge, so `r.TLS` is nil
on every live request — and the unit test fails against the old condition and
passes against the new one. The Render log confirms that build went live
(`==> Your service is live`).

**But the live response still carries no `Strict-Transport-Security` header.**
Verified again on 2026-10-24: CSP, `X-Frame-Options`, `X-Content-Type-Options`
and `Referrer-Policy` are all present; HSTS is absent.

So the premise is wrong somewhere. Do **not** guess and patch. Diagnose first.

### Diagnose, in this order

1. **Confirm what is deployed.** Do A-1 first and check `/version`. If the
   HSTS commit is not actually running, everything below is moot.
2. **Log what the app receives.** Temporarily log `r.TLS != nil`,
   `X-Forwarded-Proto`, `X-Forwarded-Ssl` and `Forwarded` for one request to
   `/`, then read the Render log. This answers the question directly instead of
   inferring it. Remove the logging afterwards.
3. The request chain is client → Cloudflare → Render → app (`Server:
   cloudflare`, `x-render-origin-server: Render` on the live response). Either
   hop could be the cause:
   - Render not setting `X-Forwarded-Proto` as assumed, or setting a different
     header;
   - the Cloudflare→Render hop being plain HTTP, which would make `http` the
     *correct* value and would itself be worth knowing, since it means that
     hop is unencrypted.
4. Note that `internal/auth/oauth.go:269` and `:354` already rely on
   `X-Forwarded-Proto == "https"` to decide whether to mark the session cookie
   `Secure`. **If that header is not arriving as `https`, the production
   session cookie is not being marked Secure either** — a second, quieter bug
   with the same root cause. Check this explicitly; do not assume it works
   just because logins succeed.

### Then fix

Whatever step 2 reveals. If the proxy uses a different header, match on it the
same way in both `securityHeaders` and `oauth.go` — one shared helper, not two
expressions that can drift. If the Cloudflare→Render hop is plain HTTP, that is
a Render/Cloudflare configuration issue to raise, not something to paper over
by always sending HSTS.

### Do NOT

- Do not send HSTS unconditionally. It is a one-year browser commitment; if it
  is ever served on a hostname that must speak plain HTTP, users are locked out
  with no server-side undo.
- Do not add a `TRUST_PROXY=true` env var that short-circuits the check without
  first establishing what the proxy actually sends.

---

# PART B — The admin UI items that are still open

These carry over verbatim from `docs/WORKORDER_admin_ui.md`. That document's
U-1 (three panel states) and U-6 (shares) are **done**. The rest are untouched
— verified: `admin_handlers.go` has no `q`, `sort`, `actor` or `action`
parameter, and `Nav.tsx` still has no link to Assumptions, Report Settings or
Shares.

Read `docs/WORKORDER_admin_ui.md` for the full detail of each. Summarised here
so this file stands alone:

## B-1 — Search only searches the page you are looking at (HIGH)

`AdminListAllPaged` (`internal/jobs/admin_handlers.go:33-70`) takes `limit`,
`offset`, `status`, `from`, `to` — **no search term**. Page size is 50
(`admin_handlers.go:28`). `AdminJobs.tsx` filters client-side with `useMemo`
over the loaded page only, while the input promises "Search prospect, owner,
ID, status…".

A prospect on page 7 returns "no results". Add `q` to the handler, plumb it
into **both** `ListAllJobsFiltered` and `CountAllJobs` so the total matches the
filter, match case-insensitively with a parameterised
`LIKE '%' || ? || '%'` (never concatenated), debounce ~300 ms, reset `offset`
to 0 on change, and delete the client-side filter so there is one filter rather
than two that disagree. Same for the deleted-jobs list.

`AdminUsers` also filters client-side but its list is unpaginated, so it is
correct. **Leave it alone.**

## B-2 — The jobs table omits the product's own headline number (HIGH)

Columns today: **When, Prospect, Status, AWS Spend, Exports, Actions.**

This is an AWS→GCP cost-projection product whose main admin list shows only the
AWS side. `gcp_od` lives on `RunResult` (`frontend/src/api/jobs.ts:13`), not on
the `Job` row, so this needs a backend change: join each job's latest run in
`ListAllJobsFiltered` and return `gcp_od` alongside `aws_spend`. Add **GCP
(OD)** and **Ratio** columns, and flag an implausible ratio visually (outside
roughly 0.5x–1.5x) so a bad run can be spotted by scanning.

Also add an **Owner** column. `owner` is already on the `Job` type and is
already shown in the *deleted*-jobs table — but not the main one, while the
search box offers to search by it. You can search a field you cannot see.

## B-3 — No sorting; pagination is prev/next only (MEDIUM)

No admin table sorts. Pagination is two buttons (`AdminJobs.tsx:596-604`) with
a fixed page size and no jump-to-page. Finding the largest bill means paging
through everything.

Add server-side `sort` (`created_at`, `aws_spend`, `prospect`, `status`) and
`dir` (`asc`/`desc`), **validated against an allow-list** and mapped to column
names in Go — never interpolate a client string into `ORDER BY`. Make headers
clickable with an indicator, keep sort and query in the URL so a view can be
shared, add a page-size control (the handler already caps `limit` at 500), and
show "Page 2 of 7 · 331 jobs".

Sorting must be server-side; sorting the loaded page would reproduce B-1 in a
new place.

## B-4 — Two pages still have no link to them (HIGH)

`AdminAssumptions` and `AdminReportSettings` are built, wired and routed
(`App.tsx:62-63`) but absent from `Nav.tsx` — reachable only by typing the URL.
They control the assumptions and report settings every projection uses. The new
Shares UI has the same problem.

Six links already crowd the bar at `sm:` width and the mobile branch exposes
only two, so do not just append more. Keep **All Reports, Logs & Engine,
Users, Catalog** top-level and move **Analytics, Audit, Assumptions, Report
Settings, Shares** into one "Settings" dropdown. `UserMenu` (`Nav.tsx:58`) is
an existing dropdown — follow its open/close and keyboard handling rather than
adding a second implementation. The mobile branch must reach every admin page.

## B-5 — The audit log cannot be filtered (MEDIUM)

`AdminAuditLog` (`internal/jobs/admin_handlers.go:379-392`) takes only `limit`
and `offset`. An audit log you can only page through blindly does not answer
the question an audit log exists for.

Add `actor`, `action` and `from`/`to`, reusing the date parsing already written
for `AdminListAllPaged` (`admin_handlers.go:48-60`) so both endpoints treat
dates identically. Surface them on `AdminAudit.tsx`. Same parameterised-SQL
rule as B-1.

---

# PART C — Not code: two dashboard actions still outstanding

Neither can be done from the repository, and the first is **deploy-blocking**.

1. **Rotate `SESSION_SECRET` in Render** to `openssl rand -base64 32`. The
   value published in `render.yaml` in a public repo from 2026-10-13 until
   commit `d41b2c0` is still the live key — the startup log confirms it
   (`not valid base64`, `decoded_len: 55`). Commit `0fff21a` now **refuses to
   start** on a non-base64 secret, so this must be rotated *before* that commit
   deploys or the service will not boot.

2. **Set `ALLOWED_DOMAINS=facets.cloud`** and remove `ALLOW_ANY_DOMAIN`. The
   live log shows `"allowed domains","domains":"*"` — any Google account can
   authenticate today. `0fff21a` logs a warning when wildcarded but does not
   change the behaviour.

---

## Out of scope here

Pipeline and pricing work is tracked separately and is **not** part of this
work order: the OS-license component for Windows/RHEL/Ubuntu Pro EC2, the
SP/RI netting policy, NAT gateway-hour modelling, `classify_mechanics`
consuming `canonical_service`, and Fargate Autopilot rate loading. Those have
their own documents under `skill/aws-gcp-cost-projection/docs/`.

---

## Verification for this work order

1. `/version` reports the real deployed commit (A-1), checked against the live
   service — not inferred from the Dockerfile.
2. The live response carries `Strict-Transport-Security`, **and** the session
   cookie is confirmed `Secure` in production (A-2).
3. `npm run build` clean; `go build ./...`, `go vet ./...`, `go test ./...`
   clean. Run `gofmt -l` only on files you touched — most of the repo is CRLF
   and will be listed spuriously.
4. Go tests that `q`, `sort` and `dir` are honoured, that an invalid `sort`
   value is **rejected rather than interpolated**, and that `CountAllJobs`
   returns the count of the filtered set.
5. With more than 50 jobs, search for a prospect known not to be on page 1 and
   confirm it is found and the count matches.
6. Every admin page reachable by clicking from the nav alone, at desktop and
   mobile widths, with no typed URLs.

---

## Done

- [ ] `/version` reports the deployed commit in production.
- [ ] HSTS present on the live response, root cause understood and the session
      cookie's `Secure` flag verified with it.
- [ ] Job and deleted-job search are server-side, parameterised, counts match,
      offset resets.
- [ ] Jobs table shows Owner, GCP (OD) and Ratio, with implausible ratios
      flagged.
- [ ] Server-side sorting on allow-listed columns; sort and query in the URL;
      page-size control and a real pagination summary.
- [ ] Assumptions, Report Settings and Shares reachable from the nav at both
      widths.
- [ ] Audit log filterable by actor, action and date range.
- [ ] `SESSION_SECRET` rotated and `ALLOWED_DOMAINS` tightened in Render.
