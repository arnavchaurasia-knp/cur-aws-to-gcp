import { useEffect, useState, useCallback } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Nav } from './Nav'
import { Sidebar } from './Sidebar'
import { ThemeToggle } from './ThemeToggle'
import type { UserInfo } from '../api/auth'

interface LayoutProps {
  user: UserInfo
  children: React.ReactNode
}

export function Layout({ user, children }: LayoutProps) {
  const location = useLocation()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mobileSidebar, setMobileSidebar] = useState(false)
  const [currency, setCurrency] = useState('USD')
  const [language, setLanguage] = useState('en')

  // Load preferences from localStorage
  useEffect(() => {
    const saved = localStorage.getItem('cur-web-currency')
    if (saved) setCurrency(saved as any)
    const savedLang = localStorage.getItem('cur-web-language')
    if (savedLang) setLanguage(savedLang)
  }, [])

  const handleCurrencyChange = useCallback((val: string) => {
    setCurrency(val)
    localStorage.setItem('cur-web-currency', val)
  }, [])

  const handleLanguageChange = useCallback((val: string) => {
    setLanguage(val)
    localStorage.setItem('cur-web-language', val)
  }, [])

  // Redirect unauthenticated users
  if (!user) return <Navigate to="/login" replace />

  const isAdmin = Boolean(user.is_admin)

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      {/* Top Navbar */}
      <header className="fixed top-0 left-0 right-0 h-16 z-50 border-b border-white/10 bg-[#0a0a0f]/80 backdrop-blur-md">
        <div className="flex items-center justify-between px-6 h-full">
          {/* Left: Logo + currency/language selector */}
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 hidden md:flex">
              <img src={require('../assets/facets-logo-f.svg')} alt="Facets" className="w-5 h-5" />
              <span className="text-xs font-bold tracking-tight text-[#00C2BB] uppercase">facets</span>
              <span className="text-xs font-bold tracking-tight text-white">.cloud</span>
            </div>

            {/* Global Currency & Language */}
            <div className="hidden md:flex items-center gap-6 border-l border-white/10 pl-6">
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-400">Currency</span>
                <select
                  value={currency}
                  onChange={e => handleCurrencyChange(e.target.value)}
                  className="bg-white/[0.03] border border-white/10 rounded px-2 py-1 text-xs text-white focus:border-[#645DF6] outline-none"
                >
                  <option value="USD">USD $</option>
                  <option value="EUR">EUR €</option>
                  <option value="GBP">GBP £</option>
                  <option value="INR">INR ₹</option>
                  <option value="JPY">JPY ¥</option>
                  <option value="SGD">SGD S$</option>
                </select>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-400">Language</span>
                <select
                  value={language}
                  onChange={e => handleLanguageChange(e.target.value)}
                  className="bg-white/[0.03] border border-white/10 rounded px-2 py-1 text-xs text-white focus:border-[#645DF6] outline-none"
                >
                  <option value="en">English</option>
                  <option value="es">Español</option>
                  <option value="fr">Français</option>
                  <option value="de">Deutsch</option>
                  <option value="ja">日本語</option>
                  <option value="hi">हिन्दी</option>
                </select>
              </div>
            </div>
          </div>

          {/* Right: Theme toggle + User dropdown */}
          <div className="flex items-center gap-3">
            <ThemeToggle />
            <Nav user={user} />
          </div>
        </div>
      </header>

      {/* Main content area (shifted right for sidebar when not collapsed) */}
      <div className={`flex transition-all duration-300 ${sidebarCollapsed ? 'pl-16' : 'pl-64'}`}>
        {/* Sidebar */}
        <Sidebar user={user} collapsed={sidebarCollapsed} />

        {/* Page Content */}
        <main
          className={`flex-1 pt-16 pb-8 transition-all duration-300 ${
            sidebarCollapsed ? 'pl-0' : ''
          }`}
        >
          {children}
        </main>
      </div>

      {/* Mobile overlay */}
      {mobileSidebar && (
        <div
          className="fixed inset-0 z-49 bg-black/60 backdrop-blur"
          onClick={() => setMobileSidebar(false)}
        />
      )}
    </div>
  )
}

export default Layout
