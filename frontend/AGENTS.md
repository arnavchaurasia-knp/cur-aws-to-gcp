# Frontend Design Rules — GCP TCO Analyzer (Facets.cloud)

> **Scope:** the `frontend/` React + TypeScript + Tailwind v4 app.
> **Who reads this:** this file is auto-loaded by the coding agent (Antigravity reads
> `AGENTS.md` from this folder) and is also the review law for any UI work. Every
> screen must obey it. When a request and this file conflict, the request wins —
> but say what you overrode and why.
>
> **Stack facts you must respect:** React 19, Tailwind CSS v4 (`@import "tailwindcss"`
> in `src/index.css`), react-router-dom v7, `sonner` for toasts. Theme = the `dark`
> class on `<html>` (dark is default; light = no `dark` class). Design tokens already
> live in `src/index.css` as `--fcp-*` / `--facets-*` CSS variables. **Use those
> tokens — do not invent parallel ones.** Fonts Plus Jakarta Sans + Inter are already
> loaded in `index.html`. Do not add npm packages or change routes/auth/business logic.

---

## 0. The one rule above all
Make deliberate choices, not templated ones. A screen should look like it was
designed for *this* product (an auditable AWS→GCP cost estimator for finance/cloud
engineers), not assembled from defaults. **Let one element be memorable; keep
everything else quiet and disciplined.** Spend boldness in exactly one place per screen.

## 1. Avoid these generic "AI SaaS" tells (unless the request explicitly asks)
These read as templated. Do not reach for them by reflex:
- All-caps tracked eyebrows/labels stacked above every heading. Use **at most one**
  overline per screen, and only when it genuinely orients the user.
- Accenting a single word in a headline with a gradient/color just for flair.
- Middle-dot meta strings (`A • B • C`). Prefer real spacing or list structure.
- A kit of identical rounded cards, all with the same radius + same soft shadow +
  gradient wash. Differentiate by hierarchy, not by cloning one card.
- A fade-and-slide entrance on every section and a hover-lift on every card.
- Tinted near-black backgrounds (`#0B0B0B`, `#111`), monospace for small data labels,
  and arrows appended to every link/button.
Structural devices (rules, borders, numbers, labels) must **encode information** —
numbered markers only for real sequences, labels only when the content needs naming.

## 2. Spacing — strict 4px grid
Every margin, padding, and gap is a multiple of **4px**. Allowed Tailwind steps:
`1`(4) `2`(8) `3`(12) `4`(16) `5`(20) `6`(24) `8`(32) `10`(40) `12`(48) `16`(64).
**Banned** (off-grid): `*-0.5`, `*-1.5`, `*-2.5`, `*-3.5` (i.e. 2/6/10/14px). If old
code has `px-3.5`/`py-2.5`, snap to the nearest allowed step.
- **Group with space, not borders.** Related items get tight, equal gaps; separate
  groups get a larger gap. Reach for a divider/border only when space can't do the job.
- **Equal page gutters** on both sides, consistent across breakpoints
  (phone `px-6` = 24, wider `px-8`/`px-12`). Never different left vs right.

## 3. Typography — one family, hierarchy by weight + color (not size alone)
- **Single family: Plus Jakarta Sans** for everything (headings and body). (Inter is a
  fallback only.) Fix `src/index.css` so Plus Jakarta Sans is first in `font-family`.
- Scale (sizes are a type scale, exempt from the 4px rule):
  | Role | Size | Weight | Color token |
  |---|---|---|---|
  | Display / product H1 | 28–32px | 700 | text-primary |
  | Section H2 | 20–22px | 600 | text-primary |
  | Subhead / lead | 15–16px | 500 | text-secondary |
  | Body | 14px | 400–500 | text-primary/secondary |
  | Label | 12–13px | 600 | text-secondary |
  | Helper / caption | 12px | 400 | text-tertiary |
- **Communicate importance with weight and color first, size second.** Don't make
  everything bold or dark; let secondary/tertiary text recede.
- Line length ≤ ~80 characters. Comfortable line-height on body (`leading-relaxed`).
- Sentence case for UI copy. Copy is design: write active CTAs, name actions
  consistently across a flow (a `Publish` button → a `Published` toast), make errors
  say what happened + how to fix it (no apology), make empty states invite action.

## 4. Color — use the existing `--fcp-*` tokens
- **Brand:** primary purple `#645DF6` (hover `#5046e5`), accent teal `#00C2BB`
  (hover `#00a8a2`). Use accent sparingly, to direct attention — not as decoration.
- **Dark (default):** bg `--fcp-bg-base #0c0d14`, surface `#12131d`, raised `#181926`;
  text `--fcp-text-primary #f8fafc` / `--fcp-text-secondary #94a3b8` /
  `--fcp-text-tertiary #64748b`; border `--fcp-border-subtle rgba(255,255,255,.12)`.
  On dark promo surfaces use `slate-200/300` for body text, **not** `gray-400/500`
  (too low contrast).
- **Light:** bg `#f8fafc`, surface `#ffffff`, raised `#f1f5f9`; text `#0f172a` /
  `#475569` / `#64748b`; border `#e2e8f0` (subtle) / `#cbd5e1` (strong).
- Keep body text contrast ≥ WCAG AA (4.5:1). Never rely on color alone to convey state.

## 5. Radius, borders, shadows — one system, used everywhere
- **Radius scale:** inputs/buttons `rounded-lg` (8px); cards/panels/dialogs
  `rounded-xl` (12px); pills `rounded-full`. **Nested elements take a smaller radius
  than their parent** (a field inside a `rounded-xl` card is `rounded-lg`).
- **Borders:** `1px`, subtle (`--fcp-border-subtle`). Borders give structure, not
  decoration. No thick or high-contrast borders.
- **Shadows:** soft and layered. Resting `shadow-sm`; hover/dialog step up to
  `shadow-md`→`shadow-lg`. Use the `--fcp-card-shadow` token for surfaces. No harsh,
  single, dark drop shadows; no elevation on things that don't lift.

## 6. Motion — purposeful, and it originates where the user acted
- **One timing for the whole app:** duration `var(--fcp-anim-dur)` (0.42s), easing
  `var(--fcp-anim-ease)` (`cubic-bezier(0.22,1,0.36,1)`). Reuse the existing utility
  classes (`.anim-fade-in-up`, `.anim-scale-in`, etc.).
- **Animate every meaningful interaction and state change:**
  - Buttons: hover, press (`active:scale-[0.99]`), release, disabled.
  - Inputs: smooth focus ring transition; clear validation feedback.
  - Dropdowns/accordions: expand/collapse from their trigger.
  - Modals/dialogs: enter **and exit** animations that originate from the trigger
    point; exit must finish cleanly without blocking navigation/unmount.
  - Route changes: a single page-enter transition.
  - Cards: subtle hover feedback **only where the card is interactive** (not on every card).
  - Toasts & data updates: natural entrance/exit; animate value/content changes.
- Keep it subtle. Prefer one orchestrated moment over motion on everything. Motion
  must never delay the user's action. **Honor `prefers-reduced-motion`** (the
  `@media` block already in `index.css` — never remove it).

## 7. Icons
One icon set, consistent **size** (16 or 20px, pick per context and keep it) and
**stroke width**. Don't mix filled and outlined styles without a reason. Align icons
to the text baseline/center of adjacent text.

## 8. Focus & accessibility (non-negotiable)
- Visible keyboard focus on every interactive element (purple ring:
  `focus:ring-2 focus:ring-[#645DF6]`). Never remove outlines without a replacement.
- Real `<label>`s tied to inputs; meaningful `alt`; correct roles.
- Full keyboard operability. Decorative elements get `aria-hidden`.

## 9. Loading, empty, error states — skeletons, not blank screens or spinners
- While content loads, show **skeleton placeholders that mirror the real layout**
  (same dimensions, so nothing shifts when data arrives). Use the shared
  `<Skeleton>` component (`src/components/Skeleton.tsx`); if it doesn't exist yet,
  create it (a `.skeleton` shimmer class on neutral `--fcp-bg-raised`→`--fcp-bg-hover`,
  respecting reduced-motion).
- Skeletons are for **initial content load** of dashboards, reports, tables, cards,
  forms — not for every tiny background refresh.
- Small actions (a button submitting, one value refreshing) use a compact **inline
  loader** (spinner inside the button), not a skeleton.
- Loaded-but-empty → a meaningful empty state that invites the next action.
- Fetch failed → a clear error state with a retry, worded per §3.
- Never use a generic full-page spinner where a content skeleton would be better.

## 10. Component consistency
Same component = same look and behavior everywhere. Reuse before building new.
Keep button heights, input heights, padding, radii, borders, and interaction states
identical across pages. Prefer shared primitives (`Button`, `Input`, `Card`, `Dialog`,
`Skeleton`) over re-styling per page.

---

## 11. Required workflow for the agent (follow in order)
1. **Plan first, before coding.** Write a 5-line plan: (a) 4–6 named hex colors from
   the tokens above, (b) the type roles you'll use, (c) the layout concept in one
   sentence + a rough ASCII wireframe, (d) the alignment choice, (e) where the single
   memorable element is and why.
2. **Self-check the plan against §1.** If any part is a generic default, change it and
   note what changed and why.
3. **Build**, using the tokens and scales above. Watch CSS specificity in `index.css`
   (the file has many `!important` light-mode overrides — make sure your classes
   aren't silently cancelled; verify the rendered result, not just the markup).
4. **Critique the rendered screen** at desktop **and** mobile widths (screenshot if
   possible). Check spacing rhythm, hierarchy/focal point, contrast, focus rings,
   reduced-motion, and the §1 tells. Apply the "remove one accessory" test — delete
   the least necessary decorative element. Fix and repeat until it passes.
