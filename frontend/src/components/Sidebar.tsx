import { Link, useLocation } from 'react-router-dom'
import facetsFIcon from '../assets/facets-logo-f.svg'
import type { UserInfo } from '../api/auth'

interface SidebarItem {
  to: string
  label: string
  icon: React.ReactNode
  adminOnly?: boolean
  facetsOnly?: boolean
}

interface SidebarProps {
  user: UserInfo
  collapsed: boolean
  onToggle: () => void
}

export function Sidebar({ user, collapsed, onToggle }: SidebarProps) {
  const location = useLocation()
  const isAdmin = Boolean(user?.is_admin)
  const isFacetsEmployee = Boolean(user?.is_facets_employee)

  const items: SidebarItem[] = [
    { to: '/', label: 'Upload CUR', icon: <UploadIcon /> },
    { to: '/jobs/:id', label: 'Job Runs', icon: <JobIcon /> },
    { to: '/admin', label: 'All Reports', icon: <ReportsIcon />, adminOnly: true },
    { to: '/admin/users', label: 'Users', icon: <UsersIcon />, adminOnly: true },
    { to: '/admin/catalog', label: 'Catalog', icon: <CatalogIcon />, adminOnly: true },
    { to: '/admin/assumptions', label: 'Assumptions', icon: <AssumptionsIcon />, adminOnly: true },
    { to: '/admin/report-settings', label: 'Report Settings', icon: <SettingsIcon />, adminOnly: true },
    { to: '/admin/audit', label: 'Audit Logs', icon: <AuditIcon />, adminOnly: true },
    { to: '/admin/analytics', label: 'Analytics', icon: <AnalyticsIcon />, adminOnly: true },
    { to: '/admin/tickets', label: 'Support Tickets', icon: <TicketIcon /> },
    { to: '/admin/bills', label: 'Uploaded Bills', icon: <BillsIcon />, adminOnly: true },
    { to: '/profile', label: 'Profile', icon: <ProfileIcon /> },
  ]

  const visibleItems = items.filter(i => {
    if (i.adminOnly && !isAdmin) return false
    if (i.facetsOnly && !isFacetsEmployee) return false
    return true
  })

  const isActive = (to: string) => {
    if (to === '/jobs/:id') return location.pathname.startsWith('/jobs/')
    return location.pathname === to
  }

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-40 flex flex-col transition-all duration-300 ease-in-out
        ${collapsed ? 'w-16' : 'w-64'}
        bg-[#0a0a0f] border-r border-white/10`}
    >
      {/* Logo area */}
      <div className="flex items-center h-14 px-3 border-b border-white/10 bg-[#0a0a0f]/95 backdrop-blur">
        {!collapsed ? (
          <Link to="/" className="flex items-center gap-2">
            <img src={facetsFIcon} alt="Facets" className="w-6 h-6" />
            <span className="text-sm font-bold tracking-tight text-white">facets.cloud</span>
          </Link>
        ) : (
          <Link to="/" className="flex items-center justify-center w-full">
            <img src={facetsFIcon} alt="Facets" className="w-7 h-7" />
          </Link>
        )}
      </div>

      {/* Nav items */}
      <nav className="flex-1 overflow-y-auto py-3 space-y-1">
        {visibleItems.map(item => {
          const active = isActive(item.to)
          return (
            <Link
              key={item.to}
              to={item.to}
              onClick={onToggle}
              className={`flex items-center gap-3 px-4 py-3 text-sm transition-colors duration-150
                ${active
                  ? 'bg-[#645DF6]/20 text-[#00C2BB] border-r-2 border-[#00C2BB]'
                  : 'text-gray-400 hover:bg-white/5 hover:text-white'}
              `}
            >
              <div className={`w-6 text-center shrink-0 ${active ? 'text-[#00C2BB]' : 'text-gray-500'}`}>
                {item.icon}
              </div>
              {!collapsed && <span>{item.label}</span>}
            </Link>
          )
        })}
      </nav>

      {/* Bottom: collapse toggle */}
      <div className="h-12 flex items-center justify-center px-2 border-t border-white/10">
        <button
          onClick={onToggle}
          className="w-8 h-8 rounded-full bg-white/5 hover:bg-white/10 flex items-center justify-center text-gray-400 hover:text-white transition-colors"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? '»' : '«'}
        </button>
      </div>
    </aside>
  )
}

// Simple inline SVG icons
function UploadIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
    </svg>
  )
}

function JobIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
    </svg>
  )
}

function ReportsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  )
}

function UsersIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
    </svg>
  )
}

function CatalogIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" />
    </svg>
  )
}

function AssumptionsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
    </svg>
  )
}

function SettingsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
    </svg>
  )
}

function AuditIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 11l3-3m0 0l3 3m-3-3v8m-1 4a9 9 0 1118 0 9 9 0 01-18 0z" />
    </svg>
  )
}

function AnalyticsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
    </svg>
  )
}

function TicketIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7h1a2 2 0 012 2v1a2 2 0 01-2 2H8a2 2 0 01-2-2V9a2 2 0 012-2zM8 15h1a2 2 0 012 2v1a2 2 0 01-2 2H8a2 2 0 01-2-2v-1a2 2 0 012-2zM8 9v1a2 2 0 01-2 2H6a2 2 0 01-2-2V9a2 2 0 012-2h1a2 2 0 012 2zM16 7h1a2 2 0 012 2v1a2 2 0 01-2 2h-1a2 2 0 01-2-2V9a2 2 0 012-2zM16 15h1a2 2 0 012 2v1a2 2 0 01-2 2h-1a2 2 0 01-2-2v-1a2 2 0 012-2zM16 9v1a2 2 0 01-2 2h-1a2 2 0 01-2-2V9a2 2 0 012-2h1a2 2 0 012 2z" />
    </svg>
  )
}

function ProfileIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
    </svg>
  )
}

function BillsIcon() {
  return (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
    </svg>
  )
}
