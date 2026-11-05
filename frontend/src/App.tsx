import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Login } from './pages/Login'
import { Upload } from './pages/Upload'
import { JobStatus } from './pages/JobStatus'
import { JobLogs } from './pages/JobLogs'
import { ReportOverride } from './pages/ReportOverride'
import { AdminJobs } from './pages/AdminJobs'
import { AdminUsers } from './pages/AdminUsers'
import { AdminAssumptions } from './pages/AdminAssumptions'
import { AdminReportSettings } from './pages/AdminReportSettings'
import { AdminAudit } from './pages/AdminAudit'
import { AdminAnalytics } from './pages/AdminAnalytics'
import { AdminHealth } from './pages/AdminHealth'
import { SharedReport } from './pages/SharedReport'
import { Tickets } from './pages/Tickets'
import { Settings } from './pages/Settings'
import { Profile } from './pages/Profile'
import { ApiTokens } from './pages/ApiTokens'
import { NotificationPreferences } from './pages/NotificationPreferences'
import { Bills } from './pages/Bills'
import { Docs } from './pages/Docs'
import { useEffect, useState } from 'react'
import { getMe } from './api/auth'
import type { UserInfo } from './api/auth'
import { Toaster } from 'sonner'
import { ThemeProvider, useTheme } from './lib/themeContext'
import { NotificationProvider } from './lib/notificationsContext'
import { TicketsProvider } from './lib/ticketsContext'

function ThemedToaster() {
  const { theme } = useTheme()
  return (
    <Toaster
      position="top-center"
      richColors
      closeButton
      theme={theme === 'dark' ? 'dark' : 'light'}
      duration={3500}
      toastOptions={{ duration: 3500 }}
      visibleToasts={3}
    />
  )
}

export default function App() {
  const [user, setUser] = useState<UserInfo | null | undefined>(undefined)

  useEffect(() => {
    getMe()
      .then(setUser)
      .catch(() => setUser(null))
  }, [])

  // /reports/:token is a public, unauthenticated page — don't block it behind
  // the getMe() loading spinner or force a login redirect.
  if (window.location.pathname.startsWith('/reports/')) {
    return (
      <BrowserRouter>
        <Routes>
          <Route path="/reports/:token" element={<SharedReport />} />
        </Routes>
      </BrowserRouter>
    )
  }

  if (user === undefined) {
    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0a0a0f',
        }}
      >
        <div
          style={{
            width: 32,
            height: 32,
            border: '3px solid #645DF6',
            borderTopColor: 'transparent',
            borderRadius: '50%',
            animation: 'spin 0.7s linear infinite',
          }}
        />
        <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
      </div>
    )
  }

  const isAdmin = Boolean(user?.is_admin)

  return (
    <ThemeProvider>
      <BrowserRouter>
        <NotificationProvider isAdmin={isAdmin}>
          <TicketsProvider>
            <ThemedToaster />
            <Routes>
            {/* Auth */}
            <Route path="/login" element={user ? <Navigate to="/" /> : <Login />} />

            {/* Zone A: Core Work & User Pages */}
            <Route path="/" element={user ? <Upload user={user} /> : <Navigate to="/login" />} />
            <Route path="/dashboard" element={user ? (isAdmin ? <Navigate to="/admin" /> : <Upload user={user} />) : <Navigate to="/login" />} />
            <Route path="/releases" element={<Navigate to="/admin" replace />} />
            <Route path="/bills" element={user ? <Bills user={user} /> : <Navigate to="/login" />} />
            <Route path="/tickets" element={user ? <Tickets user={user} /> : <Navigate to="/login" />} />
            <Route path="/profile" element={user ? <Profile user={user} /> : <Navigate to="/login" />} />
            <Route path="/profile/tokens" element={user ? <ApiTokens user={user} /> : <Navigate to="/login" />} />
            <Route path="/tokens" element={<Navigate to="/profile/tokens" replace />} />
            <Route path="/settings/tokens" element={<Navigate to="/profile/tokens" replace />} />
            <Route path="/settings/notifications" element={user ? <NotificationPreferences user={user} /> : <Navigate to="/login" />} />
            <Route path="/notifications" element={<Navigate to="/settings/notifications" replace />} />
            <Route path="/settings" element={user ? <Settings user={user} /> : <Navigate to="/login" />} />
            <Route path="/docs" element={user ? <Docs user={user} /> : <Navigate to="/login" />} />

            {/* Job Sub-Views */}
            <Route path="/jobs/:id" element={user ? <JobStatus user={user} /> : <Navigate to="/login" />} />
            <Route path="/jobs/:id/logs" element={user ? <JobLogs user={user} /> : <Navigate to="/login" />} />
            <Route path="/jobs/:id/logs/:stream" element={user ? <JobLogs user={user} /> : <Navigate to="/login" />} />
            <Route path="/jobs/:id/review" element={user ? <ReportOverride user={user} /> : <Navigate to="/login" />} />

            {/* Zone B: Admin Operations & Platform Math */}
            <Route path="/admin" element={user && isAdmin ? <AdminJobs user={user} /> : <Navigate to="/" />} />
            <Route path="/admin/findings" element={<Navigate to="/admin" replace />} />
            <Route path="/admin/health" element={user && isAdmin ? <AdminHealth user={user} /> : <Navigate to="/" />} />
            <Route path="/admin/users" element={user && isAdmin ? <AdminUsers user={user} /> : <Navigate to="/" />} />
            <Route path="/admin/assumptions" element={user && isAdmin ? <AdminAssumptions user={user} /> : <Navigate to="/" />} />
            <Route path="/admin/report-settings" element={user && isAdmin ? <AdminReportSettings user={user} /> : <Navigate to="/" />} />
            <Route path="/admin/audit" element={user && isAdmin ? <AdminAudit user={user} /> : <Navigate to="/" />} />
            <Route path="/admin/analytics" element={user && isAdmin ? <AdminAnalytics user={user} /> : <Navigate to="/" />} />
            <Route path="/admin/notifications" element={user && isAdmin ? <NotificationPreferences user={user} /> : <Navigate to="/settings/notifications" />} />
            <Route path="/admin/integrations" element={user && isAdmin ? <NotificationPreferences user={user} /> : <Navigate to="/settings/notifications" />} />

            {/* Backward-compatibility redirects */}
            <Route path="/admin/catalog" element={<Navigate to="/admin/health" replace />} />
            <Route path="/admin/bills" element={<Navigate to="/bills" replace />} />
            <Route path="/admin/tickets" element={<Navigate to="/tickets" replace />} />

            {/* Public report shares */}
            <Route path="/reports/:token" element={<SharedReport />} />

            {/* Catch-all */}
            <Route path="*" element={<Navigate to={user ? '/' : '/login'} replace />} />
          </Routes>
          </TicketsProvider>
        </NotificationProvider>
      </BrowserRouter>
    </ThemeProvider>
  )
}
