# WORK ORDER #3 — Unified, calm animation system + page transitions (AGY cur-web)

> **READ FIRST — you are an implementation model. You CANNOT browse the web or guess.** Every file
> path, line number, the exact text to find, and the exact replacement are below. Work top to bottom,
> ONE item at a time. After each edit, re-open the file and confirm the change landed. Touch ONLY the
> files named here. Do NOT add npm packages. This is a **B2C production app** — animations must feel
> smooth, consistent, and relaxing, never flashy or abrupt.

## The goal (what "done" feels like)

1. **One timing for everything.** Every entrance animation and every layout transition uses the SAME
   duration and the SAME easing, so elements "arrive together," like the sidebar menu sliding open when
   you click the Facets logo.
2. **Every page animates in.** Today most pages snap in with no animation. After this, navigating to any
   route plays ONE consistent, gentle entrance — fade + a small upward glide — matching the sidebar's
   calm feel.
3. **No per-page animation drift.** Pages currently use a mix of `anim-fade-in` (0.35s), `anim-fade-in-up`
   (0.45s), `anim-slide-down` (0.35s), `anim-scale-in` (0.3s) and `duration-100/150/200/300`. We collapse
   the *entrance* timings to one shared value so nothing feels faster or slower than its neighbor.
4. **API Tokens and Users pages: no horizontal scroll, a bit wider, visually uniform.**

## Environment facts (do not re-derive)

- Repo root: `C:\Users\arnav\Downloads\cur-web-AGY`. Frontend: `frontend/` (React 19 + Vite + Tailwind v4 + TS).
- All animation CSS lives in **`frontend/src/index.css`**. Keyframes are at lines ~236-268; the utility
  classes `.anim-*` at lines ~271-290; stagger delays `.delay-*` at ~293-299; the reduced-motion guard at
  ~492-505.
- The sidebar "menu opening" feel is the `<aside>` in `frontend/src/components/Sidebar.tsx:110` — a
  `transition-all duration-300 ease-in-out` on width. The content/header shift in
  `frontend/src/components/Layout.tsx:37` and `:67` also use `transition-all duration-300`.
- Reduced-motion must keep working: anything you animate must degrade to no-motion under
  `@media (prefers-reduced-motion: reduce)`.
- Build check after everything (from `frontend/`): `npx tsc --noEmit` → zero errors, then `npm run build`.

---

## ITEM 1 — Add one shared timing system in CSS

**File:** `frontend/src/index.css`.

**1a — add timing tokens.** Find the `:root {` block near the top (where `--fcp-bg-base:` etc. are
defined, ~line 13). Add these two variables inside that same `:root { … }` block (put them right after the
last `--fcp-…` line, before the closing `}`):
```css
  /* Unified animation timing — one duration + easing for the whole app */
  --fcp-anim-dur: 0.42s;
  --fcp-anim-ease: cubic-bezier(0.22, 1, 0.36, 1);
```
> `0.42s` is the single "relaxing" speed used everywhere from now on. `cubic-bezier(0.22,1,0.36,1)` is a
> gentle decelerate (already used by several classes) — calm, no bounce.

**1b — rewrite the `.anim-*` classes to all use the shared tokens.** Anchor: lines 271-290. Replace the
entire block (from `.anim-fade-in-up  {` through `.anim-glow-breathe { … }`) with:
```css
.anim-fade-in-up  {
  animation: fade-in-up var(--fcp-anim-dur) var(--fcp-anim-ease) both;
  will-change: transform, opacity;
  transform: translateZ(0);
}
.anim-fade-in     {
  animation: fade-in var(--fcp-anim-dur) var(--fcp-anim-ease) both;
  will-change: opacity;
}
.anim-slide-down  {
  animation: slide-down var(--fcp-anim-dur) var(--fcp-anim-ease) both;
  will-change: transform, opacity;
  transform: translateZ(0);
}
.anim-scale-in    {
  animation: scale-in var(--fcp-anim-dur) var(--fcp-anim-ease) both;
  will-change: transform, opacity;
  transform: translateZ(0);
}
.anim-glow-breathe { animation: glow-breathe 2.8s ease-in-out infinite; }
```
Now every entrance class runs at the identical duration and easing (the glow "breathe" loop stays as-is —
it is an ambient loop, not an entrance).

**1c — add the shared page-entrance class.** Immediately AFTER the `.anim-glow-breathe` line you just
wrote, add:
```css
/* ── Page entrance — the one animation every route plays on mount ── */
.page-enter {
  animation: fade-in-up var(--fcp-anim-dur) var(--fcp-anim-ease) both;
  will-change: transform, opacity;
  transform: translateZ(0);
}
```

**1d — keep reduced-motion honest.** Anchor: lines 494-499 (the selector list inside
`@media (prefers-reduced-motion: reduce)`). Add `.page-enter` to that list. Change the first selectors from:
```css
  .anim-fade-in-up, .anim-fade-in, .anim-slide-down,
  .anim-scale-in, .anim-glow-breathe, .btn-shimmer,
```
to:
```css
  .anim-fade-in-up, .anim-fade-in, .anim-slide-down,
  .anim-scale-in, .anim-glow-breathe, .btn-shimmer, .page-enter,
```

---

## ITEM 2 — Play the page-entrance on EVERY route, centrally (one place, uniform)

Instead of editing 20 pages, animate the content wrapper in `Layout` and re-key it by route so the SAME
entrance replays on every navigation.

**File:** `frontend/src/components/Layout.tsx`.

**2a — import `useLocation`.** Line 2 is:
```tsx
import { Navigate } from 'react-router-dom'
```
Replace with:
```tsx
import { Navigate, useLocation } from 'react-router-dom'
```

**2b — read the location.** Inside the component, after line 14 `export function Layout({ user, children }: LayoutProps) {`,
add as the first line of the body:
```tsx
  const location = useLocation()
```

**2c — wrap children in a keyed, animated div.** Anchor: lines 78-80:
```tsx
<main className="flex-1 pt-16 min-h-screen overflow-x-hidden">
  {children}
</main>
```
Replace with:
```tsx
<main className="flex-1 pt-16 min-h-screen overflow-x-hidden">
  <div key={location.pathname} className="page-enter">
    {children}
  </div>
</main>
```
Because `key` changes on every route, React remounts this div per navigation, so `.page-enter` replays
every time — one uniform, gentle entrance for all pages. No per-page edits needed.

> After this, REMOVE page-level entrance classes that would now double up: in
> `frontend/src/pages/ReleaseDashboard.tsx:44`, the container has `anim-fade-in-up` — delete just that one
> class token from its className so the page doesn't animate twice. (Leave `anim-*` classes that animate
> *inner* cards/sections on other pages — those are staggered content reveals, which are fine and still
> run at the unified speed after Item 1.)

---

## ITEM 3 — Make the sidebar / header / content transitions use the same timing

So the menu open, the content shift, and the page entrance all "move together."

**File:** `frontend/src/components/Sidebar.tsx`. Anchor: line 110:
```tsx
className={`fixed top-0 bottom-0 left-0 h-screen z-50 flex flex-col transition-all duration-300 ease-in-out border-r select-none
```
Replace `duration-300 ease-in-out` with `duration-[420ms] ease-[cubic-bezier(0.22,1,0.36,1)]`:
```tsx
className={`fixed top-0 bottom-0 left-0 h-screen z-50 flex flex-col transition-all duration-[420ms] ease-[cubic-bezier(0.22,1,0.36,1)] border-r select-none
```
(`420ms` ≈ the `0.42s` token; Tailwind cannot read the CSS var in a `duration-` utility, so use the literal.)

**File:** `frontend/src/components/Layout.tsx`. Two spots use `transition-all duration-300`:
- Line 37 (header): replace `transition-all duration-300` with
  `transition-all duration-[420ms] ease-[cubic-bezier(0.22,1,0.36,1)]`.
- Line 67 (content flex row): same replacement — `transition-all duration-300` →
  `transition-all duration-[420ms] ease-[cubic-bezier(0.22,1,0.36,1)]`.

Now the sidebar width, the header offset, and the content padding all glide at the same 420ms with the same
easing as the page entrance — everything arrives together.

> Leave small UI micro-interactions (hover color `transition-colors duration-150/200`, the bell icon
> `group-hover:scale-110 duration-150`, toggle switches) as they are — those are hover feedback, not
> entrance/layout motion, and should stay snappy. This item is ONLY about the three layout transitions
> above.

---

## ITEM 4 — Notification dropdown: confirm it uses the unified entrance

**File:** `frontend/src/components/NotificationDropdown.tsx`, line ~193. It should already use
`anim-slide-down origin-top-right` (from Work Order #2). Confirm that class is present; if it still shows
`animate-in fade-in slide-in-from-top-2 duration-150` (undefined classes), replace that run of classes with
`anim-slide-down origin-top-right`. After Item 1, this now runs at the unified 0.42s. No other change.

---

## ITEM 5 — API Tokens page: remove horizontal scroll, widen, breathe

**File:** `frontend/src/pages/ApiTokens.tsx`.
The tokens table has 8 columns (Name, Token, Scopes, Created, Last used, Expires, Status, Action) inside a
`max-w-4xl` container, forcing a horizontal scrollbar.

**5a — widen the page.** Anchor: line 240:
```tsx
<div className="max-w-4xl mx-auto px-6 py-8">
```
Replace with:
```tsx
<div className="max-w-6xl mx-auto px-6 py-8">
```

**5b — tighten column padding so all 8 columns fit without scroll.** The header cells (lines ~260-271) and
body cells use `px-5`. Reduce horizontal padding to `px-4` on the token table ONLY. In this file, within the
`<table>` that starts at line 275, change every `px-5` to `px-4` on the `<th>` and `<td>` elements of that
table (there are header cells around lines 260-271 and body cells around lines 294-332). Do not change
padding anywhere else in the file (the modals keep their padding).

**5c — let the table use the new width.** Anchor: line 274:
```tsx
<div className="overflow-x-auto">
```
Replace with:
```tsx
<div className="overflow-x-auto xl:overflow-x-visible">
```
On wide screens the table now lays out fully (no scroll); on small screens the scroll remains as a safe
fallback instead of squishing.

---

## ITEM 6 — Users page: widen + make it uniform with the Tokens/admin tables

**File:** `frontend/src/pages/AdminUsers.tsx`.

**6a — widen + match page padding.** Anchor: line 130:
```tsx
<div className="max-w-5xl mx-auto px-6 py-10">
```
Replace with:
```tsx
<div className="max-w-6xl mx-auto px-6 py-8">
```
(`max-w-6xl` + `py-8` matches the Tokens page so the two admin tables feel like one family.)

**6b — uniform card chrome.** Anchor: line 160:
```tsx
<div className="border border-white/10 rounded-xl overflow-hidden shadow-sm">
```
Replace with (token-driven, same as other cards so it reads identically in light + dark):
```tsx
<div className="border border-[var(--fcp-border-subtle)] rounded-xl overflow-hidden shadow-[var(--fcp-card-shadow)]">
```

> Do NOT re-add any page-level entrance class here — Item 2 already animates the whole page via Layout.

---

## Final verification checklist (do every line)

1. `cd frontend && npx tsc --noEmit` → zero errors; `npm run build` → succeeds.
2. Navigate between several admin routes (All Projections → Audit → Assumptions → Users → API Tokens):
   **every** page fades + glides in with the SAME calm speed; none snaps in (Items 1, 2).
3. Click the Facets logo to open/close the sidebar: the menu width, the top bar, and the content all move
   together at the same speed as the page entrance — nothing lags (Item 3).
4. Open the notification bell: panel slides down at the same unified speed (Item 4).
5. API Tokens page at normal desktop width: no horizontal scrollbar; all 8 columns visible; feels roomy
   (Item 5).
6. Users page: wider, same padding/card look as the Tokens page (Item 6).
7. Turn ON "reduce motion" in the OS: pages appear instantly with no animation and nothing is broken
   (Item 1d).
8. Grep-check: only ONE page-entrance mechanism exists — `.page-enter` on the Layout wrapper. No page's
   top-level container still carries `anim-fade-in-up` as its own entrance (ReleaseDashboard fixed).

**Do not** add libraries, change routes, change backend code, or alter hover/toggle micro-interactions.
If an anchor's line number shifted, search the file for the exact quoted string and edit there.
