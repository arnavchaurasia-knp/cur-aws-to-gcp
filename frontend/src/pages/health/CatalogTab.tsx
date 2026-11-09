import { useEffect, useRef, useState } from 'react'
import type { CatalogHealth, CatalogRefreshRun, SourceView } from '../../api/health'
import {
  getCatalogRefreshStatus,
  triggerCatalogRefresh,
  getRefreshSources,
  triggerSourceRefresh,
  pauseSourceRefresh,
  resumeSourceRefresh,
} from '../../api/health'
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

  // Managed sources lifecycle state
  const [sources, setSources] = useState<Record<string, SourceView> | null>(null)
  const [sourceActionLoading, setSourceActionLoading] = useState<string | null>(null)
  const [sourceActionMsg, setSourceActionMsg] = useState<string | null>(null)

  const pollIntervalRef = useRef<number | null>(null)
  const timerIntervalRef = useRef<number | null>(null)

  const loadSources = async () => {
    try {
      const res = await getRefreshSources()
      setSources(res.sources)
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    loadSources()
  }, [])

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
            loadSources()
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

  const handleTriggerSource = async (source: string) => {
    setSourceActionLoading(source)
    setSourceActionMsg(null)
    try {
      await triggerSourceRefresh(source)
      await loadSources()
      setSourceActionMsg(`Refresh triggered for ${source}`)
    } catch (err: unknown) {
      setSourceActionMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setSourceActionLoading(null)
    }
  }

  const handlePauseSource = async (source: string) => {
    setSourceActionLoading(source)
    setSourceActionMsg(null)
    try {
      await pauseSourceRefresh(source)
      await loadSources()
      setSourceActionMsg(`Paused ${source}`)
    } catch (err: unknown) {
      setSourceActionMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setSourceActionLoading(null)
    }
  }

  const handleResumeSource = async (source: string) => {
    setSourceActionLoading(source)
    setSourceActionMsg(null)
    try {
      await resumeSourceRefresh(source)
      await loadSources()
      setSourceActionMsg(`Resumed ${source}`)
    } catch (err: unknown) {
      setSourceActionMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setSourceActionLoading(null)
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
          API and orchestrated with automated retries and backoff.
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

      {/* Managed Sources Lifecycle Section */}
      <div className="p-5 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">
              Managed Refresh Sources & Lifecycle
            </h4>
            <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5">
              Automated retry with backoff, escalation gates, dependency ordering, and manual overrides.
            </p>
          </div>
          <button
            onClick={loadSources}
            className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer self-start sm:self-auto"
          >
            ↻ Refresh Sources Status
          </button>
        </div>

        {sourceActionMsg && (
          <div className="p-2.5 rounded-xl bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-200 dark:border-indigo-800 text-indigo-800 dark:text-indigo-300 text-xs">
            {sourceActionMsg}
          </div>
        )}

        {sources && Object.keys(sources).length > 0 ? (
          <div className="rounded-xl border border-[var(--fcp-border-subtle)] overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-[var(--fcp-bg-subtle)] text-[color:var(--fcp-text-muted)] border-b border-[var(--fcp-border-subtle)]">
                <tr>
                  <th className="py-2.5 px-3 font-medium">Source</th>
                  <th className="py-2.5 px-3 font-medium">Freshness & Status</th>
                  <th className="py-2.5 px-3 font-medium">Completeness & Coverage</th>
                  <th className="py-2.5 px-3 font-medium">Volume</th>
                  <th className="py-2.5 px-3 font-medium">Anomalies</th>
                  <th className="py-2.5 px-3 font-medium">History</th>
                  <th className="py-2.5 px-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                {Object.entries(sources).map(([name, { config, status, history }]) => {
                  const isPaused = status.state === 'paused'
                  const isRunning = status.state === 'running'
                  const isLoading = sourceActionLoading === name
                  const maxAttempts = config.lifecycle.max_attempts || 3
                  const attempt = status.attempt ?? 0

                  const ageDays = status.finished_at
                    ? Math.floor((Date.now() - new Date(status.finished_at).getTime()) / (1000 * 60 * 60 * 24))
                    : null
                  const isStale = config.stale_after_days && ageDays !== null ? ageDays > config.stale_after_days : false

                  let displayStatus = status.state || 'idle'
                  if (status.state === 'success' || status.state === 'idle') {
                    if (isStale) {
                      displayStatus = 'warn'
                    } else if (status.complete) {
                      displayStatus = 'ok'
                    }
                  } else if (status.state === 'failed') {
                    displayStatus = 'fail'
                  }

                  const historyRuns = (history || []).slice(0, 8)

                  return (
                    <tr key={name} className="hover:bg-[var(--fcp-hover-overlay)]">
                      <td className="py-3 px-3">
                        <div className="font-semibold text-[color:var(--fcp-text-primary)]">{name}</div>
                        <div className="text-[11px] text-[color:var(--fcp-text-muted)] font-mono">
                          {config.refresher || 'refresher'}
                        </div>
                        {config.lifecycle.depends_on && config.lifecycle.depends_on.length > 0 && (
                          <div className="text-[10px] text-[color:var(--fcp-text-secondary)] mt-0.5">
                            Depends on: {config.lifecycle.depends_on.join(', ')}
                          </div>
                        )}
                      </td>
                      <td className="py-3 px-3">
                        <StatusBadge status={displayStatus} />
                        {isStale && (
                          <div className="text-[10px] text-amber-600 dark:text-amber-400 font-medium mt-0.5">
                            Stale ({ageDays}d vs max {config.stale_after_days}d)
                          </div>
                        )}
                        {status.next_retry_at ? (
                          <div className="mt-1">
                            <span className="text-[11px] font-medium text-amber-600 dark:text-amber-400">
                              Retry {relativeTime(status.next_retry_at)}
                            </span>
                            <div className="text-[10px] text-[color:var(--fcp-text-muted)] font-mono" title={status.next_retry_at}>
                              attempt {attempt}/{maxAttempts}
                            </div>
                          </div>
                        ) : status.finished_at ? (
                          <div className="text-[10px] text-[color:var(--fcp-text-secondary)] mt-1">
                            Last {relativeTime(status.finished_at)}
                          </div>
                        ) : null}
                        {status.reason && (
                          <div className="text-[10px] text-[color:var(--fcp-text-muted)] mt-1 max-w-xs truncate" title={status.reason}>
                            {status.reason}
                          </div>
                        )}
                      </td>
                      <td className="py-3 px-3">
                        {status.expected_count && status.expected_count > 0 ? (
                          <div>
                            <div className="font-medium text-[color:var(--fcp-text-primary)]">
                              {status.fetched_count ?? 0} / {status.expected_count}{' '}
                              <span className="text-[color:var(--fcp-text-secondary)] font-normal">
                                ({(status.coverage_pct ?? 0).toFixed(1)}%)
                              </span>
                            </div>
                            <div className="w-24 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden mt-1">
                              <div
                                className={`h-full rounded-full ${
                                  status.complete ? 'bg-emerald-500' : 'bg-amber-500'
                                }`}
                                style={{ width: `${Math.min(100, status.coverage_pct ?? 0)}%` }}
                              />
                            </div>
                            <div className="text-[10px] text-[color:var(--fcp-text-muted)] mt-0.5">
                              threshold: {config.completeness_threshold}%
                            </div>
                          </div>
                        ) : (
                          <span className="text-[color:var(--fcp-text-muted)]">—</span>
                        )}
                      </td>
                      <td className="py-3 px-3">
                        {status.record_count !== undefined && status.record_count > 0 ? (
                          <div>
                            <div className="font-mono text-[color:var(--fcp-text-primary)]">
                              {status.record_count.toLocaleString()}
                            </div>
                            {status.record_delta !== undefined && status.record_delta !== 0 && (
                              <div
                                className={`text-[10px] font-mono ${
                                  status.record_delta > 0
                                    ? 'text-emerald-600 dark:text-emerald-400'
                                    : 'text-rose-600 dark:text-rose-400'
                                }`}
                              >
                                {status.record_delta > 0 ? `+${status.record_delta.toLocaleString()}` : status.record_delta.toLocaleString()}
                              </div>
                            )}
                          </div>
                        ) : (
                          <span className="text-[color:var(--fcp-text-muted)]">—</span>
                        )}
                      </td>
                      <td className="py-3 px-3">
                        {status.anomalies && status.anomalies.length > 0 ? (
                          <div>
                            <span
                              className="px-2 py-0.5 rounded text-[10px] font-semibold bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 border border-rose-200 dark:border-rose-800 cursor-help"
                              title={status.anomalies.join('\n')}
                            >
                              {status.anomalies.length} anomaly
                            </span>
                            <div className="text-[10px] text-[color:var(--fcp-text-muted)] mt-1 max-w-[140px] truncate" title={status.anomalies[0]}>
                              {status.anomalies[0]}
                            </div>
                          </div>
                        ) : (
                          <span className="text-[11px] text-emerald-600 dark:text-emerald-400 font-medium">
                            ✓ None
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-3">
                        {historyRuns.length > 0 ? (
                          <div className="flex items-center gap-1">
                            {historyRuns.map((h, i) => {
                              let dotBg = 'bg-gray-400'
                              if (h.state === 'success' && h.complete) dotBg = 'bg-emerald-500'
                              else if (h.state === 'partial' || h.state === 'retrying' || h.state === 'backing_off') dotBg = 'bg-amber-500'
                              else if (h.state === 'failed') dotBg = 'bg-rose-500'
                              else if (h.state === 'escalated') dotBg = 'bg-purple-500'
                              return (
                                <span
                                  key={i}
                                  className={`inline-block w-2.5 h-2.5 rounded-full ${dotBg} transition-transform hover:scale-125 cursor-pointer`}
                                  title={`${h.state} (${(h.coverage_pct ?? 0).toFixed(0)}%) - ${relativeTime(h.finished_at || h.started_at)}`}
                                />
                              )
                            })}
                          </div>
                        ) : (
                          <span className="text-[color:var(--fcp-text-muted)]">—</span>
                        )}
                      </td>
                      <td className="py-3 px-3">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => handleTriggerSource(name)}
                            disabled={isLoading || isRunning}
                            className="px-2.5 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-[11px] font-semibold transition-all cursor-pointer shadow-xs"
                          >
                            {isLoading ? '...' : 'Retry now'}
                          </button>
                          {isPaused ? (
                            <button
                              onClick={() => handleResumeSource(name)}
                              disabled={isLoading}
                              className="px-2.5 py-1 rounded-lg border border-emerald-300 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-50 dark:hover:bg-emerald-950/40 disabled:opacity-50 text-[11px] font-medium transition-all cursor-pointer"
                            >
                              Resume
                            </button>
                          ) : (
                            <button
                              onClick={() => handlePauseSource(name)}
                              disabled={isLoading}
                              className="px-2.5 py-1 rounded-lg border border-[var(--fcp-border-subtle)] text-[color:var(--fcp-text-secondary)] hover:bg-[var(--fcp-bg-subtle)] disabled:opacity-50 text-[11px] font-medium transition-all cursor-pointer"
                            >
                              Pause
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
        ) : (
          <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] text-xs text-[color:var(--fcp-text-muted)] text-center">
            Loading refresh sources…
          </div>
        )}
      </div>

      {/* Refresh Action Card */}
      <div className="p-5 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">Quick Refresh GCP Catalog</h4>
            <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5">
              Downloads fresh prices from Google Cloud and rebuilds the local pricing database.
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
              <StatusBadge status={refreshRun.state || 'unknown'} />
              <span>Refresh {refreshRun.state}</span>
              {refreshRun.duration_sec !== undefined && (
                <span className="text-[11px] opacity-75 font-mono">({formatDuration(refreshRun.duration_sec)})</span>
              )}
            </div>
            {refreshRun.reason && <p>{refreshRun.reason}</p>}
            {refreshRun.log_tail && (
              <pre className="p-2 rounded bg-black/10 dark:bg-black/40 font-mono text-[11px] overflow-x-auto whitespace-pre-wrap max-h-40">
                {Array.isArray(refreshRun.log_tail) ? refreshRun.log_tail.join('\n') : refreshRun.log_tail}
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
        <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">Catalog Refresh History</h4>
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
                        <StatusBadge status={run.state || 'unknown'} />
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
                                {Array.isArray(run.log_tail) ? run.log_tail.join('\n') : run.log_tail}
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
