import { useEffect, useState, useMemo } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { Reveal } from '../components/Reveal'
import { retryJob, downloadURL } from '../api/jobs'
import type { Job } from '../api/jobs'
import { listAllJobsPaged, cancelJob, deleteJob, cancelAllJobs, resumeAllJobs, getSystemState, listDeletedJobs, listShares, revokeShare } from '../api/admin'
import type { UserInfo } from '../api/auth'
import type { DeletedJob, ReportShare } from '../api/admin'
import { parseAdminApiError } from '../components/adminError'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { facetsGetLogs } from '../api/facets'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'
import { BackButton } from '../components/BackButton'

const statusColor: Record<string, string> = {
  pending:   'text-slate-600 dark:text-gray-400',
  running:   'text-[#009b95] dark:text-[#00C2BB]',
  done:      'text-[#009b95] dark:text-[#00C2BB]',
  failed:    'text-orange-500 dark:text-orange-400',
  cancelled: 'text-slate-500 dark:text-gray-500',
}

const statusIcon: Record<string, string> = {
  done: '✓', failed: '✕', pending: '–', running: '●', cancelled: '⊘',
}

function fmtMoney(v: number | null) {
  if (v == null) return '—'
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function fmtWhen(iso: string) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })
}

type StatCardProps = Readonly<{ label: string; value: string | number; color?: string; delay?: number }>
export function StatCard({ label, value, color = 'text-slate-900 dark:text-white', delay = 0 }: StatCardProps) {
  return (
    <div
      className="bg-white dark:bg-white/[0.03] border border-slate-200 dark:border-white/10 rounded-xl px-5 py-4 card-lift anim-fade-in-up shadow-sm dark:shadow-none"
      style={{ animationDelay: `${delay}ms` }}
    >
      <p className="text-xs uppercase tracking-wider text-slate-500 dark:text-gray-400 mb-1.5 font-medium">{label}</p>
      <p className={`text-2xl font-bold tabular-nums ${color}`}>{value}</p>
    </div>
  )
}

// Standardized log line: parses timestamp + level, color-codes output.
function LogLine({ line, lineNum }: Readonly<{ line: string; lineNum?: number }>) {
  const tsMatch = line.match(/^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})/)
  const levelMatch = line.match(/\b(ERROR|WARN|WARNING|INFO|DEBUG|TRACE)\b/i)

  const timestamp = tsMatch ? tsMatch[1] : ''
  const level = levelMatch ? levelMatch[1].toUpperCase() : 'INFO'
  const message = line
    .replace(/^\[?[\d\-T:\s\.]+\]?\s*/, '')
    .replace(/\b(ERROR|WARN|WARNING|INFO|DEBUG|TRACE)\b/i, '')
    .trim()

  const levelStyles: Record<string, string> = {
    ERROR: 'text-rose-400 bg-rose-950/60 border-rose-800/40',
    WARN: 'text-amber-400 bg-amber-950/60 border-amber-800/40',
    WARNING: 'text-amber-400 bg-amber-950/60 border-amber-800/40',
    INFO: 'text-[#00C2BB] bg-teal-950/40 border-teal-800/40',
    DEBUG: 'text-slate-400 bg-slate-900 border-slate-700',
    TRACE: 'text-slate-500 bg-slate-900 border-slate-800',
  }

  return (
    <div className="flex items-start gap-2.5 text-xs font-mono leading-relaxed py-1 px-2 rounded hover:bg-slate-800/60 hover:border-l-2 hover:border-l-[#00C2BB] pl-1.5 transition-all duration-100 group">
      {lineNum != null && (
        <span className="text-slate-600 group-hover:text-slate-400 select-none text-[11px] shrink-0 font-mono w-9 text-right pr-2 border-r border-slate-800/80">
          {lineNum}
        </span>
      )}
      {timestamp && (
        <span className="text-slate-400 group-hover:text-slate-300 select-none text-[11px] shrink-0 font-mono w-[150px] min-w-[150px]">
          {timestamp}
        </span>
      )}
      <span className={`px-1.5 py-0.5 rounded text-[10px] uppercase font-bold shrink-0 border ${levelStyles[level] || levelStyles.INFO}`}>
        {level}
      </span>
      <span className="text-slate-200 break-all leading-relaxed flex-1 select-text">
        {message || line}
      </span>
    </div>
  )
}

export function AdminJobs({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('All Projections — admin')
  const nav = useNavigate()
  const searchParams = useMemo(() => new URLSearchParams(window.location.search), [])
  const [tab, setTab] = useState<'active' | 'deleted' | 'shares'>(() => {
    const t = searchParams.get('tab')
    if (t === 'deleted' || t === 'shares') return t
    return 'active'
  })
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [deletedJobs, setDeletedJobs] = useState<DeletedJob[] | null>(null)
  const [shares, setShares] = useState<ReportShare[] | null>(null)
  const [revokingToken, setRevokingToken] = useState<string | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(() => {
    const o = Number.parseInt(searchParams.get('offset') || '0', 10)
    return Number.isFinite(o) && o >= 0 ? o : 0
  })
  const [pageSize, setPageSize] = useState<number>(() => {
    const ps = Number.parseInt(searchParams.get('limit') || '50', 10)
    return [25, 50, 100, 200].includes(ps) ? ps : 50
  })
  const [sortField, setSortField] = useState<string>(() => searchParams.get('sort') || 'created_at')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(() => {
    const d = searchParams.get('dir')
    return d === 'asc' ? 'asc' : 'desc'
  })
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get('status') || '')
  const [fromDate, setFromDate] = useState(() => searchParams.get('from') || '')
  const [toDate, setToDate] = useState(() => searchParams.get('to') || '')
  const [ownerFilter, setOwnerFilter] = useState(() => searchParams.get('owner') || '')
  const [searchTags, setSearchTags] = useState<string[]>(() => {
    const raw = searchParams.get('q') || ''
    return raw ? raw.split(',').map(s => s.trim()).filter(Boolean) : []
  })
  const [query, setQuery] = useState<string>('')
  const [debouncedQuery, setDebouncedQuery] = useState<string>('')

  const addSearchTag = (term: string) => {
    const t = term.trim()
    if (!t) return
    if (!searchTags.includes(t)) {
      setSearchTags(prev => [...prev, t])
      setOffset(0)
    }
    setQuery('')
  }

  const removeSearchTag = (idx: number) => {
    setSearchTags(prev => prev.filter((_, i) => i !== idx))
    setOffset(0)
  }
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const [rowError, setRowError] = useState<Record<string, string>>({})
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [halted, setHalted] = useState(false)
  const [globalBusy, setGlobalBusy] = useState(false)
  const [globalError, setGlobalError] = useState<string | null>(null)
  const [apiPermissionError, setApiPermissionError] = useState<{ endpoint: string; status: number } | null>(null)

  // Debounce search query by 300ms
  useEffect(() => {
    const handle = setTimeout(() => {
      setDebouncedQuery(query.trim())
      setOffset(0)
    }, 300)
    return () => clearTimeout(handle)
  }, [query])

  // Sync state to URL search params
  useEffect(() => {
    const params = new URLSearchParams()
    if (tab !== 'active') params.set('tab', tab)
    if (searchTags.length > 0) params.set('q', searchTags.join(','))
    if (statusFilter) params.set('status', statusFilter)
    if (fromDate) params.set('from', fromDate)
    if (toDate) params.set('to', toDate)
    if (ownerFilter) params.set('owner', ownerFilter)
    if (sortField !== 'created_at') params.set('sort', sortField)
    if (sortDir !== 'desc') params.set('dir', sortDir)
    if (pageSize !== 50) params.set('limit', String(pageSize))
    if (offset > 0) params.set('offset', String(offset))

    const newQueryStr = params.toString()
    const newRelativePathQuery = window.location.pathname + (newQueryStr ? '?' + newQueryStr : '')
    window.history.replaceState(null, '', newRelativePathQuery)
  }, [tab, debouncedQuery, statusFilter, fromDate, toDate, sortField, sortDir, pageSize, offset])

  // Dialog States
  const [confirmStopAllOpen, setConfirmStopAllOpen] = useState(false)
  const [pendingCancelJob, setPendingCancelJob] = useState<{ id: string } | null>(null)
  const [pendingDeleteJob, setPendingDeleteJob] = useState<{ id: string; prospect: string } | null>(null)
  const [pendingRevokeToken, setPendingRevokeToken] = useState<string | null>(null)

  // Handle API errors: 401 → login, 403 → permission message, other → error status
  const handleApiError = (err: unknown, endpoint: string): void => {
    const parsed = parseAdminApiError(err, endpoint, () => nav('/login'))
    if (parsed.status === 401) return
    if (parsed.status === 403) {
      setApiPermissionError({ endpoint: parsed.endpoint, status: parsed.status })
      return
    }
    setGlobalError(parsed.message)
  }

  // Log Modal
  const [activeLogJob, setActiveLogJob] = useState<Job | null>(null)
  const [logCopied, setLogCopied] = useState(false)
  const [logLines, setLogLines] = useState<string[] | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)
  const [logSearch, setLogSearch] = useState('')
  const [logLevelFilter, setLogLevelFilter] = useState('ALL')

  const toggleGroup = (owner: string) => setCollapsed(prev => {
    const next = new Set(prev)
    next.has(owner) ? next.delete(owner) : next.add(owner)
    return next
  })

  const refreshState = () => getSystemState().then(s => setHalted(s.halted || s.stop_all)).catch(err => handleApiError(err, 'getSystemState'))

  const refreshActive = () => {
    refreshState()
    return listAllJobsPaged({
      limit: pageSize,
      offset,
      status: statusFilter || undefined,
      from: fromDate || undefined,
      to: toDate || undefined,
      q: debouncedQuery || undefined,
      sort: sortField,
      dir: sortDir,
    }).then(res => { setJobs(res.jobs); setTotal(res.total) }).catch(err => handleApiError(err, 'listAllJobsPaged'))
  }

  const refreshDeleted = () => {
    return listDeletedJobs(debouncedQuery || undefined).then(setDeletedJobs).catch(err => handleApiError(err, 'listDeletedJobs'))
  }

  const refreshShares = () => {
    return listShares().then(setShares).catch(err => handleApiError(err, 'listShares'))
  }

  const handleSort = (field: string) => {
    if (sortField === field) {
      setSortDir(prev => (prev === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDir('asc')
    }
    setOffset(0)
  }

  useEffect(() => {
    refreshActive()
    refreshDeleted()
    refreshShares()
    const ticker = setInterval(() => {
      refreshActive()
      if (tab === 'deleted') refreshDeleted()
      if (tab === 'shares') refreshShares()
    }, 25_000)
    return () => clearInterval(ticker)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset, pageSize, statusFilter, fromDate, toDate, debouncedQuery, sortField, sortDir, tab])

  // Open log drawer for job
  const handleOpenLogs = async (e: React.MouseEvent, j: Job) => {
    e.stopPropagation()
    setActiveLogJob(j)
    setLogsLoading(true)
    setLogLines(null)
    setLogCopied(false)
    setLogSearch('')
    setLogLevelFilter('ALL')
    try {
      const lines = await facetsGetLogs(j.id)
      setLogLines(lines)
    } catch {
      setLogLines(['Failed to load logs for this job.'])
    } finally {
      setLogsLoading(false)
    }
  }

  const filteredModalLogs = useMemo(() => {
    if (!logLines) return []
    return logLines.filter(line => {
      if (logLevelFilter !== 'ALL') {
        const match = line.match(/\b(ERROR|WARN|WARNING|INFO|DEBUG|TRACE)\b/i)
        const lvl = match ? match[1].toUpperCase() : 'INFO'
        if (logLevelFilter === 'WARN' && (lvl !== 'WARN' && lvl !== 'WARNING')) return false
        if (logLevelFilter === 'ERROR' && lvl !== 'ERROR') return false
        if (logLevelFilter === 'INFO' && lvl !== 'INFO') return false
        if (logLevelFilter === 'DEBUG' && lvl !== 'DEBUG') return false
      }
      if (logSearch.trim()) {
        if (!line.toLowerCase().includes(logSearch.toLowerCase().trim())) return false
      }
      return true
    })
  }, [logLines, logLevelFilter, logSearch])

  const handleCopyModalLogs = () => {
    if (!logLines || logLines.length === 0) return
    const linesToCopy = filteredModalLogs.length > 0 ? filteredModalLogs : logLines
    navigator.clipboard.writeText(linesToCopy.join('\n'))
    setLogCopied(true)
    toast.success(`Logs copied to clipboard (${linesToCopy.length} lines)`)
    setTimeout(() => setLogCopied(false), 2500)
  }

  const handleDownloadModalLogs = () => {
    if (!logLines || !activeLogJob) return
    const textToDownload = filteredModalLogs.length > 0 ? filteredModalLogs.join('\n') : logLines.join('\n')
    const blob = new Blob([textToDownload], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `job-${activeLogJob.id}.log`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
    toast.success(`Downloaded job-${activeLogJob.id.slice(0, 8)}.log`)
  }

  const executeStopAll = async () => {
    setGlobalBusy(true)
    setGlobalError(null)
    try {
      await cancelAllJobs()
      toast.error('All active runs cancelled and orchestration halted')
      setConfirmStopAllOpen(false)
      await refreshActive()
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : 'stop-all failed')
    } finally {
      setGlobalBusy(false)
    }
  }

  const handleResume = async () => {
    setGlobalBusy(true)
    setGlobalError(null)
    try {
      await resumeAllJobs()
      toast.success('Orchestration resumed')
      await refreshActive()
    } catch (err) {
      setGlobalError(err instanceof Error ? err.message : 'resume failed')
    } finally {
      setGlobalBusy(false)
    }
  }

  const handleRetry = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    setBusy(prev => new Set(prev).add(id))
    setRowError(prev => { const next = { ...prev }; delete next[id]; return next })
    try {
      await retryJob(id)
      toast.success(`Job ${id.slice(0, 8)} queued for retry`)
      await refreshActive()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'retry failed'
      setRowError(prev => ({ ...prev, [id]: msg }))
      toast.error(msg)
    } finally {
      setBusy(prev => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  const handleCancelClick = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    setPendingCancelJob({ id })
  }

  const executeCancel = async () => {
    if (!pendingCancelJob) return
    const id = pendingCancelJob.id
    setBusy(prev => new Set(prev).add(id))
    try {
      await cancelJob(id)
      toast.warning(`Job ${id.slice(0, 8)} cancelled`)
      setPendingCancelJob(null)
      await refreshActive()
    } catch (err) {
      setRowError(prev => ({ ...prev, [id]: err instanceof Error ? err.message : 'cancel failed' }))
    } finally {
      setBusy(prev => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  const handleDeleteClick = (e: React.MouseEvent, id: string, prospect: string) => {
    e.stopPropagation()
    setPendingDeleteJob({ id, prospect })
  }

  const executeDelete = async () => {
    if (!pendingDeleteJob) return
    const { id } = pendingDeleteJob
    setBusy(prev => new Set(prev).add(id))
    try {
      await deleteJob(id)
      toast.success(`Job archived to deleted_jobs table`)
      setPendingDeleteJob(null)
      await refreshActive()
      await refreshDeleted()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'delete failed'
      setRowError(prev => ({ ...prev, [id]: msg }))
      toast.error(msg)
    } finally {
      setBusy(prev => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  const uniqueOwners = useMemo(() => {
    if (!jobs) return []
    const set = new Set<string>()
    jobs.forEach(j => { if (j.owner) set.add(j.owner) })
    return Array.from(set).sort()
  }, [jobs])

  const activeSearchTerms = useMemo(() => {
    const list = [...searchTags]
    if (query.trim() && !searchTags.includes(query.trim())) {
      list.push(query.trim())
    }
    return list
  }, [searchTags, query])

  const filtered = useMemo(() => {
    if (!jobs) return []
    let list = jobs
    if (ownerFilter) {
      list = list.filter(j => j.owner.toLowerCase() === ownerFilter.toLowerCase())
    }
    if (activeSearchTerms.length > 0) {
      list = list.filter(j => {
        return activeSearchTerms.every(term => {
          const q = term.toLowerCase()
          return j.prospect.toLowerCase().includes(q) ||
            j.owner.toLowerCase().includes(q) ||
            j.id.toLowerCase().includes(q) ||
            (j.status && j.status.toLowerCase().includes(q))
        })
      })
    }
    return list
  }, [jobs, ownerFilter, activeSearchTerms])

  const hasActiveFilters = Boolean(searchTags.length > 0 || query || statusFilter || ownerFilter || fromDate || toDate)

  const clearAllFilters = () => {
    setSearchTags([])
    setQuery('')
    setDebouncedQuery('')
    setStatusFilter('')
    setOwnerFilter('')
    setFromDate('')
    setToDate('')
    setOffset(0)
  }

  const filteredDeleted = useMemo(() => deletedJobs ?? [], [deletedJobs])

  const filteredShares = useMemo(() => {
    if (!shares) return []
    const q = debouncedQuery.toLowerCase()
    if (!q) return shares
    return shares.filter(s =>
      s.job_id.toLowerCase().includes(q) ||
      s.created_by.toLowerCase().includes(q) ||
      s.token.toLowerCase().includes(q)
    )
  }, [shares, debouncedQuery])

  const handleRevokeShareClick = (token: string) => {
    setPendingRevokeToken(token)
  }

  const executeRevokeShare = async () => {
    if (!pendingRevokeToken) return
    const token = pendingRevokeToken
    setRevokingToken(token)
    try {
      await revokeShare(token)
      toast.success('Share link revoked')
      setPendingRevokeToken(null)
      await refreshShares()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Revoke failed')
    } finally {
      setRevokingToken(null)
    }
  }

  const totals = useMemo(() => {
    if (!jobs) return { total: 0, done: 0, failed: 0, running: 0, spend: 0 }
    return {
      total:   total || jobs.length,
      done:    jobs.filter(j => j.status === 'done').length,
      failed:  jobs.filter(j => j.status === 'failed').length,
      running: jobs.filter(j => j.status === 'running' || j.status === 'pending').length,
      spend:   jobs.reduce((acc, j) => acc + (j.aws_spend || 0), 0),
    }
  }, [jobs, total])

  const groups = useMemo(() => {
    const byOwner = new Map<string, Job[]>()
    for (const j of filtered) {
      const list = byOwner.get(j.owner)
      if (list) list.push(j)
      else byOwner.set(j.owner, [j])
    }
    return Array.from(byOwner.entries())
      .map(([owner, list]) => ({
        owner,
        jobs: list,
        done: list.filter(j => j.status === 'done').length,
        failed: list.filter(j => j.status === 'failed').length,
        spend: list.reduce((acc, j) => acc + (j.aws_spend || 0), 0),
        latest: list[0]?.created_at ?? '',
      }))
      .sort((a, b) => b.latest.localeCompare(a.latest))
  }, [filtered])

  const page = Math.floor(offset / pageSize) + 1
  const pageCount = Math.max(1, Math.ceil(total / pageSize))

  return (
    <Layout user={user}>
      <div className="max-w-6xl mx-auto px-6 py-8">
        <BackButton fallback="/" />

        {/* Top Header & Admin Links */}
        <div className="mb-6 anim-fade-in-up flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Enterprise Operations &amp; Reports</h1>
            <p className="text-sm text-gray-400 mt-1">
              Mission control for AWS → GCP projections, audit trails, and customer run archiving.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs flex-wrap">
            {!halted ? (
              <button
                onClick={() => setConfirmStopAllOpen(true)}
                disabled={globalBusy}
                className="px-3 py-1.5 rounded-lg border border-red-500/40 text-red-400 hover:bg-red-500/10 text-xs font-semibold disabled:opacity-40 transition-colors ml-2 cursor-pointer"
              >
                {globalBusy ? 'Stopping…' : '🛑 Stop All Runs'}
              </button>
            ) : (
              <button
                onClick={handleResume}
                disabled={globalBusy}
                className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold disabled:opacity-40 transition-colors ml-2 cursor-pointer"
              >
                {globalBusy ? 'Resuming…' : '▶ Resume Orchestration'}
              </button>
            )}
          </div>
        </div>

        {/* System Halt Alert */}
        {halted && (
          <div className="mb-6 p-4 rounded-xl bg-red-950/40 border border-red-500/40 flex items-center justify-between gap-4 anim-scale-in">
            <div className="flex items-center gap-3">
              <span className="text-2xl select-none" aria-hidden="true">🛑</span>
              <div>
                <strong className="text-red-400 font-semibold text-sm">Orchestration Halted (Kill-Switch Active)</strong>
                <p className="text-xs text-gray-300 mt-0.5">
                  All job runs are stopped and new runs/retries are blocked across the host until resumed.
                </p>
              </div>
            </div>
            <button
              onClick={handleResume}
              disabled={globalBusy}
              className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold disabled:opacity-40 whitespace-nowrap transition-colors cursor-pointer"
            >
              {globalBusy ? 'Resuming…' : 'Resume Orchestration'}
            </button>
          </div>
        )}

        {globalError && (
          <div className="mb-4 p-3 rounded-lg bg-orange-950/40 border border-orange-500/40 text-orange-400 text-xs">
            {globalError}
          </div>
        )}
        {apiPermissionError && (
          <div className="mb-4 p-3 rounded-lg bg-amber-950/40 border border-amber-500/40 text-amber-400 text-xs">
            Permission denied accessing <code>{apiPermissionError.endpoint}</code> (status {apiPermissionError.status}). Contact your administrator.
          </div>
        )}

        {/* Enterprise KPI Cards */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3.5 mb-7">
          <StatCard label="Total Reports" value={totals.total} color="text-white" delay={40} />
          <StatCard label="Analyzed Spend" value={fmtMoney(totals.spend)} color="text-[#00C2BB]" delay={80} />
          <StatCard label="Active Runs" value={totals.running} color="text-[#645DF6]" delay={120} />
          <StatCard label="Failed Runs" value={totals.failed} color="text-orange-400" delay={160} />
          <StatCard label="Archived / Deleted" value={deletedJobs ? deletedJobs.length : '—'} color="text-gray-400" delay={200} />
        </div>

        {/* View Tabs */}
        <div className="flex items-center gap-2 border-b border-white/10 mb-6">
          <button
            onClick={() => setTab('active')}
            className={`pb-3 px-4 text-sm font-semibold transition-colors relative cursor-pointer ${
              tab === 'active' ? 'text-[#00C2BB]' : 'text-gray-400 hover:text-white'
            }`}
          >
            Active Reports ({total || jobs?.length || 0})
            {tab === 'active' && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#00C2BB]" />
            )}
          </button>
          <button
            onClick={() => setTab('deleted')}
            className={`pb-3 px-4 text-sm font-semibold transition-colors relative cursor-pointer ${
              tab === 'deleted' ? 'text-[#009b95] dark:text-[#00C2BB]' : 'text-slate-500 dark:text-gray-400 hover:text-slate-900 dark:hover:text-white'
            }`}
          >
            Archived / Deleted Runs ({deletedJobs ? deletedJobs.length : '…'})
            {tab === 'deleted' && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#00C2BB]" />
            )}
          </button>
          <button
            onClick={() => setTab('shares')}
            className={`pb-3 px-4 text-sm font-semibold transition-colors relative cursor-pointer ${
              tab === 'shares' ? 'text-[#009b95] dark:text-[#00C2BB]' : 'text-slate-500 dark:text-gray-400 hover:text-slate-900 dark:hover:text-white'
            }`}
          >
            Shared Links ({shares ? shares.length : '…'})
            {tab === 'shares' && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#00C2BB]" />
            )}
          </button>
        </div>

        {/* Active Tab Controls */}
        {tab === 'active' && (
          <>
            <div className="mb-6 anim-fade-in flex flex-wrap items-center gap-3">
              <div className="relative flex items-center">
                <input
                  type="text"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' || e.key === ',') {
                      e.preventDefault()
                      addSearchTag(query)
                    }
                  }}
                  placeholder="Search & hit Enter to add tag…"
                  className="w-full sm:w-80 bg-white dark:bg-white/5 border border-slate-200 dark:border-white/10 rounded-lg px-3 py-2 pr-16 text-sm text-slate-900 dark:text-gray-100
                    placeholder:text-slate-400 dark:placeholder:text-gray-500 transition-colors duration-150 focus:border-[#645DF6] shadow-sm dark:shadow-none"
                />
                {query.trim() && (
                  <button
                    type="button"
                    onClick={() => addSearchTag(query)}
                    className="absolute right-1.5 px-2 py-1 bg-[#645DF6]/15 hover:bg-[#645DF6]/30 text-[#645DF6] dark:text-[#a5a0ff] text-xs font-semibold rounded cursor-pointer transition-colors"
                    title="Add filter tag"
                  >
                    + Add
                  </button>
                )}
              </div>
              <select
                value={statusFilter}
                onChange={e => { setStatusFilter(e.target.value); setOffset(0) }}
                className="bg-white dark:bg-[#12121a] border border-slate-200 dark:border-white/10 rounded-lg px-3 py-2 text-sm text-slate-800 dark:text-gray-200 shadow-sm dark:shadow-none cursor-pointer"
              >
                <option value="">All statuses</option>
                <option value="pending">Pending</option>
                <option value="running">Running</option>
                <option value="done">Done</option>
                <option value="failed">Failed</option>
                <option value="cancelled">Cancelled</option>
              </select>
              <select
                value={ownerFilter}
                onChange={e => { setOwnerFilter(e.target.value); setOffset(0) }}
                className="bg-white dark:bg-[#12121a] border border-slate-200 dark:border-white/10 rounded-lg px-3 py-2 text-sm text-slate-800 dark:text-gray-200 shadow-sm dark:shadow-none cursor-pointer"
                title="Filter by job owner / user"
              >
                <option value="">All Owners / Users</option>
                {uniqueOwners.map(o => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
              <label className="text-xs text-slate-600 dark:text-gray-400 flex items-center gap-1.5 font-medium">
                From:
                <input type="date" value={fromDate} onChange={e => { setFromDate(e.target.value); setOffset(0) }}
                  className="bg-white dark:bg-white/5 border border-slate-200 dark:border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-800 dark:text-gray-300 shadow-sm dark:shadow-none" />
              </label>
              <label className="text-xs text-slate-600 dark:text-gray-400 flex items-center gap-1.5 font-medium">
                To:
                <input type="date" value={toDate} onChange={e => { setToDate(e.target.value); setOffset(0) }}
                  className="bg-white dark:bg-white/5 border border-slate-200 dark:border-white/10 rounded-lg px-2 py-1.5 text-xs text-slate-800 dark:text-gray-300 shadow-sm dark:shadow-none" />
              </label>
              {hasActiveFilters && (
                <button
                  type="button"
                  onClick={clearAllFilters}
                  className="text-xs font-semibold px-2.5 py-1.5 rounded-lg text-red-400 hover:text-red-300 hover:bg-red-500/10 border border-red-500/30 transition-colors cursor-pointer"
                >
                  Clear filters
                </button>
              )}
              {debouncedQuery && (
                <span className="text-xs text-gray-400 ml-auto">
                  {total} results for &lsquo;{debouncedQuery}&rsquo;
                </span>
              )}
            </div>

            {/* Amazon-style Active Filter Pills */}
            {hasActiveFilters && (
              <div className="flex flex-wrap items-center gap-2 mb-6 p-3 rounded-xl bg-white/[0.02] border border-white/10 text-xs anim-fade-in">
                <span className="text-gray-400 font-semibold mr-1">Active filters:</span>
                {searchTags.map((tag, idx) => (
                  <span key={idx} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-[#645DF6]/15 text-[#a5a0ff] border border-[#645DF6]/30">
                    <span>Search: &ldquo;{tag}&rdquo;</span>
                    <button type="button" onClick={() => removeSearchTag(idx)} className="hover:text-white cursor-pointer ml-1 font-bold" title="Remove tag">✕</button>
                  </span>
                ))}
                {query.trim() && !searchTags.includes(query.trim()) && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-white/10 text-gray-300 border border-white/20 italic">
                    <span>Typing: &ldquo;{query.trim()}&rdquo; (Press Enter to add tag)</span>
                    <button type="button" onClick={() => addSearchTag(query)} className="text-[#00C2BB] font-bold ml-1 cursor-pointer">↵</button>
                  </span>
                )}
                {statusFilter && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-[#00C2BB]/15 text-[#00C2BB] border border-[#00C2BB]/30 capitalize">
                    <span>Status: {statusFilter}</span>
                    <button type="button" onClick={() => setStatusFilter('')} className="hover:text-white cursor-pointer ml-1 font-bold">✕</button>
                  </span>
                )}
                {ownerFilter && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-500/15 text-amber-300 border border-amber-500/30">
                    <span>Owner: {ownerFilter}</span>
                    <button type="button" onClick={() => setOwnerFilter('')} className="hover:text-white cursor-pointer ml-1 font-bold">✕</button>
                  </span>
                )}
                {fromDate && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                    <span>From: {fromDate}</span>
                    <button type="button" onClick={() => setFromDate('')} className="hover:text-white cursor-pointer ml-1 font-bold">✕</button>
                  </span>
                )}
                {toDate && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                    <span>To: {toDate}</span>
                    <button type="button" onClick={() => setToDate('')} className="hover:text-white cursor-pointer ml-1 font-bold">✕</button>
                  </span>
                )}
                <button
                  type="button"
                  onClick={clearAllFilters}
                  className="ml-auto text-xs font-semibold px-2.5 py-1 rounded-md text-red-400 hover:text-red-300 hover:bg-red-500/10 border border-red-500/30 transition-colors cursor-pointer"
                >
                  Clear all
                </button>
              </div>
            )}

            {jobs === null && (
              <div className="flex items-center gap-2 text-sm text-gray-500 py-16 justify-center">
                <span className="relative flex w-2 h-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[#645DF6] opacity-60" />
                  <span className="relative inline-flex rounded-full w-2 h-2 bg-[#645DF6]" />
                </span>{' '}
                Loading reports…
              </div>
            )}

            {jobs !== null && filtered.length === 0 && (
              <p className="text-sm text-gray-500 py-16 text-center">No reports match the current filter.</p>
            )}

            {groups.length > 0 && (
              <div className="space-y-4">
                {groups.map((g, gi) => {
                  const isCollapsed = collapsed.has(g.owner)
                  return (
                    <Reveal key={g.owner} delay={gi * 30}>
                      <div className="border border-slate-200 dark:border-white/10 rounded-xl overflow-hidden bg-white dark:bg-white/[0.01] shadow-sm dark:shadow-none">
                        <button
                          onClick={() => toggleGroup(g.owner)}
                          className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-slate-50/70 dark:bg-white/[0.03]
                            hover:bg-slate-100/80 dark:hover:bg-white/[0.05] transition-colors duration-100 text-left cursor-pointer"
                        >
                          <span className="flex items-center gap-2.5 min-w-0">
                            <span className={`text-slate-500 dark:text-gray-400 text-xs transition-transform duration-150 ${isCollapsed ? '' : 'rotate-90'}`}>▶</span>
                            <span className="font-semibold text-slate-900 dark:text-gray-100 truncate">{g.owner}</span>
                            <span className="text-xs px-2 py-0.5 rounded-full bg-slate-200/70 dark:bg-white/5 text-slate-700 dark:text-gray-300 font-medium">
                              {g.jobs.length} run{g.jobs.length === 1 ? '' : 's'}
                            </span>
                          </span>
                          <span className="flex items-center gap-4 text-xs whitespace-nowrap">
                            <span className="text-[#009b95] dark:text-[#00C2BB] font-semibold">{g.done} done</span>
                            {g.failed > 0 && <span className="text-orange-500 dark:text-orange-400 font-semibold">{g.failed} failed</span>}
                            <span className="text-slate-600 dark:text-gray-400 font-medium tabular-nums">Total AWS: {fmtMoney(g.spend)}</span>
                          </span>
                        </button>

                        {!isCollapsed && (
                          <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                              <thead className="bg-slate-50 dark:bg-white/[0.02] text-[11px] uppercase tracking-wider text-slate-600 dark:text-gray-400 border-b border-slate-200 dark:border-white/5 select-none font-semibold">
                                <tr>
                                  <th
                                    onClick={() => handleSort('created_at')}
                                    className="text-left px-4 py-2.5 cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors"
                                  >
                                    When {sortField === 'created_at' && (sortDir === 'asc' ? '▲' : '▼')}
                                  </th>
                                  <th
                                    onClick={() => handleSort('prospect')}
                                    className="text-left px-4 py-2.5 cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors"
                                  >
                                    Prospect {sortField === 'prospect' && (sortDir === 'asc' ? '▲' : '▼')}
                                  </th>
                                  <th
                                    onClick={() => handleSort('owner')}
                                    className="text-left px-4 py-2.5 cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors"
                                  >
                                    Owner {sortField === 'owner' && (sortDir === 'asc' ? '▲' : '▼')}
                                  </th>
                                  <th
                                    onClick={() => handleSort('status')}
                                    className="text-left px-4 py-2.5 cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors"
                                  >
                                    Status {sortField === 'status' && (sortDir === 'asc' ? '▲' : '▼')}
                                  </th>
                                  <th
                                    onClick={() => handleSort('aws_spend')}
                                    className="text-right px-4 py-2.5 cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors"
                                  >
                                    AWS Spend {sortField === 'aws_spend' && (sortDir === 'asc' ? '▲' : '▼')}
                                  </th>
                                  <th
                                    onClick={() => handleSort('gcp_od')}
                                    className="text-right px-4 py-2.5 cursor-pointer hover:text-slate-900 dark:hover:text-white transition-colors"
                                  >
                                    GCP (OD) {sortField === 'gcp_od' && (sortDir === 'asc' ? '▲' : '▼')}
                                  </th>
                                  <th className="text-right px-4 py-2.5">Ratio</th>
                                  <th className="text-center px-4 py-2.5">Exports</th>
                                  <th className="text-right px-4 py-2.5">Actions</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-100 dark:divide-white/5">
                                {g.jobs.map(j => {
                                  const hasRatio = j.aws_spend != null && j.aws_spend > 0 && j.gcp_od != null && j.gcp_od > 0
                                  const ratio = hasRatio ? j.gcp_od! / j.aws_spend! : null
                                  const isImplausible = ratio != null && (ratio < 0.5 || ratio > 1.5)

                                  return (
                                    <tr
                                      key={j.id}
                                      onClick={() => nav(`/jobs/${j.id}`)}
                                      className="cursor-pointer hover:bg-slate-50/80 dark:hover:bg-white/[0.04] transition-colors duration-100"
                                    >
                                      <td className="px-4 py-3 text-slate-500 dark:text-gray-400 whitespace-nowrap text-xs">
                                        {fmtWhen(j.created_at)}
                                      </td>
                                      <td className="px-4 py-3">
                                        <p className="text-slate-900 dark:text-gray-200 font-semibold">{j.prospect}</p>
                                        <p className="text-[11px] text-slate-500 dark:text-gray-500 font-mono mt-0.5">{j.id.slice(0, 8)}</p>
                                      </td>
                                      <td className="px-4 py-3 text-xs text-slate-800 dark:text-gray-300 whitespace-nowrap font-medium">
                                        {j.owner || '—'}
                                      </td>
                                      <td className={`px-4 py-3 ${statusColor[j.status] ?? 'text-slate-600 dark:text-gray-400'}`}>
                                        <span className="flex items-center gap-1.5 font-semibold text-xs">
                                          <span className={j.status === 'running' ? 'animate-pulse' : ''}>
                                            {statusIcon[j.status] ?? ''}
                                          </span>
                                          {j.status.charAt(0).toUpperCase() + j.status.slice(1)}
                                        </span>
                                        {j.status === 'failed' && j.error && (
                                          <p className="text-xs text-orange-500/90 mt-0.5 max-w-xs truncate" title={j.error}>
                                            {j.error}
                                          </p>
                                        )}
                                        {rowError[j.id] && (
                                          <p className="text-xs text-orange-500 mt-0.5">{rowError[j.id]}</p>
                                        )}
                                      </td>
                                      <td className="px-4 py-3 text-right text-slate-800 dark:text-gray-200 tabular-nums font-semibold">
                                        {fmtMoney(j.aws_spend)}
                                      </td>
                                      <td className="px-4 py-3 text-right text-slate-800 dark:text-gray-200 tabular-nums font-semibold">
                                        {fmtMoney(j.gcp_od ?? null)}
                                      </td>
                                      <td className="px-4 py-3 text-right tabular-nums text-xs whitespace-nowrap">
                                        {ratio != null ? (
                                          isImplausible ? (
                                            <span
                                              title={`Ratio ${ratio.toFixed(2)}x is outside expected 0.5x–1.5x range`}
                                              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30"
                                            >
                                              <span>⚠️</span>
                                              <span>{ratio.toFixed(2)}x</span>
                                            </span>
                                          ) : (
                                            <span className="text-slate-800 dark:text-gray-200 font-semibold">
                                              {ratio.toFixed(2)}x
                                            </span>
                                          )
                                        ) : (
                                          <span className="text-slate-400 dark:text-gray-500">—</span>
                                        )}
                                      </td>

                                      {/* Direct Download Exports */}
                                      <td className="px-4 py-3 text-center whitespace-nowrap" onClick={e => e.stopPropagation()}>
                                        {j.status === 'done' ? (
                                          <div className="inline-flex items-center gap-1.5 text-xs">
                                            <a
                                              href={downloadURL(j.id)}
                                              title="Download HTML Report"
                                              className="px-2 py-1 rounded bg-slate-100 hover:bg-[#645DF6]/15 dark:bg-white/5 dark:hover:bg-[#645DF6]/30 text-slate-700 hover:text-[#645DF6] dark:text-gray-300 dark:hover:text-white border border-slate-200 dark:border-white/10 font-medium transition-colors"
                                            >
                                              HTML
                                            </a>
                                            <a
                                              href={downloadURL(j.id, undefined, 'xlsx')}
                                              title="Download Excel Spreadsheet"
                                              className="px-2 py-1 rounded bg-slate-100 hover:bg-[#00C2BB]/20 dark:bg-white/5 dark:hover:bg-[#00C2BB]/30 text-slate-700 hover:text-[#009b95] dark:text-gray-300 dark:hover:text-white border border-slate-200 dark:border-white/10 font-medium transition-colors"
                                            >
                                              XLSX
                                            </a>
                                            <a
                                              href={downloadURL(j.id, undefined, 'csv')}
                                              title="Download CSV"
                                              className="px-2 py-1 rounded bg-slate-100 hover:bg-slate-200 dark:bg-white/5 dark:hover:bg-white/10 text-slate-700 hover:text-slate-900 dark:text-gray-300 dark:hover:text-white border border-slate-200 dark:border-white/10 font-medium transition-colors"
                                            >
                                              CSV
                                            </a>
                                            <a
                                              href={downloadURL(j.id, undefined, 'bill')}
                                              title="Download Original Input Bill"
                                              className="px-2 py-1 rounded bg-[#00C2BB]/15 hover:bg-[#00C2BB]/30 text-[#009b95] dark:text-[#00C2BB] font-semibold transition-colors border border-[#00C2BB]/40"
                                            >
                                              Bill
                                            </a>
                                          </div>
                                        ) : (
                                          <span className="text-xs text-slate-400 dark:text-gray-600">—</span>
                                        )}
                                      </td>

                                      {/* Action Buttons: Only [Logs] and [Delete] (or Retry / Cancel), no redundant Bill/HTML links */}
                                      <td className="px-4 py-3 text-right whitespace-nowrap" onClick={e => e.stopPropagation()}>
                                        <div className="flex items-center justify-end gap-2">
                                          <button
                                            onClick={e => handleOpenLogs(e, j)}
                                            title="View Live Execution Logs"
                                            className="text-xs px-2.5 py-1 rounded border border-slate-200 dark:border-white/10 hover:border-slate-400 dark:hover:border-white/30 text-slate-700 dark:text-gray-300 hover:text-slate-900 dark:hover:text-white bg-slate-50 dark:bg-transparent transition-colors cursor-pointer font-medium"
                                          >
                                            Logs
                                          </button>

                                          {j.status === 'failed' && (
                                            <button
                                              onClick={e => handleRetry(e, j.id)}
                                              disabled={busy.has(j.id)}
                                              className="text-xs font-semibold px-2.5 py-1 rounded bg-[#645DF6]/20 hover:bg-[#645DF6]/40 text-[#645DF6] dark:text-[#8981ff] transition-colors cursor-pointer disabled:opacity-40"
                                            >
                                              {busy.has(j.id) ? '…' : 'Retry'}
                                            </button>
                                          )}

                                          {(j.status === 'running' || j.status === 'pending') && (
                                            <button
                                              onClick={e => handleCancelClick(e, j.id)}
                                              disabled={busy.has(j.id)}
                                              className="text-xs font-semibold px-2.5 py-1 rounded bg-orange-500/20 hover:bg-orange-500/40 text-orange-600 dark:text-orange-400 transition-colors cursor-pointer disabled:opacity-40"
                                            >
                                              {busy.has(j.id) ? '…' : 'Cancel'}
                                            </button>
                                          )}

                                          {(j.status === 'done' || j.status === 'failed' || j.status === 'cancelled') && (
                                            <button
                                              onClick={e => handleDeleteClick(e, j.id, j.prospect)}
                                              disabled={busy.has(j.id)}
                                              title="Move to Deleted Jobs Archive"
                                              className="text-xs px-2.5 py-1 rounded text-slate-500 dark:text-gray-500 hover:text-red-500 hover:bg-red-500/10 transition-colors cursor-pointer disabled:opacity-40 font-medium"
                                            >
                                              {busy.has(j.id) ? '…' : 'Delete'}
                                            </button>
                                          )}
                                        </div>
                                      </td>
                                    </tr>
                                  )
                                })}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    </Reveal>
                  )
                })}
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-4 mt-8 text-sm">
              <div className="flex items-center gap-2 text-xs text-slate-600 dark:text-gray-400">
                <span>Rows per page:</span>
                <select
                  value={pageSize}
                  onChange={e => {
                    const newSize = Number.parseInt(e.target.value, 10)
                    setPageSize(newSize)
                    setOffset(0)
                  }}
                  className="bg-white dark:bg-[#12121a] border border-slate-200 dark:border-white/10 rounded px-2 py-1 text-xs text-slate-800 dark:text-gray-200 cursor-pointer shadow-sm dark:shadow-none"
                >
                  <option value={25}>25</option>
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                  <option value={200}>200</option>
                </select>
              </div>

              <div className="flex items-center gap-4">
                <button
                  onClick={() => setOffset(o => Math.max(0, o - pageSize))}
                  disabled={offset === 0}
                  className="px-3 py-1.5 rounded-lg border border-slate-200 dark:border-white/10 text-slate-700 dark:text-gray-300 disabled:opacity-30 hover:bg-slate-100 dark:hover:bg-white/5 cursor-pointer font-medium"
                >
                  ← Prev
                </button>
                <span className="text-slate-600 dark:text-gray-400 text-xs tabular-nums font-medium">
                  Page {page} of {pageCount} · {total} jobs
                </span>
                <button
                  onClick={() => setOffset(o => o + pageSize)}
                  disabled={offset + pageSize >= total}
                  className="px-3 py-1.5 rounded-lg border border-slate-200 dark:border-white/10 text-slate-700 dark:text-gray-300 disabled:opacity-30 hover:bg-slate-100 dark:hover:bg-white/5 cursor-pointer font-medium"
                >
                  Next →
                </button>
              </div>
            </div>
          </>
        )}

        {/* Deleted / Archived Tab */}
        {tab === 'deleted' && (
          <div className="anim-fade-in space-y-4">
            <div className="flex items-center justify-between gap-4 flex-wrap mb-4">
              <input
                type="text"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="Search deleted jobs by prospect, owner, or deleted by…"
                className="w-full sm:w-80 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm
                  placeholder:text-gray-500 transition-colors duration-150 focus:border-[#645DF6]"
              />
              <span className="text-xs text-gray-400">
                Audit Trail: Jobs removed from customer view are preserved here with deleter identity and timestamp.
              </span>
            </div>

            {deletedJobs === null && (
              <div className="text-sm text-gray-500 py-16 text-center">Loading deleted jobs archive…</div>
            )}

            {deletedJobs !== null && filteredDeleted.length === 0 && (
              <div className="text-center py-16 border border-white/10 rounded-xl bg-white/[0.01]">
                <p className="text-gray-300 font-medium">No deleted jobs recorded</p>
                <p className="text-xs text-gray-500 mt-1">When customers or admins delete a run, it is archived here for compliance.</p>
              </div>
            )}

            {deletedJobs !== null && filteredDeleted.length > 0 && (
              <div className="border border-white/10 rounded-xl overflow-hidden bg-white/[0.01]">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-white/[0.02] text-[11px] uppercase tracking-wider text-gray-400 border-b border-white/5">
                      <tr>
                        <th className="text-left px-4 py-2.5">Deleted At</th>
                        <th className="text-left px-4 py-2.5">Deleted By</th>
                        <th className="text-left px-4 py-2.5">Prospect</th>
                        <th className="text-left px-4 py-2.5">Owner</th>
                        <th className="text-left px-4 py-2.5">Status Prior</th>
                        <th className="text-right px-4 py-2.5">AWS Spend</th>
                        <th className="text-left px-4 py-2.5">Job ID</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                      {filteredDeleted.map(d => (
                        <tr key={d.id} className="hover:bg-white/[0.02] transition-colors">
                          <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                            {fmtWhen(d.deleted_at)}
                          </td>
                          <td className="px-4 py-3 text-xs whitespace-nowrap">
                            <span className="px-2 py-0.5 rounded-full bg-red-500/10 text-red-300 border border-red-500/20 font-mono">
                              {d.deleted_by || 'unknown'}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-gray-200 font-medium">
                            {d.prospect}
                          </td>
                          <td className="px-4 py-3 text-xs text-gray-400">
                            {d.owner}
                          </td>
                          <td className="px-4 py-3 text-xs">
                            <span className={`capitalize ${statusColor[d.status] || 'text-gray-400'}`}>
                              {d.status}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right text-gray-300 tabular-nums text-xs">
                            {fmtMoney(d.aws_spend)}
                          </td>
                          <td className="px-4 py-3 font-mono text-[11px] text-gray-500 whitespace-nowrap">
                            {d.id}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Shared Links Tab Controls & Table */}
        {tab === 'shares' && (
          <div className="anim-fade-in">
            <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
              <input
                type="text"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="Search shares by job ID, creator, or token…"
                className="w-full sm:w-80 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm
                  placeholder:text-gray-500 transition-colors duration-150 focus:border-[#645DF6]"
              />
              <span className="text-xs text-gray-400">
                Audit &amp; Access: Active and expired prospect share links. Revoking invalidates access immediately.
              </span>
            </div>

            {shares === null && (
              <div className="text-sm text-gray-500 py-16 text-center">Loading shared report links…</div>
            )}

            {shares !== null && filteredShares.length === 0 && (
              <div className="text-center py-16 border border-white/10 rounded-xl bg-white/[0.01]">
                <p className="text-gray-300 font-medium">No shared report links found</p>
                <p className="text-xs text-gray-500 mt-1">When users generate a public link from a completed report, it appears here.</p>
              </div>
            )}

            {shares !== null && filteredShares.length > 0 && (
              <div className="border border-white/10 rounded-xl overflow-hidden bg-white/[0.01]">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-white/[0.02] text-[11px] uppercase tracking-wider text-gray-400 border-b border-white/5">
                      <tr>
                        <th className="text-left px-4 py-2.5">Masked Token</th>
                        <th className="text-left px-4 py-2.5">Job ID</th>
                        <th className="text-left px-4 py-2.5">Created By</th>
                        <th className="text-left px-4 py-2.5">Created At</th>
                        <th className="text-left px-4 py-2.5">Expires At</th>
                        <th className="text-left px-4 py-2.5">Status</th>
                        <th className="text-right px-4 py-2.5">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                      {filteredShares.map(s => (
                        <tr key={s.token} className="hover:bg-white/[0.02] transition-colors">
                          <td className="px-4 py-3 font-mono text-xs text-gray-300 whitespace-nowrap">
                            {s.token}
                          </td>
                          <td className="px-4 py-3 text-xs whitespace-nowrap">
                            <Link to={`/jobs/${s.job_id}`} className="text-[#00C2BB] hover:underline font-mono">
                              {s.job_id}
                            </Link>
                          </td>
                          <td className="px-4 py-3 text-xs text-gray-300">
                            {s.created_by || '—'}
                          </td>
                          <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                            {fmtWhen(s.created_at)}
                          </td>
                          <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                            {s.expires_at ? fmtWhen(s.expires_at) : 'Never'}
                          </td>
                          <td className="px-4 py-3 text-xs whitespace-nowrap">
                            {s.expired ? (
                              <span className="px-2 py-0.5 rounded-full bg-orange-500/10 text-orange-400 border border-orange-500/20 text-[11px]">
                                Expired
                              </span>
                            ) : (
                              <span className="px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[11px]">
                                Active
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-right whitespace-nowrap">
                            <button
                              onClick={() => handleRevokeShareClick(s.token)}
                              disabled={revokingToken === s.token}
                              className="px-2.5 py-1 rounded bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 text-xs font-medium transition-colors disabled:opacity-40 cursor-pointer"
                            >
                              {revokingToken === s.token ? 'Revoking…' : 'Revoke'}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Live Logs Slide-out / Modal */}
        {activeLogJob && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md anim-fade-in"
            onClick={(e) => { if (e.target === e.currentTarget) setActiveLogJob(null) }}
            role="dialog"
            aria-modal="true"
          >
            <div
              className="bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-default)] rounded-2xl w-full max-w-5xl max-h-[85vh] flex flex-col shadow-2xl overflow-hidden anim-scale-in text-[var(--fcp-text-primary)]"
            >
              <div className="px-6 py-4 border-b border-[var(--fcp-border-subtle)] flex items-center justify-between gap-4 bg-[var(--fcp-bg-raised)]">
                <div>
                  <div className="flex items-center gap-2.5 flex-wrap">
                    <h3 className="font-bold text-[var(--fcp-text-primary)] text-base">{activeLogJob.prospect}</h3>
                    <span className="text-xs px-2.5 py-0.5 rounded-md bg-[var(--fcp-bg-subtle)] border border-[var(--fcp-border-subtle)] font-mono text-[var(--fcp-text-secondary)]">
                      {activeLogJob.id}
                    </span>
                    <span className={`text-xs font-semibold px-2 py-0.5 rounded-md border ${
                      activeLogJob.status === 'done'
                        ? 'text-emerald-500 bg-emerald-500/10 border-emerald-500/30'
                        : activeLogJob.status === 'running'
                        ? 'text-cyan-500 bg-cyan-500/10 border-cyan-500/30'
                        : activeLogJob.status === 'failed'
                        ? 'text-rose-500 bg-rose-500/10 border-rose-500/30'
                        : 'text-gray-400 bg-gray-500/10 border-gray-500/30'
                    }`}>
                      ● {activeLogJob.status}
                    </span>
                  </div>
                  <p className="text-xs text-[var(--fcp-text-secondary)] mt-1 font-medium">Live execution console and system agent logs</p>
                </div>
                <div className="flex items-center gap-2">
                  {logLines && logLines.length > 0 && (
                    <>
                      <button
                        type="button"
                        onClick={handleCopyModalLogs}
                        className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border font-semibold transition-all duration-150 cursor-pointer ${
                          logCopied
                            ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-400'
                            : 'bg-white/5 hover:bg-white/10 border-white/15 text-slate-200 hover:text-white'
                        }`}
                        title="Copy logs to clipboard"
                      >
                        {logCopied ? (
                          <>
                            <svg className="w-3.5 h-3.5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                            </svg>
                            <span>Copied!</span>
                          </>
                        ) : (
                          <>
                            <svg className="w-3.5 h-3.5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" />
                            </svg>
                            <span>Copy Log</span>
                          </>
                        )}
                      </button>

                      <button
                        type="button"
                        onClick={handleDownloadModalLogs}
                        className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-white/15 bg-white/5 hover:bg-white/10 text-slate-200 hover:text-white transition-colors cursor-pointer font-medium"
                        title="Download raw log file"
                      >
                        <svg className="w-3.5 h-3.5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                        </svg>
                        <span>Download</span>
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    onClick={() => setActiveLogJob(null)}
                    className="text-slate-400 hover:text-white text-lg px-2 py-1 rounded transition-colors cursor-pointer"
                    title="Close modal"
                  >
                    ✕
                  </button>
                </div>
              </div>

              {/* In-modal Filter Toolbar */}
              {logLines && logLines.length > 0 && (
                <div className="px-6 py-2.5 bg-[#090d16] border-b border-slate-800 flex flex-wrap items-center justify-between gap-3 text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="text-slate-400 font-semibold mr-1">Level:</span>
                    {(['ALL', 'INFO', 'WARN', 'ERROR', 'DEBUG'] as const).map(lvl => {
                      const active = logLevelFilter === lvl
                      return (
                        <button
                          key={lvl}
                          type="button"
                          onClick={() => setLogLevelFilter(lvl)}
                          className={`px-2.5 py-1 rounded text-[11px] font-semibold transition cursor-pointer border ${
                            active
                              ? 'bg-[#645DF6] text-white border-[#645DF6]'
                              : 'text-slate-400 border-slate-800 hover:border-slate-700 hover:text-slate-200 bg-slate-900/60'
                          }`}
                        >
                          {lvl}
                        </button>
                      )
                    })}
                  </div>

                  <div className="flex items-center gap-3 flex-1 max-w-sm ml-auto">
                    <input
                      type="text"
                      placeholder="Filter log lines…"
                      value={logSearch}
                      onChange={e => setLogSearch(e.target.value)}
                      className="w-full bg-[#0d121f] border border-slate-800 rounded-lg px-3 py-1 text-xs text-slate-200 placeholder-slate-500 outline-none focus:border-[#645DF6]"
                    />
                    <span className="text-[11px] text-slate-400 whitespace-nowrap tabular-nums">
                      {filteredModalLogs.length} / {logLines.length}
                    </span>
                  </div>
                </div>
              )}

              <div
                data-terminal
                style={{ backgroundColor: '#07090e', color: '#f8fafc' }}
                className="dark-console p-4 sm:p-5 overflow-auto flex-1 font-mono text-xs leading-relaxed select-text shadow-inner"
              >
                {logsLoading && (
                  <div className="text-slate-400 flex items-center gap-2 p-3">
                    <span className="w-2 h-2 rounded-full bg-[#00C2BB] animate-ping" />
                    <span>Loading live execution logs…</span>
                  </div>
                )}
                {!logsLoading && (!logLines || logLines.length === 0) && (
                  <p className="text-slate-500 p-3 italic">No logs generated for this run yet.</p>
                )}
                {!logsLoading && logLines && logLines.length > 0 && filteredModalLogs.length === 0 && (
                  <p className="text-slate-500 p-3 italic">No log entries matched your filter &ldquo;{logSearch || logLevelFilter}&rdquo;.</p>
                )}
                {!logsLoading && filteredModalLogs.length > 0 && (
                  <div className="space-y-0.5 p-1">
                    {filteredModalLogs.map((line, i) => (
                      <LogLine key={i} line={line} lineNum={i + 1} />
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Global Stop-All Emergency Dialog with typed confirmation */}
        <ConfirmDialog
          open={confirmStopAllOpen}
          onOpenChange={setConfirmStopAllOpen}
          title="EMERGENCY: Stop All Runs"
          description="This will cancel every active run and halt orchestration across the host. New jobs will be prevented from starting until manually resumed."
          confirmLabel="Halt Orchestration"
          destructive
          loading={globalBusy}
          requireTypedWord="STOP"
          onConfirm={executeStopAll}
        />

        {/* Cancel Individual Job Dialog */}
        <ConfirmDialog
          open={pendingCancelJob !== null}
          onOpenChange={(open) => {
            if (!open) setPendingCancelJob(null)
          }}
          title="Cancel job"
          description="Cancel this running job? Active child processes will be terminated."
          confirmLabel="Cancel job"
          destructive
          loading={pendingCancelJob ? busy.has(pendingCancelJob.id) : false}
          onConfirm={executeCancel}
        />

        {/* Archive & Delete Job Dialog */}
        <ConfirmDialog
          open={pendingDeleteJob !== null}
          onOpenChange={(open) => {
            if (!open) setPendingDeleteJob(null)
          }}
          title="Archive & delete job"
          description={`Archive & delete job for "${pendingDeleteJob?.prospect}"? It will be safely moved to Deleted Jobs archive.`}
          confirmLabel="Delete job"
          destructive
          loading={pendingDeleteJob ? busy.has(pendingDeleteJob.id) : false}
          onConfirm={executeDelete}
        />

        {/* Revoke Share Link Dialog */}
        <ConfirmDialog
          open={pendingRevokeToken !== null}
          onOpenChange={(open) => {
            if (!open) setPendingRevokeToken(null)
          }}
          title="Revoke share link"
          description="Revoke this share link immediately? Anyone with this URL will see 404."
          confirmLabel="Revoke link"
          destructive
          loading={pendingRevokeToken !== null && revokingToken === pendingRevokeToken}
          onConfirm={executeRevokeShare}
        />

      </div>
    </Layout>
  )
}
