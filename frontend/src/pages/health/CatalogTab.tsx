import { useEffect, useRef, useState } from 'react'
import type { CatalogHealth, CatalogRefreshRun } from '../../api/health'
import { getCatalogRefreshStatus, triggerCatalogRefresh } from '../../api/health'
import { StatusBadge } from './StatusBadge'
import { formatDuration, relativeTime } from './format'

interface CatalogTabProps {
  catalog: CatalogHealth
  onSelectTab: (tab: string) => void
  onRefreshHealth: () => void
}

export function CatalogTab({ catalog, onSelectTab, onRefreshHealth }: Readonly<CatalogTabProps>) {
  const [refreshing, setRefreshing] = useState(false)
  const [refreshRun, setRefreshRun] = useState<CatalogRefreshRun | null>(null)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [expandedLogRun, setExpandedLogRun] = useState<string | null>(null)

  const pollIntervalRef = useRef<number | null>(null)
  const timerIntervalRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
      if (timerIntervalRef.current) clearInterval(timerIntervalRef.current)
    }
  }, [])

  const startRefresh = async () => {
    setRefreshing(true)
    setRefreshError(null)
    setElapsedSeconds(0)

    timerIntervalRef.current = window.setInterval(() => {
      setElapsedSeconds((s) => s + 1)
    }, 1000)

    try {
      await triggerCatalogRefresh()

      pollIntervalRef.current = window.setInterval(async () => {
        try {
          const run = await getCatalogRefreshStatus()
          setRefreshRun(run)
          if (run.state === 'success' || run.state === 'failed' || run.state === 'skipped') {
            if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
            if (timerIntervalRef.current) clearInterval(timerIntervalRef.current)
            setRefreshing(false)
            onRefreshHealth()
          }
        } catch {
          // ignore polling errors
        }
      }, 2000)
    } catch (err: unknown) {
      if (timerIntervalRef.current) clearInterval(timerIntervalRef.current)
      setRefreshing(false)
      const msg = err instanceof Error ? err.message : String(err)
      setRefreshError(msg)
    }
  }

  return (
    <div className="space-y-6">
      {/* Intro box */}
      <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)]">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[color:var(--fcp-text-muted)] mb-1">
          What this is
        </h3>
        <p className="text-sm text-[color:var(--fcp-text-secondary)] leading-relaxed">
          The pricing catalog is the compiled GCP price list every projection reads (
          <code className="font-mono text-xs">catalog.duckdb</code>). It is refreshed from Google Cloud's Billing
          API.
        </p>
      </div>

      {/* Catalog Metric Tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
          <div className="text-xs text-[color:var(--fcp-text-muted)] font-medium">SKU Count</div>
          <div className="text-xl font-bold text-[color:var(--fcp-text-primary)] font-mono">
            {catalog.sku_count.toLocaleString()}
          </div>
        </div>

        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
          <div className="text-xs text-[color:var(--fcp-text-muted)] font-medium">GCP Services</div>
          <div className="text-xl font-bold text-[color:var(--fcp-text-primary)] font-mono">
            {catalog.service_count}
          </div>
        </div>

        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
          <div className="text-xs text-[color:var(--fcp-text-muted)] font-medium">Price Vintage</div>
          <div className="text-sm font-semibold text-[color:var(--fcp-text-primary)] pt-1" title={catalog.fetched_at}>
            {catalog.age_days >= 0 ? `${catalog.age_days} days old` : 'unknown'}
          </div>
          <div className="text-[11px] text-[color:var(--fcp-text-muted)]">
            stale after {catalog.stale_after_days} days
          </div>
        </div>

        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
          <div className="text-xs text-[color:var(--fcp-text-muted)] font-medium">Status</div>
          <div className="pt-1">
            <StatusBadge status={catalog.status} size="md" />
          </div>
          <div className="text-[11px] text-[color:var(--fcp-text-muted)]">
            {catalog.artifact_ok ? 'Database OK' : 'Database missing/corrupt'}
          </div>
        </div>
      </div>

      {/* Problems / Warnings banner */}
      {catalog.source_problems.length > 0 && (
        <div className="p-4 rounded-xl bg-amber-50 dark:bg-amber-950/30 border border-amber-200 text-amber-900 dark:text-amber-200 text-xs space-y-1.5">
          <strong className="block font-semibold">
            {catalog.source_problems.length} price file(s) reported issues:
          </strong>
          <ul className="list-disc pl-4 space-y-0.5">
            {catalog.source_problems.map((prob) => (
              <li key={prob}>{prob}</li>
            ))}
          </ul>
        </div>
      )}

      {catalog.image_baked && (
        <div className="p-4 rounded-xl bg-gray-50 dark:bg-gray-800/60 border border-[var(--fcp-border-subtle)] text-xs text-[color:var(--fcp-text-secondary)]">
          <strong>Notice:</strong> The pricing catalog was bundled into the deployment container image. It refreshes
          automatically on each redeploy.
        </div>
      )}

      {/* Refresh Action Card */}
      <div className="p-5 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">Refresh Pricing Catalog</h4>
            <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5">
              Downloads fresh prices from Google Cloud and rebuilds the local pricing database. Takes 1–3 minutes.
            </p>
          </div>

          <button
            onClick={startRefresh}
            disabled={refreshing}
            className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-semibold transition-all cursor-pointer shadow-xs shrink-0 flex items-center gap-2"
          >
            {refreshing ? (
              <>
                <span className="animate-spin inline-block">↻</span>
                Refreshing... ({elapsedSeconds}s)
              </>
            ) : (
              'Refresh Catalog Now'
            )}
          </button>
        </div>

        {!catalog.api_key_present && (
          <div className="p-3 rounded-xl bg-amber-50 dark:bg-amber-950/40 border border-amber-300 text-amber-800 dark:text-amber-300 text-xs flex items-center justify-between gap-2">
            <span>
              <strong>Warning:</strong> No Google Cloud pricing API key is set. Catalog refresh will fail until a key
              is added.
            </span>
            <button
              onClick={() => onSelectTab('settings')}
              className="font-medium underline shrink-0 cursor-pointer"
            >
              Add key in Settings →
            </button>
          </div>
        )}

        {refreshRun && (
          <div
            className={`p-4 rounded-xl border text-xs space-y-2 ${
              refreshRun.state === 'success'
                ? 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 text-emerald-900 dark:text-emerald-200'
                : refreshRun.state === 'failed'
                  ? 'bg-rose-50 dark:bg-rose-950/30 border-rose-200 text-rose-900 dark:text-rose-200'
                  : 'bg-gray-50 dark:bg-gray-800 border-gray-200 text-gray-800 dark:text-gray-200'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold">
              <StatusBadge status={refreshRun.state === 'success' ? 'ok' : refreshRun.state === 'failed' ? 'fail' : 'unknown'} />
              <span>Refresh {refreshRun.state}</span>
              {refreshRun.duration_sec !== undefined && (
                <span className="text-[11px] opacity-75 font-mono">({formatDuration(refreshRun.duration_sec)})</span>
              )}
            </div>
            {refreshRun.reason && <p>{refreshRun.reason}</p>}
            {refreshRun.log_tail && (
              <pre className="p-2 rounded bg-black/10 dark:bg-black/40 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap max-h-40">
                {refreshRun.log_tail}
              </pre>
            )}
          </div>
        )}

        {refreshError && (
          <div className="p-3 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-200 text-rose-700 text-xs">
            {refreshError}
          </div>
        )}
      </div>

      {/* History table */}
      <div className="space-y-3">
        <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">Refresh History</h4>
        {catalog.history.length === 0 ? (
          <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] text-xs text-[color:var(--fcp-text-muted)] text-center">
            No catalog refreshes recorded in this deployment.
          </div>
        ) : (
          <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-[var(--fcp-bg-subtle)] text-[color:var(--fcp-text-muted)] border-b border-[var(--fcp-border-subtle)]">
                <tr>
                  <th className="py-2.5 px-4 font-medium">Started</th>
                  <th className="py-2.5 px-4 font-medium">State</th>
                  <th className="py-2.5 px-4 font-medium">Duration</th>
                  <th className="py-2.5 px-4 font-medium">Triggered By</th>
                  <th className="py-2.5 px-4 font-medium">Reason / Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                {catalog.history.map((run, idx) => {
                  const key = run.started_at || String(idx)
                  const isExpanded = expandedLogRun === key
                  return (
                    <tr key={key} className="hover:bg-[var(--fcp-hover-overlay)]">
                      <td className="py-2.5 px-4 font-mono text-[11px] whitespace-nowrap" title={run.started_at}>
                        {relativeTime(run.started_at)}
                      </td>
                      <td className="py-2.5 px-4">
                        <StatusBadge status={run.state === 'success' ? 'ok' : run.state === 'failed' ? 'fail' : 'unknown'} />
                      </td>
                      <td className="py-2.5 px-4 font-mono">
                        {run.duration_sec !== undefined ? formatDuration(run.duration_sec) : '—'}
                      </td>
                      <td className="py-2.5 px-4 text-[color:var(--fcp-text-secondary)]">{run.actor || 'system'}</td>
                      <td className="py-2.5 px-4 text-[color:var(--fcp-text-secondary)] max-w-md">
                        <div className="truncate" title={run.reason}>
                          {run.reason || 'completed'}
                        </div>
                        {run.log_tail && (
                          <div className="mt-1">
                            <button
                              onClick={() => setExpandedLogRun(isExpanded ? null : key)}
                              className="text-[11px] text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer"
                            >
                              {isExpanded ? 'Hide log tail' : 'Show log tail'}
                            </button>
                            {isExpanded && (
                              <pre className="mt-1 p-2 rounded bg-black/5 dark:bg-black/30 font-mono text-[10px] overflow-x-auto whitespace-pre-wrap max-h-36">
                                {run.log_tail}
                              </pre>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
