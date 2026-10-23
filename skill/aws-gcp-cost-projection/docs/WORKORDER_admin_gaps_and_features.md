# WORK ORDER — Admin features that don't do what they appear to, and the gaps worth filling

**For an implementing AI with no prior context. Read §0 and §1 first.**

Part 1 is three defects, ranked by harm. Part 2 is three features.
**Do Part 1 first and completely** — each item is small, and two of them are
things an admin currently believes are working when they are not.

---

## 0. What the audit found

I checked every admin feature for whether it has real effect. **Most of them
work.** These were verified genuinely functional — do not "fix" them:

| feature | verdict |
|---|---|
| Catalog refresh | **Honest.** Returns HTTP 409 with a reason when a runtime refresh cannot persist (image-baked deploys), rather than faking success |
| Analytics | Real — backed by `GetUsageStats()` |
| Audit log | Real — `LogAdminAction` is called from the admin handlers |
| Assumptions | Real — DB-backed, versioned, snapshotted per job, read by the pipeline |
| Admin access control | Real — `RequireAdmin` + `Session.IsAdmin`, restricted to @facets accounts |
| Cancel / Cancel-all / Resume-all | Real |

Three things do **not** behave the way the UI implies. They are Part 1.

---

## 1. Rules

1. **Do not change how a projection is calculated.** Nothing in this work order
   touches the pipeline, rates or mappings.
2. **Security changes get tests.** Items 1.1 and 1.2 are access-control
   defects; each needs a test that fails before your fix.
3. **Admin-only.** Every new endpoint goes in the same admin-only route block
   as the existing `/api/admin/*` routes. Check the surrounding code — if those
   routes sit inside a middleware group, yours go in the same group.
4. **An honest refusal beats a fake success.** Where something cannot work,
   say so in the UI in plain words.

---

# PART 1 — Defects

## 1.1 Changing a user's role does nothing until they log out (HIGH)

**What an admin sees:** the Users page, a role dropdown, set someone to
`viewer`, it saves successfully.

**What actually happens:** `AdminSetUserRole` writes the `users` table and
nothing else. But `Session.Role` is, in its own words
(`internal/auth/session.go:26`):

> "Role is the RBAC role resolved from the `users` table **at login time**"

The role lives in the user's session cookie. `Session.IsAdmin()` reads
`s.Role`. So a user who is already signed in **keeps their old role until the
session ends**.

**Why this matters:** demoting an admin to `viewer` is an access-revocation
action. The admin doing it has every reason to believe access is gone. It is
not. They keep full admin until they happen to log out.

### Fix

Make role changes take effect on the next request. Pick **one**:

- **Preferred — resolve the role per request.** In the auth middleware, after
  loading the session, look up the user's current role from the `users` table
  and use that instead of the cookie value. One indexed lookup per request.
- **Alternative — invalidate the session.** Keep a `sessions_invalidated_after`
  timestamp per user; reject sessions issued before it. More moving parts.

Then, in `AdminSetUserRole`, after a successful write, make the change
effective immediately by whichever mechanism you chose.

### Tests
- a signed-in admin demoted to `viewer` is refused on the **next** admin
  request, with no re-login
- a `viewer` promoted to `admin` is accepted on the next request
- both must **fail before the fix**

---

## 1.1b Granting admin to a non-@facets user silently never works

Same screen, a second and worse failure.

`Session.IsAdmin()` begins with a hard gate:

```go
// Admin is strictly restricted to @facets accounts.
if !s.IsFacetsEmployee() { return false }
```

`IsFacetsEmployee()` requires the email to end `@facets.cloud`, `@facets.io`,
or contain `@facets.`.

The Users page offers the roles `['viewer', 'admin', 'facets']`
(`frontend/src/pages/AdminUsers.tsx:9`) **with no indication of this rule**.
So setting `someone@gmail.com` to `admin`:

1. saves successfully,
2. is stored in the `users` table,
3. is displayed in the UI as `admin`,
4. and **`IsAdmin()` returns false forever.**

No error, no warning, no explanation. The admin has every reason to believe
they granted access. They did not. Combined with 1.1, this feature can appear
to work twice over and never take effect.

**The gate itself is correct — do not remove it.** Restricting admin to
employees is deliberate and right. The defect is that the UI hides the rule.

### Fix

1. **Return the rule from the API.** `AdminSetUserRole` should reject
   `role="admin"` for an email that fails `IsFacetsEmployee()`, with a clear
   message: *"Admin access is restricted to @facets accounts."* Reject with
   400 — do not store a role that can never apply.
2. **Show it in the UI.** Disable the `admin` option for non-@facets users and
   explain why on hover, so the rule is visible before the click, not after.
3. **Surface existing bad state.** Any user already stored as `admin` who is
   not a @facets account is shown with a warning marker — "stored as admin,
   not effective (non-@facets account)". **Do not silently rewrite those rows**;
   show them so someone can decide.

### Also clarify the third role

The dropdown offers `facets`, but `IsAdmin()` only special-cases `viewer` and
`admin` — a `facets` role falls through to the `ADMIN_EMAILS` env check, which
means it behaves as "no explicit role". Either give it a documented meaning or
remove it from the dropdown. **Report which you chose and why** — do not just
delete it if something else depends on it (search for the string first).

### Tests
- `AdminSetUserRole(email="x@gmail.com", role="admin")` is rejected with a
  message naming the restriction
- `AdminSetUserRole(email="x@facets.cloud", role="admin")` succeeds
- a pre-existing non-@facets `admin` row is still refused by `IsAdmin()` and is
  flagged in the users list

---

## 1.2 A shared report link can never be taken back (HIGH)

**What exists:** `POST /api/jobs/{id}/share` creates a row in `report_shares`:

```sql
CREATE TABLE IF NOT EXISTS report_shares (
    token      TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    created_by TEXT,
    created_at DATETIME DEFAULT (datetime('now')),
    expires_at DATETIME
);
```

**What is missing:** everything else. There is **no revoke endpoint, no list
endpoint, and no UI** — a repo-wide search for a revoke or a
`DELETE FROM report_shares` finds nothing. The `expires_at` column exists and
nothing sets it or enforces it.

**Why this matters:** these links go to prospects and contain a customer's AWS
spend. Once created, a link works forever, cannot be listed, and cannot be
withdrawn. If a link reaches the wrong person, there is no action available.

### Fix

1. **Enforce `expires_at`** in `GetSharedReport`: if it is set and in the past,
   return the same "not found" response as an unknown token. **Do not
   distinguish expired from never-existed** in the public response — that
   leaks whether a token was ever valid.
2. **Set a default expiry** on creation. 90 days is a reasonable default; allow
   the caller to pass a shorter one. Existing rows have `expires_at` NULL —
   **treat NULL as "no expiry"** so nothing already shared breaks.
3. **Add admin endpoints**, in the admin-only block:
   ```
   GET    /api/admin/shares              list: token (masked), job, created_by, created_at, expires_at
   DELETE /api/admin/shares/{token}      revoke
   ```
   Revoking deletes the row. Log it with `LogAdminAction`.
4. **Add a Shares panel** to the admin UI listing active links with a Revoke
   button, and show a job's active links on its detail view.

**Mask the token in the list** — show the first 6 characters only. An admin
page should not hand out working links in plain text.

### Tests
- a request with an expired token gets the same response as an unknown token
- a NULL `expires_at` still works (no retroactive breakage)
- a revoked token stops working immediately
- revocation is written to the admin audit log

---

## 1.3 The catalog-refresh refusal is shown as raw JSON (LOW)

The backend is correct — on a deploy where a runtime refresh cannot persist, it
returns 409 with `{"status":"skipped","reason":"..."}`. But
`triggerCatalogRefresh()` in `frontend/src/api/admin.ts` throws the **response
body as a string**, and `AdminCatalog.tsx` renders that string. The admin sees
raw JSON instead of a sentence.

### Fix
Parse the JSON in the client, throw an `Error` carrying `reason`, and render
that sentence. If `reason` is absent, fall back to the status text.

Keep the refusal visible — **do not** turn it into a success toast.

---

# PART 2 — Features worth building

Ordered by value. **2.1 is worth more than the other two combined.**

## 2.1 Compare two runs of a job (admin + customer)

A job already has multiple runs (`GET /api/jobs/{id}/runs`), and the new
regenerate-report feature creates more. Nothing can compare them.

This is the single most useful missing feature, because the question people
actually ask is *"what changed, and why is this number different from last
week?"* — and today the only way to answer it is to open two HTML files side
by side.

### Build
`GET /api/jobs/{id}/compare?from=<runA>&to=<runB>` returning, per service:
AWS cost, GCP on-demand, and the delta between runs — plus a header block
showing what differed in the inputs:

- assumptions version (each job already snapshots `assumptions.json`)
- report-settings version
- catalog vintage
- pipeline/script version if available

Then a UI showing the biggest movers first.

**The header block is the important half.** A number changing is not
interesting; a number changing *because the Aurora ACU assumption changed* is.
Without that, a diff is just two columns.

Both runs' data must still exist. If a run's directory is gone, say so
explicitly rather than showing a one-sided comparison.

## 2.2 Notification health (admin)

`internal/notify/` exists and is gated on `RESEND_API_KEY`, `RESEND_FROM` and
`SLACK_WEBHOOK_URL`. When those are unset, startup logs a warning **to the
server log** and notifications silently never fire. Nobody checking the admin
UI can tell whether notifications are on.

### Build
A small panel on the admin System page: for each channel, configured yes/no,
last send attempt, last success, last error. Plus a **Send test notification**
button.

**Never display the key values** — only whether they are present.

## 2.3 "What we could not price" on the customer report (customer)

Admins will soon have the Job Health panel. Customers get nothing — and the
honest, trust-building version of that is a short, neutral section at the end
of the report:

> **Coverage.** 94% of your AWS workload spend was mapped to a specific GCP
> SKU. The remaining 6% ($1,240) is carried at AWS cost: AWS Marketplace
> purchases, AWS Support, and two services with no direct GCP equivalent.

Build it from data the pipeline already has — the `passthrough_reason` column
and the mapped-versus-workload totals.

**Rules:** neutral and factual, no internal diagnostics, no confidence scores,
no code names. It states coverage; it does not apologise. Gate it on a
`section_coverage` report setting so it can be switched off per the report
customization feature.

---

## 3. Verification

```bash
cd /c/Users/arnav/Downloads/cur-web-AGY && go build ./... && go test ./internal/...
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
```

Record test counts before you start. Baselines today: **1,101** Python tests;
all Go packages passing.

Per item:
- **1.1** — demote a signed-in admin; their next admin request is refused with
  no re-login.
- **1.2** — expired token behaves exactly like an unknown token; NULL expiry
  still works; revoked token dead immediately; audit row written.
- **1.3** — a refused refresh shows a sentence, not JSON.
- **2.1** — comparing two runs of one job shows per-service deltas and the
  input differences; a missing run directory is reported, not hidden.
- **2.2** — the panel reflects reality when a key is removed; the test button
  reports success or the real error.
- **2.3** — the coverage numbers match the report's own totals; the section
  disappears when its setting is off.

---

## 4. Do NOT

- Do **not** change pipeline calculations anywhere in this work order.
- Do **not** show full share tokens in the admin list (§1.2).
- Do **not** tell a public caller that a token *expired* rather than simply
  not existing (§1.2).
- Do **not** make the catalog refusal look like a success (§1.3).
- Do **not** put diagnostics, confidence scores or internal code names in the
  customer coverage section (§2.3).
- Do **not** start Part 2 before Part 1 is done and tested — two of those are
  access-control defects.

---

## 5. Done

- [ ] 1.1 role changes take effect on the next request; 2 tests that failed before
- [ ] 1.1b admin rejected for non-@facets emails with a clear message; UI shows
      the rule up front; existing ineffective admin rows flagged; third role
      clarified or removed; 3 tests
- [ ] 1.2 expiry enforced, default set, list + revoke endpoints, admin panel,
      tokens masked, audited; 4 tests
- [ ] 1.3 refusal rendered as a sentence
- [ ] 2.1 run comparison with the input-difference header
- [ ] 2.2 notification health panel + test send
- [ ] 2.3 customer coverage section, gated by a report setting
- [ ] Both suites clean; Python at baseline + new tests
- [ ] Summary lists what was done and anything noticed but not fixed
