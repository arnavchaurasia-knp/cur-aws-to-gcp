import { useState, useEffect, useRef } from 'react'
import { toast } from 'sonner'
import facetsLogo from '../assets/facets-logo-full.svg'
import facetsFIconWhite from '../assets/facets-logo-f-white.svg'
import facetsTexture from '../assets/facets-texture.svg'
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
      {/* ── LEFT PANEL — sign-in ─────────────────────────────────────── */}
      <div className="w-full lg:w-[480px] shrink-0 bg-white text-gray-900 flex flex-col px-6 sm:px-12 pt-8 pb-6 relative z-10 h-screen overflow-hidden">
        {/* Logo shares the form's content column so it left-aligns with the fields */}
        <div className="w-full max-w-sm mx-auto">
          <a
            href="https://www.facets.cloud"
            target="_blank"
            rel="noopener noreferrer"
            className="w-fit inline-block hover:opacity-80 transition-opacity anim-fade-in-up"
            title="Visit facets.cloud"
          >
            <img src={facetsLogo} alt="Facets" className="h-6 w-auto" />
          </a>
        </div>

        <div className="flex-1 flex flex-col justify-center max-w-sm w-full mx-auto">
          {/* Heading block — hierarchy by weight + color, tight grouping */}
          <div className="anim-fade-in-up delay-100">
            <p className="text-[#645DF6] text-xs font-semibold tracking-wider uppercase">
              AWS → GCP Estimator
            </p>
            <h1 className="text-[28px] leading-tight font-bold tracking-tight text-gray-900 mt-2">
              GCP TCO Analyzer
            </h1>
            <p className="text-sm font-medium text-gray-500 mt-2">
              Sign in to your account
            </p>
          </div>

          {/* Email + Password login form */}
          <form onSubmit={handleEmailSubmit} className="flex flex-col gap-4 anim-fade-in-up delay-175 mt-6">
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
          <button
            type="button"
            onClick={() => {
              window.location.assign('/api/auth/login')
            }}
            className="cursor-pointer w-full flex items-center justify-center gap-3 bg-white text-gray-800 px-5 py-3 rounded-lg font-medium text-sm
              border border-gray-300 hover:bg-gray-50 hover:border-gray-400 hover:scale-[1.01] active:scale-[0.99]
              transition-all duration-150 shadow-sm anim-fade-in-up delay-250"
          >
            <GoogleIcon />
            Sign in with Google
          </button>
        </div>

        {/* Footer */}
        <footer className="mt-auto pt-4 border-t border-gray-100 flex items-center justify-between text-[11px] text-gray-400 anim-fade-in delay-500 w-full max-w-sm mx-auto">
          <span>© {new Date().getFullYear()} Facets.cloud</span>
          <div className="flex items-center gap-4 text-gray-400">
            <a href="https://www.facets.cloud/privacy-policy" target="_blank" rel="noopener noreferrer" className="hover:text-gray-600 transition-colors">Privacy</a>
            <a href="https://www.facets.cloud/demo" target="_blank" rel="noopener noreferrer" className="hover:text-gray-600 transition-colors">Demo</a>
            <a href="https://www.facets.cloud/why-facets" target="_blank" rel="noopener noreferrer" className="hover:text-gray-600 transition-colors">Why Facets</a>
          </div>
        </footer>
      </div>

      {/* ── RIGHT PANEL — brand texture + one orchestrated moment ──────── */}
      <div
        data-login-promo
        className="hidden lg:flex flex-1 flex-col items-center justify-center relative overflow-hidden px-16 text-[#f8fafc] h-screen"
        style={{
          backgroundColor: '#0a0a0f',
          backgroundImage: `url(${facetsTexture})`,
          backgroundSize: 'cover',
          backgroundPosition: 'center',
        }}
      >
        {/* Vignette so copy stays legible over the texture */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{ background: 'radial-gradient(120% 90% at 50% 0%, transparent 40%, rgba(10,10,15,0.65) 100%)' }}
        />
        {/* Single soft brand glow for life (one subtle moving element) */}
        <div
          className="login-orb absolute top-[18%] left-1/2 -translate-x-1/2 w-[28rem] h-[28rem] rounded-full pointer-events-none"
          style={{ background: 'radial-gradient(circle, rgba(100,93,246,0.20) 0%, transparent 70%)' }}
        />

        {/* Logo with the drop-to-ignite moment (the one memorable motion) */}
        <div
          ref={glyphRef}
          className="relative flex flex-col items-center mb-10 z-10"
          style={{ '--drop-dist': `${dropDistance}px` } as React.CSSProperties}
        >
          <div className="login-drop-line" />
          <div className="login-drop-dot" />
          <div className="login-glyph-wrap relative flex items-center justify-center">
            <img
              src={facetsFIconWhite}
              alt="Facets"
              className="h-20 w-auto relative z-10 drop-shadow-[0_8px_24px_rgba(0,0,0,0.5)]"
            />
          </div>
        </div>

        <div className="max-w-md relative z-10 text-center anim-fade-in-up delay-175">
          <p className="text-[11px] uppercase tracking-[0.22em] text-[#00C2BB] font-semibold">
            Intelligent cloud migration
          </p>
          <h2 className="text-[32px] leading-[1.15] font-bold tracking-tight mt-4">
            Estimate cloud costs
            <br />
            <span className="login-gradient-text">with SKU-level precision</span>
          </h2>
          <p className="text-slate-300 text-sm mt-5 leading-relaxed">
            Every line item on your AWS bill, mapped to its exact Google Cloud
            equivalent — priced across on-demand, 1-year, and 3-year committed-use
            discounts, with auditable accuracy.
          </p>

          <ul className="mt-8 flex flex-col gap-3 items-start mx-auto w-fit text-left">
            {FEATURES.map(f => (
              <li key={f} className="flex items-center gap-3 text-sm text-slate-200">
                <CheckIcon />
                {f}
              </li>
            ))}
          </ul>
        </div>

        {/* Powered by Facets */}
        <a
          href="https://www.facets.cloud"
          target="_blank"
          rel="noopener noreferrer"
          className="cursor-pointer group absolute bottom-6 right-8 flex items-center gap-2 text-xs text-slate-400 hover:text-white transition-colors duration-200 z-10"
          title="Visit Facets.cloud"
        >
          <span>Powered by</span>
          <img src={facetsFIconWhite} alt="Facets" className="h-4 w-auto opacity-80 transition-transform duration-200 group-hover:scale-110" />
        </a>
      </div>
    </div>
  )
}

function CheckIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="#00C2BB"
      strokeWidth="2.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0"
      aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
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
