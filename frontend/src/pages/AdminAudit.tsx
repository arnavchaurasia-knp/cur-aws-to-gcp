import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { listAuditLog } from '../api/admin'
import type { AuditEntry } from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { parseAdminApiError, type AdminApiErrorState } from '../components/adminError'
import { BackButton } from '../components/BackButton'

const PAGE_SIZE = 50

type SortField = 'date-desc' | 'date-asc' | 'actor-asc' | 'actor-desc' | 'action-asc' | 'action-desc'
type DateMode = 'preset' | 'datetime' | 'month'

function formatAuditTimestamp(raw: string): { formattedDate: string; formattedTime: string; relative: string } {
  if (!raw) return { formattedDate: '—', formattedTime: '', relative: '' }
  try {
    const d = new Date(raw)
    if (Number.isNaN(d.getTime())) return { formattedDate: raw, formattedTime: '', relative: '' }
    const formattedDate = d.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
    const formattedTime = d.toLocaleTimeString(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000)
    let relative = 'just now'
    if (diffSec >= 60 && diffSec < 3600) relative = `${Math.floor(diffSec / 60)}m ago`
    else if (diffSec >= 3600 && diffSec < 86400) relative = `${Math.floor(diffSec / 3600)}h ago`
    else if (diffSec >= 86400) relative = `${Math.floor(diffSec / 86400)}d ago`
    return { formattedDate, formattedTime, relative }
  } catch {
    return { formattedDate: raw, formattedTime: '', relative: '' }
  }
}

function AuditDetailsFormatter({ details }: Readonly<{ details?: string }>) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  if (!details || details.trim() === '' || details === '—') {
    return <span className="text-[var(--fcp-text-tertiary)] font-mono text-xs">—</span>
  }

  const raw = details.trim()

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    navigator.clipboard.writeText(raw)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  // 1. Key=value parsing
  const kvRegex = /\b([a-zA-Z_]+)=([^\s]+|"[^"]*")/g
  const matches = Array.from(raw.matchAll(kvRegex))
  if (matches.length >= 2) {
    return (
      <div className="flex flex-wrap items-center gap-1.5 py-0.5">
        {matches.map((m, i) => {
          const k = m[1]
          const v = m[2].replace(/^"|"$/g, '')
          let badgeColor = 'bg-white/5 text-[var(--fcp-text-secondary)] border-white/10'
          if (k === 'status') {
            if (v === 'resolved') badgeColor = 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30 font-semibold'
            else if (v === 'open') badgeColor = 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30'
            else if (v === 'closed') badgeColor = 'bg-gray-500/10 text-gray-400 border-gray-500/30'
          } else if (k === 'priority') {
            if (v === 'urgent' || v === 'high') badgeColor = 'bg-red-500/10 text-red-400 border-red-500/30 font-semibold'
            else if (v === 'medium') badgeColor = 'bg-orange-500/10 text-orange-400 border-orange-500/30'
            else badgeColor = 'bg-blue-500/10 text-blue-400 border-blue-500/30'
          }
          return (
            <span
              key={i}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono border ${badgeColor}`}
            >
              <span className="opacity-60">{k}:</span>
              <span>{v}</span>
            </span>
          )
        })}
        <button
          onClick={handleCopy}
          title="Copy raw details"
          className="text-[10px] text-[var(--fcp-text-tertiary)] hover:text-[var(--fcp-text-primary)] transition p-0.5 cursor-pointer ml-1"
        >
          {copied ? '✓' : '⧉'}
        </button>
      </div>
    )
  }

  // 2. State transition arrow: old -> new
  if (raw.includes(' -> ') || raw.includes(' → ')) {
    const parts = raw.split(/ -> | → /)
    return (
      <div className="flex items-center gap-2 text-xs py-0.5 flex-wrap">
        <span className="line-through text-[var(--fcp-text-tertiary)] font-mono">{parts[0]}</span>
        <span className="text-[#00C2BB]">→</span>
        <span className="font-semibold text-[var(--fcp-text-primary)] font-mono">{parts.slice(1).join(' → ')}</span>
        <button
          onClick={handleCopy}
          title="Copy raw details"
          className="text-[10px] text-[var(--fcp-text-tertiary)] hover:text-[var(--fcp-text-primary)] transition p-0.5 cursor-pointer ml-1"
        >
          {copied ? '✓' : '⧉'}
        </button>
      </div>
    )
  }

  // 3. Regular string with toggleable expand
  const isLong = raw.length > 100
  return (
    <div className="text-xs text-[var(--fcp-text-secondary)] leading-relaxed">
      <div className={!expanded && isLong ? 'line-clamp-2' : ''}>
        {raw}
      </div>
      <div className="flex items-center gap-2 mt-1">
        {isLong && (
          <button
            onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}
            className="text-[11px] text-[#645DF6] hover:underline cursor-pointer font-medium"
          >
            {expanded ? 'Show less' : 'Show full details'}
          </button>
        )}
        <button
          onClick={handleCopy}
          title="Copy details"
          className="text-[10px] text-[var(--fcp-text-tertiary)] hover:text-[var(--fcp-text-primary)] transition cursor-pointer"
        >
          {copied ? '✓ Copied' : '⧉ Copy'}
        </button>
      </div>
    </div>
  )
}

export function AdminAudit({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Audit log — admin')
  const nav = useNavigate()
  const [entries, setEntries] = useState<AuditEntry[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<AdminApiErrorState | null>(null)
  const [offset, setOffset] = useState(0)

  // Filter states
  const [actorFilter, setActorFilter] = useState('')
  const [actionFilter, setActionFilter] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [monthTarget, setMonthTarget] = useState('')
  const [dateMode, setDateMode] = useState<DateMode>('preset')
  const [activePreset, setActivePreset] = useState<string>('all')

  // Sorting state
  const [sortBy, setSortBy] = useState<SortField>('date-desc')
  const [selectedRowId, setSelectedRowId] = useState<number | null>(null)

  const loadData = () => {
    setLoading(true)
    setLoadError(null)

    // Calculate effective from/to
    let effFrom = fromDate
    let effTo = toDate
    if (dateMode === 'month' && monthTarget) {
      effFrom = `${monthTarget}-01`
      effTo = `${monthTarget}-31`
    }

    listAuditLog({
      limit: PAGE_SIZE,
      offset,
      actor: actorFilter.trim() || undefined,
      action: actionFilter.trim() || undefined,
      from: effFrom || undefined,
      to: effTo || undefined,
    })
      .then(res => setEntries(res))
      .catch(err => {
        const parsed = parseAdminApiError(err, 'Audit Log', () => nav('/login'))
        setLoadError(parsed)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    const timer = setTimeout(() => {
      loadData()
    }, 250)
    return () => clearTimeout(timer)
  }, [offset, actorFilter, actionFilter, fromDate, toDate, monthTarget, dateMode])

  // Count active filters
  const activeFilterCount = useMemo(() => {
    let count = 0
    if (actorFilter.trim()) count++
    if (actionFilter.trim()) count++
    if (dateMode === 'month' && monthTarget) count++
    else if (fromDate || toDate) count++
    return count
  }, [actorFilter, actionFilter, fromDate, toDate, monthTarget, dateMode])

  const clearAllFilters = () => {
    setActorFilter('')
    setActionFilter('')
    setFromDate('')
    setToDate('')
    setMonthTarget('')
    setActivePreset('all')
    setOffset(0)
  }

  const applyPreset = (preset: string) => {
    setActivePreset(preset)
    setDateMode('preset')
    setMonthTarget('')
    setOffset(0)

    const now = new Date()
    const fmt = (d: Date) => d.toISOString().slice(0, 10)

    if (preset === 'all') {
      setFromDate('')
      setToDate('')
    } else if (preset === 'today') {
      const todayStr = fmt(now)
      setFromDate(todayStr)
      setToDate(todayStr)
    } else if (preset === 'yesterday') {
      const y = new Date(now)
      y.setDate(y.getDate() - 1)
      const yStr = fmt(y)
      setFromDate(yStr)
      setToDate(yStr)
    } else if (preset === '7days') {
      const past = new Date(now)
      past.setDate(past.getDate() - 7)
      setFromDate(fmt(past))
      setToDate(fmt(now))
    } else if (preset === 'this_month') {
      const start = new Date(now.getFullYear(), now.getMonth(), 1)
      setFromDate(fmt(start))
      setToDate(fmt(now))
    } else if (preset === 'last_month') {
      const start = new Date(now.getFullYear(), now.getMonth() - 1, 1)
      const end = new Date(now.getFullYear(), now.getMonth(), 0)
      setFromDate(fmt(start))
      setToDate(fmt(end))
    }
  }

  // Sorted entries
  const sortedEntries = useMemo(() => {
    if (!entries) return null
    const list = [...entries]
    switch (sortBy) {
      case 'date-desc':
        return list.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      case 'date-asc':
        return list.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
      case 'actor-asc':
        return list.sort((a, b) => a.actor_email.localeCompare(b.actor_email))
      case 'actor-desc':
        return list.sort((a, b) => b.actor_email.localeCompare(a.actor_email))
      case 'action-asc':
        return list.sort((a, b) => a.action.localeCompare(b.action))
      case 'action-desc':
        return list.sort((a, b) => b.action.localeCompare(a.action))
      default:
        return list
    }
  }, [entries, sortBy])

  const actionCategories = [
    { label: 'All', value: '' },
    { label: 'Tickets', value: 'ticket' },
    { label: 'Assumptions', value: 'assumption' },
    { label: 'Settings', value: 'setting' },
    { label: 'Jobs', value: 'job' },
    { label: 'Users', value: 'user' },
    { label: 'Catalog', value: 'catalog' },
  ]

  return (
    <Layout user={user}>
      <div className="max-w-7xl mx-auto px-6 py-10">
        <BackButton fallback="/admin" />
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-[var(--fcp-text-primary)]">Admin Audit Log</h1>
            <p className="text-sm text-[var(--fcp-text-secondary)] mt-1">
              Complete traceable record of administrative actions across jobs, tickets, assumptions, and settings.
            </p>
          </div>

          {/* Quick Sort Control */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-[var(--fcp-text-secondary)]">Sort by:</span>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as SortField)}
              className="bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-1.5 text-xs text-[var(--fcp-text-primary)] focus:border-[#645DF6] cursor-pointer outline-none"
            >
              <option value="date-desc">Newest First</option>
              <option value="date-asc">Oldest First</option>
              <option value="actor-asc">Actor (A → Z)</option>
              <option value="actor-desc">Actor (Z → A)</option>
              <option value="action-asc">Action (A → Z)</option>
              <option value="action-desc">Action (Z → A)</option>
            </select>
          </div>
        </div>

        {/* Filter Controls Toolbar */}
        <div className="bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] rounded-2xl p-5 mb-6 space-y-4 shadow-sm">
          {/* Row 1: Search Inputs + Action Category Pills */}
          <div className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-3 flex-1">
              <div className="relative min-w-[220px] flex-1 sm:flex-initial">
                <input
                  type="text"
                  value={actorFilter}
                  onChange={e => { setActorFilter(e.target.value); setOffset(0) }}
                  placeholder="Filter by actor email…"
                  className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-1.5 text-xs text-[var(--fcp-text-primary)] placeholder-[var(--fcp-text-tertiary)] focus:border-[#645DF6] transition"
                />
              </div>

              <div className="relative min-w-[220px] flex-1 sm:flex-initial">
                <input
                  type="text"
                  value={actionFilter}
                  onChange={e => { setActionFilter(e.target.value); setOffset(0) }}
                  placeholder="Filter by action name…"
                  className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-3 py-1.5 text-xs text-[var(--fcp-text-primary)] placeholder-[var(--fcp-text-tertiary)] focus:border-[#645DF6] transition"
                />
              </div>
            </div>

            {/* Clear Filters Button */}
            {activeFilterCount > 0 && (
              <button
                type="button"
                onClick={clearAllFilters}
                className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 hover:text-red-300 border border-red-500/30 text-xs font-semibold transition cursor-pointer self-start lg:self-auto shrink-0"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
                <span>Clear Filters ({activeFilterCount})</span>
              </button>
            )}
          </div>

          {/* Action quick category filter pills */}
          <div className="flex items-center gap-2 flex-wrap pt-1 border-t border-[var(--fcp-border-subtle)]">
            <span className="text-[11px] font-medium text-[var(--fcp-text-secondary)] mr-1">Actions:</span>
            {actionCategories.map(cat => {
              const isSelected = (!cat.value && !actionFilter) || (cat.value && actionFilter.toLowerCase().includes(cat.value))
              return (
                <button
                  key={cat.label}
                  type="button"
                  onClick={() => {
                    setActionFilter(cat.value)
                    setOffset(0)
                  }}
                  className={`px-2.5 py-1 rounded-md text-xs font-medium transition cursor-pointer ${
                    isSelected
                      ? 'bg-[#645DF6] text-white shadow-sm'
                      : 'bg-[var(--fcp-input-bg)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] border border-[var(--fcp-border-subtle)]'
                  }`}
                >
                  {cat.label}
                </button>
              )
            })}
          </div>

          {/* Date, Month, Time Filter Targets */}
          <div className="pt-3 border-t border-[var(--fcp-border-subtle)] flex flex-col md:flex-row md:items-center justify-between gap-3">
            {/* Presets */}
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-[11px] font-medium text-[var(--fcp-text-secondary)] mr-1">Date:</span>
              {[
                { id: 'all', label: 'All Time' },
                { id: 'today', label: 'Today' },
                { id: 'yesterday', label: 'Yesterday' },
                { id: '7days', label: 'Last 7 Days' },
                { id: 'this_month', label: 'This Month' },
                { id: 'last_month', label: 'Last Month' },
              ].map(p => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => applyPreset(p.id)}
                  className={`px-2.5 py-1 rounded-md text-xs transition cursor-pointer ${
                    activePreset === p.id && dateMode === 'preset'
                      ? 'bg-[#00C2BB] text-black font-semibold shadow-sm'
                      : 'bg-[var(--fcp-input-bg)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] border border-[var(--fcp-border-subtle)]'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>

            {/* Custom Range / Month / DateTime pickers */}
            <div className="flex items-center gap-2 flex-wrap text-xs text-[var(--fcp-text-secondary)]">
              <div className="inline-flex rounded-lg border border-[var(--fcp-border-subtle)] p-0.5 bg-[var(--fcp-input-bg)]">
                <button
                  type="button"
                  onClick={() => { setDateMode('datetime'); setActivePreset('') }}
                  className={`px-2 py-0.5 rounded text-[11px] transition cursor-pointer ${dateMode === 'datetime' ? 'bg-[#645DF6] text-white font-medium' : 'text-[var(--fcp-text-secondary)]'}`}
                >
                  Date/Time
                </button>
                <button
                  type="button"
                  onClick={() => { setDateMode('month'); setActivePreset('') }}
                  className={`px-2 py-0.5 rounded text-[11px] transition cursor-pointer ${dateMode === 'month' ? 'bg-[#645DF6] text-white font-medium' : 'text-[var(--fcp-text-secondary)]'}`}
                >
                  Month
                </button>
              </div>

              {dateMode === 'datetime' ? (
                <div className="flex items-center gap-2 flex-wrap">
                  <div className="flex items-center gap-1.5">
                    <span>From:</span>
                    <input
                      type="datetime-local"
                      value={fromDate}
                      onChange={e => {
                        setFromDate(e.target.value)
                        setActivePreset('')
                        setOffset(0)
                      }}
                      className="bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-2 py-1 text-xs text-[var(--fcp-text-primary)] focus:border-[#645DF6] outline-none"
                    />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span>To:</span>
                    <input
                      type="datetime-local"
                      value={toDate}
                      onChange={e => {
                        setToDate(e.target.value)
                        setActivePreset('')
                        setOffset(0)
                      }}
                      className="bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-2 py-1 text-xs text-[var(--fcp-text-primary)] focus:border-[#645DF6] outline-none"
                    />
                  </div>
                </div>
              ) : dateMode === 'month' ? (
                <div className="flex items-center gap-1.5">
                  <span>Target Month:</span>
                  <input
                    type="month"
                    value={monthTarget}
                    onChange={e => {
                      setMonthTarget(e.target.value)
                      setActivePreset('')
                      setOffset(0)
                    }}
                    className="bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] rounded-lg px-2 py-1 text-xs text-[var(--fcp-text-primary)] focus:border-[#645DF6] outline-none"
                  />
                </div>
              ) : null}
            </div>
          </div>
        </div>

        {loading && (
          <div className="py-16 text-center text-[var(--fcp-text-secondary)] space-y-2">
            <div className="inline-block w-6 h-6 border-2 border-[#645DF6] border-t-transparent rounded-full animate-spin" />
            <p className="text-xs">Loading audit entries…</p>
          </div>
        )}

        {loadError && (
          <div className="p-4 rounded-xl bg-orange-950/40 border border-orange-500/40 text-orange-400 text-sm mb-6">
            <strong className="block font-semibold mb-1">
              {loadError.status === 403 ? 'Permission Denied' : 'Failed to load audit log'}
            </strong>
            <p className="text-xs text-orange-300">{loadError.message}</p>
            <button
              onClick={loadData}
              className="mt-3 px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-white text-xs font-medium border border-white/10 transition-colors cursor-pointer"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !loadError && sortedEntries !== null && sortedEntries.length === 0 && (
          <div className="py-16 text-center text-[var(--fcp-text-secondary)] border border-[var(--fcp-border-subtle)] rounded-xl bg-[var(--fcp-bg-surface)]">
            <p className="text-sm font-medium text-[var(--fcp-text-primary)]">No admin actions match the filter criteria.</p>
            <p className="text-xs text-[var(--fcp-text-tertiary)] mt-1">Try clearing or broadening your search filters.</p>
            {activeFilterCount > 0 && (
              <button
                onClick={clearAllFilters}
                className="mt-3 px-3 py-1.5 rounded-lg bg-[#645DF6] text-white text-xs font-medium cursor-pointer"
              >
                Clear all filters
              </button>
            )}
          </div>
        )}

        {sortedEntries && sortedEntries.length > 0 && (
          <div className="border border-[var(--fcp-border-subtle)] rounded-2xl overflow-hidden bg-[var(--fcp-bg-surface)] shadow-md">
            <table className="w-full text-sm border-collapse">
              <thead className="bg-[var(--fcp-bg-elevated)] border-b border-[var(--fcp-border-subtle)] text-xs uppercase tracking-wider text-[var(--fcp-text-secondary)]">
                <tr>
                  <th
                    className="text-left px-5 py-3.5 font-semibold cursor-pointer select-none hover:text-[var(--fcp-text-primary)] transition"
                    onClick={() => setSortBy(s => s === 'date-desc' ? 'date-asc' : 'date-desc')}
                    title="Click to toggle date sort"
                  >
                    <div className="flex items-center gap-1.5">
                      <span>When</span>
                      {sortBy.startsWith('date') && <span>{sortBy === 'date-desc' ? '↓' : '↑'}</span>}
                    </div>
                  </th>
                  <th
                    className="text-left px-4 py-3.5 font-semibold cursor-pointer select-none hover:text-[var(--fcp-text-primary)] transition"
                    onClick={() => setSortBy(s => s === 'actor-asc' ? 'actor-desc' : 'actor-asc')}
                    title="Click to toggle actor sort"
                  >
                    <div className="flex items-center gap-1.5">
                      <span>Actor</span>
                      {sortBy.startsWith('actor') && <span>{sortBy === 'actor-asc' ? '↑' : '↓'}</span>}
                    </div>
                  </th>
                  <th
                    className="text-left px-4 py-3.5 font-semibold cursor-pointer select-none hover:text-[var(--fcp-text-primary)] transition"
                    onClick={() => setSortBy(s => s === 'action-asc' ? 'action-desc' : 'action-asc')}
                    title="Click to toggle action sort"
                  >
                    <div className="flex items-center gap-1.5">
                      <span>Action</span>
                      {sortBy.startsWith('action') && <span>{sortBy === 'action-asc' ? '↑' : '↓'}</span>}
                    </div>
                  </th>
                  <th className="text-left px-4 py-3.5 font-semibold">Target</th>
                  <th className="text-left px-5 py-3.5 font-semibold">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                {sortedEntries.map(e => {
                  const ts = formatAuditTimestamp(e.created_at)
                  const isSelected = selectedRowId === e.id
                  return (
                    <tr
                      key={e.id}
                      onClick={() => setSelectedRowId(prev => prev === e.id ? null : e.id)}
                      className={`relative transition-all duration-150 cursor-pointer border-l-4 ${
                        isSelected
                          ? 'border-l-[#00C2BB] bg-[var(--fcp-bg-hover)] shadow-sm'
                          : 'border-l-transparent hover:border-l-[#645DF6] hover:bg-[var(--fcp-bg-hover)]'
                      } group`}
                    >
                      <td className="px-5 py-3.5 text-xs whitespace-nowrap">
                        <div className="font-medium text-[var(--fcp-text-primary)] group-hover:text-white transition-colors">
                          {ts.formattedDate}
                        </div>
                        <div className="text-[11px] font-mono text-[var(--fcp-text-tertiary)] flex items-center gap-1.5 mt-0.5">
                          <span>{ts.formattedTime}</span>
                          <span>•</span>
                          <span className="text-[#00C2BB]">{ts.relative}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3.5 text-xs">
                        <div className="text-[var(--fcp-text-primary)] font-medium truncate max-w-[200px]" title={e.actor_email}>
                          {e.actor_email}
                        </div>
                      </td>
                      <td className="px-4 py-3.5 text-xs whitespace-nowrap">
                        <span className="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-mono font-semibold bg-[#645DF6]/10 text-[#645DF6] border border-[#645DF6]/30 group-hover:bg-[#645DF6]/20 transition">
                          {e.action}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 text-xs font-mono text-[var(--fcp-text-secondary)] whitespace-nowrap max-w-[160px] truncate" title={e.target_id}>
                        {e.target_id || '—'}
                      </td>
                      <td className="px-5 py-3.5 text-xs">
                        <AuditDetailsFormatter details={e.details} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        <div className="flex items-center justify-between mt-8 text-xs text-[var(--fcp-text-secondary)]">
          <div>
            Showing entries {offset + 1} – {offset + (entries?.length ?? 0)}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setOffset(o => Math.max(0, o - PAGE_SIZE))}
              disabled={offset === 0}
              className="px-3.5 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-primary)] disabled:opacity-30 hover:bg-[var(--fcp-bg-hover)] transition cursor-pointer disabled:cursor-not-allowed"
            >
              ← Prev
            </button>
            <button
              onClick={() => setOffset(o => o + PAGE_SIZE)}
              disabled={!entries || entries.length < PAGE_SIZE}
              className="px-3.5 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] text-[var(--fcp-text-primary)] disabled:opacity-30 hover:bg-[var(--fcp-bg-hover)] transition cursor-pointer disabled:cursor-not-allowed"
            >
              Next →
            </button>
          </div>
        </div>
      </div>
    </Layout>
  )
}
