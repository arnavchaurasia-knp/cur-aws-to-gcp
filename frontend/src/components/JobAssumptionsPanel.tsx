import { useEffect, useState } from 'react'
import {
  getJobAssumptions,
  rerunJobAssumptions,
  type JobAssumption,
  type JobAssumptionsResponse,
} from '../api/jobs'
import { CATEGORY_ORDER, CATEGORY_META } from '../lib/assumptionsConstants'
import { toast } from 'sonner'

function getDomainOptions(domain?: JobAssumption['domain']): { value: string; label: string }[] | null {
  if (!domain || (domain.kind !== 'enum' && domain.kind !== 'flag')) return null
  const vals = domain.values || []
  return vals.map((v) => {
    if (typeof v === 'object' && v !== null && 'value' in v) {
      return { value: String(v.value), label: v.label || String(v.value) }
    }
    return { value: String(v), label: String(v) }
  })
}

function formatAllowedRange(a: JobAssumption): string {
  if (a.domain?.kind === 'enum' || a.domain?.kind === 'flag') {
    const vals = (a.domain.values || []).map(v => (typeof v === 'object' && v !== null ? (v as any).value : v))
    return `{${vals.join(', ')}}`
  }
  const min = a.domain?.min ?? a.min_value
  const max = a.domain?.max ?? a.max_value
  return `[${min}, ${max}]`
}

interface Props {
  jobId: string
  canEdit: boolean
  onRerunStarted?: () => void
}

export function JobAssumptionsPanel({ jobId, canEdit, onRerunStarted }: Readonly<Props>) {
  const [data, setData] = useState<JobAssumptionsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [rerunning, setRerunning] = useState(false)
  const [reason, setReason] = useState('')
  // Modified values: key -> string representation
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const toggleCollapse = (cat: string) => {
    setCollapsed(prev => ({ ...prev, [cat]: !prev[cat] }))
  }

  const loadData = async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await getJobAssumptions(jobId)
      setData(resp)
      // Initialize edits from current effective values so the inputs are populated
      const initial: Record<string, string> = {}
      for (const a of resp.assumptions) {
        initial[a.key] = String(a.value)
      }
      setEdits(initial)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load job assumptions')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [jobId])

  if (loading) {
    return (
      <div className="bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] rounded-xl p-6 shadow-xs">
        <div className="flex items-center justify-center py-8">
          <div className="flex gap-2">
            {[0, 1, 2, 3].map((i, idx) => (
              <div
                key={i}
                className="w-2.5 h-2.5 rounded-full bg-[var(--facets-teal)] animate-pulse"
                style={{ animationDelay: `${idx * 120}ms` }}
              />
            ))}
          </div>
        </div>
        <p className="text-xs text-[var(--fcp-text-secondary)] text-center font-medium">Loading job assumptions…</p>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] rounded-xl p-6 shadow-xs">
        <div className="text-center py-4">
          <p className="text-xs text-rose-500 font-medium">{error}</p>
          <button
            onClick={loadData}
            className="mt-3 text-xs text-[var(--facets-purple)] dark:text-[var(--facets-teal)] hover:underline font-semibold cursor-pointer"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (!data) return null

  const assumptionsList = data.assumptions || []
  const categoriesList = (data.categories && data.categories.length > 0) ? data.categories : CATEGORY_ORDER

  // Calculate which assumptions are currently modified relative to default values
  const dirtyKeys: string[] = []
  const overridesToSend: Record<string, number> = {}
  let validationError: string | null = null

  for (const a of assumptionsList) {
    const key = a.key
    const minVal = a.domain?.min ?? a.min_value
    const maxVal = a.domain?.max ?? a.max_value
    const defaultVal = a.default_value
    const domainOptions = getDomainOptions(a.domain)
    const editStr = edits[key]
    if (editStr !== undefined) {
      const num = Number(editStr)
      if (Number.isFinite(num)) {
        if (domainOptions) {
          if (!domainOptions.some(opt => Number(opt.value) === num)) {
            validationError = `${a.title}: value ${num} is not in allowed values ${formatAllowedRange(a)}`
          }
        } else if (num < minVal || num > maxVal) {
          validationError = `${a.title}: value ${num} is out of allowed range [${minVal}, ${maxVal}]`
        }
        if (num !== defaultVal) {
          dirtyKeys.push(key)
        }
        // Always include modified values in overrides payload
        if (num !== a.value || a.is_overridden) {
          overridesToSend[key] = num
        }
      }
    }
  }

  const handleValueChange = (key: string, val: string) => {
    setEdits(prev => ({ ...prev, [key]: val }))
  }

  const handleResetToDefault = (a: JobAssumption) => {
    setEdits(prev => ({ ...prev, [a.key]: String(a.default_value) }))
  }

  const handleRerun = async () => {
    if (!canEdit) return
    if (validationError) {
      toast.error(validationError)
      return
    }
    setRerunning(true)
    setError(null)
    try {
      await rerunJobAssumptions(jobId, overridesToSend, reason.trim())
      toast.success('Re-run triggered! Pipeline is executing with your assumptions.')
      onRerunStarted?.()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Re-run failed'
      setError(msg)
      toast.error(msg)
    } finally {
      setRerunning(false)
    }
  }

  return (
    <div className="bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] rounded-xl p-6 shadow-xs anim-fade-in-up">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-5 pb-4 border-b border-[var(--fcp-border-subtle)]">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-bold text-[var(--fcp-text-primary)] uppercase tracking-wider">
              Pricing Assumptions &amp; Sizing Knobs
            </h3>
            {data.version && (
              <span className="text-[10px] font-mono text-[var(--fcp-text-secondary)] bg-[var(--fcp-bg-raised)] px-2 py-0.5 rounded border border-[var(--fcp-border-subtle)]">
                v{data.version.slice(0, 14)}
              </span>
            )}
          </div>
          <p className="text-xs text-[var(--fcp-text-secondary)] mt-1 leading-relaxed">
            Override conversion values for <b>this job only</b>.
            Re-running will re-execute the pricing pipeline and regenerate the report.
          </p>
        </div>

        {canEdit && (
          <button
            onClick={handleRerun}
            disabled={rerunning || Boolean(validationError)}
            className="px-4 py-2 rounded-lg bg-[var(--facets-purple)] hover:bg-[var(--facets-purple-hover)] text-white text-xs font-semibold disabled:opacity-40 transition-all cursor-pointer shrink-0 shadow-xs hover:shadow"
          >
            {rerunning ? 'Launching Re-run…' : 'Re-run with my assumptions'}
          </button>
        )}
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-500 text-xs">
          {error}
        </div>
      )}

      {validationError && (
        <div className="mb-4 p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-600 dark:text-amber-400 text-xs flex items-center gap-2">
          <span>⚠️</span>
          <span>{validationError}</span>
        </div>
      )}

      {canEdit && (
        <div className="mb-5 bg-[var(--fcp-bg-raised)] border border-[var(--fcp-border-subtle)] rounded-xl p-3.5">
          <label className="block text-xs font-medium text-[var(--fcp-text-secondary)] mb-1.5" htmlFor="rerun-reason">
            Optional rationale / notes for this re-run:
          </label>
          <input
            id="rerun-reason"
            type="text"
            placeholder="e.g. Adjusted NAT VM count to match 32 microservices topology"
            value={reason}
            onChange={e => setReason(e.target.value)}
            className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-border-subtle)] rounded-lg px-3 py-1.5 text-xs text-[var(--fcp-text-primary)] placeholder:text-[var(--fcp-text-tertiary)] focus:outline-none focus:border-[#645DF6] focus:ring-2 focus:ring-[#645DF6]/20 transition-all"
          />
        </div>
      )}

      <div className="space-y-3">
        {categoriesList.map((catKey) => {
          const meta = CATEGORY_META[catKey] ?? { label: catKey.replace(/_/g, ' '), desc: '' }
          const groupAssumptions = assumptionsList.filter(
            a => (a.category || 'other') === catKey
          )
          if (groupAssumptions.length === 0) return null

          const isCollapsed = Boolean(collapsed[catKey])

          return (
            <div
              key={catKey}
              className="border border-[var(--fcp-border-subtle)] rounded-xl overflow-hidden bg-[var(--fcp-bg-surface)] shadow-2xs transition-all"
            >
              <button
                type="button"
                onClick={() => toggleCollapse(catKey)}
                className="w-full px-4 py-3 bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-bg-hover)] border-b border-[var(--fcp-border-subtle)] flex items-center justify-between text-left transition-colors duration-150 cursor-pointer select-none"
              >
                <div className="flex items-center gap-2.5">
                  <div className="w-5 h-5 rounded bg-[var(--fcp-bg-raised)] border border-[var(--fcp-border-subtle)] flex items-center justify-center transition-all">
                    <svg
                      className={`w-3 h-3 text-[var(--facets-purple)] dark:text-[var(--facets-teal)] transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
                        isCollapsed ? '-rotate-90' : 'rotate-0'
                      }`}
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </div>
                  <div>
                    <span className="text-xs font-semibold text-[var(--fcp-text-primary)] tracking-wide">
                      {meta.label}
                    </span>
                    <span className="ml-2 text-[10px] text-[var(--fcp-text-secondary)] font-mono bg-[var(--fcp-bg-raised)] px-1.5 py-0.5 rounded-full border border-[var(--fcp-border-subtle)]">
                      {groupAssumptions.length}
                    </span>
                    {meta.desc && (
                      <span className="ml-2 text-[11px] text-[var(--fcp-text-tertiary)] hidden sm:inline">{meta.desc}</span>
                    )}
                  </div>
                </div>
                <span className="text-[11px] text-[var(--fcp-text-tertiary)] font-medium">
                  {isCollapsed ? 'Expand' : 'Collapse'}
                </span>
              </button>

              <div
                className="grid transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]"
                style={{
                  gridTemplateRows: isCollapsed ? '0fr' : '1fr',
                  opacity: isCollapsed ? 0 : 1,
                  visibility: isCollapsed ? 'hidden' : 'visible',
                  transition: 'grid-template-rows 300ms cubic-bezier(0.16, 1, 0.3, 1), opacity 200ms ease, visibility 300ms',
                }}
              >
                <div className="min-h-0 overflow-x-auto">
                  <table className="w-full text-left border-collapse text-xs min-w-[700px]">
                    <thead>
                      <tr className="border-b border-[var(--fcp-border-subtle)] bg-[var(--fcp-table-header-bg)] text-[var(--fcp-text-tertiary)] font-semibold">
                        <th className="py-2.5 px-4">Assumption</th>
                        <th className="py-2.5 px-4 text-center">Effective Value</th>
                        <th className="py-2.5 px-4 text-center">Default</th>
                        <th className="py-2.5 px-4 text-center">Allowed Range</th>
                        <th className="py-2.5 px-4 text-center">Unit</th>
                        {canEdit && <th className="py-2.5 px-4 text-right">Action</th>}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--fcp-border-subtle)]">
                      {groupAssumptions.map((a) => {
                        const key = a.key
                        const title = a.title
                        const rationale = a.rationale
                        const minVal = a.domain?.min ?? a.min_value
                        const maxVal = a.domain?.max ?? a.max_value
                        const defaultVal = a.default_value
                        const unit = a.unit
                        const isOverridden = a.is_overridden
                        const domainOptions = getDomainOptions(a.domain)
                        const currentValStr = edits[key] ?? String(a.value)
                        const num = Number(currentValStr)
                        const isOutOfRange = Number.isFinite(num) && (domainOptions ? !domainOptions.some(opt => Number(opt.value) === num) : (num < minVal || num > maxVal))
                        const isModified = Number.isFinite(num) && num !== defaultVal

                        return (
                          <tr key={key} className="hover:bg-[var(--fcp-table-row-hover)] transition-colors duration-150">
                            <td className="py-3 px-4">
                              <div className="font-semibold text-[var(--fcp-text-primary)] flex items-center gap-1.5">
                                {title}
                                {(isOverridden || isModified) && (
                                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
                                    custom
                                  </span>
                                )}
                              </div>
                              <code className="text-[10px] text-[var(--fcp-text-tertiary)] font-mono mt-0.5 inline-block bg-[var(--fcp-bg-raised)] px-1 rounded border border-[var(--fcp-border-subtle)]">
                                {key}
                              </code>
                              {rationale && (
                                <p className="text-[11px] text-[var(--fcp-text-secondary)] mt-1 max-w-sm truncate" title={rationale}>
                                  {rationale}
                                </p>
                              )}
                            </td>
                            <td className="py-3 px-4 text-center">
                              {canEdit ? (
                                domainOptions ? (
                                  <div className="relative inline-block w-36">
                                    <select
                                      value={currentValStr}
                                      onChange={e => handleValueChange(key, e.target.value)}
                                      className={`w-full appearance-none rounded-lg border py-1 pl-2.5 pr-7 text-xs font-mono font-medium focus:outline-none focus:border-[#645DF6] focus:ring-2 focus:ring-[#645DF6]/20 bg-[var(--fcp-input-bg)] text-[var(--fcp-text-primary)] cursor-pointer ${
                                        isOutOfRange
                                          ? 'border-rose-500 text-rose-500'
                                          : 'border-[var(--fcp-border-strong)]'
                                      }`}
                                    >
                                      {domainOptions.map(opt => (
                                        <option
                                          key={opt.value}
                                          value={opt.value}
                                          className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]"
                                        >
                                          {opt.label}
                                        </option>
                                      ))}
                                    </select>
                                    <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2 text-[var(--fcp-text-tertiary)]">
                                      <svg className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                                        <path fillRule="evenodd" d="M5.22 8.22a.75.75 0 0 1 1.06 0L10 11.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.28a.75.75 0 0 1 0-1.06Z" clipRule="evenodd" />
                                      </svg>
                                    </div>
                                  </div>
                                ) : (
                                  <input
                                    type="number"
                                    step={a.domain?.step ?? "any"}
                                    min={minVal}
                                    max={maxVal}
                                    value={currentValStr}
                                    onChange={e => handleValueChange(key, e.target.value)}
                                    className={`w-24 text-center px-2 py-1 rounded-lg border font-mono text-xs focus:outline-none focus:border-[#645DF6] focus:ring-2 focus:ring-[#645DF6]/20 bg-[var(--fcp-input-bg)] text-[var(--fcp-text-primary)] ${
                                      isOutOfRange
                                        ? 'border-rose-500 text-rose-500'
                                        : 'border-[var(--fcp-border-strong)]'
                                    }`}
                                  />
                                )
                              ) : (
                                <span className="font-mono font-semibold text-xs text-[var(--facets-purple)] dark:text-[var(--facets-teal)]">
                                  {currentValStr}
                                </span>
                              )}
                            </td>
                            <td className="py-3 px-4 text-center font-mono text-xs text-[var(--fcp-text-secondary)]">
                              {defaultVal}
                            </td>
                            <td className="py-3 px-4 text-center font-mono text-[11px] text-[var(--fcp-text-tertiary)] whitespace-nowrap">
                              {formatAllowedRange(a)}
                            </td>
                            <td className="py-3 px-4 text-center text-xs text-[var(--fcp-text-secondary)]">
                              {unit || '—'}
                            </td>
                            {canEdit && (
                              <td className="py-3 px-4 text-right">
                                {isModified && (
                                  <button
                                    type="button"
                                    onClick={() => handleResetToDefault(a)}
                                    className="text-xs text-[var(--facets-purple)] dark:text-[var(--facets-teal)] hover:underline font-medium cursor-pointer"
                                  >
                                    Reset
                                  </button>
                                )}
                              </td>
                            )}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
