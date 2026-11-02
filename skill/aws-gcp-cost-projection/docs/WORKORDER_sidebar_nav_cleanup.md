# WORKORDER — Sidebar / left-nav cleanup

**For the implementation agent. Read fully. Verify every claim in Phase 0 before writing code.**
Self-tracking: run the probes, fill RESULT, tick the boxes, append to the PROGRESS LOG.
Scope is the **left sidebar** (`frontend/src/components/Sidebar.tsx`) plus one route fix in
`frontend/src/App.tsx`. Frontend-only, no backend changes.

---

## 0. ORIENTATION PROBES — run first, fill RESULT

| # | Probe (from repo root) | Tells you | RESULT |
|---|---|---|---|
| P1 | read `frontend/src/components/Sidebar.tsx` (`mainItems`, `bottomItems`) | the current left-nav item list + labels + routes | |
| P2 | read `frontend/src/App.tsx` `<Routes>` | which routes actually exist (an item pointing at a missing route falls through to `path="*"` -> Navigate home) | |
| P3 | `grep -rn "AdminFindings\|/admin/findings" frontend/src` | whether the Review Findings PAGE exists and is imported anywhere | |
| P4 | read `frontend/src/components/Nav.tsx` (settings dropdown) | where Assumptions is linked today (top-right ⚙ dropdown, NOT the sidebar) | |
| P5 | `grep -rn "useTitle\|<title" frontend/src/pages/AdminAssumptions.tsx frontend/src/pages/ReleaseDashboard.tsx frontend/src/pages/AdminJobs.tsx` | the in-page titles, so sidebar labels and page headers don't contradict each other | |

Known facts to confirm, not assume:
- `/admin/findings` is in the sidebar but has **no route in App.tsx** -> it redirects to `/`
  (the dead "Review Findings" link). `AdminFindings.tsx` exists but is unrouted.
- `/admin/assumptions` HAS a route and a page but is **absent from the sidebar**; it is only
  reachable from the Nav.tsx settings dropdown.

---

## 1. GOAL
A coherent left sidebar where every item goes somewhere real and the labels match what each
page is: add **Pricing Assumptions**, remove the dead **Review Findings** link, and apply the
agreed renames.

---

## 2. CHANGES (decided with user 2026-11-01)

### 2a. Remove the dead link
- [ ] Delete the `Review Findings` item (`/admin/findings`) from `mainItems` in Sidebar.tsx.
      (The `AdminFindings.tsx` page stays in the tree, just unlinked — do NOT delete the file.)
      NOTE: if a later decision is to KEEP findings, the correct fix is instead to register a
      route in App.tsx (`<Route path="/admin/findings" ... >`) and import AdminFindings — but
      per the user it is removed for now.

### 2b. Add Pricing Assumptions
- [ ] Add a `mainItems` entry: `{ to: '/admin/assumptions', label: 'Pricing Assumptions',
      icon: <AssumptionsIcon/>, adminOnly: true }`. Place it next to `Report Settings` (both are
      pricing-config pages). Add a small `AssumptionsIcon` SVG in the same style as the others
      (e.g. sliders/tune glyph), or reuse an existing icon if a fitting one exists.
- [ ] Verify `/admin/assumptions` already has its route in App.tsx (it does) so the link works.
      Keep the existing Nav.tsx dropdown link too, or remove it to avoid duplication — the user
      can decide; default: keep both (dropdown + sidebar) since they serve different habits.

### 2c. Renames (labels only; routes unchanged)
Apply exactly this mapping in `mainItems` (confirm the two **Release*** ones with the user if
their intended product meaning differs from "projection"):

| Route | Current label | New label |
|---|---|---|
| `/releases` | Release Dashboard | **Projection Dashboard** |
| `/admin` | Release Timeline | **All Projections** |
| `/` | Projects | Projects (unchanged) |
| `/admin/bills` | Uploaded Bills | Uploaded Bills (unchanged) |
| `/admin/catalog` | Pricing Catalog | Pricing Catalog (unchanged) |
| `/admin/analytics` | Cost Analytics | Cost Analytics (unchanged) |
| `/admin/audit` | Audit Trail | Audit Trail (unchanged) |
| `/admin/notifications` | Notifications | Notifications (unchanged) |
| `/admin/report-settings` | Report Settings | Report Settings (unchanged) |

- [ ] If a renamed sidebar label now contradicts the page's own in-page `<h1>`/`useTitle`
      (e.g. page still says "Release Dashboard"), update the page header to match so the nav
      and the page agree. List any you changed in the log.

---

## 3. INVARIANTS
- No route change except (optionally) registering `/admin/findings` if the KEEP path is chosen.
- `adminOnly` flags preserved: Projects, Projection Dashboard, Docs, Raise a Ticket stay
  visible to all; everything else stays admin-gated exactly as today.
- Icons stay visually uniform (same `w-5 h-5`, stroke style) — no mismatched icon set.
- Active-state highlighting (`isActive`) still works for every item, including the new one.
- Collapsed (64px rail) and expanded (256px) both render the new item with a tooltip.

## 4. PHASES
### Phase 1 — Edits
- [ ] 2a remove, 2b add, 2c renames in Sidebar.tsx; any page-header reconciliations.

### Phase 2 — Verify
- [ ] `npm run build` in frontend clean.
- [ ] Load the app (both collapsed + expanded sidebar): Pricing Assumptions navigates to the
      working `/admin/assumptions` page; Review Findings is gone; no item redirects to home.
- [ ] Non-admin session: admin-only items hidden; Projects + Projection Dashboard still shown.

## 5. OPEN DECISIONS (flag to user, don't guess)
- Keep the "Release" domain naming, or adopt "Projection Dashboard" / "All Projections"?
- Keep Assumptions in BOTH the sidebar and the Nav.tsx settings dropdown, or sidebar only?

## 6. PROGRESS LOG (append-only)
- 2026-11-01 Work order created. Root-caused Review Findings -> home: `/admin/findings` has no
  route in App.tsx; catch-all redirects it. Assumptions page/route exist but are not in the
  sidebar (only the top-right settings dropdown). Rename + add + remove set agreed with user.

## 7. IF STUCK
Sidebar is pure presentation — if a renamed item breaks active-highlighting, check `isActive()`
prefix logic against the item's `to`. If the new item 404s/redirects, the route is missing in
App.tsx, not a Sidebar bug.
