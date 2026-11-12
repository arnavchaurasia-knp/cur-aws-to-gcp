# WORK ORDER — Login screen redesign

> **Supersedes** `docs/WORKORDER_login_ui_redesign.md` (the earlier full-code version).
> This version is thin on purpose: the *how* lives in **`frontend/AGENTS.md`**, which
> the agent auto-loads. This file says *what* to build and how we judge "done."
> **Read `frontend/AGENTS.md` first and obey it.** If anything here conflicts with it,
> this file wins for the login screen only.

**File to change:** `frontend/src/pages/Login.tsx` (and `frontend/src/index.css` only if
you need to add/adjust a token or the font-family per `frontend/AGENTS.md`).
**Do not** touch routes, auth, `fetch('/api/auth/login')`, the Google OAuth flow, or any
other page. Do not add packages.

---

## 1. What this screen is
The sign-in / register screen for an **auditable AWS→GCP cost estimator** used by cloud
and finance engineers. Tone: precise, trustworthy, enterprise — not playful. Keep the
existing **two-panel layout** (left = sign-in, right = dark brand/promo) and the
email+password + Google sign-in functionality exactly as they work today.

## 2. Hard requirements (must all be true)
1. **Use the new white logo on the right (dark) panel.** Asset is already in the repo:
   `frontend/src/assets/facets-logo-f-white.png`. Use it for the large centered glyph
   (and the small "Powered by" mark). Do **not** use the colored `facets-logo-f.svg`
   on the dark panel anymore. The left panel keeps the full wordmark
   `facets-logo-full.svg`.
2. **Remove the "Terms" link.** There is **no Terms route/page** in this app — it is only
   an external link in the login footer (`facets.cloud/terms-of-service`). Delete that
   one link. Keep Privacy, Demo, Why Facets as real links. (Per `frontend/AGENTS.md §1`,
   if the remaining footer reads as a middle-dot `A • B • C` meta string, prefer plain
   spacing/structure over dots.) Afterwards, `grep -rniE "terms-of-service|>\\s*Terms\\s*<" frontend/src/`
   must return nothing. Do **not** remove unrelated variables named `terms`
   (in `AdminJobs.tsx`, `ReleaseDashboard.tsx`).
3. Preserve all existing logic: the OAuth `code`/`error` query-param handling, the error
   toasts, the drop-line glyph animation, `useTitle('Sign in')`.

## 3. Design intent (apply `frontend/AGENTS.md`; these are the screen-specific calls)
- **Focal point = the primary "Sign In / Register" button.** It is the one bold element
  on the left panel (solid brand purple `#645DF6`). Everything else on the left is quiet.
- **Hierarchy by weight + color, not size spam.** Product name is the strongest text;
  the "sign in to your account" lead is secondary; the field helper text is tertiary.
  Reconsider the stacked all-caps eyebrow + all-caps field labels per `AGENTS.md §1/§3` —
  keep at most one overline, and prefer sentence-case field labels unless you have a
  reason. Left-align the heading block with the form for clean alignment.
- **Spacing on the 4px grid** with equal side gutters; group the form fields tightly and
  separate the form / divider / Google button as distinct groups by space (`§2`).
- **Radius/borders/shadows** per `§5`: inputs & button `rounded-lg`, 1px subtle borders,
  soft shadow; the white glyph can keep a soft drop-shadow for depth on dark.
- **Right panel contrast:** body + feature text in `slate-200/300`, not dim gray (`§4`).
- **Motion** per `§6`: input focus transition, button hover/press, a restrained entrance.
  Avoid fade-and-sliding every single element in sequence; keep one orchestrated moment
  (the existing glyph drop is a good candidate). Honor reduced-motion.
- **Focus rings** visible on both inputs and both buttons (`§8`).

## 4. Responsive
- Phone (~375px): only the left sign-in panel, full width, 24px side gutters, no
  horizontal scroll. Tablet (~768px): still just the left panel (right is `lg:` only).
  Desktop (≥1024px): both panels, balanced.

## 5. Workflow (do this, per `frontend/AGENTS.md §11`)
1. Write the 5-line plan (colors / type roles / layout concept + ASCII wireframe /
   alignment / where the one memorable element is).
2. Check it against the generic-defaults list; revise and note changes.
3. Build.
4. Run the app, view `/login` at desktop + mobile, screenshot, and self-critique against
   §3–§4 and the acceptance checklist below. Fix and repeat.

## 6. Acceptance checklist (all must pass)
- [ ] `npm run build` passes with zero TypeScript errors (run from `frontend/`).
- [ ] Right dark panel shows the **white** Facets emblem; "Powered by" mark is white.
- [ ] Footer has **no Terms link**; grep in §2.2 returns nothing.
- [ ] Email/password sign-in and Google sign-in still work (logic unchanged).
- [ ] Primary button is clearly the focal point; secondary text recedes (weight/color).
- [ ] All spacing is on the 4px grid; side gutters equal; groups separated by space.
- [ ] Inputs & buttons share one radius/border/shadow system; focus rings visible.
- [ ] Right-panel body text is readable (slate-200/300, not gray-400).
- [ ] Responsive at 375 / 768 / ≥1024px; no horizontal scrollbar on phone.
- [ ] Reduced-motion: with it on, content is fully visible and motion stops.
- [ ] None of the `frontend/AGENTS.md §1` tells are present unintentionally.

---

### Reviewer loop (for Claude, after the agent implements)
Claude runs the login page, screenshots desktop + mobile, reviews against
`frontend/AGENTS.md` + §6, and returns a short **correction work order** of exact diffs
(element → current value → target value → reason). Repeat until the checklist passes.
