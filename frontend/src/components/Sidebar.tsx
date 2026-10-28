import { Link, useLocation } from 'react-router-dom'
import facetsFullLogo from '../assets/facets-logo-full.svg'
import type { UserInfo } from '../api/auth'

interface SidebarItem {
  to: string
  label: string
  icon: React.ReactNode
  badge?: string
  adminOnly?: boolean
  facetsOnly?: boolean
  section?: 'main' | 'bottom'
}

interface SidebarProps {
  user: UserInfo
  collapsed: boolean
  onToggle: () => void
}

export function Sidebar({ user, collapsed, onToggle }: SidebarProps) {
  const location = useLocation()
  const isAdmin = Boolean(user?.is_admin)

  // Main Control Plane Navigation Items:
  const mainItems: SidebarItem[] = [
    { to: '/', label: 'Projects', icon: <ProjectsIcon /> },
    { to: '/releases', label: 'Release Dashboard', icon: <ReleaseDashboardIcon /> },
    { to: '/admin', label: 'Release Timeline', icon: <ReleaseTimelineIcon />, adminOnly: true },
    { to: '/admin/bills', label: 'Uploaded Bills', icon: <ModuleRegistryIcon />, adminOnly: true },
    { to: '/admin/catalog', label: 'Pricing Catalog', icon: <ProjectTypeIcon />, adminOnly: true },
    { to: '/admin/analytics', label: 'Cost Analytics', icon: <IntelligenceIcon />, adminOnly: true },
    { to: '/admin/notifications', label: 'Notifications', icon: <BellIcon />, adminOnly: true },
    { to: '/admin/audit', label: 'Audit Trail', icon: <AuditLogsIcon />, adminOnly: true },
    { to: '/admin/report-settings', label: 'Report Settings', icon: <SettingsIcon />, adminOnly: true },
  ]

  // Pinned Bottom Section Items:
  // Docs, Invite Member, Raise a Ticket
  const bottomItems: SidebarItem[] = [
    { to: 'https://docs.facets.cloud', label: 'Docs', icon: <DocsIcon /> },
    { to: '/admin/users', label: 'Invite Member', icon: <InviteMemberIcon />, adminOnly: true },
    { to: '/admin/tickets', label: 'Raise a Ticket', icon: <TicketIcon /> },
  ]

  const visibleMainItems = mainItems.filter(i => !i.adminOnly || isAdmin)
  const visibleBottomItems = bottomItems.filter(i => !i.adminOnly || isAdmin)

  const isActive = (to: string) => {
    if (to.startsWith('http')) return false
    if (to === '/') return location.pathname === '/'
    if (to === '/releases') return location.pathname === '/releases'
    return location.pathname === to || location.pathname.startsWith(to + '/')
  }

  const renderItem = (item: SidebarItem) => {
    const active = isActive(item.to)
    const isExternal = item.to.startsWith('http')

    const content = (
      <div
        className={`flex items-center gap-3.5 px-3 py-2 rounded-lg text-sm font-medium transition-all duration-150 cursor-pointer
          ${active
            ? 'bg-white/10 text-white font-semibold shadow-sm'
            : 'text-gray-300 hover:bg-white/5 hover:text-white border border-transparent'}
        `}
        title={collapsed ? item.label : undefined}
      >
        <div className={`w-5 h-5 flex items-center justify-center shrink-0 ${active ? 'text-white' : 'text-gray-400'}`}>
          {item.icon}
        </div>
        {!collapsed && (
          <div className="flex items-center justify-between flex-1 truncate">
            <span className="truncate">{item.label}</span>
            {item.badge && (
              <span className="text-[10px] uppercase px-1.5 py-0.5 rounded font-semibold bg-white/10 text-gray-300 border border-white/20">
                {item.badge}
              </span>
            )}
          </div>
        )}
      </div>
    )

    const handleItemClick = () => {
      if (collapsed) {
        onToggle()
      }
    }

    if (isExternal) {
      return (
        <a key={item.label} href={item.to} target="_blank" rel="noreferrer" onClick={handleItemClick}>
          {content}
        </a>
      )
    }

    return (
      <Link key={item.label} to={item.to} onClick={handleItemClick}>
        {content}
      </Link>
    )
  }

  return (
    <aside
      className={`fixed top-0 bottom-0 left-0 h-screen z-50 flex flex-col transition-all duration-300 ease-in-out border-r select-none
        ${collapsed ? 'w-16' : 'w-64'}
        bg-[var(--fcp-bg-surface)] border-[var(--fcp-border-subtle)]`}
    >
      {/* Top Brand Area: Facets logo acts as toggle button, aligned at standard h-16 level */}
      <div className="flex items-center h-16 px-2.5 border-b border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shrink-0">
        <button
          onClick={onToggle}
          className="flex items-center w-full cursor-pointer group text-left focus:outline-none py-1.5 px-1.5 rounded-lg hover:bg-white/5 transition-colors"
          title={collapsed ? 'Click Facets logo to expand menu' : 'Click to minimize menu'}
        >
          {/* Logo container: expanded to 48px on collapse so the purple triangular facet (#645DF6) is completely and cleanly visible */}
          <div className="flex items-center justify-between w-full overflow-hidden">
            <div className={`flex items-center overflow-hidden shrink-0 ${collapsed ? 'w-[48px]' : 'w-auto'}`}>
              <img
                src={facetsFullLogo}
                alt="facets.cloud"
                className="h-5 max-w-none object-left object-contain"
              />
            </div>
            {!collapsed && (
              <span className="w-6 h-6 rounded-md border border-white/10 bg-white/5 flex items-center justify-center text-gray-400 group-hover:text-white group-hover:border-white/25 group-hover:bg-white/10 transition-all duration-200 ml-auto shrink-0 shadow-xs">
                <svg
                  className="w-3.5 h-3.5 transition-transform duration-200 group-hover:-translate-x-0.5"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </span>
            )}
          </div>
        </button>
      </div>

      {/* Main Nav Items List */}
      <nav className="flex-1 overflow-y-auto py-2 px-2 space-y-0.5">
        {visibleMainItems.map(renderItem)}
      </nav>

      {/* Pinned Bottom Section: Docs, Invite Member, Raise a Ticket (always immediately visible, no excessive scrolling) */}
      <div className="border-t border-white/10 p-2 space-y-0.5 bg-white/[0.01] shrink-0">
        {visibleBottomItems.map(renderItem)}
      </div>
    </aside>
  )
}

/* ── Tailored Favicon-Class SVGs for Each Control Plane Item (Uniform size) ── */

function ProjectsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
    </svg>
  )
}

function AuditLogsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  )
}

function ProjectTypeIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
    </svg>
  )
}

function ModuleRegistryIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
    </svg>
  )
}

function IntelligenceIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M13 10V3L4 14h7v7l9-11h-7z" />
    </svg>
  )
}

function ReleaseDashboardIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
    </svg>
  )
}

function ReleaseTimelineIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
    </svg>
  )
}

function SettingsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
    </svg>
  )
}

function DocsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
    </svg>
  )
}

function InviteMemberIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
    </svg>
  )
}

function TicketIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z" />
    </svg>
  )
}

function BellIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
    </svg>
  )
}
