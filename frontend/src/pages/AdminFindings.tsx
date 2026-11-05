import { useEffect, useState, useMemo, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Layout } from '../components/Layout'
import {
  listFindings,
  actionFinding,
  type ReviewFinding,
  type ListFindingsParams,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'

const SEVERITY_ORDER: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 }

const SEVERITY_BADGE: Record<string, string> = {
  HIGH: 'bg-red-950/30 text-red-400 border border-red-500/40',
  MEDIUM: 'bg-orange-950/30 text-orange-400 border border-orange-500/40',
  LOW: 'bg-yellow-950/30 text-yellow-400 border border-yellow-500/40',
}

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-white/10 text-gray-300 border border-white/10',
  accepted: 'bg-emerald-950/30 text-emerald-400 border border-emerald-500/40',
  rejected: 'bg-red-950/30 text-red-400 border border-red-500/40',
  deferred: 'bg-blue-950/30 text-blue-400 border border-blue-500/40',
}

export function AdminFindings({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Review Findings — admin')
  const [findings, setFindings] = useState<ReviewFinding[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<Set<string>>(new Set())

  // Filters
  const [serviceFilter, setServiceFilter] = useState('')
  const [severityFilter, setSeverityFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('pending')
  const [minSpend, setMinSpend] = useState<string>('')

  const services = useMemo(() => {
    const set = new Set<string>()
    for (const f of findings) if (f.canonical_service) set.add(f.canonical_service)
    return Array.from(set).sort()
  }, [findings])

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: ListFindingsParams = {}
      if (serviceFilter) params.service = serviceFilter
      if (severityFilter) params.severity = severityFilter
      if (statusFilter) params.status = statusFilter
      params.limit = 200
      const data = await listFindings(params)
      setFindings(data.findings)
      setTotal(data.total)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load findings')
    } finally {
      setLoading(false)
    }
  }, [serviceFilter, severityFilter, statusFilter])

  useEffect(() => { refresh() }, [refresh])

  const filtered = useMemo(() => {
    let list = findings
    if (minSpend) {
      const m = Number(minSpend)
      if (Number.isFinite(m)) list = list.filter(f => f.aws_od >= m)
    }
    return list.sort((a, b) => {
      const s = (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
      if (s !== 0) return s
      return b.aws_od - a.aws_od
    })
  }, [findings, minSpend])

  const money = (v: number) => `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  const ratioText = (v: number) => (Number.isFinite(v) ? v.toFixed(2) + 'x' : '—')

  const handleAction = async (finding: ReviewFinding, action: 'accept' | 'reject' | 'defer') => {
    setBusy(prev => new Set(prev).add(finding.id))
    setError(null)
    try {
      await actionFinding(finding.id, { action, note: '' })
      toast.success(`Finding ${action}ed`)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'action failed')
    } finally {
      setBusy(prev => { const next = new Set(prev); next.delete(finding.id); return next })
    }
  }

  const pendingCount = findings.filter(f => f.status === 'pending').length

  return (
    <Layout user={user}>
      <div className="max-w-6xl mx-auto px-6 py-10">
        <Link to="/admin" className="inline-flex items-center gap-1.5 mb-6 text-[#00C2BB] hover:text-white text-xs font-medium">
          ← Back to admin
        </Link>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-semibold mb-1">Review Findings</h1>
            <p className="text-sm text-gray-400">
              Advisory-reviewer findings from every job. Accepting a finding promotes it to the trusted
              refresh store so it short-circuits the LLM on the next run.
            </p>
          </div>
          <div className="text-right text-sm text-gray-400">
            <div className="font-medium text-white">{total} total</div>
            {pendingCount > 0 && <div className="text-orange-400">{pendingCount} pending triage</div>}
          </div>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-orange-950/40 border border-orange-500/40 text-orange-400 text-xs">
            {error}
          </div>
        )}

        {/* Filters */}
        <div className="mb-6 flex flex-col sm:flex-row gap-3 items-start sm:items-center">
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">Service</span>
            <select
              value={serviceFilter}
              onChange={e => setServiceFilter(e.target.value)}
              className="bg-white/5 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-[#645DF6]"
            >
              <option value="">all</option>
              {services.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">Severity</span>
            <select
              value={severityFilter}
              onChange={e => setSeverityFilter(e.target.value)}
              className="bg-white/5 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-[#645DF6]"
            >
              <option value="">all</option>
              <option value="HIGH">HIGH</option>
              <option value="MEDIUM">MEDIUM</option>
              <option value="LOW">LOW</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-500">Status</span>
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
              className="bg-white/5 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-[#645DF6]"
            >
              <option value="pending">pending</option>
              <option value="">all</option>
              <option value="accepted">accepted</option>
              <option value="rejected">rejected</option>
              <option value="deferred">deferred</option>
            </select>
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <span className="text-xs text-gray-500">Min AWS $</span>
            <input
              type="number"
              value={minSpend}
              onChange={e => setMinSpend(e.target.value)}
              placeholder="0"
              className="bg-white/5 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-gray-200 w-20 focus:outline-none focus:border-[#645DF6]"
            />
          </div>
        </div>

        {loading && (
          <div className="flex items-center justify-center h-48">
            <div className="flex gap-2">
              {[0, 1, 2, 3].map(i => (
                <div key={i} className="w-2 h-2 rounded-full bg-[#00C2BB] animate-pulse" style={{ animationDelay: `${i * 100}ms` }} />
              ))}
            </div>
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div className="flex items-center justify-center h-64 text-center">
            <div>
              <p className="text-sm text-gray-500">No findings match the filters.</p>
              {pendingCount === 0 && <p className="text-xs text-gray-500 mt-1">All findings triaged.</p>}
            </div>
          </div>
        )}

        {!loading && filtered.length > 0 && (
          <div className="border border-white/10 rounded-xl overflow-hidden shadow-sm">
            <table className="w-full text-sm">
              <thead className="bg-white/[0.04] text-xs uppercase tracking-wider text-gray-400 border-b border-white/10 sticky top-0 z-10">
                <tr>
                  <th className="text-left px-5 py-3 font-medium">Service</th>
                  <th className="text-left px-5 py-3 font-medium">Charge Type</th>
                  <th className="text-left px-5 py-3 font-medium">Finding</th>
                  <th className="text-left px-5 py-3 font-medium">Severity</th>
                  <th className="text-right px-5 py-3 font-medium">AWS $</th>
                  <th className="text-right px-5 py-3 font-medium">GCP $</th>
                  <th className="text-center px-5 py-3 font-medium">Ratio</th>
                  <th className="text-left px-5 py-3 font-medium">Suggested Action</th>
                  <th className="text-center px-5 py-3 font-medium">Status</th>
                  <th className="text-right px-5 py-3 font-medium">Triage</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {filtered.map(f => {
                  const rowBusy = busy.has(f.id)
                  return (
                    <tr key={f.id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-5 py-3 text-gray-200 font-mono text-xs truncate max-w-xs">
                        {f.canonical_service || '—'}
                        {f.aws_li_key && (
                          <span className="ml-1 text-[10px] text-gray-500 font-mono">({f.aws_li_key})</span>
                        )}
                      </td>
                      <td className="px-5 py-3 text-gray-200 text-xs">{f.charge_type || 'usage'}</td>
                      <td className="px-5 py-3 text-gray-300 text-xs font-mono">{f.finding_type}</td>
                      <td className="px-5 py-3">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${SEVERITY_BADGE[f.severity] || 'bg-white/10 text-gray-400 border border-white/10'}`}
                        >
                          {f.severity}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-right text-gray-200 font-mono text-xs">{money(f.aws_od)}</td>
                      <td className="px-5 py-3 text-right text-gray-200 font-mono text-xs">{money(f.gcp_od)}</td>
                      <td className="px-5 py-3 text-center text-gray-200 font-mono text-xs">{ratioText(f.ratio)}</td>
                      <td className="px-5 py-3 text-gray-300 text-xs truncate max-w-xs" title={f.suggested_action}>
                        {f.suggested_action || '—'}
                      </td>
                      <td className="px-5 py-3">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${STATUS_BADGE[f.status]}`}
                        >
                          {f.status}
                        </span>
                        {f.resolved_by && (
                          <div className="text-[10px] text-gray-500 mt-0.5">by {f.resolved_by}</div>
                        )}
                      </td>
                      <td className="px-5 py-3 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          {f.job_id && (
                            <Link
                              to={`/jobs/${f.job_id}/review?row=${encodeURIComponent(f.aws_li_key || '')}`}
                              className="px-2 py-1 rounded text-[10px] font-medium bg-cyan-500/10 text-cyan-400 hover:bg-cyan-500/20 border border-cyan-500/30 transition-colors"
                              title="Open review & override console"
                            >
                              Review
                            </Link>
                          )}
                          {f.status !== 'pending' ? (
                            <span className="text-[10px] text-gray-500 ml-1">resolved</span>
                          ) : (
                            <div className="flex gap-1">
                              <button
                              onClick={() => handleAction(f, 'accept')}
                              disabled={rowBusy}
                              className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                                rowBusy ? 'opacity-50 cursor-not-allowed' : 'bg-emerald-600 text-white hover:bg-emerald-500'
                              }`}
                            >
                              Accept
                            </button>
                            <button
                              onClick={() => handleAction(f, 'reject')}
                              disabled={rowBusy}
                              className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                                rowBusy ? 'opacity-50 cursor-not-allowed' : 'bg-red-600 text-white hover:bg-red-500'
                              }`}
                            >
                              Reject
                            </button>
                            <button
                              onClick={() => handleAction(f, 'defer')}
                              disabled={rowBusy}
                              className={`px-2 py-1 rounded text-[10px] font-medium transition-colors ${
                                rowBusy ? 'opacity-50 cursor-not-allowed' : 'bg-blue-600 text-white hover:bg-blue-500'
                              }`}
                            >
                              Defer
                            </button>
                          </div>
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
    </Layout>
  )
}
