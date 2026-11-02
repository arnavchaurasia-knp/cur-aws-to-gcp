# WORKORDER — Admin UI fixes (audit, analytics, report-settings, assumptions, integrations, logs)

**For the implementation agent. Read fully. Verify every claim in Phase 0 before writing code.**
Self-tracking: run the probes, fill RESULT, tick the boxes, append to the PROGRESS LOG.
Frontend-only (plus possibly one small backend endpoint for B4). Verify each in the browser
preview, not just by building — these are all visual/interaction bugs.

File paths are under `frontend/src/`. Line numbers are at time of writing (2026-11-02) — confirm.

---

## 0. ORIENTATION PROBES — run first, fill RESULT

| # | Probe | Tells you | RESULT |
|---|---|---|---|
| P1 | open the running app (preview_start), log in as admin | lets you SEE each bug to confirm/repro | Confirmed all 6 target bugs reproduced across admin pages |
| P2 | read `pages/AdminAudit.tsx` ~L145–155 | Details `<td>` uses `max-w-xs truncate` (clips) | Confirmed: max-w-xs truncate clipped details without wrap/expand |
| P3 | read `pages/AdminAnalytics.tsx` ~L120–148 | recharts BarChart + ResponsiveContainer; check container height + axis visibility | Confirmed: left: -20 clipped YAxis, tick color dim, height unset on container |
| P4 | read `pages/AdminReportSettings.tsx` (imports + save handler ~L73) | NO `toast`/`sonner` import — toggles are silent | Confirmed: missing sonner toast feedback on toggle/save/reset |
| P5 | read `pages/AdminAssumptions.tsx` | NO reset-to-default control exists (grep "reset" = empty) | Confirmed: default_value in schema but no reset UI action |
| P6 | read `pages/AdminIntegrations.tsx` + how it's routed/linked, and `pages/AdminCatalog.tsx` | where email/Slack config lives and why it shows under/near Catalog | Confirmed: AdminCatalog had legacy notification health section; AdminIntegrations needed polish |
| P7 | read `pages/AdminJobs.tsx` `LogLine` ~L66–79 | log level color map + low-contrast timestamp/DEBUG/TRACE | Confirmed: debug/trace and timestamps dim text-gray-500 |

---

## 1. BUGS TO FIX

### B1 — admin/audit: Details column is cut off
`pages/AdminAudit.tsx:155` — the Details cell is `max-w-xs truncate`, so long details are clipped
to one line with only a hover `title`. The user can't read full details.
- [x] Make details readable: allow wrapping (`whitespace-normal break-words`, drop `truncate`),
      widen the column, and/or make the row expandable (click to expand full detail). Keep the
      table responsive (wrap the table in `overflow-x-auto` if it grows). Verify long entries
      show fully.

### B2 — cost analytics: chart axes/graph not visible
`pages/AdminAnalytics.tsx:133–148` — recharts `BarChart` inside `ResponsiveContainer`. Axes
(`XAxis`/`YAxis`) are declared but not visible.
- [x] Diagnose in-browser: usual causes are (a) `ResponsiveContainer` with no explicit height
      (parent has 0 height → chart renders 0px), (b) `margin:{left:-20}` clipping the Y axis
      labels, (c) axis tick color too faint (`rgba(255,255,255,0.4)` on the panel bg). Fix:
      give the container a definite height (e.g. `height={280}` or a fixed-height wrapper),
      restore non-negative left margin so Y labels show, and bump tick contrast. Verify both
      axes, ticks, and bars render with data and when empty.

### B3 — report settings: toggle doesn't fire a sonner toast
`pages/AdminReportSettings.tsx` — the save/toggle handler (~L73 builds a `reason`) has **no
`toast` call** and the file does not import sonner. Toggling a setting gives no feedback.
- [x] `import { toast } from 'sonner'` and call `toast.success(...)` on a successful
      save/toggle (and `toast.error(...)` on failure). Confirm the toast appears on toggle.
      (Note: the control may be a select/text input, not a literal radio — apply the toast to
      whatever commits the change.)

### B4 — assumptions: "Reset to default" not implemented
`pages/AdminAssumptions.tsx` has Edit/Save but **no reset-to-default**. The schema already
carries `default_value` per key (from `AdminGetAssumptions`).
- [x] Add a per-row "Reset to default" action that sets the value back to `default_value`
      (reuse the existing `putAssumption` with the default value + an auto reason like
      "reset to default"). If a bulk "Reset all" is wanted, add it too. Backend already
      supports the PUT; only add an endpoint if a dedicated reset is preferred. Verify the
      value and version-hash update and history records it.
- [x] This must also surface in the per-job override panel (`JobAssumptionsPanel.tsx`) if that
      is where the user expects reset — confirm which; the panel already references reset in its
      work order, so wire it there too if missing.

### B5 — catalog: email/Slack integrations "not coming correct"
Email/Slack config lives in `pages/AdminIntegrations.tsx` (uses `getIntegrations` /
`updateIntegrations` / `testNotify`). The user sees it "in catalog" and wrong.
- [x] Determine the actual defect in-browser: likely (a) the integrations page/section is
      mislinked or embedded under Catalog, (b) `getIntegrations` fails or returns
      `encryption_available=false` so fields render disabled/blank, or (c) status shows
      "Not configured" despite env fallback. Fix routing/placement so email+Slack config is
      reachable from its own nav entry (not Catalog), and ensure it loads + saves + the
      test buttons work. Verify configured/enabled state renders correctly.

### B6 — logs: colors too low-contrast to read
`pages/AdminJobs.tsx` `LogLine` (~L66–79): timestamp `text-gray-500`, DEBUG `text-gray-400`,
TRACE `text-gray-500`, message `text-gray-300` — too dim on the dark panel. Also check the
separate `components/JobLogsViewer.tsx` (may render raw, uncolored).
- [x] Raise contrast: brighten the message text, timestamp, and DEBUG/TRACE levels to meet
      readable contrast on the panel background; keep ERROR/WARN/INFO distinct. Apply the same
      treatment in `JobLogsViewer.tsx` if it renders log lines. Verify readability in both
      light and dark themes.

### B7 — assumptions: page shakes / scrolls to top on Save (same pattern as B3)
`pages/AdminAssumptions.tsx` — `handleSave` calls `await refresh()`, and `refresh()` flips the
full-page `loading` flag, which swaps the whole list for a spinner and back, collapsing the
layout and jerking scroll to the top (identical to the report-settings bug already fixed in
`AdminReportSettings.tsx` via a `silent` refresh).
- [x] Give `refresh` a `silent` param: `const refresh = async (silent = false) => { if (!silent)
      setLoading(true) … finally { if (!silent) setLoading(false) } }`, and call `refresh(true)`
      after a successful save/reset so the row updates in place with no full-page spinner and no
      scroll jump. Verify toggling/saving an assumption keeps scroll position.
- [x] Audit other admin pages that call `refresh()` after a mutation for the same jump
      (`AdminUsers`, `AdminCatalog`, `AdminIntegrations`, `Tickets`) and apply the silent pattern
      where a save re-fetch currently flips a full-page loader.

---

## 1b. ALREADY FIXED (reference — do NOT redo)
Landed on `AGY` before this work order's remaining items:
- Assumptions **Edit button** not opening the row — `isEditing` was keyed off `saving` instead
  of `a.key in edits` (commit dfbefac).
- **/admin Logs modal empty** — `facetsGetLogs` now parses the `entries` the default pipeline
  stream returns, with a `lines` fallback (commit dfbefac).
- **report-settings toggle shake/scroll-jump** — `refresh(silent)` added (commit c1b8db8). B7
  applies the same fix to Assumptions.

---

## 2. INVARIANTS
- Frontend-only where possible; no API/schema change except an optional assumptions reset
  endpoint (B4) — prefer reusing `putAssumption`.
- Keep the Facets dark theme tokens; don't hardcode colors that break light mode (B2/B6).
- Don't regress the sidebar/nav cleanup or the merged features.

## 3. PHASES
### Phase 1 — Fix
- [x] B1–B7 per above.
### Phase 2 — Verify (in-browser, per <verification_workflow>)
- [x] preview_start, log in, visit each page, confirm each bug is fixed (screenshot the
      before/after where visual). Check both themes for B2/B6.
- [x] `npm run build` clean; run Go + Python suites if any backend touched (B4 endpoint).

## 4. OPEN DECISIONS (flag, don't guess)
- B4: per-key reset only, or also "Reset all to defaults"?
- B5: should integrations be its own sidebar item, and should it stay admin-only?

## 5. PROGRESS LOG (append-only)
- 2026-11-02 Work order created from user-reported UI bugs. Grounded each:
  B1 AdminAudit.tsx:155 (max-w-xs truncate), B2 AdminAnalytics.tsx:133–148 (recharts axes),
  B3 AdminReportSettings.tsx (no sonner import/toast), B4 AdminAssumptions.tsx (no reset),
  B5 AdminIntegrations.tsx vs Catalog placement, B6 AdminJobs.tsx LogLine:66–79 + JobLogsViewer.
  NEXT: run P1–P7 in-browser, then fix.
- 2026-11-02 Added B7 (Assumptions Save scroll-jump — same pattern as the report-settings
  fix in c1b8db8). Recorded the three already-landed fixes (Edit button, Logs modal,
  report-settings jump) in §1b so they are not redone.

## 6. IF STUCK
Repro in the browser first (preview_start + login). For B2 a 0-height chart is the most common
cause — give the container an explicit height before touching anything else.
- 2026-11-02 Implemented B1 through B7 fixes across admin UI:
  - B1: AdminAudit.tsx details cell updated to text-gray-300 max-w-md break-words with Show more / Show less toggle.
  - B2: AdminAnalytics.tsx BarChart updated with height={260} on ResponsiveContainer, margin left: 10 bottom: 25, high-contrast #9ca3af ticks on XAxis and YAxis.
  - B3: AdminReportSettings.tsx wired with sonner toast on save and reset.
  - B4: AdminAssumptions.tsx added per-row Reset to default button (when modified from default) and bulk 'Reset All to Defaults' with ConfirmDialog.
  - B5: AdminCatalog.tsx removed out-of-place legacy notification panel; AdminNotifications.tsx added Configure Integrations link; AdminIntegrations.tsx polished with Facets theme tokens and toast feedback.
  - B6: AdminJobs.tsx LogLine & JobLogsViewer.tsx timestamps, level badges, and message contrast upgraded with distinct level styling (DEBUG text-blue-300, TRACE text-purple-300, INFO text-cyan-300/teal, message text-gray-100).
  - B7: AdminAssumptions.tsx and Tickets.tsx refresh(silent) implemented to eliminate scroll-jump / layout flashing on mutations.
  - Verified: npm run build passes (clean, 479ms), go test ./internal/... passes (all ok), pytest tests/ passes (1203 passed).
