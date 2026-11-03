import { useEffect, useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Layout } from '../components/Layout'
import { listJobs, downloadURL, type Job } from '../api/jobs'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { formatMoney } from '../lib/preferencesContext'
import { BackButton } from '../components/BackButton'

function statusBadge(status: string) {
  switch (status) {
    case 'done':
      return <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-[#10B981]/15 text-[#10B981] border border-[#10B981]/30">DONE</span>
    case 'running':
      return <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-[#00C2BB]/15 text-[#00C2BB] border border-[#00C2BB]/30 animate-pulse">RUNNING</span>
    case 'failed':
      return <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-red-500/15 text-red-400 border border-red-500/30">FAILED</span>
    default:
      return <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-white/10 text-gray-400 border border-white/20 uppercase">{status}</span>
  }
}

export function ReleaseDashboard({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Projection Dashboard')
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [sortBy, setSortBy] = useState<string>('created_desc')

  useEffect(() => {
    setLoading(true)
    listJobs()
      .then(setJobs)
      .finally(() => setLoading(false))
  }, [])

  const filteredAndSorted = useMemo(() => {
    let result = jobs.filter(j => {
      const q = query.toLowerCase().trim()
      const matchesQuery = !q ||
        j.prospect.toLowerCase().includes(q) ||
        j.owner.toLowerCase().includes(q) ||
        j.id.toLowerCase().includes(q)
      const matchesStatus = statusFilter === 'all' || j.status === statusFilter
      return matchesQuery && matchesStatus
    })

    result.sort((a, b) => {
      switch (sortBy) {
        case 'created_asc':
          return new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
        case 'spend_desc':
          return (b.aws_spend || 0) - (a.aws_spend || 0)
        case 'spend_asc':
          return (a.aws_spend || 0) - (b.aws_spend || 0)
        case 'prospect_asc':
          return a.prospect.localeCompare(b.prospect)
        case 'created_desc':
        default:
          return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      }
    })

    return result
  }, [jobs, query, statusFilter, sortBy])

  const hasActiveFilters = query.trim() !== '' || statusFilter !== 'all' || sortBy !== 'created_desc'

  return (
    <Layout user={user}>
      <div className="max-w-6xl mx-auto px-6 py-8">
        <BackButton fallback="/" />
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Projection Dashboard</h1>
            <p className="text-xs text-gray-400 mt-1">
              Active and completed cost projection releases across all your cloud customer pipelines.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="btn-shimmer px-4 py-2 rounded-lg text-xs font-semibold text-white shadow-sm transition-transform hover:scale-102 cursor-pointer"
            >
              + New Estimation Release
            </Link>
          </div>
        </div>

        {/* Filter bar */}
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[240px] max-w-sm">
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search by prospect, owner or release ID…"
              className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-gray-500 outline-none focus:border-[#645DF6] transition-colors"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-white text-xs cursor-pointer"
              >
                ✕
              </button>
            )}
          </div>

          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-400">Status:</label>
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
              className="bg-[#12131d] border border-white/10 rounded-lg px-3 py-2 text-xs text-gray-200 outline-none focus:border-[#645DF6] cursor-pointer"
            >
              <option value="all">All statuses</option>
              <option value="done">Done</option>
              <option value="running">Running</option>
              <option value="pending">Pending</option>
              <option value="failed">Failed</option>
            </select>
          </div>

          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-400">Sort:</label>
            <select
              value={sortBy}
              onChange={e => setSortBy(e.target.value)}
              className="bg-[#12131d] border border-white/10 rounded-lg px-3 py-2 text-xs text-gray-200 outline-none focus:border-[#645DF6] cursor-pointer"
            >
              <option value="created_desc">Created (newest first)</option>
              <option value="created_asc">Created (oldest first)</option>
              <option value="spend_desc">AWS Spend (high to low)</option>
              <option value="spend_asc">AWS Spend (low to high)</option>
              <option value="prospect_asc">Prospect name (A-Z)</option>
            </select>
          </div>

          {hasActiveFilters && (
            <button
              type="button"
              onClick={() => {
                setQuery('')
                setStatusFilter('all')
                setSortBy('created_desc')
              }}
              className="text-xs px-2.5 py-1.5 rounded-lg border border-white/10 text-gray-400 hover:text-white hover:bg-white/5 transition-colors cursor-pointer"
            >
              Clear filters
            </button>
          )}

          <span className="text-xs text-gray-500 ml-auto tabular-nums">
            {filteredAndSorted.length} release{filteredAndSorted.length === 1 ? '' : 's'}
          </span>
        </div>

        {/* Releases Table */}
        <div className="border border-white/10 rounded-xl overflow-hidden bg-white/[0.02]">
          {loading ? (
            <div className="py-16 text-center text-gray-500 text-xs">
              <div className="flex items-center justify-center gap-2 mb-2">
                <div className="w-2 h-2 rounded-full bg-[#00C2BB] animate-ping" />
              </div>
              Loading releases…
            </div>
          ) : filteredAndSorted.length === 0 ? (
            <div className="py-16 text-center text-gray-500 text-xs">
              No releases found matching your filters.
            </div>
          ) : (
            <table className="w-full text-left text-xs">
              <thead className="bg-white/[0.03] text-[11px] uppercase tracking-wider text-gray-400 border-b border-white/10">
                <tr>
                  <th className="px-5 py-3">Release / Prospect</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3 text-right">AWS Spend</th>
                  <th className="px-5 py-3 text-right">GCP OD Est.</th>
                  <th className="px-5 py-3">Created</th>
                  <th className="px-5 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {filteredAndSorted.map(j => (
                  <tr key={j.id} className="hover:bg-white/[0.02] transition-colors group">
                    <td className="px-5 py-3.5">
                      <Link to={`/jobs/${j.id}`} className="font-semibold text-white hover:text-[#00C2BB] transition-colors block">
                        {j.prospect}
                      </Link>
                      <span className="font-mono text-[10px] text-gray-500">{j.id}</span>
                    </td>
                    <td className="px-5 py-3.5">
                      {statusBadge(j.status)}
                    </td>
                    <td className="px-5 py-3.5 text-right font-mono text-gray-300">
                      {j.aws_spend != null ? formatMoney(j.aws_spend, "$") : '—'}
                    </td>
                    <td className="px-5 py-3.5 text-right font-mono text-[#00C2BB]">
                      {j.gcp_od != null ? formatMoney(j.gcp_od, "$") : '—'}
                    </td>
                    <td className="px-5 py-3.5 text-gray-400 whitespace-nowrap">
                      {new Date(j.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </td>
                    <td className="px-5 py-3.5 text-right whitespace-nowrap">
                      <div className="flex items-center justify-end gap-2">
                        <Link
                          to={`/jobs/${j.id}`}
                          className="px-2.5 py-1 rounded bg-white/5 hover:bg-white/10 text-white font-medium transition-colors cursor-pointer"
                        >
                          View Release
                        </Link>
                        {j.status === 'done' && (
                          <a
                            href={downloadURL(j.id)}
                            className="px-2 py-1 rounded border border-white/10 hover:border-white/30 text-gray-300 hover:text-white transition-colors cursor-pointer"
                            title="Download HTML"
                          >
                            HTML
                          </a>
                        )}
                        <a
                          href={downloadURL(j.id, undefined, 'bill')}
                          className="px-2 py-1 rounded border border-[#00C2BB]/30 text-[#00C2BB] hover:bg-[#00C2BB]/10 transition-colors cursor-pointer"
                          title="Download Original Input Bill"
                        >
                          Bill
                        </a>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </Layout>
  )
}
export default ReleaseDashboard
