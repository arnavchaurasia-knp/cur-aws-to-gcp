import { useEffect, useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Nav } from '../components/Nav'
import { listAllJobs } from '../api/jobs'
import type { Job } from '../api/jobs'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'

const statusColor: Record<string, string> = {
  pending: 'text-gray-400',
  running: 'text-[#00C2BB]',
  done: 'text-[#00C2BB]',
  failed: 'text-orange-400',
}

function fmtMoney(v: number | null) {
  if (v == null) return '—'
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function fmtWhen(iso: string) {
  const d = new Date(iso)
  return d.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })
}

export function AdminJobs({ user }: { user: UserInfo }) {
  useTitle('All reports — admin')
  const nav = useNavigate()
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [query, setQuery] = useState<string>('')

  useEffect(() => { listAllJobs().then(setJobs) }, [])

  const filtered = useMemo(() => {
    if (!jobs) return []
    const q = query.trim().toLowerCase()
    if (!q) return jobs
    return jobs.filter(j =>
      j.owner.toLowerCase().includes(q) ||
      j.prospect.toLowerCase().includes(q) ||
      j.status.toLowerCase().includes(q)
    )
  }, [jobs, query])

  const totals = useMemo(() => {
    if (!jobs) return { total: 0, done: 0, failed: 0, running: 0 }
    return {
      total: jobs.length,
      done: jobs.filter(j => j.status === 'done').length,
      failed: jobs.filter(j => j.status === 'failed').length,
      running: jobs.filter(j => j.status === 'running' || j.status === 'pending').length,
    }
  }, [jobs])

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-white">
      <Nav user={user} />
      <div className="max-w-6xl mx-auto px-6 py-10">
        <div className="flex items-baseline justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold">All reports</h1>
            <p className="text-sm text-gray-500 mt-1">
              Every projection across all FSRs. Click a row to open the report.
            </p>
          </div>
          {jobs && (
            <div className="text-xs text-gray-400 flex gap-4">
              <span>{totals.total} total</span>
              <span className="text-[#00C2BB]">{totals.done} done</span>
              <span className="text-[#00C2BB]">{totals.running} active</span>
              <span className="text-orange-400">{totals.failed} failed</span>
            </div>
          )}
        </div>

        {jobs && jobs.length > 0 && (
          <div className="mb-4">
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Filter by owner, prospect, or status…"
              className="w-full sm:w-80 bg-white/5 border border-white/10 rounded-md px-3 py-2 text-sm
                placeholder:text-gray-500 outline-none focus:border-[#645DF6]"
            />
            {query && (
              <span className="ml-3 text-xs text-gray-500">
                {filtered.length} of {jobs.length}
              </span>
            )}
          </div>
        )}

        {jobs === null && <p className="text-sm text-gray-500">Loading…</p>}
        {jobs !== null && filtered.length === 0 && <p className="text-sm text-gray-500">No reports.</p>}
        {filtered.length > 0 && (
          <div className="border border-white/10 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-white/[0.03] text-xs uppercase tracking-wider text-gray-400">
                <tr>
                  <th className="text-left px-4 py-3">When</th>
                  <th className="text-left px-4 py-3">Owner</th>
                  <th className="text-left px-4 py-3">Prospect</th>
                  <th className="text-left px-4 py-3">Status</th>
                  <th className="text-right px-4 py-3">AWS spend (pre-tax)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {filtered.map(j => (
                  <tr key={j.id} onClick={() => nav(`/jobs/${j.id}`)}
                      className="cursor-pointer hover:bg-white/[0.03]">
                    <td className="px-4 py-3 text-gray-400 whitespace-nowrap">{fmtWhen(j.created_at)}</td>
                    <td className="px-4 py-3 text-gray-300">{j.owner}</td>
                    <td className="px-4 py-3 text-gray-200">{j.prospect}</td>
                    <td className={`px-4 py-3 ${statusColor[j.status] ?? 'text-gray-400'}`}>{j.status}</td>
                    <td className="px-4 py-3 text-right text-gray-300 tabular-nums">{fmtMoney(j.aws_spend)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
