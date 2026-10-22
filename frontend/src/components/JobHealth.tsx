import { useEffect, useState } from 'react'
import {
  getJobDiagnostics,
  getJobDiagnosticRecords,
  type JobDiagnosticsResponse,
  type JobDiagnosticRecord,
  type JobDiagnosticByCode,
} from '../api/admin'
import { formatDollars } from '../lib/formatDollars'

export function JobHealth({ jobId }: Readonly<{ jobId: string }>) {
  const [data, setData] = useState<JobDiagnosticsResponse | null>(null)
  const [records, setRecords] = useState<JobDiagnosticRecord[]>([])
  const [expandedCode, setExpandedCode] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const d = await getJobDiagnostics(jobId)
      setData(d)
      if (d?.available && d.summary) {
        const r = await getJobDiagnosticRecords(jobId)
        setRecords(r?.records ?? [])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load diagnostics')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { refresh() }, [jobId])

  if (loading) {
    return (
      <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5">
        <p className="text-sm text-gray-500">Loading diagnostics…</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5">
        <p className="text-xs text-orange-400">{error}</p>
      </div>
    )
  }

  if (!data || !data.available) {
    return (
      <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5">
        <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
          Job Health
        </h3>
        <p className="text-sm text-gray-400">
          Diagnostics are not available for this run (job files expired).
        </p>
      </div>
    )
  }

  const summary = data.summary!
  const hasErrors = summary.error_count > 0
  const hasWarnings = summary.warn_count > 0

  let verdict: string
  let verdictColor: string
  if (hasErrors) {
    verdict = `${summary.error_count} error${summary.error_count === 1 ? '' : 's'} affecting ${formatDollars(summary.error_usd)} (${summary.error_pct_of_workload.toFixed(1)}% of workload)`
    verdictColor = 'text-red-400'
  } else if (hasWarnings) {
    verdict = `${summary.warn_count} warning${summary.warn_count === 1 ? '' : 's'} — review before sending`
    verdictColor = 'text-orange-400'
  } else {
    verdict = 'No issues recorded for this run.'
    verdictColor = 'text-[#00C2BB]'
  }

  const byCode = summary.by_code
  const sortedCodes = Object.entries(byCode).sort(([, a], [, b]) => b.usd - a.usd)

  return (
    <div className="bg-white/[0.02] border border-white/10 rounded-lg p-5">
      <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
        Job Health
      </h3>

      {/* Verdict line */}
      <div className={`mb-4 p-3 rounded bg-white/5 ${verdictColor}`}>
        <p className="text-sm font-medium">{verdict}</p>
      </div>

      {/* by_code table */}
      <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
        Diagnostic findings by code
      </h4>
      {sortedCodes.length === 0 ? (
        <p className="text-sm text-gray-400">No diagnostic findings recorded.</p>
      ) : (
        <table className="w-full text-left text-sm border-collapse mb-4">
          <thead>
            <tr className="border-b border-white/10">
              <th className="pb-2 text-gray-400">Code</th>
              <th className="pb-2 text-gray-400">Level</th>
              <th className="pb-2 text-gray-400 text-right">Count</th>
              <th className="pb-2 text-gray-400 text-right">Rows</th>
              <th className="pb-2 text-gray-400 text-right">$ Exposure</th>
              <th className="pb-2 text-gray-400">Source</th>
              <th className="pb-2 text-gray-400 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {sortedCodes.map(([code, info]) => (
              <tr key={code} className="border-b border-white/5 hover:bg-white/5">
                <td className="py-2 font-mono text-xs">{code}</td>
                <td className="py-2 text-xs">
                  <span
                    className={`px-1.5 py-0.5 rounded ${
                      info.level === 'error'
                        ? 'bg-red-400/20 text-red-400'
                        : 'bg-orange-400/20 text-orange-400'
                    }`}
                  >
                    {info.level}
                  </span>
                </td>
                <td className="py-2 text-xs text-right">{info.count}</td>
                <td className="py-2 text-xs text-right">{info.rows}</td>
                <td className="py-2 text-xs text-right tabular-nums text-[#00C2BB]">
                  ${info.usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </td>
                <td className="py-2 text-xs font-mono">
                  {Array.isArray(info.sources) ? info.sources.join(', ') : info.sources}
                </td>
                <td className="py-2 text-right">
                  <button
                    onClick={() => setExpandedCode(expandedCode === code ? null : code)}
                    className="px-2 py-1 rounded text-xs text-[#645DF6] hover:text-[#7d77f8] hover:bg-white/5 transition-colors"
                  >
                    {expandedCode === code ? 'Hide' : 'Show'}
                  </button>
                </td>
              </tr>
              {expandedCode === code && (
                <tr>
                  <td colSpan={7} className="p-3 bg-white/5">
                    <div className="flex flex-col gap-2">
                      {Object.entries(info.detail || {}).map(([k, v]) => (
                        <div key={k} className="text-xs text-gray-300">
                          <span className="font-semibold text-gray-400">{k}:</span>{' '}
                          {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                        </div>
                      ))}
                    </div>
                  </td>
                </tr>
              )}
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
