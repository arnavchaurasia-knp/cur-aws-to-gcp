import { Link, useLocation } from 'react-router-dom'
import facetsFullLogo from '../assets/facets-logo-full.svg'
import facetsLogoF from '../assets/facets-logo-f.svg'
import type { UserInfo } from '../api/auth'

export interface SidebarItem {
  to: string
  label: string
  icon: React.ReactNode
  badge?: string
  adminOnly?: boolean
  customerOnly?: boolean
  external?: boolean
}

export interface SidebarGroup {
  id: string
  title: string
  adminOnly?: boolean
  customerOnly?: boolean
  items: SidebarItem[]
}

interface SidebarProps {
  user: UserInfo
  collapsed: boolean
  onToggle: () => void
}

export function Sidebar({ user, collapsed, onToggle }: SidebarProps) {
  const location = useLocation()
  const isAdmin = Boolean(user?.is_admin)

  // Clean, structured Information Architecture groups without bloat
  const groups: SidebarGroup[] = [
    {
      id: 'work',
      title: 'Work',
      items: [
        { to: '/', label: 'Projects', icon: <ProjectsIcon /> },
        { to: '/bills', label: 'Uploaded Bills', icon: <BillsIcon /> },
      ],
    },
    {
      id: 'operations',
      title: 'Operations',
      adminOnly: true,
      items: [
        { to: '/admin', label: 'All Projections', icon: <ReleaseTimelineIcon /> },
        { to: '/admin/health', label: 'System Health', icon: <HealthPulseIcon /> },
      ],
    },
    {
      id: 'pricing',
      title: 'Pricing Math',
      adminOnly: true,
      items: [
        { to: '/admin/analytics', label: 'Cost Analytics', icon: <AnalyticsIcon /> },
        { to: '/admin/assumptions', label: 'Pricing Assumptions', icon: <AssumptionsIcon /> },
      ],
    },
    {
      id: 'configuration',
      title: 'Configuration',
      adminOnly: true,
      items: [
        { to: '/admin/report-settings', label: 'Report Settings', icon: <SettingsIcon /> },
        { to: '/settings/notifications', label: 'Delivery Channels', icon: <DeliveryChannelsIcon /> },
        { to: '/admin/audit', label: 'Audit Trail', icon: <AuditLogsIcon /> },
        { to: '/admin/users', label: 'User Management', icon: <UsersIcon /> },
      ],
    },
  ]

  // Pinned Bottom Section
  const bottomItems: SidebarItem[] = [
    { to: '/docs', label: 'Docs & API', icon: <DocsIcon /> },
    { to: '/tickets', label: 'Raise a Ticket', icon: <TicketIcon /> },
  ]

  const isItemVisible = (item: SidebarItem) => {
    if (item.adminOnly && !isAdmin) return false
    if (item.customerOnly && isAdmin) return false
    return true
  }

  const isGroupVisible = (group: SidebarGroup) => {
    if (group.adminOnly && !isAdmin) return false
    if (group.customerOnly && isAdmin) return false
    return group.items.some(isItemVisible)
  }

  const visibleGroups = groups.filter(isGroupVisible)
  const visibleBottomItems = bottomItems.filter(isItemVisible)

  const isActive = (to: string) => {
    if (to.startsWith('http')) return false
    if (to === '/') return location.pathname === '/'
    if (to === '/admin') return location.pathname === '/admin'
    return location.pathname === to || location.pathname.startsWith(to + '/')
  }

  const renderItem = (item: SidebarItem) => {
    const active = isActive(item.to)
    const isExternal = item.external || item.to.startsWith('http')

    const content = (
      <div
        className={`flex items-center gap-3 px-3 py-1.5 rounded-lg text-sm font-medium transition-all duration-150 cursor-pointer select-none
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
          <div className="flex items-center justify-between flex-1 truncate anim-fade-in min-w-0">
            <span className="truncate">{item.label}</span>
            {item.badge && (
              <span className="text-[10px] uppercase px-1.5 py-0.5 rounded font-semibold bg-white/10 text-gray-300 border border-white/20 shrink-0 ml-1.5">
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
      data-sidebar
      className={`fixed top-0 bottom-0 left-0 h-screen z-50 flex flex-col transition-all duration-200 ease-in-out border-r select-none
        ${collapsed ? 'w-16' : 'w-64'}
        bg-[var(--fcp-bg-surface)] border-[var(--fcp-border-subtle)]`}
    >
      {/* Top Brand Area: Facets logo acts as toggle button, aligned at standard h-16 level */}
      <div className="flex items-center h-16 px-2.5 border-b border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shrink-0 overflow-hidden">
        <button
          onClick={onToggle}
          className="relative flex items-center w-full h-10 px-2 rounded-lg hover:bg-white/5 transition-colors cursor-pointer group text-left focus:outline-none"
          title={collapsed ? 'Click Facets logo to expand menu' : 'Click to minimize menu'}
        >
          {/* Collapsed state: standalone F logo centered */}
          <div
            className={`absolute inset-0 flex items-center justify-center transition-opacity duration-200 ${
              collapsed ? 'opacity-100' : 'opacity-0 pointer-events-none'
            }`}
          >
            <img
              src={facetsLogoF}
              alt="Facets"
              className="h-5 w-auto object-contain shrink-0"
            />
          </div>

          {/* Expanded state: full logo and collapse chevron */}
          <div
            className={`flex items-center justify-between w-full min-w-0 transition-opacity duration-200 ${
              collapsed ? 'opacity-0 pointer-events-none' : 'opacity-100'
            }`}
          >
            <img
              src={facetsFullLogo}
              alt="facets.cloud"
              className="h-5 w-auto object-contain shrink-0"
            />
            <span className="w-6 h-6 rounded-md border border-white/10 bg-white/5 flex items-center justify-center text-gray-400 group-hover:text-white group-hover:border-white/25 group-hover:bg-white/10 transition-all duration-150 ml-auto shrink-0 shadow-xs">
              <svg
                className="w-3.5 h-3.5 transition-transform duration-150 group-hover:-translate-x-0.5"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
            </span>
          </div>
        </button>
      </div>

      {/* Main Nav Items List — clean grouping with compact padding */}
      <nav className="flex-1 overflow-y-auto py-2.5 px-2.5 space-y-2">
        {visibleGroups.map((group, groupIdx) => {
          const visibleItems = group.items.filter(isItemVisible)
          if (visibleItems.length === 0) return null

          return (
            <div key={group.id} className="space-y-0.5">
              {!collapsed ? (
                <div className={`px-3 py-1 ${groupIdx > 0 ? 'mt-2 border-t border-[var(--fcp-border-subtle)]/50 pt-2' : ''}`}>
                  <p className="text-[10px] font-bold uppercase tracking-wider text-gray-500 font-mono">
                    {group.title}
                  </p>
                </div>
              ) : groupIdx > 0 ? (
                <div className="my-1.5 mx-2 h-px bg-[var(--fcp-border-subtle)]/50" />
              ) : null}

              {visibleItems.map(renderItem)}
            </div>
          )
        })}
      </nav>

      {/* Pinned Bottom Section: Docs, Raise a Ticket */}
      <div className="border-t border-[var(--fcp-border-subtle)] p-2 space-y-0.5 bg-white/[0.01] shrink-0">
        {visibleBottomItems.map(renderItem)}
      </div>
    </aside>
  )
}

/* ── Tailored Favicon-Class SVGs for Each Control Plane Item ── */

function ProjectsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
    </svg>
  )
}

function BillsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  )
}

function ReleaseTimelineIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
    </svg>
  )
}

function HealthPulseIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z" />
    </svg>
  )
}

function AssumptionsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
    </svg>
  )
}

function AnalyticsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
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

function SettingsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
    </svg>
  )
}

function DeliveryChannelsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
    </svg>
  )
}

function UsersIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
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

function TicketIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z" />
    </svg>
  )
}
