import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { JobsHealth } from '../../api/health'
import { StatusBadge } from './StatusBadge'
import { formatDuration, relativeTime } from './format'

interface JobsTabProps {
  jobs: JobsHealth
}

export function JobsTab({ jobs }: Readonly<JobsTabProps>) {
  const [expandedErrorId, setExpandedErrorId] = useState<string | null>(null)

  return (
    <div className="space-y-6">
      {/* Intro box */}
      <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)]">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[color:var(--fcp-text-muted)] mb-1">
          What this is
        </h3>
        <p className="text-sm text-[color:var(--fcp-text-secondary)] leading-relaxed">
          The projection jobs pipeline processes uploaded AWS billing files into GCP equivalent estimates. Status
          is derived from recent job completions and the global pipeline pause switch.
        </p>
      </div>

      {/* Global Pipeline Halted Banner */}
      {jobs.halted && (
        <div className="p-4 rounded-xl bg-rose-600 text-white shadow-md flex items-center justify-between gap-3">
          <div>
            <strong className="block font-bold text-sm">Pipeline Execution Halted (STOP ALL)</strong>
            <p className="text-xs opacity-90 mt-0.5">
              Job orchestration is paused. New uploads and retries will not run until processing is resumed.
            </p>
          </div>
          <Link
            to="/admin"
            className="px-3 py-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-white text-xs font-semibold shrink-0"
          >
            Open All Projections →
          </Link>
        </div>
      )}

      {/* Metric Tiles */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
          <div className="text-xs text-[color:var(--fcp-text-muted)] font-medium">Running</div>
          <div className="text-2xl font-bold text-[color:var(--fcp-text-primary)] font-mono">{jobs.running}</div>
        </div>

        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
          <div className="text-xs text-[color:var(--fcp-text-muted)] font-medium">Queued</div>
          <div className="text-2xl font-bold text-[color:var(--fcp-text-primary)] font-mono">{jobs.queued}</div>
        </div>

        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
          <div className="text-xs text-[color:var(--fcp-text-muted)] font-medium">Concurrency Limit</div>
          <div className="text-2xl font-bold text-[color:var(--fcp-text-primary)] font-mono">
            {jobs.concurrency_limit}
          </div>
          <Link
            to="/admin/report-settings"
            className="text-[10px] text-indigo-600 dark:text-indigo-400 hover:underline block pt-0.5"
          >
            Change limit →
          </Link>
        </div>

        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
          <div className="text-xs text-[color:var(--fcp-text-muted)] font-medium">Recent Failure Rate</div>
          <div className="text-2xl font-bold text-[color:var(--fcp-text-primary)] font-mono">
            {jobs.recent_failure_pct.toFixed(0)}%
          </div>
          <div className="text-[11px] text-[color:var(--fcp-text-muted)]">
            {jobs.recent_failed} of last {jobs.recent_window}
          </div>
        </div>

        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
          <div className="text-xs text-[color:var(--fcp-text-muted)] font-medium">Avg Duration</div>
          <div className="text-2xl font-bold text-[color:var(--fcp-text-primary)] font-mono">
            {formatDuration(jobs.avg_duration_sec)}
          </div>
        </div>

        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1">
          <div className="text-xs text-[color:var(--fcp-text-muted)] font-medium">Status</div>
          <div className="pt-1">
            <StatusBadge status={jobs.status} size="md" />
          </div>
        </div>
      </div>

      {/* Recent Failures List */}
      <div className="space-y-3">
        <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">Recent Failures</h4>
        {jobs.recent_failures.length === 0 ? (
          <div className="p-6 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] text-center text-xs text-emerald-600 dark:text-emerald-400 font-medium">
            ✓ No recent job failures recorded.
          </div>
        ) : (
          <div className="space-y-3">
            {jobs.recent_failures.map((f) => {
              const isExpanded = expandedErrorId === f.id
              return (
                <div
                  key={f.id}
                  className="p-4 rounded-xl border border-rose-200 dark:border-rose-900/60 bg-rose-50/40 dark:bg-rose-950/20 space-y-2 text-xs"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <strong className="font-semibold text-rose-900 dark:text-rose-200 text-sm">
                        {f.prospect || 'Unnamed Job'}
                      </strong>
                      <span className="text-rose-700 dark:text-rose-400 text-[11px]">({f.owner})</span>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-[color:var(--fcp-text-muted)] whitespace-nowrap" title={f.updated_at}>
                        {relativeTime(f.updated_at)}
                      </span>
                      <Link
                        to={`/jobs/${f.id}`}
                        className="px-2 py-1 rounded bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] font-medium hover:underline text-[11px]"
                      >
                        Job Details
                      </Link>
                      <Link
                        to={`/jobs/${f.id}/logs`}
                        className="px-2 py-1 rounded bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] font-medium hover:underline text-[11px]"
                      >
                        View Logs
                      </Link>
                    </div>
                  </div>

                  {f.error && (
                    <div className="pt-1">
                      <div className="font-mono text-[11px] text-rose-800 dark:text-rose-300 bg-white/50 dark:bg-black/20 p-2 rounded border border-rose-200/50 dark:border-rose-900/40 whitespace-pre-wrap">
                        {isExpanded ? f.error : f.error.slice(0, 300)}
                        {f.error.length > 300 && (
                          <button
                            onClick={() => setExpandedErrorId(isExpanded ? null : f.id)}
                            className="ml-2 font-sans font-semibold text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer"
                          >
                            {isExpanded ? 'Show less' : '...show full error'}
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
