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
| P1 | open the running app (preview_start), log in as admin | lets you SEE each bug to confirm/repro | |
| P2 | read `pages/AdminAudit.tsx` ~L145–155 | Details `<td>` uses `max-w-xs truncate` (clips) | |
| P3 | read `pages/AdminAnalytics.tsx` ~L120–148 | recharts BarChart + ResponsiveContainer; check container height + axis visibility | |
| P4 | read `pages/AdminReportSettings.tsx` (imports + save handler ~L73) | NO `toast`/`sonner` import — toggles are silent | |
| P5 | read `pages/AdminAssumptions.tsx` | NO reset-to-default control exists (grep "reset" = empty) | |
| P6 | read `pages/AdminIntegrations.tsx` + how it's routed/linked, and `pages/AdminCatalog.tsx` | where email/Slack config lives and why it shows under/near Catalog | |
| P7 | read `pages/AdminJobs.tsx` `LogLine` ~L66–79 | log level color map + low-contrast timestamp/DEBUG/TRACE | |

---

## 1. BUGS TO FIX

### B1 — admin/audit: Details column is cut off
`pages/AdminAudit.tsx:155` — the Details cell is `max-w-xs truncate`, so long details are clipped
to one line with only a hover `title`. The user can't read full details.
- [ ] Make details readable: allow wrapping (`whitespace-normal break-words`, drop `truncate`),
      widen the column, and/or make the row expandable (click to expand full detail). Keep the
      table responsive (wrap the table in `overflow-x-auto` if it grows). Verify long entries
      show fully.

### B2 — cost analytics: chart axes/graph not visible
`pages/AdminAnalytics.tsx:133–148` — recharts `BarChart` inside `ResponsiveContainer`. Axes
(`XAxis`/`YAxis`) are declared but not visible.
- [ ] Diagnose in-browser: usual causes are (a) `ResponsiveContainer` with no explicit height
      (parent has 0 height → chart renders 0px), (b) `margin:{left:-20}` clipping the Y axis
      labels, (c) axis tick color too faint (`rgba(255,255,255,0.4)` on the panel bg). Fix:
      give the container a definite height (e.g. `height={280}` or a fixed-height wrapper),
      restore non-negative left margin so Y labels show, and bump tick contrast. Verify both
      axes, ticks, and bars render with data and when empty.

### B3 — report settings: toggle doesn't fire a sonner toast
`pages/AdminReportSettings.tsx` — the save/toggle handler (~L73 builds a `reason`) has **no
`toast` call** and the file does not import sonner. Toggling a setting gives no feedback.
- [ ] `import { toast } from 'sonner'` and call `toast.success(...)` on a successful
      save/toggle (and `toast.error(...)` on failure). Confirm the toast appears on toggle.
      (Note: the control may be a select/text input, not a literal radio — apply the toast to
      whatever commits the change.)

### B4 — assumptions: "Reset to default" not implemented
`pages/AdminAssumptions.tsx` has Edit/Save but **no reset-to-default**. The schema already
carries `default_value` per key (from `AdminGetAssumptions`).
- [ ] Add a per-row "Reset to default" action that sets the value back to `default_value`
      (reuse the existing `putAssumption` with the default value + an auto reason like
      "reset to default"). If a bulk "Reset all" is wanted, add it too. Backend already
      supports the PUT; only add an endpoint if a dedicated reset is preferred. Verify the
      value and version-hash update and history records it.
- [ ] This must also surface in the per-job override panel (`JobAssumptionsPanel.tsx`) if that
      is where the user expects reset — confirm which; the panel already references reset in its
      work order, so wire it there too if missing.

### B5 — catalog: email/Slack integrations "not coming correct"
Email/Slack config lives in `pages/AdminIntegrations.tsx` (uses `getIntegrations` /
`updateIntegrations` / `testNotify`). The user sees it "in catalog" and wrong.
- [ ] Determine the actual defect in-browser: likely (a) the integrations page/section is
      mislinked or embedded under Catalog, (b) `getIntegrations` fails or returns
      `encryption_available=false` so fields render disabled/blank, or (c) status shows
      "Not configured" despite env fallback. Fix routing/placement so email+Slack config is
      reachable from its own nav entry (not Catalog), and ensure it loads + saves + the
      test buttons work. Verify configured/enabled state renders correctly.

### B6 — logs: colors too low-contrast to read
`pages/AdminJobs.tsx` `LogLine` (~L66–79): timestamp `text-gray-500`, DEBUG `text-gray-400`,
TRACE `text-gray-500`, message `text-gray-300` — too dim on the dark panel. Also check the
separate `components/JobLogsViewer.tsx` (may render raw, uncolored).
- [ ] Raise contrast: brighten the message text, timestamp, and DEBUG/TRACE levels to meet
      readable contrast on the panel background; keep ERROR/WARN/INFO distinct. Apply the same
      treatment in `JobLogsViewer.tsx` if it renders log lines. Verify readability in both
      light and dark themes.

---

## 2. INVARIANTS
- Frontend-only where possible; no API/schema change except an optional assumptions reset
  endpoint (B4) — prefer reusing `putAssumption`.
- Keep the Facets dark theme tokens; don't hardcode colors that break light mode (B2/B6).
- Don't regress the sidebar/nav cleanup or the merged features.

## 3. PHASES
### Phase 1 — Fix
- [ ] B1–B6 per above.
### Phase 2 — Verify (in-browser, per <verification_workflow>)
- [ ] preview_start, log in, visit each page, confirm each bug is fixed (screenshot the
      before/after where visual). Check both themes for B2/B6.
- [ ] `npm run build` clean; run Go + Python suites if any backend touched (B4 endpoint).

## 4. OPEN DECISIONS (flag, don't guess)
- B4: per-key reset only, or also "Reset all to defaults"?
- B5: should integrations be its own sidebar item, and should it stay admin-only?

## 5. PROGRESS LOG (append-only)
- 2026-11-02 Work order created from user-reported UI bugs. Grounded each:
  B1 AdminAudit.tsx:155 (max-w-xs truncate), B2 AdminAnalytics.tsx:133–148 (recharts axes),
  B3 AdminReportSettings.tsx (no sonner import/toast), B4 AdminAssumptions.tsx (no reset),
  B5 AdminIntegrations.tsx vs Catalog placement, B6 AdminJobs.tsx LogLine:66–79 + JobLogsViewer.
  NEXT: run P1–P7 in-browser, then fix.

## 6. IF STUCK
Repro in the browser first (preview_start + login). For B2 a 0-height chart is the most common
cause — give the container an explicit height before touching anything else.
