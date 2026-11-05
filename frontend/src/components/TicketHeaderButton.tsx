import { useState, useEffect, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTickets } from '../lib/ticketsContext'
import { useTheme } from '../lib/themeContext'

function formatTimeAgo(isoString?: string): string {
  if (!isoString) return ''
  const date = new Date(isoString)
  const diffMs = Date.now() - date.getTime()
  if (diffMs < 0) return 'Just now'
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

function getStatusBadge(status: string, isDark: boolean) {
  switch (status) {
    case 'open':
      return isDark
        ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30'
        : 'bg-cyan-50 text-cyan-700 border-cyan-200'
    case 'in_progress':
      return isDark
        ? 'bg-[#645DF6]/15 text-[#918bf8] border-[#645DF6]/30'
        : 'bg-indigo-50 text-indigo-700 border-indigo-200'
    case 'resolved':
      return isDark
        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
        : 'bg-emerald-50 text-emerald-700 border-emerald-200'
    case 'closed':
      return isDark
        ? 'bg-gray-500/10 text-gray-400 border-gray-500/30'
        : 'bg-gray-100 text-gray-600 border-gray-200'
    default:
      return isDark
        ? 'bg-gray-500/10 text-gray-400 border-gray-500/30'
        : 'bg-gray-100 text-gray-600 border-gray-200'
  }
}

export function TicketHeaderButton() {
  const { tickets, openTickets, openCount, loading, refresh } = useTickets()
  const [open, setOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<'active' | 'all'>('active')
  const menuRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const { theme } = useTheme()
  const isDark = theme === 'dark'

  // Close dropdown on outside click or Escape key
  useEffect(() => {
    if (!open) return
    const handleOutsideClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleOutsideClick)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  const displayedTickets = activeTab === 'active' ? openTickets : tickets

  const handleItemClick = (ticketId: string) => {
    setOpen(false)
    navigate(`/tickets?ticket=${encodeURIComponent(ticketId)}`)
  }

  return (
    <div className="relative" ref={menuRef}>
      {/* Trigger Button - matches NotificationDropdown & ThemeToggle dimensions and style */}
      <button
        onClick={() => setOpen(prev => !prev)}
        className={`relative w-9 h-9 rounded-full border flex items-center justify-center transition-all duration-200 cursor-pointer shadow-xs group ${
          isDark
            ? open
              ? 'border-[#00C2BB]/50 bg-[#00C2BB]/15 text-[#00C2BB]'
              : 'border-white/10 hover:border-white/25 bg-white/5 hover:bg-white/10 text-gray-300 hover:text-white'
            : open
              ? 'border-[#00C2BB] bg-[#00C2BB]/10 text-[#008f8a]'
              : 'border-gray-300 hover:border-gray-400 bg-white hover:bg-gray-50 text-gray-700 hover:text-black shadow-sm'
        }`}
        title="Support Tickets"
        aria-label="Support Tickets"
        aria-expanded={open}
        aria-haspopup="true"
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
            d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z"
          />
        </svg>

        {openCount > 0 && (
          <span className="absolute -top-1 -right-1 flex h-4 w-4">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#00C2BB] opacity-75" />
            <span className="relative inline-flex items-center justify-center rounded-full h-4 w-4 bg-[#00C2BB] text-[9px] font-bold text-black font-mono shadow-xs">
              {openCount > 9 ? '9+' : openCount}
            </span>
          </span>
        )}
      </button>

      {/* Dropdown Menu */}
      {open && (
        <div
          className={`absolute right-0 mt-2 w-84 sm:w-96 max-w-[92vw] border rounded-2xl shadow-2xl overflow-hidden z-50 anim-slide-down origin-top-right transition-colors duration-150 ${
            isDark
              ? 'bg-[#0e1017]/95 backdrop-blur-xl border-white/15 text-white shadow-2xl shadow-black/60'
              : 'bg-white/95 backdrop-blur-xl border-gray-200 text-gray-900 shadow-2xl shadow-slate-900/10'
          }`}
        >
          {/* Header */}
          <div
            className={`px-4 py-3 border-b flex items-center justify-between ${
              isDark ? 'border-white/10 bg-white/[0.03]' : 'border-gray-100 bg-gray-50/80'
            }`}
          >
            <div className="flex items-center gap-2">
              <span
                className={`w-6 h-6 rounded-lg flex items-center justify-center shrink-0 border ${
                  isDark
                    ? 'bg-[#00C2BB]/10 text-[#00C2BB] border-[#00C2BB]/20'
                    : 'bg-[#00C2BB]/15 text-[#008f8a] border-[#00C2BB]/30'
                }`}
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z"
                  />
                </svg>
              </span>
              <span className={`text-xs font-semibold tracking-wide uppercase ${isDark ? 'text-white' : 'text-gray-900'}`}>
                Support Tickets
              </span>
              <span
                className={`text-[10px] px-2 py-0.5 rounded-full font-mono font-medium border ${
                  openCount > 0
                    ? isDark
                      ? 'bg-[#00C2BB]/15 text-[#00C2BB] border-[#00C2BB]/30'
                      : 'bg-teal-50 text-teal-700 border-teal-200'
                    : isDark
                      ? 'bg-white/10 text-gray-400 border-white/10'
                      : 'bg-gray-100 text-gray-500 border-gray-200'
                }`}
              >
                {openCount} active
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={refresh}
                disabled={loading}
                title="Refresh tickets"
                aria-label="Refresh tickets"
                className={`p-1.5 rounded-md transition-colors cursor-pointer ${
                  isDark
                    ? 'text-gray-400 hover:text-white hover:bg-white/5'
                    : 'text-gray-500 hover:text-gray-900 hover:bg-gray-100'
                }`}
              >
                <svg
                  className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                  />
                </svg>
              </button>
              <Link
                to="/tickets"
                onClick={() => setOpen(false)}
                className={`text-xs font-medium transition-colors hover:underline ${
                  isDark ? 'text-[#00C2BB]' : 'text-[#008f8a]'
                }`}
              >
                Helpdesk →
              </Link>
            </div>
          </div>

          {/* Filter Tabs */}
          <div
            className={`px-3 py-1.5 border-b flex items-center gap-1.5 text-xs ${
              isDark ? 'border-white/5 bg-white/[0.01]' : 'border-gray-100 bg-white'
            }`}
          >
            <button
              onClick={() => setActiveTab('active')}
              className={`px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all cursor-pointer ${
                activeTab === 'active'
                  ? isDark
                    ? 'bg-white/10 text-white shadow-xs'
                    : 'bg-gray-100 text-gray-900 font-semibold'
                  : isDark
                    ? 'text-gray-400 hover:text-gray-200'
                    : 'text-gray-500 hover:text-gray-800'
              }`}
            >
              Active ({openCount})
            </button>
            <button
              onClick={() => setActiveTab('all')}
              className={`px-2.5 py-1 rounded-lg text-[11px] font-medium transition-all cursor-pointer ${
                activeTab === 'all'
                  ? isDark
                    ? 'bg-white/10 text-white shadow-xs'
                    : 'bg-gray-100 text-gray-900 font-semibold'
                  : isDark
                    ? 'text-gray-400 hover:text-gray-200'
                    : 'text-gray-500 hover:text-gray-800'
              }`}
            >
              All ({tickets.length})
            </button>
          </div>

          {/* Ticket List */}
          <div
            className={`max-h-80 overflow-y-auto divide-y ${
              isDark ? 'divide-white/5' : 'divide-gray-100'
            }`}
          >
            {displayedTickets.length === 0 ? (
              <div className="py-8 px-4 text-center">
                <div
                  className={`w-10 h-10 mx-auto rounded-full flex items-center justify-center mb-2.5 ${
                    isDark ? 'bg-white/5 text-gray-400' : 'bg-gray-100 text-gray-500'
                  }`}
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={1.5}
                      d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
                    />
                  </svg>
                </div>
                <p className={`text-xs font-medium ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
                  {activeTab === 'active' ? 'No active support tickets' : 'No support tickets found'}
                </p>
                <p className={`text-[11px] mt-0.5 ${isDark ? 'text-gray-500' : 'text-gray-400'}`}>
                  {activeTab === 'active'
                    ? 'All tickets have been resolved or closed.'
                    : 'Need assistance? Raise a new support ticket.'}
                </p>
                <Link
                  to="/tickets"
                  onClick={() => setOpen(false)}
                  className="mt-3.5 inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-[#00C2BB] hover:bg-[#00a8a2] text-black transition-colors cursor-pointer"
                >
                  <span>+</span>
                  <span>Raise a Ticket</span>
                </Link>
              </div>
            ) : (
              displayedTickets.slice(0, 8).map(t => {
                const isHighPriority = t.priority === 'high'
                const cleanDescription = (t.description || '').replace(/\s+/g, ' ').trim()

                return (
                  <div
                    key={t.id}
                    onClick={() => handleItemClick(t.id)}
                    className={`px-4 py-3 transition-colors cursor-pointer group flex items-start gap-3 border-l-2 ${
                      t.status === 'resolved' || t.status === 'closed'
                        ? 'border-transparent'
                        : isHighPriority
                          ? 'border-red-500'
                          : 'border-[#00C2BB]'
                    } ${
                      isDark ? 'hover:bg-white/[0.04]' : 'hover:bg-slate-50'
                    }`}
                  >
                    {/* Content Column */}
                    <div className="flex-1 min-w-0">
                      {/* Row 1: Title and Status Pill */}
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <div className="flex items-center gap-1.5 min-w-0">
                          {isHighPriority && (
                            <span
                              className="w-1.5 h-1.5 rounded-full bg-red-500 shrink-0"
                              title="High Priority"
                            />
                          )}
                          <span
                            className={`text-xs font-semibold leading-tight truncate transition-colors ${
                              isDark
                                ? 'text-gray-100 group-hover:text-[#00C2BB]'
                                : 'text-gray-900 group-hover:text-[#008f8a]'
                            }`}
                          >
                            {t.title}
                          </span>
                        </div>
                        <span
                          className={`text-[10px] px-2 py-0.5 rounded-full font-mono uppercase tracking-wide border font-medium shrink-0 ${getStatusBadge(
                            t.status,
                            isDark
                          )}`}
                        >
                          {t.status.replace('_', ' ')}
                        </span>
                      </div>

                      {/* Row 2: Clean Truncated Description Preview */}
                      {cleanDescription ? (
                        <p
                          className={`text-[11px] line-clamp-1 leading-normal mb-1.5 truncate ${
                            isDark
                              ? 'text-gray-400 group-hover:text-gray-300'
                              : 'text-gray-600 group-hover:text-gray-800'
                          }`}
                        >
                          {cleanDescription}
                        </p>
                      ) : null}

                      {/* Row 3: Structured Metadata & Manage link */}
                      <div className="flex items-center justify-between text-[10px] pt-0.5">
                        <div className="flex items-center gap-1.5 text-gray-400 dark:text-gray-500">
                          <span className="font-mono text-[9px] px-1.5 py-0.2 rounded bg-black/5 dark:bg-white/5 border border-black/5 dark:border-white/5">
                            #{t.id.slice(0, 6)}
                          </span>
                          {t.category && (
                            <span className="capitalize font-medium">
                              {t.category}
                            </span>
                          )}
                          <span>•</span>
                          <span>{formatTimeAgo(t.created_at || t.updated_at)}</span>
                        </div>

                        <span className="text-[10px] font-medium text-[#00C2BB] opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-0.5 shrink-0">
                          Manage <span>→</span>
                        </span>
                      </div>
                    </div>
                  </div>
                )
              })
            )}
          </div>

          {/* Footer */}
          <div
            className={`px-4 py-2.5 border-t flex items-center justify-between text-xs ${
              isDark ? 'border-white/10 bg-white/[0.02]' : 'border-gray-200 bg-gray-50/80'
            }`}
          >
            <Link
              to="/tickets"
              onClick={() => setOpen(false)}
              className={`text-[11px] font-medium transition-colors ${
                isDark ? 'text-gray-400 hover:text-white' : 'text-gray-600 hover:text-black'
              }`}
            >
              View all tickets &amp; history
            </Link>
            <Link
              to="/tickets"
              onClick={() => setOpen(false)}
              className={`font-medium text-[11px] flex items-center gap-1 transition-colors hover:underline ${
                isDark ? 'text-[#00C2BB]' : 'text-[#008f8a]'
              }`}
            >
              Open Helpdesk →
            </Link>
          </div>
        </div>
      )}
    </div>
  )
}
