# WORK ORDER ADDENDUM — Route IA, round 2 (role-scoped routes, merges, theming)

**Status:** Ready for implementation
**Supersedes/extends:** `docs/WORKORDER_route_ia_redesign.md`
**Audience:** Implementation AI agent + human reviewer
**Context:** Round 1 wired the routes but a live review found duplicate destinations, a crashing page, and a non-theme-responsive widget. This addendum records the owner's decisions from that review. Scope is small and mechanical — do NOT rewrite pages, only re-scope visibility, merge two pages, and fix styling.

---

## 0. Vocabulary (read first)

- **Customer** = a non-admin end user of the tool (`user.is_admin === false`). A real audience — not everyone is an admin.
- **Admin** = Facets internal team (`user.is_admin === true`).
- Today the sidebar supports only `adminOnly` (show to admins). This addendum introduces the **opposite** need: **customer-only** items (shown to customers, hidden from admins) because admins have an equivalent admin page and the duplicate just clutters their view.

---

## 1. Already done in the previous pass (do not redo)

These are committed/working as of this addendum — verify, don't re-implement:

- **Health crash fixed.** Root cause: `GET /api/admin/health` returned `catalog.total_skus` / `catalog.total_services`, but the page (`AdminHealth.tsx`) reads `health.catalog.sku_count` / `service_count`, so `undefined.toLocaleString()` threw and white-screened. Fixed by aligning the Go JSON tags in `HealthCatalog` (`internal/jobs/admin_handlers.go`) to `sku_count` / `service_count`. **Lesson for the agent: `getJSON<T>()` is an unchecked cast — TS will NOT catch backend/frontend field-name drift. When you add or rename any `/api` field, grep the consuming page for the exact JSON keys.**
- **Two literal sidebar duplicates removed:** "Invite Member" (was a second link to `/admin/users`) and "Support Tickets" (was a second link to `/tickets`).
- **Account group moved into the user dropdown** (`UserMenu` in `components/Nav.tsx`): Settings is reached via the avatar menu ("Profile & Settings" → `/settings`), not the sidebar.
- **Prior security fixes** (owner-scoped ticket delete + bill download/preview/delete) remain in place.

---

## 2. Decision A — Role-scope duplicate routes (customer vs admin)

**Principle (owner's words):** if a page is for customers, show it only to customers; do not also show it on the admin side when an admin-side page already shows the same thing. Apply this to every such pair, not just the dashboard.

### A.1 Add a `customerOnly` capability to the sidebar

In `components/Sidebar.tsx`:
- Extend `SidebarItem` / `SidebarGroup` with `customerOnly?: boolean`.
- Visibility rule per item/group:
  - `adminOnly` → visible only when `isAdmin`.
  - `customerOnly` → visible only when `!isAdmin`.
  - neither → visible to both.
- Update both the group filter (`visibleGroups`) and the per-item filter (`visibleItems` / `visibleBottomItems`) to honor `customerOnly`.

### A.2 The dashboard pair — the concrete case

- `/releases` → `ReleaseDashboard` = **Projection Dashboard**, the **customer's** view.
- `/admin` → `AdminJobs` = **All Projections**, the **admin's** view (same underlying `/api/jobs` data, bulk ops).

**Do:**
1. Mark the sidebar "Work" item `{ to: '/releases', label: 'Projection Dashboard' }` as `customerOnly: true` so **admins no longer see it** (they use All Projections).
2. Keep `/releases` fully functional for customers — it is their only dashboard; do NOT remove the route or the page.
3. Before hiding it from admins, check `ReleaseDashboard` has no unique feature the admin needs that isn't already in `AdminJobs`. If one exists, port it into `AdminJobs` first, then hide. (Round-1 reading showed both use `listJobs` + `downloadURL`; confirm nothing admin-only lives only in `ReleaseDashboard`.)

### A.3 Sweep for other same-thing pairs

Audit every route for the same customer/admin duplication and apply `customerOnly` / `adminOnly` so each concept appears once per audience. Known candidates to check (list, confirm, then scope — do not blindly hide):
- Bills: `/bills` is already the single bills page (owner-scoped backend). Confirm there is no separate admin bills duplicate still linked.
- Any customer-facing page that an admin page restates. Report the final matrix (route → visible to customer? admin?) in your summary.

---

## 3. Decision B — Merge Integrations INTO the Notifications route

**Finding presented to owner:** `/admin/integrations` (Slack/email *config* + a catalog-status panel) and `/admin/notifications` (the event *feed*) are technically different pages, but the owner wants them unified under the Notifications route because to the operator they are one concern, and it reads better there.

**Decision: keep ONE route — the Notifications route — and fold Integrations into it.**

**Do:**
1. In `pages/AdminNotifications.tsx`, add the Slack/email integration configuration (move the config UI from `pages/AdminIntegrations.tsx`): email enable/from/key, Slack enable/webhook, and the test buttons. Prefer a **tabbed** layout on the Notifications page: tab 1 "Feed" (existing), tab 2 "Integrations / Delivery" (the moved config). Keep the feed as the default tab.
2. Reuse the existing API calls as-is: `getIntegrations` / `updateIntegrations` / `testNotify` (`GET/PUT /api/admin/integrations`, `POST /api/admin/notify/test`). **No backend route changes** — only the frontend location of the UI moves.
3. The catalog-status/refresh panel currently also living in `AdminIntegrations` does NOT belong with notifications — move that piece to `/admin/health` (which already has a catalog panel) or `/admin/catalog`, not into the Notifications page.
4. Remove the "Integrations" item from the sidebar Configuration group.
5. Add a redirect: `/admin/integrations` → `/admin/notifications` (in `App.tsx`, same pattern as the existing back-compat redirects) so old links and the dead `Nav.tsx` dropdown entry still resolve.
6. Rename the sidebar item if helpful: "Notifications Feed" → "Notifications" (it now also holds delivery config).
7. After the move, delete `pages/AdminIntegrations.tsx` only once nothing imports it (check `App.tsx` and `Nav.tsx`).

**Guardrail:** secrets are write-only in the current integrations API (blank = keep). Preserve that behavior exactly when moving the fields; do not echo stored webhook/key values into the DOM.

---

## 4. Decision C — Fix the Ticket header button (`components/TicketHeaderButton.tsx`)

**Problem (owner):** it "is not looking good" and has **hardcoded black / is not theme-responsive**. Confirmed in code:
- Badge count uses `text-black` (line ~59) — unreadable / wrong in light and dark themes.
- Dropdown panel uses hardcoded `bg-[#12131d]`, plus `text-white`, `bg-white/10`, `border-white/10`, `bg-white/[0.02]` throughout — all fixed dark values, not theme tokens.

**Do:** restyle to match the app's theming system (the same CSS variables other components use):
- Replace hardcoded colors with the `--fcp-*` design tokens already used elsewhere (e.g. `bg-[var(--fcp-bg-surface)]`, `text-[color:var(--fcp-text-primary)]`, `border-[var(--fcp-border-subtle)]`, `hover:bg-[var(--fcp-hover-overlay)]`). Mirror what `AdminHealth.tsx` and `Layout.tsx` use so light/dark both render correctly.
- Badge: drop `text-black`; use a token that stays legible on the accent fill (e.g. white on the teal `#00C2BB`, or a token pair). Verify contrast in both themes.
- General polish: align the dropdown's radius/shadow/spacing with `NotificationDropdown.tsx` so the two header widgets look like one system (they sit next to each other).
- Keep behavior identical: appears only when there are open tickets (it already filters to non-resolved/closed and shows a count), polls every 30s, items deep-link to `/tickets?ticket=<id>`.

**Verify in BOTH themes** (toggle via the header `ThemeToggle`): the button, badge, and dropdown must be readable and on-brand in light and dark.

---

## 5. Verification (do it live this round, not build-only)

Build/typecheck is necessary but NOT sufficient — the round-1 Health crash compiled cleanly. Run the app and click through.

1. **Start servers with a dev admin session.** Backend honors `DEV_AUTH_BYPASS=true` (see `cmd/server/main.go` ~line 95) which creates a fake `dev@facets.cloud` (admin) session, no Google OAuth. Frontend: `npm run dev --prefix frontend` (Vite on :5173, proxies `/api` to the Go server on :8080). Note: the env flag's name contains "bypass" and may trip a sandbox classifier — the human may need to start the backend, or grant the permission.
2. **To test the customer view**, exercise a non-admin session (the dev bypass is admin; use a real non-admin login, or temporarily assert `is_admin:false` in the UI) and confirm: Projection Dashboard is visible to the customer and the admin does NOT see it; admin sees All Projections.
3. **Click every route** in the sidebar + dropdown. Confirm no white-screen (watch the browser console for the class of error that caused the Health crash — `Cannot read properties of undefined`).
4. **`/admin/health`** renders all four KPI cards with real numbers.
5. **Notifications route** shows both the feed and the moved Integrations config; test-send still works; `/admin/integrations` redirects.
6. **Ticket button** readable in light AND dark.
7. Backend `go build ./... && go test ./internal/...`; frontend `tsc --noEmit` + `npm run build`.

Report the final **route → audience** matrix and screenshots/console-clean confirmation, not just "it compiles."

---

## 6. Still open from the parent work order (unchanged, not in this addendum's scope)

- **Bill→job foreign key:** unique bills are deduplicated in `uploaded_bills`; reference the bill from each job run (FK on content hash) so multiple job runs can point at one stored bill. `UpsertUploadedBill` currently does not persist `owner`, so bill ownership is still effectively shared — decide whether to stamp owner + job linkage together. Frontend display of the link is optional. (Design this separately; it touches the job-creation path.)
- **Report Override console** (`/jobs/:id/review`) — parent doc §7, still its own task.

---

## 7. Summary checklist for the agent

- [ ] Add `customerOnly` to sidebar model + both filters.
- [ ] Mark Projection Dashboard `customerOnly`; verify no admin-only feature lost; keep route/page.
- [ ] Sweep other routes; produce the audience matrix.
- [ ] Move Slack/email config into `AdminNotifications` (tabbed); relocate catalog panel to Health/Catalog.
- [ ] Remove Integrations sidebar item; add `/admin/integrations` → `/admin/notifications` redirect; delete `AdminIntegrations.tsx` once unreferenced; preserve write-only secrets.
- [ ] Re-theme `TicketHeaderButton` with `--fcp-*` tokens; fix `text-black` badge; match `NotificationDropdown`.
- [ ] Live click-through in both themes + both audiences; builds/tests/tsc green.

*End of addendum.*
