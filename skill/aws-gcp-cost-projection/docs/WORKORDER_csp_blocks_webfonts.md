# WORK ORDER — The new CSP blocks the app's webfonts in production only

**For an implementing AI with no prior context. Read fully before editing.**
Paths are from the repository root.

**Scope: the `Content-Security-Policy` header in
`cmd/server/main.go:securityHeaders` (~line 431).** Two directives. No other
header changes, no frontend changes. **No pipeline or pricing code is
involved.**

---

## 1. The problem in plain English

`securityHeaders` sets:

```go
w.Header().Set("Content-Security-Policy",
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';")
```

The same middleware covers **both** the generated HTML report **and** the React
SPA — the comment above it says so explicitly. The policy was reasoned out for
the report, and that reasoning is correct:

> the generated HTML report uses inline styles … so style-src allows
> 'unsafe-inline'. The report contains no inline `<script>` tags … so
> script-src can stay 'self'.

**But the SPA is not the report.** `frontend/index.html:11` loads a stylesheet
from an external origin:

```html
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
```

Under the policy above:

| resource | directive that applies | result |
|---|---|---|
| `fonts.googleapis.com` stylesheet | `style-src 'self' 'unsafe-inline'` | **blocked** — no external origin listed |
| `fonts.gstatic.com` font files | no `font-src` → falls back to `default-src 'self'` | **blocked** |

So **Plus Jakarta Sans and Inter never load.** The UI silently falls back to
system fonts on a customer-facing product with deliberate typography.

### Why nobody will notice before a customer does

The Go server serves `index.html` and the built frontend
(`cmd/server/main.go:197, 309, 317`), so the CSP applies to the SPA **in
production only**. Local development is served by Vite, which never passes
through this middleware. It works on every developer's machine and breaks on
Render.

---

## 2. The fix

Add the two origins the page actually needs:

```go
w.Header().Set("Content-Security-Policy",
    "default-src 'self'; "+
    "img-src 'self' data:; "+
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "+
    "font-src 'self' https://fonts.gstatic.com;")
```

`style-src` covers the stylesheet request; `font-src` covers the font files it
then pulls from `fonts.gstatic.com`. **Both are required** — adding only the
first leaves the fonts blocked and looks like the fix did not work.

Update the comment above the function to say the policy covers the SPA as well
as the report, and that the font origins are there for the SPA. The next person
to tighten this needs to know why they are listed.

---

## 3. Do NOT

- Do **not** add `https://raw.githubusercontent.com` to `img-src`. It appears
  in `index.html:27` as an `og:image` meta tag, which is fetched by social-media
  crawlers, **not by the page**. CSP does not apply to it, and adding it widens
  the policy for no reason.
- Do **not** add `'unsafe-inline'` to `script-src` or `default-src`. The
  report has no inline scripts and the SPA ships JS as static assets — the
  existing reasoning is right and should stay.
- Do **not** relax `default-src`. Add the specific directives instead.
- Do **not** remove `X-Frame-Options: DENY`. I checked: nothing in
  `frontend/src/` uses an `iframe`, so nothing is embedded and DENY is safe.
- Do **not** "fix" this by self-hosting the fonts as part of this work order.
  That is a reasonable thing to want and a different change, with its own build
  and licensing considerations.

---

## 4. Verification

A unit test on the header string is necessary but **not sufficient** — it
proves the string, not that the page renders. Do both.

1. `cmd/server` test: the CSP contains `fonts.googleapis.com` in `style-src`
   and `fonts.gstatic.com` in `font-src`.
2. `go build ./...` and `go vet ./...` clean.
3. **Load the built frontend through the Go server** — not Vite — and confirm
   the browser console reports **no CSP violations**, and that the fonts
   actually apply (the heading font should be Plus Jakarta Sans, not the system
   default).

Step 3 is the one that matters. This defect exists precisely because the dev
path bypasses the middleware.

---

## 5. Context

`securityHeaders` is new, and the rest of it is sound: `X-Content-Type-Options`,
`X-Frame-Options`, `Referrer-Policy`, and HSTS correctly gated on `r.TLS != nil`
so it is not sent over plain HTTP. The build is currently green — this is a
runtime-behaviour defect, not a compile error.

---

## 6. Done

- [ ] `style-src` includes `https://fonts.googleapis.com`.
- [ ] `font-src` added with `'self' https://fonts.gstatic.com`.
- [ ] Comment explains the policy covers the SPA and why the font origins are
      listed.
- [ ] Test asserts both directives; `go build` and `go vet` clean.
- [ ] Frontend loaded **through the Go server** shows no CSP violations and
      renders in Plus Jakarta Sans.
