import { useEffect, useState, useMemo } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Nav } from '../components/Nav'
import { Reveal } from '../components/Reveal'
import { retryJob, downloadURL } from '../api/jobs'
import type { Job } from '../api/jobs'
import { listAllJobsPaged, cancelJob, deleteJob, cancelAllJobs, resumeAllJobs, getSystemState, listDeletedJobs } from '../api/admin'
import type { UserInfo } from '../api/auth'
import type { DeletedJob } from '../api/admin'
import { facetsGetLogs } from '../api/facets'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'

const statusColor: Record<string, string> = {
  pending:   'text-gray-400',
  running:   'text-[#00C2BB]',
  done:      'text-[#00C2BB]',
  failed:    'text-orange-400',
  cancelled: 'text-gray-500',
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
export function StatCard({ label, value, color = 'text-white', delay = 0 }: StatCardProps) {
  return (
    <div
      className="bg-white/[0.03] border border-white/10 rounded-xl px-5 py-4 card-lift anim-fade-in-up"
      style={{ animationDelay: `${delay}ms` }}
    >
      <p className="text-xs uppercase tracking-wider text-gray-400 mb-1.5">{label}</p>
      <p className={`text-2xl font-bold tabular-nums ${color}`}>{value}</p>
    </div>
  )
}

const PAGE_SIZE = 25

export function AdminJobs({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('All reports — admin')
  const nav = useNavigate()
  const [tab, setTab] = useState<'active' | 'deleted'>('active')
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [deletedJobs, setDeletedJobs] = useState<DeletedJob[] | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [statusFilter, setStatusFilter] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [query, setQuery] = useState<string>(() => new URLSearchParams(window.location.search).get('q') || '')
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const [rowError, setRowError] = useState<Record<string, string>>({})
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [halted, setHalted] = useState(false)
  const [globalBusy, setGlobalBusy] = useState(false)
  const [globalError, setGlobalError] = useState<string | null>(null)

  // Log Modal
  const [activeLogJob, setActiveLogJob] = useState<Job | null>(null)
  const [logLines, setLogLines] = useState<string[] | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)

  const toggleGroup = (owner: string) => setCollapsed(prev => {
    const next = new Set(prev)
    next.has(owner) ? next.delete(owner) : next.add(owner)
    return next
  })

  const refreshState = () => getSystemState().then(s => setHalted(s.halted || s.stop_all)).catch(() => {})

  const refreshActive = () => {
    refreshState()
    return listAllJobsPaged({
      limit: PAGE_SIZE,
      offset,
      status: statusFilter || undefined,
      from: fromDate || undefined,
      to: toDate || undefined,
    }).then(res => { setJobs(res.jobs); setTotal(res.total) })
  }

  const refreshDeleted = () => {
    return listDeletedJobs().then(setDeletedJobs).catch(() => setDeletedJobs([]))
  }


  useEffect(() => {
    refreshActive()
    refreshDeleted()
    const ticker = setInterval(() => {
      refreshActive()
      if (tab === 'deleted') refreshDeleted()
    }, 25_000)
    return () => clearInterval(ticker)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset, statusFilter, fromDate, toDate, tab])

  // Open log drawer for job
  const handleOpenLogs = async (e: React.MouseEvent, j: Job) => {
    e.stopPropagation()
    setActiveLogJob(j)
    setLogsLoading(true)
    setLogLines(null)
    try {
      const lines = await facetsGetLogs(j.id)
      setLogLines(lines)
    } catch {
      setLogLines(['Failed to load logs for this job.'])
    } finally {
      setLogsLoading(false)
    }
  }

  const handleStopAll = async () => {
    if (!confirm('EMERGENCY: Stop ALL running jobs across the host and halt orchestration?')) return
    if (!confirm('DOUBLE-CONFIRM: This will cancel every active run and prevent any new jobs from starting until resumed. Proceed?')) return
    setGlobalBusy(true)
    setGlobalError(null)
    try {
      await cancelAllJobs()
      toast.error('All active runs cancelled and orchestration halted')
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

  const handleCancel = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    if (!confirm('Cancel this job?')) return
    setBusy(prev => new Set(prev).add(id))
    try {
      await cancelJob(id)
      toast.warning(`Job ${id.slice(0, 8)} cancelled`)
      await refreshActive()
    } catch (err) {
      setRowError(prev => ({ ...prev, [id]: err instanceof Error ? err.message : 'cancel failed' }))
    } finally {
      setBusy(prev => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  const handleDelete = async (e: React.MouseEvent, id: string, prospect: string) => {
    e.stopPropagation()
    if (!confirm(`Archive & delete job for "${prospect}"? It will be safely moved to Deleted Jobs archive.`)) return
    setBusy(prev => new Set(prev).add(id))
    try {
      await deleteJob(id)
      toast.success(`Job archived to deleted_jobs table`)
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

  const filtered = useMemo(() => {
    if (!jobs) return []
    const q = query.trim().toLowerCase()
    if (!q) return jobs
    return jobs.filter(j =>
      j.owner.toLowerCase().includes(q) ||
      j.prospect.toLowerCase().includes(q) ||
      j.status.toLowerCase().includes(q) ||
      j.id.toLowerCase().includes(q)
    )
  }, [jobs, query])

  const filteredDeleted = useMemo(() => {
    if (!deletedJobs) return []
    const q = query.trim().toLowerCase()
    if (!q) return deletedJobs
    return deletedJobs.filter(j =>
      j.owner.toLowerCase().includes(q) ||
      j.prospect.toLowerCase().includes(q) ||
      j.deleted_by.toLowerCase().includes(q) ||
      j.id.toLowerCase().includes(q)
    )
  }, [deletedJobs, query])

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

  const page = Math.floor(offset / PAGE_SIZE) + 1
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="min-h-screen text-white bg-[#0a0a0f]">
      <Nav user={user} />
      <div className="max-w-6xl mx-auto px-6 py-8">

        {/* Top Header & Admin Links */}
        <div className="mb-6 anim-fade-in-up flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Enterprise Operations &amp; Reports</h1>
            <p className="text-sm text-gray-400 mt-1">
              Mission control for AWS → GCP projections, audit trails, and customer run archiving.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs flex-wrap">
            <Link to="/facets" className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-white font-medium border border-white/10 transition-colors">
              ⚡ Logs &amp; Engine
            </Link>
            <Link to="/admin/users" className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-gray-300 hover:text-white font-medium border border-white/10 transition-colors">
              Users
            </Link>
            <Link to="/admin/catalog" className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-gray-300 hover:text-white font-medium border border-white/10 transition-colors">
              Catalog
            </Link>
            <Link to="/admin/analytics" className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-gray-300 hover:text-white font-medium border border-white/10 transition-colors">
              Analytics
            </Link>
            <Link to="/admin/audit" className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-gray-300 hover:text-white font-medium border border-white/10 transition-colors">
              Audit
            </Link>
            {!halted ? (
              <button
                onClick={handleStopAll}
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
              tab === 'deleted' ? 'text-[#00C2BB]' : 'text-gray-400 hover:text-white'
            }`}
          >
            Archived / Deleted Runs ({deletedJobs ? deletedJobs.length : '…'})
            {tab === 'deleted' && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#00C2BB]" />
            )}
          </button>
        </div>

        {/* Active Tab Controls */}
        {tab === 'active' && (
          <>
            <div className="mb-6 anim-fade-in flex flex-wrap items-center gap-3">
              <input
                type="text"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="Search prospect, owner, ID, status…"
                className="w-full sm:w-80 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm
                  placeholder:text-gray-500 transition-colors duration-150 focus:border-[#645DF6]"
              />
              <select
                value={statusFilter}
                onChange={e => { setStatusFilter(e.target.value); setOffset(0) }}
                className="bg-[#12121a] border border-white/10 rounded-lg px-3 py-2 text-sm text-gray-200"
              >
                <option value="">All statuses</option>
                <option value="pending">Pending</option>
                <option value="running">Running</option>
                <option value="done">Done</option>
                <option value="failed">Failed</option>
                <option value="cancelled">Cancelled</option>
              </select>
              <label className="text-xs text-gray-400 flex items-center gap-1.5">
                From:
                <input type="date" value={fromDate} onChange={e => { setFromDate(e.target.value); setOffset(0) }}
                  className="bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-gray-300" />
              </label>
              <label className="text-xs text-gray-400 flex items-center gap-1.5">
                To:
                <input type="date" value={toDate} onChange={e => { setToDate(e.target.value); setOffset(0) }}
                  className="bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-xs text-gray-300" />
              </label>
              {query && (
                <span className="text-xs text-gray-400 ml-auto">
                  {filtered.length} matches
                </span>
              )}
            </div>

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
                      <div className="border border-white/10 rounded-xl overflow-hidden bg-white/[0.01]">
                        <button
                          onClick={() => toggleGroup(g.owner)}
                          className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-white/[0.03]
                            hover:bg-white/[0.05] transition-colors duration-100 text-left cursor-pointer"
                        >
                          <span className="flex items-center gap-2.5 min-w-0">
                            <span className={`text-gray-500 text-xs transition-transform duration-150 ${isCollapsed ? '' : 'rotate-90'}`}>▶</span>
                            <span className="font-semibold text-gray-100 truncate">{g.owner}</span>
                            <span className="text-xs px-2 py-0.5 rounded-full bg-white/5 text-gray-400">
                              {g.jobs.length} run{g.jobs.length === 1 ? '' : 's'}
                            </span>
                          </span>
                          <span className="flex items-center gap-4 text-xs whitespace-nowrap">
                            <span className="text-[#00C2BB] font-medium">{g.done} done</span>
                            {g.failed > 0 && <span className="text-orange-400 font-medium">{g.failed} failed</span>}
                            <span className="text-gray-400 tabular-nums">Total AWS: {fmtMoney(g.spend)}</span>
                          </span>
                        </button>

                        {!isCollapsed && (
                          <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                              <thead className="bg-white/[0.02] text-[11px] uppercase tracking-wider text-gray-400 border-b border-white/5">
                                <tr>
                                  <th className="text-left px-4 py-2.5">When</th>
                                  <th className="text-left px-4 py-2.5">Prospect</th>
                                  <th className="text-left px-4 py-2.5">Status</th>
                                  <th className="text-right px-4 py-2.5">AWS Spend</th>
                                  <th className="text-center px-4 py-2.5">Exports</th>
                                  <th className="text-right px-4 py-2.5">Actions</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-white/5">
                                {g.jobs.map(j => (
                                  <tr
                                    key={j.id}
                                    onClick={() => nav(`/jobs/${j.id}`)}
                                    className="cursor-pointer hover:bg-white/[0.04] transition-colors duration-100"
                                  >
                                    <td className="px-4 py-3 text-gray-400 whitespace-nowrap text-xs">
                                      {fmtWhen(j.created_at)}
                                    </td>
                                    <td className="px-4 py-3">
                                      <p className="text-gray-200 font-medium">{j.prospect}</p>
                                      <p className="text-[11px] text-gray-500 font-mono mt-0.5">{j.id.slice(0, 8)}</p>
                                    </td>
                                    <td className={`px-4 py-3 ${statusColor[j.status] ?? 'text-gray-400'}`}>
                                      <span className="flex items-center gap-1.5 font-medium">
                                        <span className={`text-xs ${j.status === 'running' ? 'animate-pulse' : ''}`}>
                                          {statusIcon[j.status] ?? ''}
                                        </span>
                                        {j.status.charAt(0).toUpperCase() + j.status.slice(1)}
                                      </span>
                                      {j.status === 'failed' && j.error && (
                                        <p className="text-xs text-orange-400/80 mt-0.5 max-w-xs truncate" title={j.error}>
                                          {j.error}
                                        </p>
                                      )}
                                      {rowError[j.id] && (
                                        <p className="text-xs text-orange-400 mt-0.5">{rowError[j.id]}</p>
                                      )}
                                    </td>
                                    <td className="px-4 py-3 text-right text-gray-300 tabular-nums font-medium">
                                      {fmtMoney(j.aws_spend)}
                                    </td>

                                    {/* Direct Download Exports */}
                                    <td className="px-4 py-3 text-center whitespace-nowrap" onClick={e => e.stopPropagation()}>
                                      {j.status === 'done' ? (
                                        <div className="inline-flex items-center gap-1.5 text-xs">
                                          <a
                                            href={downloadURL(j.id)}
                                            title="Download HTML Report"
                                            className="px-2 py-1 rounded bg-white/5 hover:bg-[#645DF6]/30 text-gray-300 hover:text-white transition-colors"
                                          >
                                            HTML
                                          </a>
                                          <a
                                            href={downloadURL(j.id, undefined, 'xlsx')}
                                            title="Download Excel Spreadsheet"
                                            className="px-2 py-1 rounded bg-white/5 hover:bg-[#00C2BB]/30 text-gray-300 hover:text-white transition-colors"
                                          >
                                            XLSX
                                          </a>
                                          <a
                                            href={downloadURL(j.id, undefined, 'csv')}
                                            title="Download CSV"
                                            className="px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-gray-300 hover:text-white transition-colors"
                                          >
                                            CSV
                                          </a>
                                        </div>
                                      ) : (
                                        <span className="text-xs text-gray-600">—</span>
                                      )}
                                    </td>

                                    {/* Action Buttons */}
                                    <td className="px-4 py-3 text-right whitespace-nowrap" onClick={e => e.stopPropagation()}>
                                      <div className="flex items-center justify-end gap-2.5">
                                        <button
                                          onClick={e => handleOpenLogs(e, j)}
                                          title="View Live Execution Logs"
                                          className="text-xs px-2 py-1 rounded border border-white/10 hover:border-white/30 text-gray-300 hover:text-white transition-colors cursor-pointer"
                                        >
                                          Logs
                                        </button>

                                        {j.status === 'failed' && (
                                          <button
                                            onClick={e => handleRetry(e, j.id)}
                                            disabled={busy.has(j.id)}
                                            className="text-xs font-semibold px-2 py-1 rounded bg-[#645DF6]/20 hover:bg-[#645DF6]/40 text-[#8981ff] transition-colors cursor-pointer disabled:opacity-40"
                                          >
                                            {busy.has(j.id) ? '…' : 'Retry'}
                                          </button>
                                        )}

                                        {(j.status === 'running' || j.status === 'pending') && (
                                          <button
                                            onClick={e => handleCancel(e, j.id)}
                                            disabled={busy.has(j.id)}
                                            className="text-xs font-semibold px-2 py-1 rounded bg-orange-500/20 hover:bg-orange-500/40 text-orange-400 transition-colors cursor-pointer disabled:opacity-40"
                                          >
                                            {busy.has(j.id) ? '…' : 'Cancel'}
                                          </button>
                                        )}

                                        {(j.status === 'done' || j.status === 'failed' || j.status === 'cancelled') && (
                                          <button
                                            onClick={e => handleDelete(e, j.id, j.prospect)}
                                            disabled={busy.has(j.id)}
                                            title="Move to Deleted Jobs Archive"
                                            className="text-xs px-2 py-1 rounded text-gray-500 hover:text-red-400 hover:bg-red-500/10 transition-colors cursor-pointer disabled:opacity-40"
                                          >
                                            {busy.has(j.id) ? '…' : 'Delete'}
                                          </button>
                                        )}
                                      </div>
                                    </td>
                                  </tr>
                                ))}
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

            {total > PAGE_SIZE && (
              <div className="flex items-center justify-center gap-4 mt-8 text-sm">
                <button
                  onClick={() => setOffset(o => Math.max(0, o - PAGE_SIZE))}
                  disabled={offset === 0}
                  className="px-3 py-1.5 rounded-lg border border-white/10 text-gray-300 disabled:opacity-30 hover:bg-white/5 cursor-pointer"
                >
                  ← Prev
                </button>
                <span className="text-gray-500 text-xs">Page {page} of {pageCount} · {total} total</span>
                <button
                  onClick={() => setOffset(o => o + PAGE_SIZE)}
                  disabled={offset + PAGE_SIZE >= total}
                  className="px-3 py-1.5 rounded-lg border border-white/10 text-gray-300 disabled:opacity-30 hover:bg-white/5 cursor-pointer"
                >
                  Next →
                </button>
              </div>
            )}
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

        {/* Live Logs Slide-out / Modal */}
        {activeLogJob && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm anim-fade-in">
            <div className="bg-[#12121a] border border-white/10 rounded-2xl w-full max-w-4xl max-h-[85vh] flex flex-col shadow-2xl overflow-hidden anim-scale-in">
              <div className="px-6 py-4 border-b border-white/10 flex items-center justify-between gap-4 bg-white/[0.02]">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-white">{activeLogJob.prospect}</h3>
                    <span className="text-xs px-2 py-0.5 rounded bg-white/10 font-mono text-gray-400">
                      {activeLogJob.id}
                    </span>
                    <span className={`text-xs font-semibold ${statusColor[activeLogJob.status] ?? 'text-gray-400'}`}>
                      ● {activeLogJob.status}
                    </span>
                  </div>
                  <p className="text-xs text-gray-400 mt-0.5">Live execution and agent logs</p>
                </div>
                <div className="flex items-center gap-2">
                  {logLines && logLines.length > 0 && (
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(logLines.join('\n'))
                        toast.success('Logs copied to clipboard')
                      }}
                      className="text-xs px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-gray-300 hover:text-white transition-colors cursor-pointer"
                    >
                      Copy Log
                    </button>
                  )}
                  <button
                    onClick={() => setActiveLogJob(null)}
                    className="text-gray-400 hover:text-white text-lg px-2 py-1 rounded transition-colors cursor-pointer"
                  >
                    ✕
                  </button>
                </div>
              </div>

              <div className="p-6 overflow-auto flex-1 font-mono text-xs bg-black/40 text-gray-300 leading-relaxed whitespace-pre-wrap">
                {logsLoading && (
                  <div className="text-gray-500 flex items-center gap-2">
                    <span className="animate-pulse">Loading live execution logs…</span>
                  </div>
                )}
                {!logsLoading && (!logLines || logLines.length === 0) && (
                  <p className="text-gray-500">No logs generated for this run yet.</p>
                )}
                {!logsLoading && logLines && logLines.length > 0 && logLines.join('\n')}
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
