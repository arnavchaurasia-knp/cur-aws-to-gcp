# WORK ORDER — Route & Information-Architecture Redesign

**Status:** Ready for implementation
**Author:** Product/architecture pass (Claude, with repo owner)
**Audience:** Implementation AI agent + human reviewer
**Scope:** Frontend route/nav restructure + a small set of new pages + thin backend deltas. **This is NOT a rewrite** — ~80% of the destination pages already exist and are merely being regrouped, deep-linked, or promoted from admin-only to user-visible.

---

## 0. How to read this document

This work order is written so an implementation model can execute it **without re-deriving the design**. It gives you:

1. The exact **current state** (routes, pages, endpoints) so you don't guess.
2. The **target information architecture** (5 groups) and the rationale.
3. A **per-route mapping table**: for every route, whether to keep / merge / promote / create, which file backs it, and which API it calls.
4. Two **genuinely new features** (full-page logs, Report Override console) spec'd in detail.
5. A **regression / wiring-correctness mandate** (section 8) — the single most important non-feature requirement.
6. **Guardrails** (section 9) — things you must NOT do.
7. A **phased implementation order** (section 10).

**Golden rule:** This is a reorganization. Preserve every existing behavior. When in doubt, move a component, do not rewrite it. If a change would alter an API contract, STOP and flag it (see section 9).

---

## 1. Context & goal

This product ingests an AWS bill (CUR / Cost Explorer CSV / bill PDF) and projects the equivalent Google Cloud cost. It has two audiences:

- **Users** (prospects / customers / sales-assisted): upload a bill, watch the job run, read the report, tweak assumptions, raise support tickets.
- **Admins** (Facets internal team): operate the pipeline, fix bad projections, manage pricing catalog/assumptions, configure the product, manage users.

The current sidebar is **one flat list** mixing both audiences and mixing unrelated admin concerns (operations, pricing math, product config). It "feels random" because there is no grouping — not because routes are missing. Two concrete problems the owner called out:

1. **Manual DB surgery.** To fix a bad projection (remove a bogus line item, override an assumption), a Facets member currently edits data by hand. This must become an auditable in-product action so **any** admin can do it (the "Report Override console", section 7).
2. **No per-user self-serve.** Email/Slack notification prefs, API tokens (needed for MCP), assumptions visibility, and tickets are admin-only or scattered. Users should self-serve these.

**Goal:** Regroup into a coherent IA, deep-link the job sub-views, promote the right things to users, and add the two missing operational features — while keeping every existing feature wired.

---

## 2. Current state inventory (ground truth — do not re-discover)

### 2.1 Frontend routes (`frontend/src/App.tsx`)

| Route | Component | Guard |
|---|---|---|
| `/login` | `Login` | public |
| `/` | `Upload` | user |
| `/releases` | `ReleaseDashboard` | user |
| `/jobs/:id` | `JobStatus` | user |
| `/admin` | `AdminJobs` | admin |
| `/admin/users` | `AdminUsers` | admin |
| `/admin/catalog` | `AdminCatalog` | admin |
| `/admin/assumptions` | `AdminAssumptions` | admin |
| `/admin/report-settings` | `AdminReportSettings` | admin |
| `/admin/integrations` | `AdminIntegrations` | admin |
| `/admin/audit` | `AdminAudit` | admin |
| `/admin/analytics` | `AdminAnalytics` | admin |
| `/admin/notifications` | `AdminNotifications` | admin |
| `/admin/tickets` | `Tickets` | admin |
| `/admin/bills` | `Bills` | admin |
| `/profile` | `Profile` | user |
| `/profile/tokens` | `ApiTokens` | user |
| `/reports/:token` | `SharedReport` | public |
| `*` | redirect | — |

### 2.2 Pages that EXIST but are NOT routed

- **`frontend/src/pages/AdminFindings.tsx`** — a built "failed jobs & issues" page. It is imported nowhere in `App.tsx`. **This is the owner's requested "failed job runs / issues" route, already ~90% built.** Backend endpoints already exist: `GET /api/admin/findings`, `GET /api/admin/jobs/{id}/findings`, `POST /api/admin/findings/{id}/action`.

### 2.3 Components relevant to this work

- `components/Sidebar.tsx` — flat nav, `mainItems[]` + `bottomItems[]`, `adminOnly` flag per item. **This is the primary file you restructure for grouping.**
- `components/JobLogsViewer.tsx` — already supports the 3 log streams (`pipeline | engine | diagnostics`) with level/script/search filters, copy, and "Download full" deep link to `/api/jobs/:id/logs?stream=...`. Currently embedded as a section inside `JobStatus`.
- `components/JobAssumptionsPanel.tsx` — per-job assumptions, already embedded in `JobStatus`, `canEdit` gated by `user.is_admin || job.owner === user.email`.
- `lib/notificationsContext.tsx` — global notification feed. **IMPORTANT: it is hard-gated to `isAdmin`** (feed only fetches when `isAdmin` is true; polling, refresh-on-nav, cross-tab sync all behind that gate). Any promotion of notifications to users requires revisiting this gate.
- `components/NotificationDropdown.tsx` — the bell dropdown. The requested "ticket button like the bell" should mirror this component's pattern.

### 2.4 Backend API surface (`cmd/server/main.go`)

Key existing endpoints (abbreviated):

- **Jobs (user):** `POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{id}`, `/summary`, `/progress`, `/cancel`, `/retry`, `/refine`, `/download`, **`/logs`** (3 streams), `/runs`, `/share`, `/assumptions`, `/assumptions/rerun`, `DELETE /api/jobs/{id}`.
- **Bills:** `GET /api/bills/check`, `POST /api/jobs/existing-bill` (user); `GET /api/admin/bills`, `/{hash}/download|view|preview`, `DELETE /{hash}` (admin).
- **User self-serve:** `GET/PUT /api/user/profile`, **`GET/PUT /api/user/notifications`**, `GET/PUT /api/user/preferences`, `GET/POST/DELETE /api/user/tokens`.
- **Admin ops:** `/api/admin/jobs` (paged), cancel/resume-all, regenerate-report, recalculate, `/api/admin/system/state`, deleted-jobs.
- **Admin pricing:** `/api/admin/catalog/status|refresh`, `/api/admin/assumptions` (+history), `/api/admin/analytics`.
- **Admin config:** `/api/admin/report-settings` (+history, reset), `/api/admin/integrations`, `/api/admin/audit`, `/api/admin/notify/status|test`, `/api/admin/users` (+role, delete).
- **Admin tickets:** `POST/GET /api/admin/tickets`, `GET/PUT/DELETE /api/admin/tickets/{id}`.
- **Admin findings:** `GET /api/admin/findings`, `GET /api/admin/jobs/{id}/findings`, `POST /api/admin/findings/{id}/action`.
- **Admin diagnostics:** `/api/admin/jobs/{id}/diagnostics`, `/diagnostics/records`, `/compare`.
- **Auth/scopes:** token middleware enforces scopes (`ScopeJobsRead`, `ScopeJobsWrite`). MCP (`internal/mcp/tools.go`) proxies through these same endpoints using user API tokens — **so user tokens are load-bearing for MCP.**

---

## 3. Target information architecture — the 5 groups

The organizing principle: **group by the question the user is asking**, not by feature name. Five groups, two sidebar zones.

```
ZONE A — USER (always visible)
  ├─ Work      → "the thing and everything attached to it"
  └─ Me        → "my account / self-serve"

ZONE B — ADMIN (visible only when is_admin)
  ├─ Operations    → "what's running / what's broken?"
  ├─ Pricing       → "is the math right?"
  └─ Configuration → "how does the product behave?"

GLOBAL
  └─ /docs (in-app) → help / API / MCP / runbook  (visible to all)
```

### Rationale (keep this, it explains the non-obvious calls)

- **Logs and per-job assumptions are NOT top-level routes.** They have no meaning without a job, so they are sub-routes / tabs of `/jobs/:id`. A standalone `/logs` would be an orphan. (There are two different "assumptions" concepts: **per-job assumptions** live on the job; **global pricing assumptions/variables** live under Pricing. Keep them distinct.)
- **`/admin` vs `/releases` overlap is resolved by role**, not by deleting either: `/releases` = the user's read-only projection dashboard; `/admin` = the operator's job table with bulk ops. Never maintain two admin job tables.
- **Catalog is too thin to be its own destination** (it's essentially a refresh button). Fold it into the Pricing group alongside Analytics/Assumptions.
- **Audit is a log you read, not a setting you change** — so it sits in Configuration but as its own tab, not merged into a "settings" blob (this is where GPT's blueprint over-merged).
- **Health is the operational heart**, not an afterthought: agy config + live token spend + catalog cardinality = "is the engine OK?". It gets a first-class home under Operations.

---

## 4. Target route tree

```
# ---- PUBLIC ----
/login                         Login
/reports/:token                SharedReport        (public share)

# ---- ZONE A: WORK (user) ----
/                              Upload / Projects
/jobs/:id                      JobStatus (tabbed: Report · Logs · Assumptions)
/jobs/:id/logs                 JobLogs (full page, 3 streams)        [NEW route, existing component]
/jobs/:id/logs/:stream         JobLogs deep-linked to one stream     [NEW]
/jobs/:id/review               ReportOverride console (admin)        [NEW feature — section 7]
/bills                         Bills                 (PROMOTE to user; was /admin/bills)
/releases                      ReleaseDashboard      (user read-only projection dashboard)

# ---- ZONE A: ME (user) ----
/settings                      Settings (tabbed)                      [MERGE wrapper]
  tab: notifications           → email + Slack toggle (per user)
  tab: tokens                  → ApiTokens (API & MCP)
  tab: preferences             → Profile/preferences
/tickets                       Tickets               (PROMOTE to user; shows own tickets)

# ---- ZONE B: OPERATIONS (admin) ----
/admin                         AdminJobs             (All Projections table)
/admin/findings                AdminFindings         (WIRE UP — page already exists)
/admin/health                  AdminHealth                            [NEW — section 6]

# ---- ZONE B: PRICING (admin) ----
/admin/pricing                 (redirect to /admin/pricing/catalog, or a tabbed shell)
/admin/pricing/catalog         AdminCatalog          (+ refresh button)
/admin/pricing/assumptions     AdminAssumptions      (global pricing variables)
/admin/pricing/analytics       AdminAnalytics

# ---- ZONE B: CONFIGURATION (admin) ----
/admin/config/report-settings  AdminReportSettings
/admin/config/integrations     AdminIntegrations     (org Slack/Email)
/admin/config/audit            AdminAudit            (admin change log)
/admin/config/users            AdminUsers            (RBAC + grant admin)

# ---- GLOBAL ----
/docs                          Docs (in-app)                          [NEW, optional/low-priority]
```

> **Route-path note:** moving `/admin/catalog` → `/admin/pricing/catalog` etc. is cosmetically nice but churns deep links and the active-state logic. **Acceptable alternative:** keep the existing flat `/admin/*` paths and only change the **sidebar grouping** (section headers), not the URLs. Decide with the owner; the grouping value is 90% in the sidebar, not the URL. If keeping flat URLs, still add the NEW routes (`/jobs/:id/logs`, `/admin/findings`, `/admin/health`, `/settings`, promote `/bills` + `/tickets`).

---

## 5. Per-route action table

Legend: **KEEP** (no change) · **PROMOTE** (admin→user visibility) · **WIRE** (component exists, add route) · **MERGE** (fold into tabbed shell) · **NEW** (build).

| Target route | Action | Backing file | API | Notes |
|---|---|---|---|---|
| `/` | KEEP | `pages/Upload.tsx` | `POST /api/jobs` | — |
| `/jobs/:id` | ENHANCE | `pages/JobStatus.tsx` | existing | Formalize Report/Logs/Assumptions as tabs; Logs tab links to `/jobs/:id/logs`. |
| `/jobs/:id/logs[/:stream]` | WIRE/NEW | new thin page wrapping `components/JobLogsViewer.tsx` | `GET /api/jobs/:id/logs?stream=` | Component already supports 3 streams + filters. Page = `Layout` + `BackButton` + `JobLogsViewer` with `initialStream` from `:stream`. |
| `/jobs/:id/review` | NEW | `pages/ReportOverride.tsx` | NEW endpoints (section 7) | Admin-only. |
| `/bills` | PROMOTE | `pages/Bills.tsx` | add user-scoped bills list | Expose to users to reuse uploads. Backend currently `/api/admin/bills`; add a user-scoped variant OR relax guard to owner-filtered. |
| `/releases` | KEEP | `pages/ReleaseDashboard.tsx` | `GET /api/jobs` | User read-only dashboard. |
| `/settings` (+tabs) | MERGE | new `pages/Settings.tsx` wrapping existing | `/api/user/notifications`, `/api/user/tokens`, `/api/user/preferences` | Fold `ApiTokens` + notification prefs + preferences. Add Slack field (section 8.3). |
| `/tickets` | PROMOTE | `pages/Tickets.tsx` | needs user-scoped ticket endpoints | Currently admin-only `/api/admin/tickets`. Add `/api/user/tickets` (own tickets) OR owner-filter. |
| `/admin` | KEEP | `pages/AdminJobs.tsx` | `/api/admin/jobs` | — |
| `/admin/findings` | WIRE | `pages/AdminFindings.tsx` | `/api/admin/findings` | **Just add the route + sidebar entry. Highest ROI, lowest effort.** |
| `/admin/health` | NEW | `pages/AdminHealth.tsx` | NEW (section 6) | agy config, token spend, catalog cardinality. |
| `/admin/catalog` | KEEP | `pages/AdminCatalog.tsx` | `/api/admin/catalog/*` | Regroup under Pricing in sidebar. |
| `/admin/assumptions` | KEEP | `pages/AdminAssumptions.tsx` | `/api/admin/assumptions` | Global pricing vars. Optionally expose READ-ONLY to users. |
| `/admin/analytics` | KEEP | `pages/AdminAnalytics.tsx` | `/api/admin/analytics` | — |
| `/admin/report-settings` | KEEP | `pages/AdminReportSettings.tsx` | `/api/admin/report-settings` | Regroup under Configuration. |
| `/admin/integrations` | KEEP | `pages/AdminIntegrations.tsx` | `/api/admin/integrations` | Org-level Slack/Email. |
| `/admin/audit` | KEEP | `pages/AdminAudit.tsx` | `/api/admin/audit` | — |
| `/admin/users` | KEEP | `pages/AdminUsers.tsx` | `/api/admin/users` | Grant admin here. |
| `/admin/notifications` | KEEP | `pages/AdminNotifications.tsx` | `/api/admin/notifications` | Keep as the full feed page behind the bell. |
| `/docs` | NEW (low pri) | `pages/Docs.tsx` | static + link to OpenAPI/MCP | API reference + MCP connection + "how projections work". |

---

## 6. NEW — `/admin/health`

**Purpose:** single "is the engine healthy?" operational dashboard. Owner explicitly asked for agy config, active token usage (full token-spend data), and catalog health by cardinality check, plus "any other health checks."

**Panels:**
1. **agy / engine config** — active engine + version, model id, concurrency, which deterministic scripts run per phase. Source from `GET /api/engine-settings` (exists) + a new `GET /api/admin/health/engine`.
2. **Token usage** — per-engine token spend (prompt/completion/total), cost, per-job and aggregate. Needs a new `GET /api/admin/health/tokens` aggregating from job records.
3. **Catalog health** — cardinality checks: # SKUs, # services, coverage %, vintage/staleness, last refresh. Extend `GET /api/admin/catalog/status` or add `GET /api/admin/health/catalog`.
4. **Pipeline health** — queue depth, running/failed job counts (reuse `/api/admin/system/state`), recent failure rate (reuse findings).

**Backend:** add handlers under `/api/admin/health/*`. Reuse existing aggregations where possible; do not duplicate data sources.

**Frontend:** `pages/AdminHealth.tsx`, card grid, admin-guarded. Cardinality numbers should be computed server-side (do not pull the whole catalog to the browser to count it).

---

## 7. NEW — Report Override console (`/jobs/:id/review`)

**This is the highest-value feature in this work order.** It replaces manual DB surgery with an auditable, in-product, admin-usable action — directly satisfying the owner's goal that *any* Facets member can do via admin what previously required hand edits (and which the LLM sometimes hallucinated).

**Capabilities (per line item of a job's projection):**
1. **Suppress / un-suppress a row** — replaces "manually removing data from the report". Soft flag, reversible.
2. **Override a SKU mapping or rate** — replaces hand-editing an assumption for one job. Operator picks the correct GCP target / rate.
3. **Pin an assumption for this job** — lock a value so re-runs don't revert it.
4. Every change is **written to the Audit Trail** and **triggers a deterministic recalculation** (reuse the existing `recalculate` path — `POST /api/admin/jobs/{id}/recalculate`) so the report reflects the override without a full LLM re-run.

**Integration with findings:** a finding on `/admin/findings` should deep-link to `/jobs/:id/review` with the offending row focused, so "issue → one-click override → audited" is a single flow.

**Backend (new endpoints):**
- `GET  /api/admin/jobs/{id}/line-items` — list projected rows with current mapping/rate/suppressed flag.
- `POST /api/admin/jobs/{id}/line-items/{row}/override` — body: `{suppressed?, gcp_target?, rate?, pinned_assumption?}`. Writes override record, logs to audit, triggers recalculation.
- `DELETE /api/admin/jobs/{id}/line-items/{row}/override` — revert.

**Data model:** store overrides in a per-job overrides table/column that the deterministic recalc chain reads and applies **after** mapping/pricing but **before** report generation. Overrides must survive recalculation (that's the point). Confirm placement with the pipeline owner — the override application point is the one design decision that needs care.

**Frontend:** `pages/ReportOverride.tsx` — a table of line items with inline suppress toggle, target/rate editors, save → toast + recalc. Admin-only. Reuse `ConfirmDialog` for destructive suppress.

> **Note:** this feature has real backend/pipeline depth. If it must be split, ship the route tree + logs page + findings/health first, and treat the Override console as its own follow-up work order. Flag to owner.

---

## 8. REGRESSION / WIRING-CORRECTNESS MANDATE (critical)

> The owner's explicit requirement: **all previously-built features — filtering, sorting, notifications, "and some other things as well" — must stay wired correctly through this reshuffle.** Moving a page must not orphan its behavior.

This is a reorganization, so the biggest risk is **silent breakage**: a component moves, a prop is dropped, a context provider no longer wraps a promoted page, a filter state resets. Treat the following as a hard checklist. **Audit every item; do not assume.**

### 8.1 Features that must remain functional after the move

Audit each of these (they live across ~20 components/pages — `AdminJobs`, `ReleaseDashboard`, `Bills`, `Tickets`, `AdminAudit`, `AdminAssumptions`, `AdminUsers`, `AdminFindings`, `JobList`, `RunHistory`, `JobLogsViewer`, `NotificationDropdown`, etc.):

- **Sorting** — every sortable table keeps its sort keys/order controls.
- **Filtering** — every filter (status, level, script, search, date) keeps working and keeps its state on navigation where it did before.
- **Pagination** — admin job list paging (`AdminListAllPaged`) intact.
- **Notifications** — bell feed, unread count, mark-all-read, cross-tab sync, 12s poll, refresh-on-nav, `triggerNotificationRefresh()` fire points after mutations. **See 8.2.**
- **Share links** — `shareJob` + 90-day link + clipboard.
- **Downloads** — HTML/XLSX/CSV(summary)/CSV(line-item)/JSON/original-bill from `JobStatus`.
- **Job actions** — refine, retry, cancel, delete, regenerate, recalculate, compare runs.
- **Per-job assumptions panel** — edit + rerun, `canEdit` gating.
- **Logs** — 3 streams, filters, copy, download-full (now also reachable at `/jobs/:id/logs`).
- **Token management** — create/revoke/purge user API tokens (MCP-load-bearing).
- **Tickets** — create/list/update/delete lifecycle.

### 8.2 Notification context gate — the trap

`lib/notificationsContext.tsx` currently **only works for admins** (`isAdmin` gates fetch/poll/sync). `App.tsx` passes `isAdmin` into `NotificationProvider`. Consequences you must handle:

- If you promote any notification-bearing surface to users (e.g. a user ticket-status bell), the provider must be allowed to run for non-admins **with a user-scoped feed**, or you add a **separate** user feed. Do **not** silently flip the gate — that would fire admin-only `/api/admin/notifications` for users and 403.
- The requested **"ticket button like the bell"** (appears only when tickets exist, dropdown, clickable tickets) should mirror `NotificationDropdown.tsx` but read from a **ticket** source, not the admin notification feed. Keep it a distinct component/state.

### 8.3 Per-user Slack + email toggle (new, small)

Backend `GET/PUT /api/user/notifications` already exists for per-user prefs. Add:
- an **email on/off** toggle (owner: "at least email mailing thing user should be able to toggle") — if not already present in the payload, add the field.
- a **per-user Slack** destination/toggle — add a Slack field to the user notifications model + the `/settings` notifications tab. Org-level Slack stays in `/admin/config/integrations`; this is the *per-user* opt-in.

### 8.4 Provider / layout wrapping

Every promoted or new page must still be wrapped by the providers it relies on (`ThemeProvider`, `NotificationProvider`, `Layout`, preferences context). Promoting `Bills`/`Tickets` out of the admin block in `App.tsx` must not drop them out of a provider that previously wrapped them.

### 8.5 Verification

- Frontend: typecheck + build (`npm run build` in `frontend/`), click-through every moved route.
- Backend: `go build ./...` + existing tests (`go test ./...`), especially `internal/jobs/admin_panel_testplan_test.go` and `admin_findings_test.go` which assert the route registrations.
- Add a smoke test asserting the new routes register and guards hold (admin routes 302/403 for non-admins; promoted routes 200 for users).

---

## 9. GUARDRAILS — do NOT do these

1. **Do NOT rename the API to `/api/v1/`.** GPT's blueprint suggested it; it is a breaking change that churns the frontend, every issued user token, and the MCP proxy (`internal/mcp/tools.go`) for zero user-visible gain. Keep the existing `/api/...` paths. Add new endpoints under the existing scheme.
2. **Do NOT create a top-level `/logs` or standalone global assumptions route.** Logs belong to `/jobs/:id`. Keep per-job vs global assumptions separate.
3. **Do NOT merge Audit into a generic "settings" page.** It's a read log; keep it its own Configuration tab.
4. **Do NOT flip the notification `isAdmin` gate without a user-scoped feed** (section 8.2).
5. **Do NOT rewrite moved components.** Move/wrap them. Preserve props and behavior.
6. **Do NOT weaken auth scopes** when promoting pages. User-visible bills/tickets must be **owner-filtered** server-side, not just hidden in the UI. Never expose other users' data by relaxing a guard.
7. **Do NOT pull the full catalog to the browser** to compute cardinality — count server-side.
8. If any step forces an API contract change beyond *adding* endpoints/fields, **STOP and flag** to the owner before proceeding.

---

## 10. Phased implementation order

Ship in this order; each phase is independently verifiable.

**Phase 1 — Zero-risk wins (do first)**
- Wire `/admin/findings` → `AdminFindings` (route + sidebar). Page already exists.
- Add `/jobs/:id/logs[/:stream]` full-page route wrapping existing `JobLogsViewer`.
- Restructure `Sidebar.tsx` into the 5 groups (section headers); keep existing URLs if not changing paths.

**Phase 2 — Promotions + /settings**
- Promote `/bills` and `/tickets` to users (with owner-filtered backend endpoints).
- Build `/settings` tabbed shell folding `ApiTokens` + notification prefs + preferences.
- Add per-user email toggle + per-user Slack (section 8.3).
- Add the ticket header button (mirrors `NotificationDropdown`).

**Phase 3 — Health**
- Build `/admin/health` + its `/api/admin/health/*` endpoints.

**Phase 4 — Report Override console** *(largest; may become its own work order)*
- Backend: line-items list + override endpoints + override-aware recalc.
- Frontend: `/jobs/:id/review` console, findings deep-link.

**Phase 5 — Polish**
- `/docs` in-app page.
- Optional: migrate `/admin/*` URLs to grouped paths (`/admin/pricing/*`, `/admin/config/*`) with redirects — only if owner wants the URL change.

**After every phase:** run the section 8 regression checklist for the surfaces touched.

---

## 11. Open decisions for the owner (flag, don't assume)

1. **URLs:** keep flat `/admin/*` (grouping only in sidebar) or migrate to `/admin/pricing/*` + `/admin/config/*`? (Recommendation: keep flat for now; grouping value is in the sidebar.)
2. **Override application point** in the deterministic recalc chain (section 7) — needs pipeline-owner confirmation.
3. **Assumptions to users:** read-only view of global pricing assumptions for users — yes/no?
4. **Report Override console** as part of this order or split into its own?

---

*End of work order.*
