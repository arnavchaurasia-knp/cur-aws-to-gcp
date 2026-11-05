import { useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { ThemeToggle } from './ThemeToggle'
import { UserMenu } from './Nav'
import { NotificationDropdown } from './NotificationDropdown'
import { TicketHeaderButton } from './TicketHeaderButton'
import type { UserInfo } from '../api/auth'

interface LayoutProps {
  user: UserInfo
  children: React.ReactNode
}

export function Layout({ user, children }: LayoutProps) {
  const location = useLocation()
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem('fcp-sidebar-collapsed') === '1' } catch { return false }
  })

  const toggleSidebar = () => {
    setSidebarCollapsed(prev => {
      const next = !prev
      try { localStorage.setItem('fcp-sidebar-collapsed', next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
  }

  // Redirect unauthenticated users
  if (!user) return <Navigate to="/login" replace />

  const isAdmin = Boolean(user?.is_admin)

  return (
    <div className="min-h-screen bg-[var(--fcp-bg-base)] text-[var(--fcp-text-primary)] transition-colors duration-200">
      {/* Top Navbar */}
      <header
        className={`fixed top-0 right-0 h-16 z-30 border-b border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-base)]/90 backdrop-blur-md transition-all duration-[420ms] ease-[cubic-bezier(0.22,1,0.36,1)] ${
          sidebarCollapsed ? 'left-16' : 'left-64'
        }`}
      >
        <div className="flex items-center justify-between px-6 h-full">
          {/* Left: AWS → GCP Cost Projection */}
          <div className="flex items-center">
            <span className="text-sm font-semibold tracking-wide">
              AWS → GCP Cost Projection
            </span>
          </div>

          {/* Right: Ticket Header Button + Notification Bell (admin) + Theme Toggle + User Dropdown */}
          <div className="flex items-center gap-2">
            {/* Quick Support Tickets Header Button */}
            <TicketHeaderButton />

            {/* Admin Interactive Notification Bell with Dropdown */}
            {isAdmin && <NotificationDropdown />}

            {/* Round Favicon Theme Toggle */}
            <ThemeToggle />

            {/* Separator */}
            <div className="h-5 w-px bg-gray-300 dark:bg-white/10" />

            {/* Round User Dropdown */}
            <UserMenu user={user} />
          </div>
        </div>
      </header>

      {/* Main content layout with universal sidebar */}
      <div className={`flex transition-all duration-[420ms] ease-[cubic-bezier(0.22,1,0.36,1)] ${sidebarCollapsed ? 'pl-16' : 'pl-64'}`}>
        <Sidebar
          user={user}
          collapsed={sidebarCollapsed}
          onToggle={toggleSidebar}
        />

        {/* Page Content: occupies full page smoothly */}
        <main className="flex-1 min-w-0 pt-16 min-h-screen overflow-x-hidden">
          <div key={location.pathname} className="page-enter min-w-0 w-full">
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}

export default Layout
