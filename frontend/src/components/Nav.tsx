import facetsFIcon from '../assets/facets-logo-f.svg'
import type { UserInfo } from '../api/auth'
import { Link } from 'react-router-dom'
import { useState } from 'react'

export function Nav({ user }: Readonly<{ user: UserInfo }>) {
  return (
    <nav className="flex items-center justify-between px-6 py-3 border-b border-white/10 anim-slide-down backdrop-blur-sm bg-[#0a0a0f]/80 sticky top-0 z-30">
      <div className="flex items-center gap-3">
        <Link to="/" className="flex items-center gap-3 transition-opacity duration-150 hover:opacity-85">
          <img src={facetsFIcon} alt="Facets" className="h-5" />
          <span className="hidden sm:inline text-white/30">|</span>
          <span className="text-sm font-semibold tracking-tight">
            <span className="hidden sm:inline">AWS → GCP Cost Estimator</span>
            <span className="sm:hidden">Estimator</span>
          </span>
        </Link>
      </div>
      <div className="flex items-center gap-4 text-sm text-gray-400">
        {user.is_admin && (
          <div className="hidden sm:flex items-center gap-4">
            <Link to="/admin" className="nav-link text-[#00C2BB]">All Reports</Link>
            <Link to="/admin/users" className="nav-link text-gray-400 hover:text-white">Users</Link>
            <Link to="/admin/catalog" className="nav-link text-gray-400 hover:text-white">Catalog</Link>
            <Link to="/admin/analytics" className="nav-link text-gray-400 hover:text-white">Analytics</Link>
            <Link to="/admin/audit" className="nav-link text-gray-400 hover:text-white">Audit</Link>
          </div>
        )}
        {user.is_admin && (
          <Link to="/admin" className="sm:hidden nav-link text-[#00C2BB]">Admin</Link>
        )}
        <UserMenu user={user} />
      </div>
    </nav>
  )
}

function UserMenu({ user }: Readonly<{ user: UserInfo }>) {
  const [open, setOpen] = useState(false)
  const initial = (user.name || user.email || '?').trim().charAt(0).toUpperCase()
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 rounded-full p-0.5 pr-2 hover:bg-white/5 transition-colors duration-150"
        aria-haspopup="true"
        aria-expanded={open}
      >
        {user.picture ? (
          <img
            src={user.picture}
            alt=""
            referrerPolicy="no-referrer"
            className="h-8 w-8 rounded-full object-cover"
          />
        ) : (
          <span className="h-8 w-8 rounded-full bg-[#645DF6] text-white text-sm font-semibold flex items-center justify-center">
            {initial}
          </span>
        )}
        <span className="hidden sm:inline text-gray-300 text-sm max-w-[160px] truncate">
          {user.name || user.email}
        </span>
        <svg className="hidden sm:block h-3 w-3 text-gray-500" viewBox="0 0 12 12" fill="none">
          <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <>
          {/* click-outside backdrop */}
          <button
            aria-label="Close menu"
            className="fixed inset-0 z-40 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div className="absolute right-0 mt-2 w-60 z-50 rounded-lg border border-white/10 bg-[#13131a] shadow-xl overflow-hidden">
            <div className="px-4 py-3 border-b border-white/10">
              <p className="text-sm font-medium text-white truncate">{user.name || 'Signed in'}</p>
              <p className="text-xs text-gray-400 truncate">{user.email}</p>
            </div>
            <button
              onClick={async () => {
                setOpen(false)
                await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' })
                window.location.replace('/login')
              }}
              className="w-full text-left px-4 py-2.5 text-sm text-gray-300 hover:bg-white/5 hover:text-white transition-colors duration-150"
            >
              Sign Out
            </button>
          </div>
        </>
      )}
    </div>
  )
}
