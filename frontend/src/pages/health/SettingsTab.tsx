import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { EnvListResponse, EnvTestResult, EnvVarView } from '../../api/health'
import { clearEnv, testEnv } from '../../api/health'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { EnvVarEditor } from './EnvVarEditor'

interface SettingsTabProps {
  envData: EnvListResponse | null
  loading: boolean
  onRefreshEnv: () => void
}

export function SettingsTab({ envData, loading, onRefreshEnv }: Readonly<SettingsTabProps>) {
  const [filter, setFilter] = useState('')
  const [editingVar, setEditingVar] = useState<EnvVarView | null>(null)
  const [showAddPicker, setShowAddPicker] = useState(false)
  const [clearingKey, setClearingKey] = useState<string | null>(null)
  const [testingKey, setTestingKey] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<{ key: string; res: EnvTestResult } | null>(null)
  const [whyDeploymentOpen, setWhyDeploymentOpen] = useState(false)

  if (loading && !envData) {
    return <div className="py-20 text-center text-xs text-[color:var(--fcp-text-muted)]">Loading settings...</div>
  }

  if (!envData) return null

  const { encryption, categories, vars, ignored_present } = envData

  const handleClearOverride = async () => {
    if (!clearingKey) return
    try {
      await clearEnv(clearingKey)
      setClearingKey(null)
      onRefreshEnv()
    } catch {
      // handled
    }
  }

  const handleQuickTest = async (key: string) => {
    setTestingKey(key)
    setTestResult(null)
    try {
      const res = await testEnv(key)
      setTestResult({ key, res })
    } catch (err: unknown) {
      setTestResult({
        key,
        res: {
          ok: false,
          status: 'fail',
          detail: err instanceof Error ? err.message : String(err),
        },
      })
    } finally {
      setTestingKey(null)
    }
  }

  const filteredVars = vars.filter((v) => {
    if (!filter) return true
    const q = filter.toLowerCase()
    return v.key.toLowerCase().includes(q) || v.title.toLowerCase().includes(q) || v.category.toLowerCase().includes(q)
  })

  const unsetManagedVars = vars.filter((v) => v.mode === 'managed' && !v.effective.set)

  return (
    <div className="space-y-6">
      {/* Intro box */}
      <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)]">
        <h3 className="text-xs font-bold uppercase tracking-wider text-[color:var(--fcp-text-muted)] mb-1">
          What this is
        </h3>
        <p className="text-sm text-[color:var(--fcp-text-secondary)] leading-relaxed">
          Manage operational configuration and API credentials. Overrides configured in this console are encrypted
          at rest using AES-GCM and apply immediately without requiring a backend restart.
        </p>
      </div>

      {/* Encryption Key Status Card */}
      <div className="p-5 rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] space-y-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="text-base">🔐</span>
            <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">
              Encryption Key for Saved Secrets
            </h4>
          </div>

          <span
            className={`text-xs px-2.5 py-0.5 rounded-full font-medium border ${
              encryption.available
                ? 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300'
                : 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-950/40 dark:text-rose-300'
            }`}
          >
            {encryption.available ? 'Ready' : 'Unavailable'}
          </span>
        </div>

        <div className="text-xs text-[color:var(--fcp-text-secondary)] leading-relaxed">
          {encryption.source === 'env' && (
            <span>
              Configured via <code className="font-mono text-xs">SETTINGS_ENCRYPTION_KEY</code> environment variable ✓
            </span>
          )}
          {(encryption.source === 'file' || encryption.source === 'generated') && (
            <span>
              Auto-generated key stored on disk at <code className="font-mono text-[11px]">{encryption.key_path}</code>.
              Fine for local development; for ephemeral container servers, define{' '}
              <code className="font-mono text-xs">SETTINGS_ENCRYPTION_KEY</code> in deployment configuration.
            </span>
          )}
          {encryption.source === 'none' && (
            <span className="text-rose-600 dark:text-rose-400">
              No encryption key is available ({encryption.problem}). Set SETTINGS_ENCRYPTION_KEY in .env and restart.
            </span>
          )}
        </div>
      </div>

      {/* Ignored Variables Box */}
      {ignored_present.length > 0 && (
        <div className="p-4 rounded-xl bg-amber-50 dark:bg-amber-950/30 border border-amber-300 dark:border-amber-800/60 text-amber-900 dark:text-amber-200 text-xs space-y-2">
          <div className="font-semibold flex items-center gap-2">
            <span>⚠️</span>
            <span>Ignored Environment Variables Present</span>
          </div>
          <p>
            The following environment variables are present in the server environment but are never read by the
            application:
          </p>
          <ul className="list-disc pl-5 space-y-1">
            {ignored_present.map((iv) => (
              <li key={iv.key}>
                <code className="font-mono font-semibold">{iv.key}</code>: use{' '}
                <code className="font-mono font-semibold">{iv.use_instead}</code> instead. {iv.description}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Toolbar: Filter & Add */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="relative flex-1 max-w-sm">
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter settings by key or title..."
            className="w-full px-3 py-1.5 text-xs rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] text-[color:var(--fcp-text-primary)] focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
          {filter && (
            <button
              onClick={() => setFilter('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-gray-400 hover:text-gray-700 cursor-pointer"
            >
              ✕
            </button>
          )}
        </div>

        <button
          onClick={() => setShowAddPicker(true)}
          disabled={unsetManagedVars.length === 0}
          className="px-3.5 py-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs font-semibold shadow-xs transition-colors cursor-pointer shrink-0"
        >
          + Add a Setting Override
        </button>
      </div>

      {/* Category Groups */}
      <div className="space-y-6">
        {categories.map((cat) => {
          const catVars = filteredVars.filter((v) => v.category === cat)
          if (catVars.length === 0) return null

          return (
            <div key={cat} className="space-y-3">
              <h4 className="text-xs font-bold uppercase tracking-wider text-[color:var(--fcp-text-muted)]">
                {cat}
              </h4>

              <div className="rounded-2xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] divide-y divide-[var(--fcp-border-subtle)] shadow-2xs">
                {catVars.map((v) => {
                  const isOverridden = v.override.set
                  const isEffective = v.effective.set
                  const isTestingThis = testingKey === v.key
                  const hasTestResult = testResult?.key === v.key

                  return (
                    <div key={v.key} className="p-4 space-y-2">
                      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-2">
                        <div className="space-y-0.5">
                          <div className="flex items-center gap-2">
                            <strong className="text-sm font-semibold text-[color:var(--fcp-text-primary)]">
                              {v.title}
                            </strong>
                            <code className="text-xs font-mono text-[color:var(--fcp-text-muted)]">{v.key}</code>
                            {v.required && (
                              <span className="text-[10px] font-bold uppercase px-1.5 py-0.2 rounded bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300">
                                required
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-[color:var(--fcp-text-secondary)] leading-relaxed">
                            {v.description}
                          </p>
                          {v.note && (
                            <p className="text-[11px] text-amber-700 dark:text-amber-300 pt-0.5">{v.note}</p>
                          )}
                        </div>

                        {/* Actions by mode */}
                        <div className="flex items-center gap-2 shrink-0 pt-1 sm:pt-0">
                          {v.mode === 'managed' && (
                            <>
                              {isOverridden && (
                                <button
                                  onClick={() => setClearingKey(v.key)}
                                  className="px-2.5 py-1 rounded-lg border border-[var(--fcp-border-subtle)] text-xs text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 cursor-pointer font-medium"
                                >
                                  Remove Override
                                </button>
                              )}

                              <button
                                onClick={() => setEditingVar(v)}
                                className="px-3 py-1 rounded-lg bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] hover:bg-[var(--fcp-hover-overlay)] text-xs font-medium text-[color:var(--fcp-text-primary)] cursor-pointer"
                              >
                                {isOverridden ? 'Edit' : isEffective ? 'Override' : 'Configure'}
                              </button>

                              {v.testable && (
                                <button
                                  onClick={() => handleQuickTest(v.key)}
                                  disabled={isTestingThis}
                                  className="px-2.5 py-1 rounded-lg border border-[var(--fcp-border-subtle)] text-xs text-[color:var(--fcp-text-secondary)] hover:bg-[var(--fcp-hover-overlay)] cursor-pointer disabled:opacity-50"
                                >
                                  {isTestingThis ? '...' : 'Test'}
                                </button>
                              )}
                            </>
                          )}

                          {v.mode === 'integration' && (
                            <Link
                              to="/admin/integrations"
                              className="px-3 py-1 rounded-lg bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] hover:bg-[var(--fcp-hover-overlay)] text-xs font-medium text-indigo-600 dark:text-indigo-400"
                            >
                              Manage on Integrations →
                            </Link>
                          )}

                          {v.mode === 'deployment' && (
                            <div className="relative">
                              <span className="text-[11px] text-[color:var(--fcp-text-muted)] italic">
                                Read-only in app
                              </span>
                              <button
                                type="button"
                                onClick={() => setWhyDeploymentOpen(!whyDeploymentOpen)}
                                className="ml-1 text-[11px] text-indigo-600 dark:text-indigo-400 cursor-pointer"
                                title="Why is this read-only?"
                              >
                                (?)
                              </button>
                            </div>
                          )}
                        </div>
                      </div>

                      {/* Status indicator row */}
                      <div className="flex flex-wrap items-center gap-3 pt-1 text-xs text-[color:var(--fcp-text-muted)] font-mono">
                        <div>
                          Status:{' '}
                          {isOverridden ? (
                            <span className="text-indigo-600 dark:text-indigo-400 font-semibold">
                              Overridden in app ({v.override.preview})
                            </span>
                          ) : isEffective ? (
                            <span className="text-emerald-600 dark:text-emerald-400">
                              Set ({v.effective.source}: {v.effective.preview || 'present'})
                            </span>
                          ) : (
                            <span className="text-gray-400">Not set</span>
                          )}
                        </div>

                        {v.server.set && isOverridden && (
                          <div className="text-[11px] text-[color:var(--fcp-text-muted)]">
                            Server baseline: {v.server.preview}
                          </div>
                        )}

                        {v.override.updated_by && (
                          <div className="text-[11px] text-[color:var(--fcp-text-muted)]">
                            Last changed by {v.override.updated_by}
                          </div>
                        )}
                      </div>

                      {/* Quick test result display */}
                      {hasTestResult && (
                        <div
                          className={`mt-2 p-2.5 rounded-lg border text-xs ${
                            testResult.res.ok
                              ? 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 text-emerald-900'
                              : 'bg-rose-50 dark:bg-rose-950/30 border-rose-200 text-rose-900'
                          }`}
                        >
                          <div className="font-semibold">
                            {testResult.res.ok ? '✓ Test Passed' : '✗ Test Failed'}{' '}
                            {testResult.res.latency_ms !== undefined && `(${testResult.res.latency_ms}ms)`}
                          </div>
                          <div>{testResult.res.detail}</div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      {/* Why deployment read-only info modal */}
      {whyDeploymentOpen && (
        <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] text-xs text-[color:var(--fcp-text-secondary)] space-y-1">
          <strong className="block text-[color:var(--fcp-text-primary)]">
            Why are deployment settings read-only in this console?
          </strong>
          <p>
            Changing core security, storage, or session keys from a web session could immediately invalidate sessions,
            disconnect databases, or lock out administrators. For security and stability, deployment-level parameters
            must be updated directly in the server's environment or <code className="font-mono">.env</code> file
            followed by a restart.
          </p>
        </div>
      )}

      {/* Add picker modal */}
      {showAddPicker && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 anim-fade-in">
          <div className="w-full max-w-md bg-[var(--fcp-bg-surface)] rounded-2xl shadow-2xl border border-[var(--fcp-border-subtle)] overflow-hidden">
            <div className="p-4 border-b border-[var(--fcp-border-subtle)] flex items-center justify-between">
              <h3 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">Add Setting Override</h3>
              <button
                onClick={() => setShowAddPicker(false)}
                className="text-gray-400 hover:text-gray-700 cursor-pointer"
              >
                ✕
              </button>
            </div>
            <div className="p-4 max-h-80 overflow-y-auto divide-y divide-[var(--fcp-border-subtle)]">
              {unsetManagedVars.map((v) => (
                <button
                  key={v.key}
                  onClick={() => {
                    setShowAddPicker(false)
                    setEditingVar(v)
                  }}
                  className="w-full text-left py-2.5 px-2 hover:bg-[var(--fcp-hover-overlay)] rounded-lg transition-colors cursor-pointer"
                >
                  <div className="font-semibold text-xs text-[color:var(--fcp-text-primary)]">{v.title}</div>
                  <div className="text-[11px] font-mono text-[color:var(--fcp-text-muted)]">{v.key}</div>
                  <div className="text-[11px] text-[color:var(--fcp-text-secondary)] truncate">{v.description}</div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Edit modal */}
      {editingVar && (
        <EnvVarEditor
          envVar={editingVar}
          onClose={() => setEditingVar(null)}
          onSaved={() => {
            setEditingVar(null)
            onRefreshEnv()
          }}
        />
      )}

      {/* Clear override confirm */}
      <ConfirmDialog
        open={clearingKey !== null}
        onOpenChange={(open) => !open && setClearingKey(null)}
        title="Remove Setting Override?"
        description={`This will delete the in-app override for ${clearingKey} and revert to the server's startup environment value.`}
        confirmLabel="Remove Override"
        destructive
        onConfirm={handleClearOverride}
      />
    </div>
  )
}
