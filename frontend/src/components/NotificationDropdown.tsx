import { useState, useEffect, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getAdminNotifications, type NotificationItem } from '../api/admin'
import { useTheme } from '../lib/themeContext'

function formatTimeAgo(isoString: string): string {
  if (!isoString) return ''
  const date = new Date(isoString)
  const diffMs = Date.now() - date.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return 'Just now'
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  const diffDays = Math.floor(diffHr / 24)
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

export function NotificationDropdown() {
  const [open, setOpen] = useState(false)
  const [feed, setFeed] = useState<NotificationItem[]>([])
  const [loading, setLoading] = useState(false)
  const [unreadCount, setUnreadCount] = useState(0)
  const [lastViewedTimestamp, setLastViewedTimestamp] = useState<number>(() => {
    try {
      const saved = localStorage.getItem('cur_admin_notif_last_viewed')
      return saved ? parseInt(saved, 10) : 0
    } catch {
      return 0
    }
  })

  const menuRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  const fetchFeed = async () => {
    try {
      setLoading(true)
      const data = await getAdminNotifications()
      const items = data.feed || []
      setFeed(items)

      // Calculate unread items (occurred after lastViewedTimestamp)
      if (lastViewedTimestamp > 0) {
        const unread = items.filter(
          item => new Date(item.timestamp).getTime() > lastViewedTimestamp
        ).length
        setUnreadCount(unread)
      } else {
        setUnreadCount(Math.min(items.length, 5))
      }
    } catch {
      // Quiet fail for dropdown poll
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchFeed()
    // Poll every 30 seconds for live admin updates
    const interval = setInterval(fetchFeed, 30000)
    return () => clearInterval(interval)
  }, [lastViewedTimestamp])

  // Close dropdown on outside click
  useEffect(() => {
    const handleOutsideClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    if (open) {
      document.addEventListener('mousedown', handleOutsideClick)
    }
    return () => document.removeEventListener('mousedown', handleOutsideClick)
  }, [open])

  const handleToggle = () => {
    const nextState = !open
    setOpen(nextState)
    if (nextState) {
      // Mark as read
      const now = Date.now()
      setLastViewedTimestamp(now)
      setUnreadCount(0)
      try {
        localStorage.setItem('cur_admin_notif_last_viewed', now.toString())
      } catch {
        // ignore
      }
    }
  }

  const handleItemClick = (link: string) => {
    setOpen(false)
    if (link) {
      navigate(link)
    }
  }

  const getIcon = (type: string) => {
    switch (type) {
      case 'job_failed':
        return (
          <span className="w-7 h-7 rounded-full bg-red-500/10 border border-red-500/30 text-red-400 flex items-center justify-center shrink-0">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </span>
        )
      case 'ticket_raised':
        return (
          <span className="w-7 h-7 rounded-full bg-amber-500/10 border border-amber-500/30 text-amber-400 flex items-center justify-center shrink-0">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z" />
            </svg>
          </span>
        )
      case 'assumption_changed':
        return (
          <span className="w-7 h-7 rounded-full bg-purple-500/10 border border-purple-500/30 text-purple-400 flex items-center justify-center shrink-0">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
          </span>
        )
      case 'setting_changed':
        return (
          <span className="w-7 h-7 rounded-full bg-blue-500/10 border border-blue-500/30 text-blue-400 flex items-center justify-center shrink-0">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </span>
        )
      default:
        return (
          <span className="w-7 h-7 rounded-full bg-[#00C2BB]/10 border border-[#00C2BB]/30 text-[#00C2BB] flex items-center justify-center shrink-0">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </span>
        )
    }
  }

  const { theme } = useTheme()
  const isDark = theme === 'dark'

  return (
    <div className="relative" ref={menuRef}>
      {/* Bell Icon Trigger Button */}
      <button
        onClick={handleToggle}
        title="Admin Notifications"
        aria-label="Admin Notifications"
        className={`relative w-9 h-9 rounded-full border flex items-center justify-center transition-all duration-200 cursor-pointer shadow-xs group ${
          isDark
            ? 'border-white/10 hover:border-white/25 bg-white/5 hover:bg-white/10 text-gray-300 hover:text-white'
            : 'border-gray-300 hover:border-gray-400 bg-white hover:bg-gray-50 text-gray-700 hover:text-black shadow-sm'
        }`}
      >
        <svg
          className="w-4 h-4 transition-transform duration-150 group-hover:scale-110"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.8}
            d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
          />
        </svg>

        {/* Unread badge count */}
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 flex h-4 w-4">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-4 w-4 bg-red-500 text-[9px] font-bold text-white items-center justify-center shadow-sm">
              {unreadCount > 9 ? '9+' : unreadCount}
            </span>
          </span>
        )}
      </button>

      {/* Notification Dropdown Panel */}
      {open && (
        <div className={`absolute right-0 mt-2 w-96 max-w-[92vw] border rounded-2xl shadow-2xl overflow-hidden z-50 animate-in fade-in slide-in-from-top-2 duration-150 ${
          isDark
            ? 'bg-[#0c0d14] border-white/15 text-white'
            : 'bg-white border-gray-200 text-gray-900 shadow-xl'
        }`}>
          {/* Header */}
          <div className={`px-4 py-3 border-b flex items-center justify-between ${
            isDark ? 'border-white/10 bg-white/[0.02]' : 'border-gray-200 bg-gray-50'
          }`}>
            <div className="flex items-center gap-2">
              <span className={`text-xs font-semibold tracking-wide uppercase ${isDark ? 'text-white' : 'text-gray-900'}`}>
                Admin Activity
              </span>
              <span className="text-[10px] bg-[#00C2BB]/20 text-[#00C2BB] px-1.5 py-0.2 rounded font-mono">
                Live
              </span>
            </div>
            <button
              onClick={fetchFeed}
              disabled={loading}
              className="text-[11px] text-gray-400 hover:text-white transition flex items-center gap-1"
            >
              <svg className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Refresh
            </button>
          </div>

          {/* Activity List */}
          <div className={`max-h-[380px] overflow-y-auto divide-y ${
            isDark ? 'divide-white/5' : 'divide-gray-100'
          }`}>
            {feed.length === 0 ? (
              <div className={`py-10 text-center text-xs ${isDark ? 'text-gray-500' : 'text-gray-400'}`}>
                {loading ? 'Checking activity...' : 'No recent events recorded.'}
              </div>
            ) : (
              feed.slice(0, 15).map(item => (
                <div
                  key={item.id}
                  onClick={() => handleItemClick(item.link)}
                  className={`px-4 py-3 transition-colors cursor-pointer flex items-start gap-3 group ${
                    isDark ? 'hover:bg-white/[0.03]' : 'hover:bg-gray-50'
                  }`}
                >
                  {getIcon(item.type)}

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2 mb-0.5">
                      <p className={`text-xs font-medium truncate ${
                        isDark ? 'text-gray-200 group-hover:text-white' : 'text-gray-900 group-hover:text-black'
                      }`}>
                        {item.title}
                      </p>
                      <span className={`text-[10px] shrink-0 whitespace-nowrap ${isDark ? 'text-gray-500' : 'text-gray-400'}`}>
                        {formatTimeAgo(item.timestamp)}
                      </span>
                    </div>

                    <p className={`text-[11px] line-clamp-2 leading-tight ${isDark ? 'text-gray-400' : 'text-gray-600'}`}>
                      {item.message}
                    </p>

                    {item.actor && (
                      <div className="flex items-center gap-1.5 mt-1">
                        <span className={`text-[9px] font-mono ${isDark ? 'text-gray-500' : 'text-gray-400'}`}>by {item.actor}</span>
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Footer Link to Dedicated Feed Route */}
          <div className={`px-4 py-2.5 border-t flex items-center justify-between text-xs ${
            isDark ? 'border-white/10 bg-white/[0.01]' : 'border-gray-200 bg-gray-50/50'
          }`}>
            <span className={`text-[11px] ${isDark ? 'text-gray-500' : 'text-gray-500'}`}>
              Showing top {Math.min(feed.length, 15)} events
            </span>
            <Link
              to="/admin/notifications"
              onClick={() => setOpen(false)}
              className="text-[#00C2BB] hover:underline font-medium text-[11px] flex items-center gap-1"
            >
              Open Full Center →
            </Link>
          </div>
        </div>
      )}
    </div>
  )
}
