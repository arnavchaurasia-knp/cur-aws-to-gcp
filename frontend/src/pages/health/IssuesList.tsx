import { useState } from 'react'
import type { HealthIssue, IssueAction } from '../../api/health'

interface IssuesListProps {
  issues: HealthIssue[]
  onAction: (action: IssueAction) => void
}

export function IssuesList({ issues, onAction }: Readonly<IssuesListProps>) {
  const [showTips, setShowTips] = useState(false)

  const urgentIssues = issues.filter((i) => i.severity === 'critical' || i.severity === 'warning')
  const tipIssues = issues.filter((i) => i.severity === 'info')

  if (issues.length === 0) {
    return (
      <div className="p-6 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] text-center space-y-1">
        <div className="text-emerald-500 font-semibold text-sm">✓ Nothing needs attention</div>
        <p className="text-xs text-[color:var(--fcp-text-secondary)]">
          All monitored components, tools, and configurations are operating within expected parameters.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {urgentIssues.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">
              Needs attention ({urgentIssues.length})
            </h3>
          </div>

          <div className="space-y-3">
            {urgentIssues.map((issue) => {
              const isCrit = issue.severity === 'critical'
              const borderClass = isCrit
                ? 'border-rose-300 dark:border-rose-900/60 bg-rose-50/50 dark:bg-rose-950/20'
                : 'border-amber-300 dark:border-amber-900/60 bg-amber-50/50 dark:bg-amber-950/20'
              const badgeClass = isCrit
                ? 'bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300'
                : 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'

              return (
                <div
                  key={issue.id}
                  className={`p-4 rounded-xl border ${borderClass} space-y-2 transition-all shadow-2xs`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded ${badgeClass}`}>
                        {issue.severity}
                      </span>
                      <span className="text-xs font-semibold text-[color:var(--fcp-text-muted)] uppercase tracking-wider">
                        {issue.area}
                      </span>
                      <strong className="text-sm font-semibold text-[color:var(--fcp-text-primary)]">
                        {issue.title}
                      </strong>
                    </div>

                    {issue.action && (
                      <button
                        onClick={() => onAction(issue.action!)}
                        className="self-start sm:self-auto px-3 py-1 rounded-lg bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-hover-overlay)] border border-[var(--fcp-border-subtle)] text-xs font-medium text-[color:var(--fcp-text-primary)] transition-colors cursor-pointer shadow-2xs shrink-0"
                      >
                        {issue.action.label} →
                      </button>
                    )}
                  </div>

                  <p className="text-xs text-[color:var(--fcp-text-secondary)] leading-relaxed">
                    {issue.detail}
                  </p>

                  <div className="text-xs pt-1 border-t border-[var(--fcp-border-subtle)]/50">
                    <span className="font-semibold text-[color:var(--fcp-text-primary)]">What to do: </span>
                    <span className="text-[color:var(--fcp-text-secondary)]">{issue.fix}</span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {tipIssues.length > 0 && (
        <div className="pt-2">
          <button
            onClick={() => setShowTips(!showTips)}
            className="flex items-center gap-2 text-xs font-medium text-[color:var(--fcp-text-secondary)] hover:text-[color:var(--fcp-text-primary)] transition-colors cursor-pointer"
          >
            <span>{showTips ? '▼' : '▶'}</span>
            <span>{tipIssues.length} tip{tipIssues.length === 1 ? '' : 's'} & recommendations</span>
          </button>

          {showTips && (
            <div className="mt-3 space-y-2 pl-4 border-l-2 border-[var(--fcp-border-subtle)]">
              {tipIssues.map((issue) => (
                <div
                  key={issue.id}
                  className="p-3 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-bold uppercase px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300">
                        tip
                      </span>
                      <strong className="text-xs font-medium text-[color:var(--fcp-text-primary)]">
                        {issue.title}
                      </strong>
                    </div>
                    {issue.action && (
                      <button
                        onClick={() => onAction(issue.action!)}
                        className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer shrink-0"
                      >
                        {issue.action.label} →
                      </button>
                    )}
                  </div>
                  <p className="text-xs text-[color:var(--fcp-text-secondary)]">{issue.detail}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
