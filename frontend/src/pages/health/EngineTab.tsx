import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { EngineHealth, EngineProbe } from '../../api/health'
import { testEngine } from '../../api/health'
import { StatusBadge } from './StatusBadge'
import { relativeTime } from './format'

interface EngineTabProps {
  engine: EngineHealth
  onSelectTab: (tab: string) => void
  onRefreshHealth: () => void
}

export function EngineTab({ engine, onSelectTab, onRefreshHealth }: Readonly<EngineTabProps>) {
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<EngineProbe | null>(null)
  const [testError, setTestError] = useState<string | null>(null)

  const runLiveTest = async () => {
    setTesting(true)
    setTestError(null)
    setTestResult(null)
    try {
      const probe = await testEngine()
      setTestResult(probe)
      onRefreshHealth()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setTestError(msg)
    } finally {
      setTesting(false)
    }
  }

  let keySourceLabel = 'Missing'
  if (engine.key_source === 'app') {
    keySourceLabel = 'Set in this app (Settings & Secrets)'
  } else if (engine.key_source === 'server') {
    keySourceLabel = 'From server .env'
  }

  return (
    <div className="space-y-6">
      {/* Intro box */}
      <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)]">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[color:var(--fcp-text-muted)] mb-1">
          What this is
        </h3>
        <p className="text-sm text-[color:var(--fcp-text-secondary)] leading-relaxed">
          The AI engine is the <code className="font-mono text-xs">agy</code> tool calling Google's Gemini
          model. Every projection uses it to map AWS line items to GCP equivalents and apply family normalizations.
        </p>
      </div>

      {/* Configuration & Status Cards */}
      <div className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] divide-y divide-[var(--fcp-border-subtle)]">
        <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <div className="text-xs font-medium text-[color:var(--fcp-text-muted)]">Active Model</div>
            <div className="text-sm font-semibold text-[color:var(--fcp-text-primary)] font-mono">
              {engine.model}{' '}
              <span className="text-xs font-normal text-[color:var(--fcp-text-muted)]">
                (source: {engine.model_source})
              </span>
            </div>
          </div>
          <Link
            to="/admin/report-settings"
            className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline"
          >
            Change in Report Settings →
          </Link>
        </div>

        <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <div className="text-xs font-medium text-[color:var(--fcp-text-muted)]">AI Engine Tool (agy)</div>
            <div className="text-sm font-semibold text-[color:var(--fcp-text-primary)]">
              {engine.cli_found ? (
                <span>
                  Found at <code className="font-mono text-xs">{engine.cli_path}</code> (v
                  {engine.cli_version || 'unknown'})
                </span>
              ) : (
                <span className="text-rose-600 dark:text-rose-400 font-normal">
                  Tool not found on PATH or specified location
                </span>
              )}
            </div>
          </div>
          <button
            onClick={() => onSelectTab('settings')}
            className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer"
          >
            Configure AGY_PATH →
          </button>
        </div>

        <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <div className="text-xs font-medium text-[color:var(--fcp-text-muted)]">API Key (ANTIGRAVITY_API_KEY)</div>
            <div className="text-sm font-semibold text-[color:var(--fcp-text-primary)] flex items-center gap-2">
              <span>{engine.key_present ? 'Present' : 'Not configured'}</span>
              <span className="text-xs font-normal text-[color:var(--fcp-text-secondary)]">({keySourceLabel})</span>
            </div>
          </div>
          <button
            onClick={() => onSelectTab('settings')}
            className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:underline cursor-pointer"
          >
            Manage in Settings & Secrets →
          </button>
        </div>

        <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <div className="text-xs font-medium text-[color:var(--fcp-text-muted)]">Verification Status</div>
            <div className="text-sm text-[color:var(--fcp-text-primary)] flex items-center gap-2">
              <StatusBadge status={engine.status} />
              <span className="text-xs text-[color:var(--fcp-text-secondary)]">
                {engine.verified_by ? `verified by ${engine.verified_by}` : 'not verified recently'}
              </span>
            </div>
          </div>
          {engine.last_successful_job_at && (
            <div className="text-xs text-[color:var(--fcp-text-muted)]">
              Last successful projection job: {relativeTime(engine.last_successful_job_at)}
            </div>
          )}
        </div>
      </div>

      {/* Live Test Action */}
      <div className="p-5 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">Test AI Engine</h4>
            <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-0.5">
              Sends a single test prompt to verify connectivity and API key validity. Takes 5–30 seconds.
            </p>
          </div>

          <button
            onClick={runLiveTest}
            disabled={testing || engine.live_test_disabled}
            className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-semibold transition-all cursor-pointer shadow-xs shrink-0 flex items-center gap-2"
          >
            {testing ? (
              <>
                <span className="animate-spin inline-block">↻</span>
                Testing Engine...
              </>
            ) : (
              'Run Live Test'
            )}
          </button>
        </div>

        {engine.live_test_disabled && (
          <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-800 text-xs text-gray-600 dark:text-gray-300">
            Live test probe is turned off (AGY_HEALTH_PROBE_DISABLED=1).
          </div>
        )}

        {testResult && (
          <div
            className={`p-4 rounded-xl border text-xs space-y-1 ${
              testResult.state === 'ok'
                ? 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 text-emerald-900 dark:text-emerald-200'
                : 'bg-rose-50 dark:bg-rose-950/30 border-rose-200 text-rose-900 dark:text-rose-200'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold">
              <StatusBadge status={testResult.state} />
              <span>{testResult.state === 'ok' ? 'Test Passed' : 'Test Failed'}</span>
              {testResult.latency_ms !== undefined && (
                <span className="text-[11px] opacity-75 font-mono">({testResult.latency_ms}ms)</span>
              )}
            </div>
            <p className="opacity-90">{testResult.detail}</p>
          </div>
        )}

        {testError && (
          <div className="p-3 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-200 text-rose-700 text-xs">
            {testError}
          </div>
        )}
      </div>

      {/* Test History Table */}
      <div className="space-y-3">
        <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">Test History</h4>
        {engine.history.length === 0 ? (
          <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] text-xs text-[color:var(--fcp-text-muted)] text-center">
            No live tests recorded yet. Run a test above to record verification.
          </div>
        ) : (
          <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-[var(--fcp-bg-subtle)] text-[color:var(--fcp-text-muted)] border-b border-[var(--fcp-border-subtle)]">
                <tr>
                  <th className="py-2.5 px-4 font-medium">Time</th>
                  <th className="py-2.5 px-4 font-medium">Result</th>
                  <th className="py-2.5 px-4 font-medium">Latency</th>
                  <th className="py-2.5 px-4 font-medium">Actor</th>
                  <th className="py-2.5 px-4 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                {engine.history.map((rec) => (
                  <tr key={rec.checked_at} className="hover:bg-[var(--fcp-hover-overlay)]">
                    <td className="py-2.5 px-4 font-mono text-[11px] whitespace-nowrap" title={rec.checked_at}>
                      {relativeTime(rec.checked_at)}
                    </td>
                    <td className="py-2.5 px-4">
                      <StatusBadge status={rec.state} />
                    </td>
                    <td className="py-2.5 px-4 font-mono">{rec.latency_ms}ms</td>
                    <td className="py-2.5 px-4 text-[color:var(--fcp-text-secondary)]">{rec.actor || 'system'}</td>
                    <td className="py-2.5 px-4 text-[color:var(--fcp-text-secondary)] max-w-md truncate" title={rec.detail}>
                      {rec.detail}
                    </td>
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
