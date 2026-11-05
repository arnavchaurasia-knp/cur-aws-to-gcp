import { useMemo, useState } from 'react'
import type { EnvTestResult, EnvVarView } from '../../api/health'
import { setEnv, testEnv } from '../../api/health'

interface EnvVarEditorProps {
  envVar: EnvVarView | null
  onClose: () => void
  onSaved: (updated: EnvVarView) => void
}

export function EnvVarEditor({ envVar, onClose, onSaved }: Readonly<EnvVarEditorProps>) {
  const [value, setValue] = useState(envVar?.secret ? '' : envVar?.override.set ? envVar.override.preview || '' : '')
  const [showSecret, setShowSecret] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Testing
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<EnvTestResult | null>(null)

  const validationError = useMemo(() => {
    if (!envVar) return null
    if (!value) return 'Value cannot be empty'
    if (value.includes('\n') || value.includes('\r')) return 'Must be a single line'
    if (value.trim() !== value) return 'Must not have leading or trailing spaces'
    if (envVar.secret && value.length < 8) return 'Secret must be at least 8 characters long'
    if (envVar.options && envVar.options.length > 0 && !envVar.options.includes(value)) {
      return `Must be one of: ${envVar.options.join(', ')}`
    }
    return null
  }, [value, envVar])

  if (!envVar) return null

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (validationError) return
    setSaving(true)
    setError(null)
    try {
      const updated = await setEnv(envVar.key, value)
      onSaved(updated)
      if (!envVar.testable) {
        onClose()
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await testEnv(envVar.key)
      setTestResult(res)
    } catch (err: unknown) {
      setTestResult({
        ok: false,
        status: 'fail',
        detail: err instanceof Error ? err.message : String(err),
      })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4 anim-fade-in">
      <div className="w-full max-w-lg bg-[var(--fcp-bg-surface)] rounded-2xl shadow-2xl border border-[var(--fcp-border-subtle)] overflow-hidden">
        {/* Header */}
        <div className="p-5 border-b border-[var(--fcp-border-subtle)] flex items-center justify-between gap-3">
          <div>
            <h3 className="text-base font-bold text-[color:var(--fcp-text-primary)]">
              {envVar.override.set ? 'Edit Setting Override' : 'Configure Setting'}
            </h3>
            <div className="text-xs font-mono text-[color:var(--fcp-text-muted)] pt-0.5">{envVar.key}</div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-gray-400 hover:text-gray-700 dark:hover:text-white cursor-pointer"
          >
            ✕
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSave} className="p-5 space-y-4">
          <div>
            <div className="text-sm font-semibold text-[color:var(--fcp-text-primary)]">{envVar.title}</div>
            <p className="text-xs text-[color:var(--fcp-text-secondary)] mt-1 leading-relaxed">
              {envVar.description}
            </p>
            {envVar.note && (
              <p className="text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/30 p-2 rounded-lg border border-amber-200 mt-2">
                {envVar.note}
              </p>
            )}
          </div>

          <div className="space-y-1.5 pt-2">
            <label className="block text-xs font-medium text-[color:var(--fcp-text-muted)]">
              {envVar.secret ? 'Secret Value (stored encrypted)' : 'Value'}
            </label>

            {envVar.options && envVar.options.length > 0 ? (
              <select
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className="w-full p-2.5 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-subtle)] text-xs text-[color:var(--fcp-text-primary)] focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="">Select an option...</option>
                {envVar.options.map((opt) => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))}
              </select>
            ) : (
              <div className="relative">
                <input
                  type={envVar.secret && !showSecret ? 'password' : 'text'}
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  placeholder={envVar.secret && envVar.effective.preview ? `Current: ${envVar.effective.preview}` : envVar.example || 'Enter value...'}
                  className="w-full p-2.5 pr-14 font-mono text-xs rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-subtle)] text-[color:var(--fcp-text-primary)] focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
                {envVar.secret && (
                  <button
                    type="button"
                    onClick={() => setShowSecret(!showSecret)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-[color:var(--fcp-text-muted)] hover:text-[color:var(--fcp-text-primary)] cursor-pointer"
                  >
                    {showSecret ? 'Hide' : 'Show'}
                  </button>
                )}
              </div>
            )}

            {validationError && (
              <div className="text-[11px] text-rose-600 dark:text-rose-400 font-medium">
                {validationError}
              </div>
            )}

            <div className="text-[11px] text-[color:var(--fcp-text-muted)]">
              Applies immediately — no server restart needed.
            </div>
          </div>

          {error && (
            <div className="p-3 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-200 text-rose-700 text-xs">
              {error}
            </div>
          )}

          {testResult && (
            <div
              className={`p-3 rounded-xl border text-xs space-y-1 ${
                testResult.ok
                  ? 'bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 text-emerald-900'
                  : 'bg-rose-50 dark:bg-rose-950/30 border-rose-200 text-rose-900'
              }`}
            >
              <div className="font-semibold flex items-center gap-1.5">
                <span>{testResult.ok ? '✓ Test Passed' : '✗ Test Failed'}</span>
                {testResult.latency_ms !== undefined && (
                  <span className="font-mono text-[11px] opacity-75">({testResult.latency_ms}ms)</span>
                )}
              </div>
              <p className="opacity-90">{testResult.detail}</p>
            </div>
          )}

          <div className="flex items-center justify-between pt-4 border-t border-[var(--fcp-border-subtle)]">
            <div>
              {envVar.testable && (
                <button
                  type="button"
                  onClick={handleTest}
                  disabled={testing}
                  className="px-3 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] text-xs font-medium text-[color:var(--fcp-text-secondary)] hover:bg-[var(--fcp-hover-overlay)] cursor-pointer disabled:opacity-50"
                >
                  {testing ? 'Testing...' : 'Test Setting'}
                </button>
              )}
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onClose}
                className="px-3.5 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] text-xs font-medium text-[color:var(--fcp-text-secondary)] hover:bg-[var(--fcp-hover-overlay)] cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={saving || Boolean(validationError)}
                className="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs font-semibold shadow-xs cursor-pointer"
              >
                {saving ? 'Saving...' : 'Save Override'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
