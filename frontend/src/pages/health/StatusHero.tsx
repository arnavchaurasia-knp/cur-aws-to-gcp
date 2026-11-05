import type { HealthResponse } from '../../api/health'
import { relativeTime } from './format'

interface StatusHeroProps {
  health: HealthResponse
  onRefresh: () => void
  loading: boolean
  autoRefresh: boolean
  onToggleAutoRefresh: () => void
}

export function StatusHero({
  health,
  onRefresh,
  loading,
  autoRefresh,
  onToggleAutoRefresh,
}: Readonly<StatusHeroProps>) {
  let heroTitle = 'Everything is working'
  let heroBg =
    'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-800/50 text-emerald-950 dark:text-emerald-100'
  let dotBg = 'bg-emerald-500'

  if (health.status === 'fail') {
    heroTitle = 'Something is broken'
    heroBg =
      'bg-rose-50 dark:bg-rose-950/30 border-rose-200 dark:border-rose-800/50 text-rose-950 dark:text-rose-100'
    dotBg = 'bg-rose-500'
  } else if (health.status === 'warn') {
    heroTitle = 'Needs attention'
    heroBg =
      'bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-800/50 text-amber-950 dark:text-amber-100'
    dotBg = 'bg-amber-500'
  }

  return (
    <div className={`p-6 rounded-2xl border shadow-sm transition-all ${heroBg}`}>
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2.5">
            <span className={`w-3 h-3 rounded-full ${dotBg} shadow-sm`} />
            <h2 className="text-xl font-bold tracking-tight">{heroTitle}</h2>
          </div>
          <p className="text-sm opacity-90 max-w-2xl">{health.summary}</p>
          <div className="pt-2 text-xs opacity-75 font-mono">
            Backend started {relativeTime(health.server.started_at)} · build{' '}
            {health.server.build_sha.slice(0, 8)} ({health.server.go_version})
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs cursor-pointer select-none px-3 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-hover-overlay)] transition-colors">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={onToggleAutoRefresh}
              className="rounded text-indigo-600 focus:ring-0 cursor-pointer"
            />
            <span className="text-[color:var(--fcp-text-primary)]">Auto-refresh (30s)</span>
          </label>

          <button
            onClick={onRefresh}
            disabled={loading}
            className="px-4 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-hover-overlay)] text-xs font-medium text-[color:var(--fcp-text-primary)] transition-colors cursor-pointer flex items-center gap-2 shadow-xs disabled:opacity-60"
            title={health.checked_at}
          >
            <span className={`inline-block ${loading ? 'animate-spin' : ''}`}>↻</span>
            <span>Checked {relativeTime(health.checked_at)} · Refresh</span>
          </button>
        </div>
      </div>
    </div>
  )
}
