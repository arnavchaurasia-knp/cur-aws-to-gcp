import facetsFIcon from '../assets/facets-logo-f.svg'
import type { UserInfo } from '../api/auth'
import { Link, useLocation } from 'react-router-dom'
import { useState, useRef, useEffect } from 'react'

export function Nav({ user }: Readonly<{ user: UserInfo }>) {
  const location = useLocation()
  const email = (user?.email || '').toLowerCase()
  const isFacetsUser = Boolean(
    email && (
      email.endsWith('@facets.cloud') ||
      email.endsWith('@facets.io') ||
      email.includes('@facets.')
    )
  )

  const navItemClass = (path: string) => {
    const isActive = location.pathname === path
    return isActive
      ? 'nav-link text-[#00C2BB] font-medium'
      : 'nav-link text-gray-400 hover:text-white transition-colors duration-150'
  }

  return (
    <nav className="flex items-center justify-between px-6 py-3 border-b border-white/10 anim-slide-down backdrop-blur-md bg-[#0a0a0f]/85 sticky top-0 z-30">
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
        {isFacetsUser && (
          <div className="hidden sm:flex items-center gap-4">
            <Link to="/admin" className={navItemClass('/admin')}>All Reports</Link>
            <Link to="/facets" className={navItemClass('/facets')}>Logs &amp; Engine</Link>
            <Link to="/admin/users" className={navItemClass('/admin/users')}>Users</Link>
            <Link to="/admin/catalog" className={navItemClass('/admin/catalog')}>Catalog</Link>
            <Link to="/admin/analytics" className={navItemClass('/admin/analytics')}>Analytics</Link>
            <Link to="/admin/audit" className={navItemClass('/admin/audit')}>Audit</Link>
          </div>
        )}
        {isFacetsUser && (
          <div className="sm:hidden flex items-center gap-2 text-xs">
            <Link to="/admin" className="nav-link text-[#00C2BB]">Admin</Link>
            <Link to="/facets" className="nav-link text-gray-400">Logs</Link>
          </div>
        )}
        <UserMenu user={user} isFacetsUser={isFacetsUser} />
      </div>
    </nav>
  )
}

function UserMenu({ user, isFacetsUser }: Readonly<{ user: UserInfo; isFacetsUser?: boolean }>) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const initial = (user.name || user.email || '?').trim().charAt(0).toUpperCase()

  useEffect(() => {
    if (!open) return
    const handleClickOutside = (e: MouseEvent | TouchEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('touchstart', handleClickOutside)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('touchstart', handleClickOutside)
    }
  }, [open])

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 rounded-full p-0.5 pr-2 hover:bg-white/5 transition-colors duration-150 cursor-pointer"
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
        <svg className={`hidden sm:block h-3 w-3 text-gray-500 transition-transform duration-150 ${open ? 'rotate-180' : ''}`} viewBox="0 0 12 12" fill="none">
          <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-64 z-50 rounded-lg border border-white/10 bg-[#13131a] shadow-2xl overflow-hidden anim-scale-in">
          <div className="px-4 py-3 border-b border-white/10">
            <p className="text-sm font-medium text-white truncate">{user.name || 'Signed in'}</p>
            <p className="text-xs text-gray-400 truncate">{user.email}</p>
            {isFacetsUser && (
              <span className="inline-block mt-1.5 px-2 py-0.5 text-[10px] uppercase font-semibold tracking-wider bg-[#00C2BB]/15 text-[#00C2BB] border border-[#00C2BB]/30 rounded">
                Facets Team Admin
              </span>
            )}
          </div>

          {isFacetsUser && (
            <div className="py-1 border-b border-white/10 text-xs">
              <Link
                to="/admin"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2 px-4 py-2 text-gray-300 hover:bg-white/5 hover:text-white transition-colors"
              >
                <span>📊</span>
                <span>All Reports (Admin)</span>
              </Link>
              <Link
                to="/facets"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2 px-4 py-2 text-gray-300 hover:bg-white/5 hover:text-white transition-colors"
              >
                <span>⚡</span>
                <span>Execution Logs &amp; Engine</span>
              </Link>
            </div>
          )}

          <button
            onClick={async () => {
              setOpen(false)
              await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' })
              window.location.replace('/login')
            }}
            className="w-full text-left px-4 py-2.5 text-sm text-gray-300 hover:bg-white/5 hover:text-white transition-colors duration-150 cursor-pointer"
          >
            Sign Out
          </button>
        </div>
      )}
    </div>
  )
}
