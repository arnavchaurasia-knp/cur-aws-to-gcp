# WORK ORDER — Login UI Redesign + Design Foundation (Phase 1)

**Author (architect):** orchestrator model
**Executor (you):** implementation model
**Repo:** `cur-web-AGY`
**Frontend path:** `frontend/`
**Date:** 2026-11-11

---

## 0. HOW TO READ THIS DOCUMENT (READ THIS FIRST — DO NOT SKIP)

You are the builder. I am the architect. Every decision has already been made
for you in this document. **You do not need to design anything, choose any color,
pick any spacing value, or invent any code.** Your only job is to apply the exact
changes written below, in order, exactly as written.

Rules you MUST obey:

1. **Do not improvise.** If something is not written in this work order, do not do it.
2. **Do not refactor unrelated code.** Only touch the files listed in each TASK.
3. **Do not change any business logic, API calls, routes, or auth behavior.**
   The sign-in form, the Google button, the OAuth redirect handling, and the
   `fetch('/api/auth/login')` call MUST keep working exactly as they do today.
4. **Do not add npm packages.** Everything uses what is already installed
   (React 19, Tailwind CSS v4, react-router-dom v7, sonner).
5. **Do not invent data, fake delays, or mock content.**
6. When a TASK says "replace the whole file," delete the old contents completely
   and paste the new contents exactly.
7. When a TASK says "find this / replace with this," the "find" text must match
   the file exactly. If it does not match, STOP and report it — do not guess.
8. After all tasks, run the **VERIFICATION** section. Every checkbox must pass.

If any instruction cannot be applied as written, STOP and report the exact file,
the exact line, and what you saw instead. Do not continue past a failed step.

---

## 1. CONTEXT — WHAT THIS APP IS AND HOW IT IS BUILT

- This is the **GCP TCO Analyzer** frontend (brand: **Facets.cloud**). It maps
  AWS bills to GCP costs.
- Framework: **React 19 + TypeScript + Vite**. Styling: **Tailwind CSS v4**
  (imported in `frontend/src/index.css` via `@import "tailwindcss";`).
- Theming: a class `dark` is toggled on the `<html>` element. Dark is the default.
  Light mode = `<html>` WITHOUT the `dark` class. See `frontend/src/lib/theme.ts`.
- Global design tokens and animations already live in `frontend/src/index.css`
  as CSS variables named `--fcp-*` and `--facets-*`, plus utility classes like
  `.anim-fade-in-up`, `.btn-shimmer`, `.login-bg`, etc.
- Toasts use the `sonner` library (already wired in `frontend/src/App.tsx`).
- Fonts **Plus Jakarta Sans** and **Inter** are already loaded in
  `frontend/index.html` via Google Fonts. (You do not need to add them.)

Files you will touch in this work order (and ONLY these):

| TASK | File | Action |
|------|------|--------|
| 1 | `frontend/src/index.css` | Edit (add/adjust tokens) |
| 2 | `frontend/src/assets/facets-logo-f-white.png` | Already added — just confirm it exists |
| 3 | `frontend/src/pages/Login.tsx` | Replace the whole file |
| 4 | (Terms link) | Already removed inside TASK 3 — just verify |
| 5 | `frontend/src/components/Skeleton.tsx` | Create new file (foundation) |

Do not touch any other file.

---

## 2. THE DESIGN SYSTEM (THESE ARE THE DECISIONS — USE THESE VALUES ONLY)

You will not choose values. You will use the ones below.

### 2.1 Spacing — 4px grid
Only use spacing that is a multiple of **4px**. In Tailwind that means these
utilities are allowed: `p-1`(4) `p-2`(8) `p-3`(12) `p-4`(16) `p-5`(20) `p-6`(24)
`p-8`(32) `p-10`(40) `p-12`(48), and the matching `m-*`, `gap-*`, `px/py`, `mt/mb`, etc.

**BANNED** (not on the 4px grid) — never use these: `p-2.5` (10px), `px-3.5` (14px),
`py-2.5` (10px), `mt-0.5` (2px), `gap-2.5` (10px), or any `*-0.5`, `*-1.5`, `*-2.5`,
`*-3.5` spacing. If you see one in old code you are replacing, snap it to the nearest
allowed value as the new code below already does.

(Font sizes are a separate type scale — the 4px rule does NOT apply to font sizes.)

### 2.2 Border radius scale
- Inputs & buttons: `rounded-lg` (8px)
- Cards / panels / dialogs: `rounded-xl` (12px)
- Pills / small chips: `rounded-full`
- Nested elements use a SMALLER radius than their parent.

### 2.3 Shadows (use the token, not ad-hoc values)
- Resting surface: `shadow-sm`
- Hover lift / dialog: `shadow-md` → `shadow-lg`
- Do not use harsh/black heavy shadows.

### 2.4 Typography (single family: Plus Jakarta Sans)
| Role | Size | Weight | Color token |
|------|------|--------|-------------|
| Page / product H1 | 28px | 700 | primary text |
| Section heading (H2) | 20–22px | 600 | primary text |
| Eyebrow / overline | 12px | 600, uppercase, tracking-wide | brand purple |
| Field label | 12px | 600, uppercase, tracking-wide | secondary text |
| Body | 14px | 400–500 | primary/secondary |
| Helper / caption | 12px | 400 | tertiary text |

### 2.5 Color tokens (ALREADY EXIST in `index.css` — use these names)
- Brand primary (purple): `#645DF6`  (hover `#5046e5`)
- Brand accent (teal): `#00C2BB`  (hover `#00a8a2`)
- Light surfaces: white `#ffffff`, page `#f8fafc`, raised `#f1f5f9`
- Light text: primary `#0f172a`, secondary `#475569`, tertiary `#64748b`
- Light border: `#e2e8f0` (subtle) / `#cbd5e1` (strong)
- Dark promo panel text: primary `#f8fafc`, secondary `#cbd5e1` (NOT gray-400 —
  see TASK 3, we are raising contrast on the dark panel).

### 2.6 Animation (ONE timing for the whole app — tokens already exist)
- Duration: `var(--fcp-anim-dur)` = `0.42s`
- Easing: `var(--fcp-anim-ease)` = `cubic-bezier(0.22, 1, 0.36, 1)`
- Reduced motion is already handled by the `@media (prefers-reduced-motion)`
  block in `index.css`. Do not remove it.

---

## TASK 1 — Make Plus Jakarta Sans the primary font (foundation)

**Problem:** The font is loaded, but the global `font-family` in `index.css` lists
the system fonts FIRST, so Plus Jakarta Sans almost never actually renders. This
breaks "single font family" consistency.

**File:** `frontend/src/index.css`

**Find this exact block (around lines 60–66):**

```css
html {
  scroll-behavior: smooth;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Plus Jakarta Sans', Inter, Oxygen, Ubuntu, Cantarell, sans-serif;
}
```

**Replace it with:**

```css
html {
  scroll-behavior: smooth;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  /* Single primary family across the whole app. System fonts are only a fallback
     while the webfont loads. */
  font-family: 'Plus Jakarta Sans', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
}
```

That is the ONLY change in TASK 1. Do not touch anything else in `index.css`.

---

## TASK 2 — Confirm the white logo asset exists

The architect already placed the white Facets emblem here:

```
frontend/src/assets/facets-logo-f-white.png
```

**Action:** Confirm the file exists (it is ~355 KB, 1631×964, white "F" emblem on
transparent background). You do NOT need to create or download it.

If, and only if, the file is missing, STOP and report it — do not substitute
another logo.

This asset is used in TASK 3 on the right-hand dark panel.

---

## TASK 3 — Replace the Login page (`frontend/src/pages/Login.tsx`)

This is the main visible deliverable. **Replace the ENTIRE contents** of
`frontend/src/pages/Login.tsx` with the code block below.

What changed (for your understanding — the code already reflects all of it):
- **Right (dark) panel now uses the NEW WHITE logo** (`facets-logo-f-white.png`)
  for both the big centered glyph and the small "Powered by" mark.
- **Terms link REMOVED** from the footer (see TASK 4). Privacy / Demo / Why Facets stay.
- Spacing snapped to the **4px grid** (no more `px-3.5`, `py-2.5`, `mt-0.5`).
- Clearer **visual hierarchy**: eyebrow → product H1 → "Sign in" → form, left-aligned.
- Primary **Sign In / Register** button is the clear focal point.
- Higher **contrast** on the dark panel's supporting text.
- All existing **logic is preserved byte-for-byte** (OAuth code handling, error
  toasts, `fetch('/api/auth/login')`, the drop-line glyph animation, `useTitle`).
- Accessibility: labels kept, visible focus rings kept, `alt` text kept.

**>>> PASTE THIS AS THE COMPLETE NEW FILE: <<<**

```tsx
import { useState, useEffect, useRef } from 'react'
import { toast } from 'sonner'
import facetsLogo from '../assets/facets-logo-full.svg'
import facetsFIconWhite from '../assets/facets-logo-f-white.png'
import { useTitle } from '../lib/useTitle'

const FEATURES = [
  'Per-line-item AWS → GCP service mapping',
  'On-demand, 1-year, and 3-year CUD pricing',
  'Customer-shareable HTML report in minutes',
]

export function Login() {
  useTitle('Sign in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)

  const glyphRef = useRef<HTMLDivElement>(null)
  const [dropDistance, setDropDistance] = useState<number>(340)

  useEffect(() => {
    const measure = () => {
      if (glyphRef.current) {
        const rect = glyphRef.current.getBoundingClientRect()
        if (rect.top > 0) {
          setDropDistance(rect.top)
        }
      }
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)

    // Handle case where user configured /login as redirect in Google Console
    const code = params.get('code')
    const state = params.get('state')
    if (code) {
      toast.loading('Completing Google authentication…', { duration: 3500 })
      window.location.replace(`/api/auth/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state || '')}`)
      return
    }

    const err = params.get('error')
    if (err === 'google_oauth_not_configured') {
      toast.error('Google OAuth is not configured on this server. Please sign in with email and password below.', { duration: 3500 })
    } else if (err === 'forbidden') {
      toast.error('Access denied: account domain is not permitted.', { duration: 3500 })
    } else if (err) {
      toast.error(`Authentication error: ${err}`, { duration: 3500 })
    }
    // Clean up error query param from address bar so it does not persist across refreshes
    if (err) {
      window.history.replaceState({}, document.title, window.location.pathname)
    }
  }, [])

  const handleEmailSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const cleanEmail = email.trim()
    if (!cleanEmail || !password) return

    setLoading(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify({ email: cleanEmail, password }),
      })

      if (!res.ok) {
        let msg = 'Sign-in failed'
        try {
          const data = await res.json()
          msg = data.error || msg
        } catch {
          const text = await res.text()
          if (text) msg = text
        }
        throw new Error(msg)
      }

      toast.success('Signed in successfully!', { duration: 3500 })
      // Hard redirect to reload session state in App
      window.location.replace('/')
    } catch (err: any) {
      toast.error(err.message || 'Failed to sign in. Please try again.', { duration: 3500 })
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen h-screen flex overflow-hidden">
      {/* ── LEFT PANEL — clean, accessible sign-in ───────────────────── */}
      <div className="w-full lg:w-[480px] shrink-0 bg-white text-gray-900 flex flex-col px-6 sm:px-12 pt-6 pb-4 relative z-10 h-screen overflow-hidden">
        <a
          href="https://www.facets.cloud"
          target="_blank"
          rel="noopener noreferrer"
          className="w-fit inline-block hover:opacity-80 transition-opacity anim-fade-in-up self-start"
          title="Visit facets.cloud"
        >
          <img src={facetsLogo} alt="Facets" className="h-6 w-auto" />
        </a>

        <div className="flex-1 flex flex-col justify-center max-w-sm w-full mx-auto">
          <div className="w-full flex flex-col">
            {/* Heading block — clear hierarchy, left-aligned */}
            <div className="anim-fade-in-up delay-100">
              <p className="text-[#645DF6] text-xs font-semibold tracking-wider uppercase">
                AWS → GCP Estimator
              </p>
              <h1 className="text-[28px] leading-tight font-bold tracking-tight text-gray-900 mt-2">
                GCP TCO Analyzer
              </h1>
              <h2 className="text-base font-medium text-gray-500 mt-4">
                Sign in to your account
              </h2>
            </div>

            {/* Email + Password login form */}
            <form onSubmit={handleEmailSubmit} className="flex flex-col gap-4 anim-fade-in-up delay-175 mt-8">
              <div className="flex flex-col gap-2">
                <label htmlFor="email" className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  required
                  placeholder="you@company.com or you@gmail.com"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  className="w-full px-4 py-3 text-sm bg-gray-50 border border-gray-300 rounded-lg text-gray-900 placeholder:text-gray-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-[#645DF6] focus:border-transparent transition-all duration-150"
                />
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="password" className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  required
                  placeholder="Enter your password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  className="w-full px-4 py-3 text-sm bg-gray-50 border border-gray-300 rounded-lg text-gray-900 placeholder:text-gray-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-[#645DF6] focus:border-transparent transition-all duration-150"
                />
                <p className="text-xs text-gray-400">
                  First time? Entering email &amp; password creates your account automatically.
                </p>
              </div>

              <button
                type="submit"
                disabled={loading || !email.trim() || !password}
                className="cursor-pointer w-full flex items-center justify-center gap-2 bg-[#645DF6] hover:bg-[#5046e5] text-white px-5 py-3 rounded-lg font-semibold text-sm shadow-sm transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-md hover:scale-[1.01] active:scale-[0.99] mt-1"
              >
                {loading ? (
                  <span className="inline-block h-4 w-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                ) : (
                  <span>Sign In / Register</span>
                )}
              </button>
            </form>

            {/* Divider */}
            <div className="flex items-center gap-4 anim-fade-in-up delay-200 my-6">
              <div className="flex-1 h-px bg-gray-200" />
              <span className="text-[11px] text-gray-400 uppercase tracking-widest font-medium">or continue with google</span>
              <div className="flex-1 h-px bg-gray-200" />
            </div>

            {/* Google OAuth Button */}
            <div className="flex flex-col gap-2 anim-fade-in-up delay-250">
              <button
                type="button"
                onClick={() => {
                  window.location.assign('/api/auth/login')
                }}
                className="cursor-pointer w-full flex items-center justify-center gap-3 bg-white text-gray-800 px-5 py-3 rounded-lg font-medium text-sm
                  border border-gray-300 hover:bg-gray-50 hover:border-gray-400 hover:scale-[1.01] active:scale-[0.99]
                  transition-all duration-150 shadow-sm"
              >
                <GoogleIcon />
                Sign in with Google
              </button>
            </div>
          </div>
        </div>

        {/* Footer — Terms link removed */}
        <footer className="mt-auto pt-4 border-t border-gray-100 flex items-center justify-between text-[11px] text-gray-400 anim-fade-in delay-500">
          <span>© {new Date().getFullYear()} Facets.cloud</span>
          <div className="flex items-center gap-3 text-gray-400">
            <a
              href="https://www.facets.cloud/privacy-policy"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-gray-600 transition-colors"
            >
              Privacy
            </a>
            <span aria-hidden="true">•</span>
            <a
              href="https://www.facets.cloud/demo"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-gray-600 transition-colors"
            >
              Demo
            </a>
            <span aria-hidden="true">•</span>
            <a
              href="https://www.facets.cloud/why-facets"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-gray-600 transition-colors"
            >
              Why Facets
            </a>
          </div>
        </footer>
      </div>

      {/* ── RIGHT PANEL — dark, animated presentation (WHITE LOGO) ────── */}
      <div className="login-bg hidden lg:flex flex-1 flex-col items-center justify-center relative overflow-hidden px-16 text-white h-screen">
        <div className="login-orb absolute top-1/4 left-1/4 w-96 h-96 rounded-full pointer-events-none"
             style={{ background: 'radial-gradient(circle, rgba(100,93,246,0.16) 0%, transparent 70%)' }} />
        <div className="login-orb absolute bottom-1/4 right-1/4 w-80 h-80 rounded-full pointer-events-none"
             style={{ background: 'radial-gradient(circle, rgba(0,194,187,0.12) 0%, transparent 70%)', animationDelay: '5s' }} />

        <div
          ref={glyphRef}
          className="relative flex flex-col items-center mb-8"
          style={{ '--drop-dist': `${dropDistance}px` } as React.CSSProperties}
        >
          <div className="login-drop-line" />
          <div className="login-drop-dot" />
          <div className="login-glyph-wrap relative rounded-2xl flex items-center justify-center">
            <img
              src={facetsFIconWhite}
              alt="Facets"
              className="h-24 w-auto relative z-10 transition-transform duration-300 drop-shadow-[0_8px_20px_rgba(0,0,0,0.45)]"
            />
          </div>
        </div>

        <div className="max-w-md relative z-10 text-center anim-fade-in-up delay-175">
          <p className="login-eyebrow text-xs uppercase tracking-[0.3em] text-[#00C2BB] mb-4">Intelligent Cloud Migration</p>
          <h2 className="login-headline text-3xl font-semibold leading-tight tracking-tight">
            Estimate cloud costs <span className="login-gradient-text">with SKU-level precision</span>
          </h2>
          <p className="text-slate-300 text-sm mt-4 leading-relaxed">
            Deterministically maps every line item on your AWS bill to its exact
            Google Cloud equivalent, pricing out on-demand, 1-year, and 3-year
            committed-use discounts with auditable accuracy.
          </p>

          <ul className="mt-8 flex flex-col gap-3 items-start mx-auto w-fit text-left">
            {FEATURES.map((f, i) => (
              <li key={f} className="login-feature flex items-center gap-3 text-sm text-slate-200"
                  style={{ animationDelay: `${350 + i * 120}ms` }}>
                <span className="login-feature-dot shrink-0 w-1.5 h-1.5 rounded-full bg-[#645DF6]"
                      style={{ animationDelay: `${1000 + i * 220}ms` }} />
                {f}
              </li>
            ))}
          </ul>
        </div>

        {/* Powered by Facets linking to facets.cloud (white mark) */}
        <a
          href="https://www.facets.cloud"
          target="_blank"
          rel="noopener noreferrer"
          className="cursor-pointer group absolute bottom-6 right-8 flex items-center gap-2 text-xs text-slate-400 hover:text-white transition-colors duration-200 z-10"
          title="Visit Facets.cloud"
        >
          <span>Powered by</span>
          <img
            src={facetsFIconWhite}
            alt="Facets"
            className="h-4 opacity-80 transition-transform duration-200 group-hover:scale-110"
          />
        </a>
      </div>
    </div>
  )
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.07 17.74 9.5 24 9.5z"/>
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.18 1.48-4.97 2.31-8.16 2.31-6.26 0-11.57-3.59-13.46-8.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
    </svg>
  )
}
```

**>>> END OF FILE CONTENT <<<**

Notes for you:
- The old right-panel import `import facetsFIcon from '../assets/facets-logo-f.svg'`
  is GONE on purpose — we replaced it with `facetsFIconWhite`. Do not re-add it.
- Do not rename `facets-logo-full.svg`; the left-panel wordmark still uses it.

---

## TASK 4 — Verify the Terms link is removed

The user explicitly asked to **remove the Terms route/link.**

Important fact (already confirmed by the architect): **there is NO internal Terms
page or route in this app.** The only "Terms" reference was a single external link
in the Login footer that pointed to `https://www.facets.cloud/terms-of-service`.

TASK 3 already deleted that link (the new footer has only Privacy • Demo • Why Facets).

**Your verification action:** search the whole frontend to confirm nothing else
references Terms. Run from `frontend/`:

```bash
grep -rniE "terms-of-service|>\\s*Terms\\s*<|/terms" src/
```

Expected result: **no matches.** (Matches for unrelated words like `searchTerms`
or `terms.every(...)` in `AdminJobs.tsx` / `ReleaseDashboard.tsx` are variable
names — those are NOT the Terms link; leave them alone.)

If you find any real Terms *link* or route anywhere else, STOP and report it.
Do not remove anything that is just a variable named `terms`.

---

## TASK 5 — Create a reusable Skeleton component (foundation for later screens)

The design guidelines require skeleton loaders instead of blank screens/spinners.
Right now there is no shared skeleton component (11 files hand-roll `animate-pulse`).
Create ONE reusable primitive now so future screens use it consistently. This task
is additive — it creates a new file and breaks nothing.

**5a.** Add the shimmer keyframe + class to `frontend/src/index.css`.
Find the line that reads exactly:

```css
/* ── Card hover lift ────────────────────────────────────────── */
```

**Insert this block immediately ABOVE that line:**

```css
/* ── Skeleton loading placeholders ──────────────────────────── */
@keyframes skeleton-shimmer {
  0%   { background-position: -200% 0; }
  100% { background-position:  200% 0; }
}
.skeleton {
  border-radius: 8px;
  background: linear-gradient(
    90deg,
    var(--fcp-bg-raised) 25%,
    var(--fcp-bg-hover) 37%,
    var(--fcp-bg-raised) 63%
  );
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s ease-in-out infinite;
}
@media (prefers-reduced-motion: reduce) {
  .skeleton { animation: none; }
}
```

**5b.** Create the new file `frontend/src/components/Skeleton.tsx` with exactly
this content:

```tsx
type SkeletonProps = {
  /** Tailwind width/height/shape classes, e.g. "h-4 w-32 rounded-full" */
  className?: string
}

/**
 * Reusable loading placeholder. Use instead of blank screens or spinners while
 * content is loading. Match the dimensions of the real content it replaces.
 *
 * Example:
 *   <Skeleton className="h-4 w-40" />
 *   <Skeleton className="h-24 w-full rounded-xl" />
 */
export function Skeleton({ className = '' }: SkeletonProps) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />
}

/** A block of stacked text-line skeletons. */
export function SkeletonText({ lines = 3, className = '' }: { lines?: number; className?: string }) {
  return (
    <div className={`flex flex-col gap-2 ${className}`} aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="skeleton h-3"
          style={{ width: i === lines - 1 ? '60%' : '100%' }}
        />
      ))}
    </div>
  )
}
```

Do not wire this into any page in this work order. It is a foundation primitive
for the next phase. Just make sure it compiles (TASK VERIFICATION covers that).

---

## VERIFICATION (RUN EVERY STEP — ALL MUST PASS)

Run from the `frontend/` directory.

### V1 — It type-checks and builds
```bash
npm run build
```
Expected: build completes with **no TypeScript errors**. If it fails, read the
error, find the file/line, and fix only what the error names. Do not change logic.

### V2 — It runs
```bash
npm run dev
```
Open the printed localhost URL and go to `/login`.

### V3 — Visual checklist (compare against this list)
- [ ] Left panel: eyebrow "AWS → GCP ESTIMATOR" (purple) → big "GCP TCO Analyzer"
      heading → "Sign in to your account" → form. Left-aligned, clean spacing.
- [ ] The **Sign In / Register** button is solid purple `#645DF6` and is clearly
      the strongest element on the panel.
- [ ] Right panel shows the **WHITE Facets emblem** (not the teal/purple one),
      centered, with the glowing drop-line animation lighting it.
- [ ] Right panel body text and feature list are clearly readable (lighter slate
      text, not dim gray).
- [ ] Footer shows **Privacy • Demo • Why Facets** and **NO "Terms"** link.
- [ ] The small "Powered by" mark bottom-right is the white emblem.

### V4 — Interaction / state checklist
- [ ] Focusing the email or password input shows a purple focus ring.
- [ ] The Sign In button is disabled (dimmed) until BOTH email and password are filled.
- [ ] Clicking Sign In with junk credentials still shows an error toast (logic intact).
- [ ] "Sign in with Google" still navigates to `/api/auth/login` (logic intact).

### V5 — Responsive
- [ ] Narrow the browser to phone width (~375px): the right dark panel hides, the
      left sign-in panel fills the screen, no horizontal scrollbar appears.
- [ ] Tablet width (~768px): still only the left panel (the right panel is `lg:` only).
- [ ] Desktop (≥1024px): both panels visible, balanced.

### V6 — Reduced motion
- [ ] In OS/browser settings enable "reduce motion," reload `/login`: animations
      stop (no drop-dot, no shimmer), content is fully visible. (This is handled by
      existing CSS — just confirm nothing is permanently invisible.)

### V7 — Terms is gone everywhere
```bash
grep -rniE "terms-of-service|>\\s*Terms\\s*<" src/
```
Expected: **no matches.**

---

## ACCEPTANCE — what "done" means
1. `npm run build` passes with zero errors.
2. `/login` matches V3–V6 exactly.
3. V7 returns no matches.
4. No file outside the TASK table in section 1 was modified.
5. Sign-in (email/password) and Google sign-in still function.

If all five are true, the work order is complete. Report back with: the build
result (pass/fail), and confirmation of each checkbox above.

---

## WHAT YOU MUST NOT DO (final reminders)
- ❌ Do not add, remove, or change any route in `frontend/src/App.tsx`.
- ❌ Do not change `fetch('/api/auth/login')` or any auth logic.
- ❌ Do not install packages or upgrade dependencies.
- ❌ Do not touch other pages (`Upload.tsx`, admin pages, etc.) — that is a later phase.
- ❌ Do not remove the `prefers-reduced-motion` CSS block.
- ❌ Do not delete `facets-logo-f.svg` (other code may still use it elsewhere).
- ❌ Do not rename the white logo asset.

---

## APPENDIX — NEXT PHASES (NOT part of this work order; for the architect only)
These are intentionally out of scope here. Do not start them.
- Phase 2: Roll the 4px spacing + radius/shadow/type tokens across `Upload`, `Bills`,
  `Tickets`, `Settings`, `Profile`, and the admin pages.
- Phase 3: Replace ad-hoc `animate-pulse` usages in the 11 pages with `<Skeleton>`.
- Phase 4: Extract shared `<Button>`, `<Input>`, `<Card>` primitives and migrate pages.
