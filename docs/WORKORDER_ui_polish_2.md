# WORK ORDER #2 — Frontend UI polish, round 2 (AGY cur-web)

> **READ FIRST — you are an implementation model. You CANNOT browse the web or guess.** Every file
> path, line number, the exact text to find, and the exact replacement are in this document. Work top
> to bottom, ONE item at a time. After each edit, re-open the file and confirm the change landed. Touch
> only the files named in an item. Do NOT add npm packages, do NOT invent a new design system.

## Environment facts (do not re-derive)

- Repo root: `C:\Users\arnav\Downloads\cur-web-AGY`. Frontend: `frontend/` (React 19 + Vite + Tailwind v4 + TS).
  Backend: Go (`internal/`, `cmd/`).
- **Reuse these, never add new libs:**
  - `sonner` toasts: `import { toast } from 'sonner'` → `toast.success(...) / toast.error(...)`.
  - Reusable confirm: `frontend/src/components/ConfirmDialog.tsx` (Radix, already animated). Use it for
    every confirm — props: `open, onOpenChange, title, description, confirmLabel, destructive, loading,
    onConfirm`.
  - Charts: `recharts` (only in `AdminAnalytics.tsx`).
- **Icons = inline `<svg>`** in the house style: `fill="none" viewBox="0 0 24 24" stroke="currentColor"
  strokeWidth={1.8}`, size `w-4 h-4` / `w-5 h-5`. **Never use emoji or a `✕`/`+` glyph as an icon.**
- **Theme tokens** (CSS vars in `frontend/src/index.css`; dark at `:root` ~lines 13-28, light ~33-48):
  `--fcp-bg-base, --fcp-bg-surface, --fcp-bg-raised, --fcp-bg-subtle, --fcp-bg-hover,
  --fcp-text-primary, --fcp-text-secondary, --fcp-text-tertiary, --fcp-border-subtle,
  --fcp-border-strong, --fcp-input-bg, --fcp-input-border, --fcp-card-shadow`.
  Use via `bg-[var(--fcp-bg-surface)]`, `text-[color:var(--fcp-text-primary)]`,
  `border-[var(--fcp-border-subtle)]`, `shadow-[var(--fcp-card-shadow)]`. **Any element that must be
  visible in BOTH light and dark mode must use tokens**, never raw `bg-gray-800` / `rgba(255,255,255,…)`.
- **Animation helper classes already defined** in `index.css` (~lines 271-290): `anim-fade-in`,
  `anim-fade-in-up`, `anim-slide-down`, `anim-scale-in`, `anim-glow-breathe`. A
  `prefers-reduced-motion` guard exists (~line 494). **IMPORTANT: the classes
  `animate-in`, `fade-in`, `slide-in-from-top-2` are NOT defined in this project** (no
  `tailwindcss-animate` plugin) — any element using them has NO animation. Replace them with the
  `anim-*` classes above.
- **Cursor rule:** every clickable element (button, toggle header, `<select>`, custom control) must have
  `cursor-pointer` in its className. Several are missing it — that is the "change mouse pointer to click"
  complaint.
- Accent colors: primary `#645DF6` (indigo), secondary `#00C2BB` (teal). Do not add new accents.

Build check after everything (run from `frontend/`): `npm run build` must compile with zero TS errors.
Backend build check (run from repo root): `go build ./...` must succeed.

---

## ITEM 1 — Pricing Assumptions: header block "text below text" looks unstyled

**File:** `frontend/src/pages/AdminAssumptions.tsx`
The complaint is the top block renders as plain stacked text:
```
Pricing Assumptions
Human-chosen conversion values used by the projection pipeline…
v3d1703433b13
Version hash of all assumption values
```
**Anchor:** lines 131-146 — the `<div className="flex flex-col sm:flex-row …">` header. Replace that whole
header `<div>` (lines 131-146) with a proper titled header + a version **card**:
```tsx
<div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-5 mb-8">
  <div className="max-w-2xl">
    <h1 className="text-2xl font-bold tracking-tight text-[color:var(--fcp-text-primary)]">Pricing Assumptions</h1>
    <p className="text-sm text-[color:var(--fcp-text-secondary)] mt-2 leading-relaxed">
      Human-chosen conversion values used by the projection pipeline. Each assumption is versioned —
      editing one changes the version hash for all subsequent jobs. Values are clamped to the allowed range.
    </p>
  </div>
  <div className="shrink-0 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-[var(--fcp-card-shadow)] px-4 py-3 anim-fade-in">
    <div className="text-[10px] uppercase tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1">Version hash</div>
    <div className="flex items-center gap-2">
      <span className="w-1.5 h-1.5 rounded-full bg-[#00C2BB]" />
      <span className="font-mono text-sm font-semibold text-[#00C2BB]">v{data?.version}</span>
    </div>
    <div className="text-[10px] text-[color:var(--fcp-text-tertiary)] mt-1">of all assumption values</div>
  </div>
</div>
```
This turns the loose version text into a labeled card and keeps the heading + description as a proper
titled block.

---

## ITEM 2 — Pricing Assumptions: cursor pointer on the category dropdown AND the Edit button

**File:** `frontend/src/pages/AdminAssumptions.tsx`

**2a — category header (the expand/collapse "dropdown").** Anchor: the `<button type="button"
onClick={() => toggleCollapse(catKey)}` (around line 172-176). Its className currently is:
```
w-full px-5 py-3.5 bg-white/[0.03] hover:bg-white/[0.05] border-b border-white/10 flex items-center justify-between text-left transition-colors
```
Add `cursor-pointer` to the end of that className string.

**2b — the Edit button.** Anchor: around lines 296-301, the button whose text is `Edit`:
```tsx
className="px-2.5 py-1.5 rounded border border-white/10 text-gray-300 hover:text-white hover:border-white/30 text-[10px] transition-colors"
```
Add `cursor-pointer`. Do the SAME (add `cursor-pointer`) to the `Save` button (lines ~284) and the
`Cancel` button (lines ~289) in that same row if they lack it.

---

## ITEM 3 — Pricing Assumptions: editing a row forces a scroll; expand the edit UI so Save/Cancel are visible

**File:** `frontend/src/pages/AdminAssumptions.tsx`
**Problem:** when you click Edit, the value input + reason input + Save/Cancel appear crammed inside narrow
table cells (value cell lines 225-269, action cell lines 278-302), so the reason field and buttons get
pushed out of view and you must scroll.

**Fix:** when a row is being edited, render an **expanded edit panel spanning the full row width** instead
of squeezing controls into cells. Concretely, inside the `groupAssumptions.map((a) => { … })` block, after
the normal `<tr>…</tr>` for that assumption, conditionally render a second full-width row that holds the
editor. Find the closing `</tr>` of the assumption row (line ~304, right before `)` and `})`). Keep the
display row but when `isEditing` is true, (a) in the value cell show just the current value read-only, and
(b) add this editor row immediately after the `</tr>`:
```tsx
{isEditing && (
  <tr className="bg-white/[0.02]">
    <td colSpan={6} className="px-5 py-4 anim-slide-down origin-top">
      <div className="flex flex-col sm:flex-row sm:items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-[10px] uppercase tracking-wider text-[color:var(--fcp-text-tertiary)]">New value</label>
          {a.key === 'aurora_acu_ram_gib' ? (
            <select
              value={edit.value}
              onChange={e => handleValueChange(a.key, e.target.value)}
              className="bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded px-2.5 py-1.5 text-sm cursor-pointer focus:border-[#645DF6] outline-none"
            >
              {RATIO_OPTIONS.map(opt => (<option key={opt.value} value={opt.value}>{opt.label}</option>))}
            </select>
          ) : (
            <input
              type="number" step="any" min={a.min_value} max={a.max_value}
              value={edit.value}
              onChange={e => handleValueChange(a.key, e.target.value)}
              className="bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded px-2.5 py-1.5 w-32 text-sm focus:border-[#645DF6] outline-none"
            />
          )}
          {edit.value && !inRange && <span className="text-[10px] text-red-400">out of range [{a.min_value} – {a.max_value}]</span>}
        </div>
        <div className="flex flex-col gap-1.5 flex-1">
          <label className="text-[10px] uppercase tracking-wider text-[color:var(--fcp-text-tertiary)]">Reason (required)</label>
          <input
            type="text" placeholder="Why is this changing?"
            value={edit.reason}
            onChange={e => handleReasonChange(a.key, e.target.value)}
            className="bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded px-3 py-1.5 text-sm placeholder:text-gray-500 focus:border-[#645DF6] outline-none w-full"
          />
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => handleSave(a.key)}
            disabled={!edit.value || !edit.reason.trim() || !inRange}
            className="px-4 py-2 rounded bg-[#645DF6] text-white text-xs font-medium cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[#645DF6]/85 transition-colors"
          >
            {saving === a.key ? 'Saving…' : 'Save'}
          </button>
          <button
            onClick={() => setEdits(prev => { const next = { ...prev }; delete next[a.key]; return next })}
            className="px-4 py-2 rounded text-xs text-[color:var(--fcp-text-secondary)] hover:text-[color:var(--fcp-text-primary)] hover:bg-white/5 cursor-pointer transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </td>
  </tr>
)}
```
Then SIMPLIFY the original cells so they no longer duplicate the editor: in the value cell (lines 225-269)
keep only the `else` branch (the read-only value display) by removing the `isEditing ?` ternary and always
rendering the value span; in the action cell (lines 278-302) when `isEditing` is true render nothing (the
Save/Cancel now live in the expanded panel), otherwise render the `Edit` button as before. The editor panel
is full width, so Save/Cancel are always visible without horizontal scrolling.

> If restructuring both cells is risky, the MINIMUM acceptable fix is: add the full-width `{isEditing && (
> <tr>…)}` editor panel above, and leave the old cramped cells — but change the old reason input and the
> Save/Cancel to still work. Prefer the full clean version.

---

## ITEM 4 — Notification dropdown opens abruptly (no animation)

**File:** `frontend/src/components/NotificationDropdown.tsx`
**Root cause:** the panel uses classes that don't exist in this project. **Anchor:** line 193:
```tsx
<div className={`absolute right-0 mt-2 w-96 max-w-[92vw] border rounded-2xl shadow-2xl overflow-hidden z-50 animate-in fade-in slide-in-from-top-2 duration-150 ${
```
Replace `animate-in fade-in slide-in-from-top-2 duration-150` with the project's `anim-slide-down origin-top-right`:
```tsx
<div className={`absolute right-0 mt-2 w-96 max-w-[92vw] border rounded-2xl shadow-2xl overflow-hidden z-50 anim-slide-down origin-top-right ${
```
`anim-slide-down` (defined in `index.css`) fades + slides the panel down smoothly.

Also add `cursor-pointer` to the bell trigger button if missing (line 160 className already has
`cursor-pointer` — leave it) and to the per-item rows (line 235 already has `cursor-pointer` — leave it).

---

## ITEM 5 — Sidebar opens abruptly when you click an item while collapsed

**File(s):** `frontend/src/components/Layout.tsx`, `frontend/src/components/Sidebar.tsx`

**5a — persist collapsed state so navigation does not reset it (hard snap).** Each page renders its own
`<Layout>`, so navigating remounts it and resets the sidebar to expanded. In
`frontend/src/components/Layout.tsx`, replace line 15:
```tsx
const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
```
with:
```tsx
const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
  try { return localStorage.getItem('fcp-sidebar-collapsed') === '1' } catch { return false }
})
const toggleSidebar = () => {
  setSidebarCollapsed(prev => {
    const next = !prev
    try { localStorage.setItem('fcp-sidebar-collapsed', next ? '1' : '0') } catch { /* ignore */ }
    return next
  })
}
```
Then find (line ~63) `onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}` and replace with
`onToggle={toggleSidebar}`. (If Work Order #1 Item 2 already did this, skip 5a.)

**5b — smooth width + label fade.** `frontend/src/components/Sidebar.tsx` line 110 already has
`transition-all duration-300 ease-in-out` on the `<aside>`, so the WIDTH animates. The labels pop in
instantly though. Anchor: line 74-83, the `{!collapsed && (…)}` block holding the label. Wrap the label
container so it fades in. Change line 74 from:
```tsx
{!collapsed && (
  <div className="flex items-center justify-between flex-1 truncate">
```
to:
```tsx
{!collapsed && (
  <div className="flex items-center justify-between flex-1 truncate anim-fade-in">
```
Now when the sidebar expands, the width glides (existing transition) and the labels fade in instead of
snapping.

---

## ITEM 6 — Give the option to DELETE a token (hard delete), not just revoke

Today the only action is **Revoke** (soft — sets `revoked_at`, row stays in the list). The user wants to
remove a token entirely. The backend currently has NO hard-delete route, so add one (small, mirrors the
existing revoke), then wire the frontend.

### 6a — Backend DB method
**File:** `internal/db/api_tokens.go`. The existing `RevokeAPIToken` is at lines 225-238. Immediately AFTER
its closing `}` (line 238), add a sibling that hard-deletes:
```go
// DeleteAPIToken permanently removes a token row owned by email.
func (d *DB) DeleteAPIToken(keyID, email string) error {
	result, err := d.conn.Exec(`
		DELETE FROM api_tokens
		WHERE key_id = ? AND user_email = ?
	`, keyID, strings.ToLower(email))
	if err != nil {
		return err
	}
	n, _ := result.RowsAffected()
	if n == 0 {
		return sql.ErrNoRows
	}
	return nil
}
```
(`strings` and `sql` are already imported in this file — RevokeAPIToken uses both.)

### 6b — Backend handler
**File:** `internal/jobs/handler.go`. The `RevokeUserAPIToken` handler is at lines 1935-1956. Immediately
AFTER its closing `}` (line 1956), add:
```go
func (h *Handler) DeleteUserAPIToken(w http.ResponseWriter, r *http.Request) {
	sess := auth.SessionFromCtx(r.Context())
	if sess == nil {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}
	if auth.TokenAuthenticated(r.Context()) {
		http.Error(w, `{"error":"token-authenticated requests cannot manage tokens"}`, http.StatusForbidden)
		return
	}
	keyID := chi.URLParam(r, "key_id")
	if err := h.db.DeleteAPIToken(keyID, sess.Email); err != nil {
		if err == sql.ErrNoRows {
			http.Error(w, `{"error":"token not found"}`, http.StatusNotFound)
			return
		}
		http.Error(w, fmt.Sprintf(`{"error":"failed to delete api token: %s"}`, err.Error()), http.StatusInternalServerError)
		return
	}
	_ = h.db.LogAdminAction(sess.Email, "token.delete", keyID, "")
	w.WriteHeader(http.StatusNoContent)
}
```

### 6c — Register the route
**File:** `cmd/server/main.go`. Find (line 289):
```go
r.Delete("/api/user/tokens/{key_id}", jobHandler.RevokeUserAPIToken)
```
Add a NEW line right below it (use a distinct sub-path so it does not collide with the revoke DELETE):
```go
r.Delete("/api/user/tokens/{key_id}/purge", jobHandler.DeleteUserAPIToken)
```

### 6d — Frontend API function
**File:** `frontend/src/api/auth.ts`. `revokeUserToken` is at lines 135-137 and looks like:
```ts
export async function revokeUserToken(keyId: string): Promise<void> {
  const res = await fetch(`/api/user/tokens/${keyId}`, { method: 'DELETE' })
```
Immediately after that function, add (match its exact style, including whatever error handling it uses):
```ts
export async function deleteUserToken(keyId: string): Promise<void> {
  const res = await fetch(`/api/user/tokens/${keyId}/purge`, { method: 'DELETE' })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(text || `delete failed (${res.status})`)
  }
}
```
> Open `revokeUserToken` and copy its exact `if (!res.ok)` pattern so the two read identically.

### 6e — Frontend button + confirm
**File:** `frontend/src/pages/ApiTokens.tsx`.
1. Import it (line 6 imports `revokeUserToken` — add `deleteUserToken`):
   ```tsx
   import { createUserToken, getUserTokens, revokeUserToken, deleteUserToken, type APIToken, type CreateUserTokenRequest } from '../api/auth'
   ```
2. Add state near the other hooks (after line 88 `const [revokeTarget, …]`):
   ```tsx
   const [deleteTarget, setDeleteTarget] = useState<{ keyId: string; tokenName: string } | null>(null)
   const [deletingTok, setDeletingTok] = useState(false)
   ```
3. Add a handler next to `handleRevoke` (after line 173):
   ```tsx
   const handleDeleteToken = async () => {
     if (!deleteTarget) return
     setDeletingTok(true)
     try {
       await deleteUserToken(deleteTarget.keyId)
       setTokens(prev => prev.filter(t => t.key_id !== deleteTarget.keyId))
       toast.success(`Deleted "${deleteTarget.tokenName}"`)
       setDeleteTarget(null)
     } catch (err) {
       toast.error(err instanceof Error ? err.message : 'delete failed')
     } finally {
       setDeletingTok(false)
     }
   }
   ```
4. In the Action cell (lines 320-332), the current logic shows "Revoke" for active tokens and a `—` for
   revoked/expired. Replace that cell content so a **trash-icon Delete button is ALWAYS available**, and
   Revoke stays for active tokens. Replace lines 320-332 (`<td className="px-5 py-4 text-right"> … </td>`)
   with:
   ```tsx
   <td className="px-5 py-4 text-right">
     <div className="inline-flex items-center gap-2 justify-end">
       {!revoked && !expired && (
         <button
           onClick={() => confirmRevoke(token.key_id, token.name)}
           disabled={revoking === token.key_id}
           className="text-[10px] text-orange-400 hover:text-orange-300 underline cursor-pointer disabled:opacity-40"
         >
           Revoke
         </button>
       )}
       <button
         onClick={() => setDeleteTarget({ keyId: token.key_id, tokenName: token.name })}
         title="Delete token permanently"
         className="inline-flex items-center justify-center w-7 h-7 rounded border border-white/10 text-gray-400 hover:text-red-400 hover:border-red-500/40 transition-colors cursor-pointer"
       >
         <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
           <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
         </svg>
       </button>
     </div>
   </td>
   ```
5. Add a second `ConfirmDialog` right after the existing revoke one (after line 488):
   ```tsx
   <ConfirmDialog
     open={!!deleteTarget}
     onOpenChange={(o) => { if (!o) setDeleteTarget(null) }}
     title={`Delete "${deleteTarget?.tokenName ?? ''}" permanently?`}
     description="This removes the token from your list entirely. Any integration still using it will stop working. This cannot be undone."
     confirmLabel="Delete token"
     destructive
     loading={deletingTok}
     onConfirm={handleDeleteToken}
   />
   ```
6. While here, fix the two emoji-as-icon glyphs: the `✕` close button (line 358) → replace its text with
   an inline X svg (`<svg className="w-4 h-4" …><path … d="M6 18L18 6M6 6l12 12" /></svg>`); the `+` on the
   Create button (line 240) → replace `<span>+</span>` with a small plus svg
   (`<svg className="w-4 h-4" …><path … d="M12 4v16m8-8H4" /></svg>`).

---

## ITEM 7 — Report Settings & Pricing Assumptions: "title + metadata" rows read as plain text

The setting/assumption rows stack `title`, the mono `key`, and the `description/rationale` as three loose
lines. Give the key a pill and the description clearer separation so it reads as structured metadata, not a
paragraph.

**7a — Report Settings.** File `frontend/src/pages/AdminReportSettings.tsx`, anchor lines 242-246:
```tsx
<td className="py-3.5 px-6">
  <div className="font-semibold text-white text-xs">{s.title}</div>
  <div className="text-[10px] font-mono text-gray-500 mt-0.5">{s.key}</div>
  <div className="text-[11px] text-gray-400 mt-1 leading-relaxed">{s.description}</div>
</td>
```
Replace with:
```tsx
<td className="py-3.5 px-6">
  <div className="flex items-center gap-2 flex-wrap">
    <span className="font-semibold text-[color:var(--fcp-text-primary)] text-xs">{s.title}</span>
    <span className="font-mono text-[9px] text-[color:var(--fcp-text-tertiary)] bg-[var(--fcp-bg-subtle)] border border-[var(--fcp-border-subtle)] px-1.5 py-0.5 rounded">{s.key}</span>
  </div>
  <div className="text-[11px] text-[color:var(--fcp-text-secondary)] mt-1.5 leading-relaxed max-w-md">{s.description}</div>
</td>
```

**7b — Pricing Assumptions.** File `frontend/src/pages/AdminAssumptions.tsx`, anchor lines 218-223 (the
assumption title cell):
```tsx
<td className="px-5 py-4">
  <div>
    <div className="font-semibold text-white">{a.title}</div>
    <div className="text-xs text-gray-500 mt-0.5 font-mono">{a.key}</div>
    <div className="text-xs text-gray-500 mt-1 max-w-xs truncate" title={a.rationale}>{a.rationale}</div>
  </div>
</td>
```
Replace with:
```tsx
<td className="px-5 py-4">
  <div className="flex items-center gap-2 flex-wrap">
    <span className="font-semibold text-[color:var(--fcp-text-primary)]">{a.title}</span>
    <span className="font-mono text-[9px] text-[color:var(--fcp-text-tertiary)] bg-[var(--fcp-bg-subtle)] border border-[var(--fcp-border-subtle)] px-1.5 py-0.5 rounded">{a.key}</span>
  </div>
  <div className="text-xs text-[color:var(--fcp-text-secondary)] mt-1.5 leading-relaxed max-w-sm" title={a.rationale}>{a.rationale}</div>
</td>
```
(Dropping `truncate` lets the rationale wrap instead of being cut.)

---

## ITEM 8 — Pricing Catalog page "looks so bad"

**File:** `frontend/src/pages/AdminCatalog.tsx`. The page is a bare page with one flat status panel (and,
unless Work Order #1 removed it, a duplicate email/Slack panel). Make it a clean card layout using tokens.

**8a — header.** Anchor lines 74-75:
```tsx
<h1 className="text-2xl font-semibold mb-1">GCP catalog</h1>
<p className="text-sm text-gray-500 mb-8">Freshness of the GCP SKU pricing catalog used for projections.</p>
```
Replace with:
```tsx
<h1 className="text-2xl font-bold tracking-tight text-[color:var(--fcp-text-primary)]">GCP Pricing Catalog</h1>
<p className="text-sm text-[color:var(--fcp-text-secondary)] mt-2 mb-8 max-w-2xl leading-relaxed">
  Freshness of the GCP SKU pricing catalog used for every projection. Refresh pulls the latest public
  Google Cloud rates.
</p>
```

**8b — status as a 2-up card grid.** Anchor the `{status && (…)}` block (lines 94-118). Replace the inner
content (keep the `{status && (` wrapper) so the two metrics are cards and the button sits below:
```tsx
{status && (
  <div className="space-y-6">
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)] anim-fade-in-up">
        <div className="text-[10px] uppercase tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1.5">Last refreshed</div>
        <div className="text-xl font-semibold tabular-nums text-[color:var(--fcp-text-primary)]">
          {status.age_days >= 9999 ? 'unknown' : `${status.age_days} day${status.age_days === 1 ? '' : 's'} ago`}
        </div>
      </div>
      <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-5 shadow-[var(--fcp-card-shadow)] anim-fade-in-up" style={{ animationDelay: '60ms' }}>
        <div className="text-[10px] uppercase tracking-wider text-[color:var(--fcp-text-tertiary)] mb-1.5">Status</div>
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${status.stale ? 'bg-orange-400' : 'bg-[#00C2BB]'}`} />
          <span className={`text-xl font-semibold ${status.stale ? 'text-orange-400' : 'text-[#00C2BB]'}`}>
            {status.stale ? 'Stale' : 'Fresh'}
          </span>
        </div>
      </div>
    </div>
    {actionError && <p className="text-xs text-orange-400">{actionError}</p>}
    <button
      onClick={handleRefresh}
      disabled={refreshing}
      className="btn-shimmer w-full sm:w-auto px-6 py-2.5 rounded-lg font-medium text-white text-sm cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
    >
      {refreshing ? 'Starting refresh…' : 'Refresh now'}
    </button>
  </div>
)}
```

**8c — remove the duplicate email/Slack panel if still present.** If lines ~120-160 still contain the
`{/* Notification health panel */}` block (email/Slack "Configured/Not configured"), DELETE it and the now
unused code (`notifyStatus`, `getNotifyStatus`, `notifyTest`, `handleNotifyTest`, `notifying`,
`setNotifying`, and the `NotifyStatus` type import). That config already lives on the integrations page.
Fix any "unused variable" TS errors `npm run build` reports. (If Work Order #1 already removed it, skip 8c.)

Also widen the container: anchor line 72 `max-w-2xl` → change to `max-w-4xl` for a less cramped page.

---

## Final verification checklist (do every line)

1. `cd frontend && npm run build` → zero TS errors. From repo root `go build ./...` → succeeds (Item 6).
2. Assumptions page: header shows title + description + a version **card** (Item 1); category header and
   Edit/Save/Cancel show a pointer cursor on hover (Item 2); clicking Edit opens a full-width panel with
   value + reason + Save/Cancel all visible without scrolling (Item 3); title rows show a key **pill** and
   wrapping rationale (Item 7b).
3. Click the bell → the dropdown slides/fades in, not instant (Item 4).
4. Collapse sidebar, navigate to another route → stays collapsed; expanding glides and labels fade (Item 5).
5. API Tokens page: every token row has a trash **Delete** button; confirming removes it from the list and
   shows a toast; no `✕`/`+` text glyphs remain (Item 6).
6. Report Settings: setting rows show a key pill + separated description (Item 7a).
7. Pricing Catalog: clean two-card layout in both light and dark mode, no email/Slack panel (Item 8).
8. No raw `bg-gray-800` / `rgba(255,255,255,…)` left in any element you edited — tokens only.

**Do not** add libraries, rename routes, change unrelated pages, or touch backend code outside Item 6.
If an anchor's line number has shifted, search the file for the exact quoted string and edit there.
