# WORK ORDER — Frontend UI polish pass (AGY cur-web)

> **READ THIS FIRST — you are an implementation model. You CANNOT search the web, you CANNOT
> guess file locations, and you must NOT invent new design systems or new libraries.** Everything
> you need is in this document. Every file path, line number, exact string to find, and exact
> replacement is given below. Work top to bottom, one item at a time. After each item, re-read the
> file you changed to confirm the edit applied. Do not touch any file not named in an item.

## Environment facts (do not re-derive these)

- Repo root: `C:\Users\arnav\Downloads\cur-web-AGY`
- Frontend root: `frontend/` — **React 19 + Vite + Tailwind v4 + TypeScript**.
- Installed libraries you MUST reuse (do NOT add new ones — there is no `npm install` step allowed):
  - **`sonner`** (toasts) — import with `import { toast } from 'sonner'`. The `<Toaster />` is already
    mounted globally. Use `toast.success(...)`, `toast.error(...)`, `toast.message(...)`. Already used in
    `frontend/src/pages/AdminAssumptions.tsx:12` and `frontend/src/pages/AdminUsers.tsx:9`.
  - **`@radix-ui/react-alert-dialog`** — already wrapped by the reusable component
    `frontend/src/components/ConfirmDialog.tsx`. **Use that component for every confirm dialog.**
  - **`recharts`** (charts) — used only in `frontend/src/pages/AdminAnalytics.tsx`.
  - **`react-router-dom` v7**.
- **Icons:** this project draws icons as **inline `<svg>` functions** (e.g. `Sidebar.tsx` lines 161-264).
  There is **NO** `react-icons` / FontAwesome npm package installed and you may NOT add one. When an item
  says "use a professional icon", it means **write a clean inline `<svg>`** in the same stroke style as the
  existing ones: `fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}`, sizes `w-4 h-4`
  or `w-5 h-5`. Copy that exact pattern. Do not use emoji as icons.
- **Theme tokens** are CSS variables defined in `frontend/src/index.css`:
  - Dark values at `:root` lines 13-28, light values at the light block lines 33-48.
  - Usable tokens: `--fcp-bg-base`, `--fcp-bg-surface`, `--fcp-bg-raised`, `--fcp-bg-subtle`,
    `--fcp-bg-hover`, `--fcp-text-primary`, `--fcp-text-secondary`, `--fcp-text-tertiary`,
    `--fcp-border-subtle`, `--fcp-border-strong`, `--fcp-card-shadow`.
  - **RULE:** any color that must be visible in BOTH light and dark mode must use these tokens via
    `[color:var(--fcp-...)]` / `bg-[var(--fcp-...)]` / `border-[var(--fcp-...)]`. Raw `rgba(255,255,255,...)`
    is invisible in light mode — that is the root cause of Item 4.
- **Animation helpers** already in `frontend/src/index.css` (lines ~271-290): classes
  `anim-fade-in-up`, `anim-fade-in`, `anim-slide-down`, `anim-scale-in`, `anim-glow-breathe`. A
  `prefers-reduced-motion` guard already exists at ~line 494. **Reuse these classes; do not write new
  `@keyframes` unless an item explicitly tells you to.**
- Accent colors already in use across the app: primary `#645DF6` (indigo), secondary `#00C2BB` (teal).
  Keep using these two; do not introduce new accent colors.

After all items, run from `frontend/`:
```
npm run build
```
It MUST compile with no TypeScript errors. If it fails, fix only the error it reports; do not refactor.

---

## ITEM 1 — Audit log "Details" column is cut off (truncated)

**File:** `frontend/src/pages/AdminAudit.tsx`
**Anchor:** line 155, inside the table body:
```tsx
<td className="px-4 py-3 text-gray-500 text-xs max-w-xs truncate" title={e.details}>{e.details || '—'}</td>
```
**Problem:** `max-w-xs truncate` clips the details text to one short line.
**Fix — replace that single `<td>` with:**
```tsx
<td className="px-4 py-3 text-gray-500 text-xs align-top">
  <div className="max-w-md whitespace-pre-wrap break-words leading-relaxed">{e.details || '—'}</div>
</td>
```
This lets long details wrap onto multiple lines inside a wider cell instead of being cut.

Also widen the page container so the table has room. **Anchor:** line 60:
```tsx
<div className="max-w-5xl mx-auto px-6 py-10">
```
Replace `max-w-5xl` with `max-w-7xl` (this one page only).

---

## ITEM 2 — Sidebar "snaps open" abruptly when navigating while collapsed

**Root cause (do not fix any other way):** the collapsed state lives in local component state inside
`frontend/src/components/Layout.tsx:15`:
```tsx
const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
```
Every page renders its own `<Layout>`, so navigating to a new route **remounts Layout and resets
`sidebarCollapsed` to `false`** — the sidebar jumps from collapsed back to expanded with no transition.
The fix is to persist the state so it survives route changes.

**Step 2a — persist + restore the state.** In `frontend/src/components/Layout.tsx`, replace line 15:
```tsx
const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
```
with:
```tsx
const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
  try { return localStorage.getItem('fcp-sidebar-collapsed') === '1' } catch { return false }
})
```
Then, directly AFTER that `useState` line, add:
```tsx
const toggleSidebar = () => {
  setSidebarCollapsed(prev => {
    const next = !prev
    try { localStorage.setItem('fcp-sidebar-collapsed', next ? '1' : '0') } catch { /* ignore */ }
    return next
  })
}
```
You must also add `useEffect` is NOT needed. Keep the existing `import { useState } from 'react'` on line 1
(do not add useEffect).

**Step 2b — use the new toggle.** In the same file, find (line ~63):
```tsx
onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
```
Replace with:
```tsx
onToggle={toggleSidebar}
```

**Why this fixes the snap:** on every page the sidebar now mounts already in the correct collapsed width,
so there is no width change to animate on navigation. The existing `transition-all duration-300` on the
`<aside>` (`Sidebar.tsx:110`) still animates the intentional toggle click.

---

## ITEM 3 — Pricing Assumptions category dropdown button "scales up and down" and has no animation

**File:** `frontend/src/pages/AdminAssumptions.tsx`

**Problem A (jitter / scaling):** the collapse header uses a text caret `▶ / ▼` (lines 178-180) whose glyph
width differs, and the whole header's height changes because the description line only renders when
expanded. Replace the caret with a fixed-size rotating SVG chevron and keep the layout stable.

**Anchor:** lines 177-180:
```tsx
<div className="flex items-center gap-3">
  <span className="text-xs text-[#00C2BB] font-mono">
    {isCollapsed ? '▶' : '▼'}
  </span>
```
Replace those four lines with:
```tsx
<div className="flex items-center gap-3">
  <svg
    className={`w-4 h-4 shrink-0 text-[#00C2BB] transition-transform duration-200 ${isCollapsed ? '' : 'rotate-90'}`}
    fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
  >
    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
  </svg>
```
The chevron now rotates smoothly 90° instead of swapping glyphs.

**Problem B (no open/close animation):** the body renders with a bare `{!isCollapsed && (...)}` at line 198.
Wrap the revealed table in the existing reveal animation. **Anchor:** line 198:
```tsx
{!isCollapsed && (
  <div className="overflow-x-auto">
```
Replace with:
```tsx
{!isCollapsed && (
  <div className="overflow-x-auto anim-slide-down origin-top">
```
(`anim-slide-down` is defined in `index.css`; it fades+slides the panel in.)

---

## ITEM 4 — Light mode: chart X/Y axes and gridlines are invisible

**File:** `frontend/src/pages/AdminAnalytics.tsx`
**Root cause:** the chart hardcodes white strokes, invisible on the light background.
**Anchors:** lines 134-136:
```tsx
<CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false} />
<XAxis dataKey="day" stroke="rgba(255,255,255,0.4)" fontSize={11} tickLine={false} axisLine={{ stroke: 'rgba(255,255,255,0.1)' }} />
<YAxis stroke="rgba(255,255,255,0.4)" fontSize={11} allowDecimals={false} tickLine={false} axisLine={{ stroke: 'rgba(255,255,255,0.1)' }} />
```
Recharts needs real color strings (it cannot read Tailwind classes), so read the theme token at runtime.

**Step 4a** — at the TOP of the component function body (find the function
`export function AdminAnalytics(` and the first line inside it), add a helper that reads a CSS variable.
Place this block immediately after the opening `{` of the component, before the first hook:
```tsx
const axisColor = 'var(--fcp-text-tertiary)'
const gridColor = 'var(--fcp-border-subtle)'
```
Recharts accepts `var(...)` strings for `stroke`. Now replace lines 134-136 with:
```tsx
<CartesianGrid strokeDasharray="3 3" stroke={gridColor} vertical={false} />
<XAxis dataKey="day" stroke={axisColor} fontSize={11} tickLine={false} axisLine={{ stroke: gridColor }} />
<YAxis stroke={axisColor} fontSize={11} allowDecimals={false} tickLine={false} axisLine={{ stroke: gridColor }} />
```
If `AdminAnalytics.tsx` has any OTHER chart or any other `rgba(255,255,255,...)` stroke value, apply the
same swap: grid/axisLine → `gridColor`, axis text/line → `axisColor`. Do not change the `<Bar>` fill color.

---

## ITEM 5 — Replace the hand-rolled role-change confirm modal with the shared ConfirmDialog + cards

**File:** `frontend/src/pages/AdminUsers.tsx`
**Problem:** lines 231-274 are a custom inline modal using **emoji icons** (`⚠️ 🔒 ℹ️`, lines 241-243)
and ad-hoc styling — this is the "really bad UI" the report flags. Replace it with the project's
`ConfirmDialog` component, which already has proper animation (`anim-scale-in`), focus management, and
theme styling.

**Step 5a** — add the import. After line 9 (`import { toast } from 'sonner'`) add:
```tsx
import { ConfirmDialog } from '../components/ConfirmDialog'
```

**Step 5b** — delete the ENTIRE custom modal block, lines 230-274 (the comment
`{/* Confirmation dialog */}` through its closing `)}`), and replace it with:
```tsx
{/* Confirmation dialog */}
<ConfirmDialog
  open={!!confirm}
  onOpenChange={(o) => { if (!o) closeConfirm() }}
  title={severityTitle(confirm)}
  description={
    <>
      {severityBody(confirm)}
      {confirm && error[confirm.email] && (
        <p className="mt-2 text-xs text-orange-400">{error[confirm.email]}</p>
      )}
    </>
  }
  confirmLabel={confirmButtonLabel(confirm)}
  cancelLabel="Cancel"
  destructive={confirm?.severity === 'grant_admin' || confirm?.severity === 'revoke_admin'}
  loading={confirm ? busy.has(confirm.email) : false}
  onConfirm={handleConfirm}
/>
```
Leave all the helper functions (`severityTitle`, `severityBody`, `confirmButtonLabel`, `handleConfirm`,
`openConfirm`, `closeConfirm`) untouched — they still work. `ConfirmDialog` renders its own animated
overlay, so the emoji block is gone.

**Step 5c (success feedback already correct):** `handleConfirm` already calls
`toast.success(...)` on line 100 — leave it.

---

## ITEM 6 — Wire in CRUD: "Delete user" and "Delete ticket" (endpoints already exist on the backend)

The Go backend already exposes these (verified):
- `DELETE /api/admin/users/{email}` → handler `AdminDeleteUser` (`cmd/server/main.go:333`)
- `DELETE /api/admin/tickets/{id}` → handler `AdminDeleteTicket` (`cmd/server/main.go:362`)

The frontend never calls them. Add the API functions and buttons.

**Step 6a — add API functions.** File `frontend/src/api/admin.ts`. Find the existing function
`setUserRole` (starts at line 99) and, immediately AFTER its closing `}`, add:
```ts
export async function deleteUser(email: string): Promise<void> {
  const res = await fetch(`/api/admin/users/${encodeURIComponent(email)}`, {
    method: 'DELETE', credentials: 'include',
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(text || `delete failed (${res.status})`)
  }
}
```
> Copy the exact fetch/credentials/error pattern from `deleteJob` at `admin.ts:78-83` — match it so the
> code reads like its neighbors. If `deleteJob` uses a shared helper (e.g. `http.ts`) instead of raw
> `fetch`, use that same helper here instead of the raw fetch above.

Then find `updateTicket` (line 558) and after its closing `}` add:
```ts
export async function deleteTicket(id: string): Promise<void> {
  const res = await fetch(`/api/admin/tickets/${encodeURIComponent(id)}`, {
    method: 'DELETE', credentials: 'include',
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(text || `delete failed (${res.status})`)
  }
}
```

**Step 6b — Delete-user button + confirm in AdminUsers.** File `frontend/src/pages/AdminUsers.tsx`.
This page already has a confirm mechanism and (after Item 5) the `ConfirmDialog` import. Add a SECOND,
separate confirm for deletion so you do not entangle it with the role-change `confirm` state.

Near the other `useState` hooks (after line 17 `const [globalError, setGlobalError] = ...`), add:
```tsx
const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
const [deleting, setDeleting] = useState(false)
```
Add the import for the API (top of file, with the other admin imports around line 4
`import { listUsers, setUserRole } from '../api/admin'`): change it to
```tsx
import { listUsers, setUserRole, deleteUser } from '../api/admin'
```
Add the handler (place it right after `handleConfirm`, before the `return`):
```tsx
const handleDelete = async () => {
  if (!deleteTarget) return
  setDeleting(true)
  try {
    await deleteUser(deleteTarget)
    await refresh()
    toast.success(`Deleted ${deleteTarget}`)
    setDeleteTarget(null)
  } catch (err) {
    toast.error(err instanceof Error ? err.message : 'delete failed')
  } finally {
    setDeleting(false)
  }
}
```
In the Actions cell (the `<td>` containing the "View runs →" `<Link>`, around lines 214-221), add a delete
button next to it. Replace that `<td>`'s inner content so it holds both controls, and do NOT offer delete
for locked `@facets` core accounts (they are permanently admin). Use `isFacets` which is already computed
on line 155. Replace the Actions `<td>` (lines 214-221) with:
```tsx
<td className="px-5 py-3 text-right">
  <div className="inline-flex items-center gap-2">
    <Link
      to={`/admin?q=${encodeURIComponent(u.email)}`}
      className="inline-flex items-center gap-1.5 px-3 py-1 rounded bg-[#00C2BB]/10 text-[#00C2BB] hover:bg-[#00C2BB]/20 text-xs font-medium transition-colors"
    >
      View runs →
    </Link>
    {!isFacets && (
      <button
        onClick={() => setDeleteTarget(u.email)}
        title="Delete user"
        className="inline-flex items-center justify-center w-7 h-7 rounded border border-white/10 text-gray-400 hover:text-red-400 hover:border-red-500/40 transition-colors cursor-pointer"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      </button>
    )}
  </div>
</td>
```
Add a second `ConfirmDialog` just below the role-change one you added in Item 5:
```tsx
<ConfirmDialog
  open={!!deleteTarget}
  onOpenChange={(o) => { if (!o) setDeleteTarget(null) }}
  title={`Delete ${deleteTarget ?? ''}?`}
  description="This permanently removes the user account and their access. This cannot be undone."
  confirmLabel="Delete user"
  destructive
  loading={deleting}
  onConfirm={handleDelete}
/>
```

**Step 6c — Delete-ticket button in Tickets.** File `frontend/src/pages/Tickets.tsx`.
First FIX the existing bad pattern: line 257 uses the native blocking `confirm(...)`:
```tsx
if (!confirm(`Mark ticket "${t.title}" as resolved?`)) return
```
Native `confirm()` is the ugly-UI the user is complaining about. Read the surrounding handler and
convert that action to use the shared `ConfirmDialog` + `toast` the same way as Item 5 (add a
`confirmResolve` state holding the ticket, render one `ConfirmDialog`, call the resolve API in its
`onConfirm`, then `toast.success`). Then add a delete action using the new `deleteTicket` API
(import it from `../api/admin`) wired through its own `ConfirmDialog` + `toast`, with the same trash
`<svg>` icon markup as Step 6b. Do not use native `confirm()` or `alert()` anywhere.

**Step 6d — ApiTokens native confirm.** File `frontend/src/pages/ApiTokens.tsx`, line 147 uses
`window.confirm(...)`. Convert it to the shared `ConfirmDialog` + `toast` pattern as well (same approach
as Item 5). This is the last remaining native dialog.

> After 6c/6d there must be **zero** occurrences of `confirm(` or `alert(` as runtime calls in
> `frontend/src`. Verify by reading each changed file.

---

## ITEM 7 — "Text below text" everywhere → professional cards

The complaint is that several admin pages stack bare `label / value` lines instead of using cards. Two
pages are the worst offenders and have token-driven card styling available.

**7a — AdminCatalog status block.** File `frontend/src/pages/AdminCatalog.tsx`, lines 94-118. The
"Last refreshed" and "Status" rows are flat label/value lines. Convert the two rows into a 2-up card grid.
Replace the inner content of the `{status && (...)}` block (keep the outer wrapper and the Refresh button)
so the two metrics render as cards:
```tsx
<div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
  <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-4 shadow-[var(--fcp-card-shadow)] anim-fade-in-up">
    <div className="text-xs uppercase tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">Last refreshed</div>
    <div className="text-lg font-semibold tabular-nums text-[color:var(--fcp-text-primary)]">
      {status.age_days >= 9999 ? 'unknown' : `${status.age_days} day${status.age_days === 1 ? '' : 's'} ago`}
    </div>
  </div>
  <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-4 shadow-[var(--fcp-card-shadow)] anim-fade-in-up" style={{ animationDelay: '60ms' }}>
    <div className="text-xs uppercase tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">Status</div>
    <div className={`text-lg font-semibold ${status.stale ? 'text-orange-400' : 'text-[#00C2BB]'}`}>
      {status.stale ? 'Stale' : 'Fresh'}
    </div>
  </div>
</div>
```
Keep `{actionError && ...}` and the `Refresh now` button AFTER this grid, inside the same parent.
Use `mt-6` on the button wrapper so spacing stays even.

**7b — general rule for any other flat list you convert:** a card is
`rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-4 shadow-[var(--fcp-card-shadow)]`
with a `text-xs uppercase tracking-wider text-[color:var(--fcp-text-tertiary)]` label and a
`text-[color:var(--fcp-text-primary)]` value. Add `anim-fade-in-up` for entrance. Only apply this to
pages named in this work order — do NOT go reformat unrelated pages.

---

## ITEM 8 — Pricing Catalog page: drop the email/Slack panel, merge the near-empty page

**Context:** `frontend/src/pages/AdminCatalog.tsx` shows a **"Notification health"** panel (lines 120-160)
with Email/Slack "Configured / Not configured". This duplicates the real integrations editor at
`frontend/src/pages/AdminIntegrations.tsx` (route `/admin/integrations`) and is "not of much use" on the
catalog page. After removing it the catalog page is nearly empty, so fold it together with the
integrations settings into ONE page.

**Step 8a — remove the panel.** In `AdminCatalog.tsx` delete lines 120-160 (the
`{/* Notification health panel */}` `<div>...</div>` block, including its `{notifyStatus && (...)}`).
Then remove the now-unused code it depended on:
- the `notifyStatus` state (line 21) and its setter usage,
- `getNotifyStatus` and `notifyTest` from the import (lines 7-8 area) and the `NotifyStatus` type import,
- in `refresh()` change `const [s, n] = await Promise.all([getCatalogStatus(), getNotifyStatus()])` to
  `const s = await getCatalogStatus()` and drop `setNotifyStatus(n)`,
- delete `handleNotifyTest`, `notifying`, `setNotifying`.
Fix any resulting "unused variable" TypeScript errors that `npm run build` reports.

**Step 8b — merge routes.** The catalog page is now just the GCP freshness card. Combine it with the
integrations editor so one admin page holds both "Pricing Catalog freshness" and "Notification
integrations". Do this by rendering BOTH sections on the `/admin/catalog` page:
1. In `AdminCatalog.tsx`, below the catalog `{status && (...)}` card, import and render the integrations
   editor's body. The simplest safe approach: add a second section to `AdminCatalog` that links to the
   full integrations editor, OR (preferred) move the catalog freshness card into `AdminIntegrations.tsx`
   as a top section and point the sidebar's "Pricing Catalog" entry at `/admin/integrations`.

   **Preferred concrete steps:**
   - In `frontend/src/components/Sidebar.tsx` line 31, change the Pricing Catalog target:
     ```tsx
     { to: '/admin/catalog', label: 'Pricing Catalog', icon: <ProjectTypeIcon />, adminOnly: true },
     ```
     Leave the route but make `/admin/catalog` render the combined page. In
     `frontend/src/App.tsx` line 63, the catalog route stays; line 66 is the integrations route.
   - Add the GCP catalog-freshness card (the `{status && ...}` block from `AdminCatalog.tsx`, now in
     card form from Item 7a) as the FIRST section inside `AdminIntegrations.tsx` return, under its
     `<h1>`. Give it its own `getCatalogStatus()` load in that page's `refresh()`.
   - Change `App.tsx` line 63 to render the integrations component at the catalog path too:
     ```tsx
     <Route path="/admin/catalog" element={user && isAdmin ? <AdminIntegrations user={user} /> : <Navigate to="/" />} />
     ```
     and delete the now-duplicate `/admin/integrations` route on line 66 (or keep both pointing at
     `AdminIntegrations` — but do NOT leave an orphan `AdminCatalog` with no route if it still imports
     removed symbols). If `AdminCatalog.tsx` becomes unused, delete the file and remove its import in
     `App.tsx`.
   - Update the page `<h1>` in `AdminIntegrations.tsx` (line 94) to `Pricing Catalog & Notifications`.

   > This is the one structurally involved item. Make the smallest set of edits that results in: ONE
   > admin page reachable from the "Pricing Catalog" sidebar entry that shows (1) GCP catalog freshness
   > card with a working "Refresh now" button and (2) the email/Slack integrations editor. No dead
   > imports, `npm run build` clean.

**Step 8c — restyle the integrations cards to tokens.** `AdminIntegrations.tsx` currently uses hardcoded
`bg-gray-800/50 border-gray-700` (lines 112, 141) and `bg-gray-900 border-gray-600` inputs — these look
wrong in light mode. Replace each `<section ...>` wrapper class
`mb-6 rounded-lg border border-gray-700 bg-gray-800/50 p-5` with
`mb-6 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)] anim-fade-in-up`.
Replace input classes `bg-gray-900 border border-gray-600` with
`bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)]` (there are input tokens defined in
`index.css` lines 26-27 / 46-47). Replace the `Save changes` button `bg-indigo-600 hover:bg-indigo-500`
with `bg-[#645DF6] hover:bg-[#645DF6]/85` to match the app accent.

---

## ITEM 9 — Swap any remaining emoji / random icons for inline SVGs

Emoji used AS icons (not content) must become inline `<svg>` in the house style (Item 0 pattern). Known
spots (fix at minimum these):
- `frontend/src/pages/AdminUsers.tsx` lines 241-243 — handled by Item 5 (ConfirmDialog has no emoji).
- `frontend/src/pages/AdminAssumptions.tsx` caret `▶ / ▼` — handled by Item 3.
After finishing Items 3 and 5, read each remaining page you touched and confirm no emoji glyph is used as
an interactive icon. Do NOT hunt through untouched pages — only the files named in this work order.

---

## ITEM 10 — Toasts instead of inline error strings on the pages you touched

Where a page you edited for Items 1-8 shows transient success/failure as a bare colored `<p>` or sets a
local `savedMsg`/`error` string for a one-off action, switch the **action feedback** to `sonner`:
`toast.success('Saved')` / `toast.error(message)`. Keep **load-time** error panels (the big retry boxes)
as they are — those are page-state, not transient toasts. Specifically:
- `AdminIntegrations.tsx`: the `savedMsg` "Saved." (line 64/173) → `toast.success('Saved')` and drop the
  inline `{savedMsg && ...}` span; the email/slack test results may stay inline (they're contextual to the
  field) OR move to `toast` — your call, but be consistent.
- Do not convert the load-error retry boxes in `AdminCatalog` / `AdminAudit` / `AdminAssumptions`.

---

## Final verification checklist (do every line)

1. `cd frontend && npm run build` → compiles, zero TS errors. Fix only reported errors.
2. Grep check (read files, since you cannot run grep tools): there must be **no** runtime `alert(` or
   `confirm(` calls left in `frontend/src` (ApiTokens line 147 and Tickets line 257 converted).
3. Audit page: a long "Details" value wraps to multiple lines, not clipped (Item 1).
4. Collapse the sidebar, navigate to another admin route → sidebar STAYS collapsed, no snap (Item 2).
5. Assumptions page: category header chevron rotates smoothly; body slides in (Item 3).
6. Toggle the app to light mode (ThemeToggle, top bar) → Analytics chart axes + gridlines are visible
   (Item 4).
7. Users page: change a role → animated ConfirmDialog (no emoji). Delete button → confirm → user removed,
   toast shown (Items 5, 6).
8. "Pricing Catalog" sidebar entry → one page with catalog freshness card + integrations editor; no
   standalone email/slack "health" panel left on the old catalog page (Item 8).
9. Cards use `--fcp-*` tokens so they look right in BOTH themes (Items 7, 8c).

**Do not** add libraries, change backend Go code, rename routes beyond Item 8, or restyle pages not listed
here. If any item's anchor text does not match the file exactly (e.g. line numbers shifted), search the
file for the quoted string and apply the change there — the quoted strings are exact at time of writing.
