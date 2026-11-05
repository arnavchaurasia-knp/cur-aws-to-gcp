import facetsFIcon from '../assets/facets-logo-f.svg'
import type { UserInfo } from '../api/auth'
import { Link, useLocation } from 'react-router-dom'
import { useState, useRef, useEffect } from 'react'

export function Nav({ user }: Readonly<{ user: UserInfo }>) {
  const location = useLocation()
  const isAdmin = Boolean(user?.is_admin)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const settingsRef = useRef<HTMLDivElement>(null)
  const mobileRef = useRef<HTMLDivElement>(null)

  const navItemClass = (path: string, exact = true) => {
    const isActive = exact ? location.pathname === path : location.pathname.startsWith(path)
    return isActive
      ? 'nav-link text-[#00C2BB] font-medium'
      : 'nav-link text-gray-400 hover:text-white transition-colors duration-150'
  }

  const isSettingsActive = [
    '/admin/analytics',
    '/admin/audit',
    '/admin/assumptions',
    '/admin/report-settings',
    '/settings/notifications',
  ].some(p => location.pathname === p) || (location.pathname === '/admin' && location.search.includes('tab=shares'))

  // Close dropdown on outside click
  useEffect(() => {
    if (!settingsOpen) return
    const handleClickOutside = (e: MouseEvent | TouchEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setSettingsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('touchstart', handleClickOutside)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('touchstart', handleClickOutside)
    }
  }, [settingsOpen])

  // Close mobile drawer on route change or click outside
  useEffect(() => {
    setMobileMenuOpen(false)
    setSettingsOpen(false)
  }, [location.pathname, location.search])

  useEffect(() => {
    if (!mobileMenuOpen) return
    const handleClickOutside = (e: MouseEvent | TouchEvent) => {
      if (mobileRef.current && !mobileRef.current.contains(e.target as Node)) {
        setMobileMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('touchstart', handleClickOutside)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('touchstart', handleClickOutside)
    }
  }, [mobileMenuOpen])

  return (
    <nav className="flex items-center justify-between px-6 py-3 border-b border-[var(--fcp-border-subtle)] anim-slide-down backdrop-blur-md bg-[var(--fcp-bg-base)]/85 sticky top-0 z-30">
      <div className="flex items-center gap-3">
        <Link to="/" className="flex items-center gap-3 transition-opacity duration-150 hover:opacity-85">
          <img src={facetsFIcon} alt="Facets" className="h-5" />
          <span className="hidden sm:inline text-white/30">|</span>
          <span className="text-sm font-semibold tracking-tight text-[var(--fcp-text-primary)]">
            <span className="hidden sm:inline">AWS → GCP Cost Estimator</span>
            <span className="sm:hidden">Estimator</span>
          </span>
        </Link>
      </div>

      <div className="flex items-center gap-4 text-sm text-[var(--fcp-text-secondary)]">
        {/* Desktop Nav */}
        <div className="hidden sm:flex items-center gap-4">
          {isAdmin && (
            <Link to="/admin" className={navItemClass('/admin')}>All Reports</Link>
          )}
          {isAdmin && (
            <>
              <Link to="/admin/users" className={navItemClass('/admin/users')}>Users</Link>
              <Link to="/admin/health" className={navItemClass('/admin/health')}>System Health</Link>

              {/* Settings Dropdown */}
              <div className="relative" ref={settingsRef}>
                <button
                  onClick={() => setSettingsOpen(v => !v)}
                  className={`flex items-center gap-1.5 py-1.5 px-2.5 rounded-lg hover:bg-white/5 transition-colors cursor-pointer ${
                    isSettingsActive ? 'text-[#00C2BB] font-medium' : 'text-gray-400 hover:text-white'
                  }`}
                  aria-haspopup="true"
                  aria-expanded={settingsOpen}
                >
                  <span>Settings</span>
                  <svg
                    className={`h-3 w-3 text-gray-400 transition-transform duration-150 ${settingsOpen ? 'rotate-180' : ''}`}
                    viewBox="0 0 12 12"
                    fill="none"
                  >
                    <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>

                {settingsOpen && (
                  <div className="absolute right-0 mt-2 w-56 z-50 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-2xl p-1.5 overflow-hidden anim-scale-in text-xs">
                    <Link
                      to="/admin/analytics"
                      onClick={() => setSettingsOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 rounded-xl text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors"
                    >
                      <svg className="w-4 h-4 text-[#00C2BB]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                      </svg>
                      <span className="font-medium">Analytics &amp; Math</span>
                    </Link>

                    <Link
                      to="/admin/audit"
                      onClick={() => setSettingsOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 rounded-xl text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors"
                    >
                      <svg className="w-4 h-4 text-[#645DF6]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                      </svg>
                      <span className="font-medium">Audit Trail</span>
                    </Link>

                    <Link
                      to="/admin/assumptions"
                      onClick={() => setSettingsOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 rounded-xl text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors"
                    >
                      <svg className="w-4 h-4 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                      </svg>
                      <span className="font-medium">Pricing Assumptions</span>
                    </Link>

                    <Link
                      to="/admin/report-settings"
                      onClick={() => setSettingsOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 rounded-xl text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors"
                    >
                      <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      <span className="font-medium">Report Configuration</span>
                    </Link>

                    <Link
                      to="/settings/notifications"
                      onClick={() => setSettingsOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 rounded-xl text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors"
                    >
                      <svg className="w-4 h-4 text-sky-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                      </svg>
                      <span className="font-medium">Delivery Channels</span>
                    </Link>

                    <Link
                      to="/tickets"
                      onClick={() => setSettingsOpen(false)}
                      className="flex items-center gap-2.5 px-3 py-2 rounded-xl text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors"
                    >
                      <svg className="w-4 h-4 text-violet-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z" />
                      </svg>
                      <span className="font-medium">Support Tickets</span>
                    </Link>
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Mobile Hamburger / Menu Button */}
        <div className="sm:hidden flex items-center gap-2 text-xs" ref={mobileRef}>
          <button
            onClick={() => setMobileMenuOpen(v => !v)}
            className="p-2 rounded-xl border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] cursor-pointer"
            aria-label="Toggle Navigation Menu"
            aria-expanded={mobileMenuOpen}
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              {mobileMenuOpen ? (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              ) : (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              )}
            </svg>
          </button>

          {mobileMenuOpen && (
            <div className="absolute top-full left-0 right-0 bg-[var(--fcp-bg-surface)] border-b border-[var(--fcp-border-subtle)] p-4 shadow-2xl flex flex-col gap-2 anim-slide-down">
              <Link to="/" onClick={() => setMobileMenuOpen(false)} className="text-[var(--fcp-text-primary)] hover:text-[#00C2BB] py-1.5 font-medium">
                Upload New CUR
              </Link>
              {isAdmin && (
                <>
                  <Link to="/admin" onClick={() => setMobileMenuOpen(false)} className="text-[var(--fcp-text-primary)] hover:text-[#00C2BB] py-1.5 font-medium">
                    All Reports
                  </Link>
                  <Link to="/admin/users" onClick={() => setMobileMenuOpen(false)} className="text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] py-1.5">
                    Users
                  </Link>
                  <Link to="/admin/health" onClick={() => setMobileMenuOpen(false)} className="text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] py-1.5">
                    System Health
                  </Link>
                  <div className="pt-2 border-t border-[var(--fcp-border-subtle)] space-y-1">
                    <p className="text-[10px] uppercase font-bold tracking-wider text-[var(--fcp-text-tertiary)] mb-1">Settings &amp; Tools</p>
                    <Link to="/profile" onClick={() => setMobileMenuOpen(false)} className="block py-1 text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)]">
                      User Profile
                    </Link>
                    <Link to="/profile/tokens" onClick={() => setMobileMenuOpen(false)} className="block py-1 text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)]">
                      API Tokens
                    </Link>
                    <Link to="/settings/notifications" onClick={() => setMobileMenuOpen(false)} className="block py-1 text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)]">
                      Delivery &amp; Notifications
                    </Link>
                    <Link to="/admin/report-settings" onClick={() => setMobileMenuOpen(false)} className="block py-1 text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)]">
                      Report Settings
                    </Link>
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        <UserMenu user={user} />
      </div>
    </nav>
  )
}

/* ─────────────────────────────────────────────────────────────────────────────
   UserMenu: Professional, Industry-Grade Dropdown (Zero Cheap Emojis)
   ───────────────────────────────────────────────────────────────────────────── */

export function UserMenu({ user }: Readonly<{ user: UserInfo }>) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const initial = (user.name || user.email || '?').trim().charAt(0).toUpperCase()
  const isAdmin = Boolean(user?.is_admin)

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
        className="flex items-center gap-2.5 rounded-full p-1 pr-3 transition-all duration-150 cursor-pointer border border-transparent hover:border-[var(--fcp-border-subtle)] hover:bg-[var(--fcp-bg-surface-hover)] focus:outline-none focus:ring-2 focus:ring-[#00C2BB]/20"
        aria-haspopup="true"
        aria-expanded={open}
      >
        <div className="relative shrink-0">
          {user.picture ? (
            <img
              src={user.picture}
              alt=""
              referrerPolicy="no-referrer"
              className="h-8 w-8 rounded-full object-cover border border-white/20"
            />
          ) : (
            <div className="h-8 w-8 rounded-full bg-gradient-to-tr from-[#645DF6] to-[#00C2BB] text-white text-xs font-bold flex items-center justify-center shadow-sm">
              {initial}
            </div>
          )}
          <span className="absolute bottom-0 right-0 h-2 w-2 rounded-full bg-emerald-500 ring-2 ring-[var(--fcp-bg-base)]" />
        </div>

        <span className="hidden sm:inline text-sm max-w-[150px] truncate font-medium text-[var(--fcp-text-primary)]">
          {user.name || user.email}
        </span>

        <svg
          className={`hidden sm:block h-3.5 w-3.5 text-[var(--fcp-text-tertiary)] transition-transform duration-200 ${
            open ? 'rotate-180' : ''
          }`}
          viewBox="0 0 12 12"
          fill="none"
        >
          <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-72 z-50 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)] shadow-2xl p-1.5 overflow-hidden anim-scale-in">
          {/* User Identity Header Card */}
          <div className="p-3 mb-1 rounded-xl bg-[var(--fcp-bg-surface-hover)]/70 border border-[var(--fcp-border-subtle)]/50">
            <div className="flex items-center gap-3">
              {user.picture ? (
                <img
                  src={user.picture}
                  alt=""
                  referrerPolicy="no-referrer"
                  className="h-10 w-10 rounded-full object-cover border border-white/20 shrink-0"
                />
              ) : (
                <div className="h-10 w-10 rounded-full bg-gradient-to-tr from-[#645DF6] to-[#00C2BB] text-white text-sm font-bold flex items-center justify-center shrink-0 shadow-sm">
                  {initial}
                </div>
              )}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-bold text-[var(--fcp-text-primary)] truncate">
                  {user.name || 'User Profile'}
                </p>
                <p className="text-[11px] font-mono text-[var(--fcp-text-secondary)] truncate">
                  {user.email}
                </p>
                <div className="flex items-center gap-1.5 mt-1.5">
                  {isAdmin ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-[#645DF6]/15 text-[#645DF6] dark:text-[#a5a0ff] border border-[#645DF6]/30">
                      <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                      </svg>
                      Admin
                    </span>
                  ) : (
                    <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold text-[var(--fcp-text-tertiary)] bg-gray-500/10 border border-gray-500/20">
                      Member
                    </span>
                  )}
                  {user.is_facets_employee && (
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#00C2BB]/15 text-[#00C2BB] border border-[#00C2BB]/30">
                      Facets Team
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Section: Account & Core Configuration */}
          <div className="space-y-0.5 py-1">
            <Link
              to="/profile"
              onClick={() => setOpen(false)}
              className="flex items-center justify-between px-3 py-2 rounded-xl text-xs font-medium text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors group"
            >
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-lg bg-[var(--fcp-bg-surface-hover)] border border-[var(--fcp-border-subtle)] text-[#00C2BB] flex items-center justify-center group-hover:scale-105 transition-transform">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                </div>
                <div>
                  <span className="font-semibold block text-[var(--fcp-text-primary)]">User Profile</span>
                  <span className="text-[10px] text-[var(--fcp-text-tertiary)] block">Name, organization &amp; preferences</span>
                </div>
              </div>
              <svg className="w-3.5 h-3.5 text-[var(--fcp-text-tertiary)] group-hover:translate-x-0.5 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </Link>

            <Link
              to="/settings/notifications"
              onClick={() => setOpen(false)}
              className="flex items-center justify-between px-3 py-2 rounded-xl text-xs font-medium text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors group"
            >
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-lg bg-[var(--fcp-bg-surface-hover)] border border-[var(--fcp-border-subtle)] text-[#645DF6] flex items-center justify-center group-hover:scale-105 transition-transform">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                  </svg>
                </div>
                <div>
                  <span className="font-semibold block text-[var(--fcp-text-primary)]">Notifications &amp; Delivery</span>
                  <span className="text-[10px] text-[var(--fcp-text-tertiary)] block">Email &amp; Slack alert channels</span>
                </div>
              </div>
              <svg className="w-3.5 h-3.5 text-[var(--fcp-text-tertiary)] group-hover:translate-x-0.5 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </Link>

            <Link
              to="/profile/tokens"
              onClick={() => setOpen(false)}
              className="flex items-center justify-between px-3 py-2 rounded-xl text-xs font-medium text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors group"
            >
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-lg bg-[var(--fcp-bg-surface-hover)] border border-[var(--fcp-border-subtle)] text-emerald-500 flex items-center justify-center group-hover:scale-105 transition-transform">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                  </svg>
                </div>
                <div>
                  <span className="font-semibold block text-[var(--fcp-text-primary)]">API Tokens</span>
                  <span className="text-[10px] text-[var(--fcp-text-tertiary)] block">REST API &amp; MCP access keys</span>
                </div>
              </div>
              <svg className="w-3.5 h-3.5 text-[var(--fcp-text-tertiary)] group-hover:translate-x-0.5 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </Link>
          </div>

          {/* Section: Admin Operations (if admin) */}
          {isAdmin && (
            <div className="pt-2 mt-1 border-t border-[var(--fcp-border-subtle)] space-y-0.5">
              <div className="px-3 py-1">
                <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--fcp-text-tertiary)]">
                  Admin Operations
                </span>
              </div>

              <Link
                to="/admin"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-medium text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors group"
              >
                <div className="w-7 h-7 rounded-lg bg-[var(--fcp-bg-surface-hover)] border border-[var(--fcp-border-subtle)] text-[#00C2BB] flex items-center justify-center group-hover:scale-105 transition-transform">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
                  </svg>
                </div>
                <span className="font-semibold text-[var(--fcp-text-primary)]">All Projections</span>
              </Link>

              <Link
                to="/admin/health"
                onClick={() => setOpen(false)}
                className="flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-medium text-[var(--fcp-text-primary)] hover:bg-[var(--fcp-bg-surface-hover)] transition-colors group"
              >
                <div className="w-7 h-7 rounded-lg bg-[var(--fcp-bg-surface-hover)] border border-[var(--fcp-border-subtle)] text-emerald-500 flex items-center justify-center group-hover:scale-105 transition-transform">
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                  </svg>
                </div>
                <span className="font-semibold text-[var(--fcp-text-primary)]">System Health Console</span>
              </Link>
            </div>
          )}

          {/* Section: Sign Out */}
          <div className="pt-2 mt-1 border-t border-[var(--fcp-border-subtle)]">
            <button
              onClick={async () => {
                setOpen(false)
                await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' })
                window.location.replace('/login')
              }}
              className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-medium text-rose-600 dark:text-rose-400 hover:bg-rose-500/10 transition-colors cursor-pointer"
            >
              <div className="w-7 h-7 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-500 flex items-center justify-center">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                </svg>
              </div>
              <span className="font-semibold">Sign Out</span>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
