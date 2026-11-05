import { useEffect, useState, useMemo } from 'react'
import { Layout } from '../components/Layout'
import {
  getAssumptions,
  putAssumption,
  getAssumptionHistory,
  type Assumption,
  type AssumptionHistory,
  type AssumptionsResponse,
} from '../api/admin'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { toast } from 'sonner'
import { CATEGORY_ORDER, CATEGORY_META } from '../lib/assumptionsConstants'
import { BackButton } from '../components/BackButton'
import { triggerNotificationRefresh } from '../lib/notificationsContext'

const RATIO_OPTIONS = [
  { value: '4', label: '1:4 (compute-optimised, C4/N2D)' },
  { value: '8', label: '1:8 (balanced, N2/N4/E2)' },
  { value: '16', label: '1:16 (memory-optimised, M3)' },
]

export function AdminAssumptions({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Pricing Assumptions — Admin')
  const [data, setData] = useState<AssumptionsResponse | null>(null)
  const [history, setHistory] = useState<AssumptionHistory[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')

  // Per-row edit state: key -> value string
  const [edits, setEdits] = useState<Record<string, string>>({})
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const toggleCollapse = (cat: string) => {
    setCollapsed(prev => ({ ...prev, [cat]: !prev[cat] }))
  }

  const allCategories = useMemo(() => {
    return (data?.categories && data.categories.length > 0 ? data.categories : CATEGORY_ORDER)
  }, [data])

  const areAllCollapsed = useMemo(() => {
    return allCategories.length > 0 && allCategories.every(cat => collapsed[cat])
  }, [allCategories, collapsed])

  const toggleAllCollapse = () => {
    const nextState = !areAllCollapsed
    const updated: Record<string, boolean> = {}
    for (const cat of allCategories) {
      updated[cat] = nextState
    }
    setCollapsed(updated)
  }

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const d = await getAssumptions()
      setData(d)
      const h = await getAssumptionHistory()
      setHistory(h)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load assumptions')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const getEdit = (key: string) => edits[key] ?? ''

  const handleValueChange = (key: string, val: string) => {
    setEdits(prev => ({ ...prev, [key]: val }))
  }

  const handleCancel = (key: string) => {
    setEdits(prev => {
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const handleStartEdit = (a: Assumption) => {
    setEdits(prev => ({
      ...prev,
      [a.key]: String(a.value),
    }))
  }

  const handleResetToDefault = (key: string, defaultVal: number) => {
    handleValueChange(key, String(defaultVal))
  }

  const handleSave = async (key: string) => {
    const editVal = edits[key]
    if (editVal === undefined) return
    const num = Number(editVal)
    if (!Number.isFinite(num)) {
      toast.error('Value must be a valid number')
      return
    }
    const assumption = data?.assumptions.find(a => a.key === key)
    if (!assumption) return
    if (num < assumption.min_value || num > assumption.max_value) {
      toast.error(`Value must be between ${assumption.min_value} and ${assumption.max_value}`)
      return
    }
    setSaving(key)
    setError(null)
    try {
      await putAssumption(key, { value: num, reason: 'Direct change' })
      handleCancel(key)
      await refresh()
      triggerNotificationRefresh()
      toast.success(`Updated "${assumption.title}" to ${num}`)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Save failed'
      setError(msg)
      toast.error(msg)
    } finally {
      setSaving(null)
    }
  }

  // Filter assumptions by search
  const filteredAssumptions = useMemo(() => {
    if (!data?.assumptions) return []
    if (!searchQuery.trim()) return data.assumptions
    const q = searchQuery.toLowerCase()
    return data.assumptions.filter(a =>
      a.title.toLowerCase().includes(q) ||
      a.key.toLowerCase().includes(q) ||
      (a.rationale && a.rationale.toLowerCase().includes(q)) ||
      (a.category && a.category.toLowerCase().includes(q))
    )
  }, [data, searchQuery])

  if (loading) {
    return (
      <Layout user={user}>
        <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-16">
          <div className="flex flex-col items-center justify-center h-48 space-y-4">
            <div className="flex gap-2">
              {[0, 1, 2, 3].map((i, idx) => (
                <div
                  key={i}
                  className="w-2.5 h-2.5 rounded-full bg-[var(--facets-teal)] animate-pulse"
                  style={{ animationDelay: `${idx * 120}ms` }}
                />
              ))}
            </div>
            <p className="text-sm text-[var(--fcp-text-secondary)] font-medium">Loading pricing assumptions…</p>
          </div>
        </div>
      </Layout>
    )
  }

  if (error && !data) {
    return (
      <Layout user={user}>
        <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-16 text-center">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-rose-500/10 text-rose-500 mb-4">
            <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"/>
              <line x1="12" y1="8" x2="12" y2="12"/>
              <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
          </div>
          <p className="text-sm font-medium text-rose-500">{error}</p>
          <button
            onClick={() => { setError(null); refresh() }}
            className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--facets-purple)] text-white text-xs font-semibold hover:bg-[var(--facets-purple-hover)] transition-colors cursor-pointer shadow-xs"
          >
            Retry Loading
          </button>
        </div>
      </Layout>
    )
  }

  return (
    <Layout user={user}>
      <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 transition-all duration-200">
        <BackButton fallback="/admin" />

        {/* Page Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mb-8 pb-6 border-b border-[var(--fcp-border-subtle)]">
          <div className="space-y-1">
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold tracking-tight text-[var(--fcp-text-primary)]">
                Pricing Assumptions
              </h1>
              {data?.version && (
                <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-mono font-medium bg-[var(--facets-teal)]/10 text-[var(--facets-teal)] border border-[var(--facets-teal)]/20 shadow-2xs">
                  <svg className="w-3 h-3 opacity-70" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <line x1="4" y1="9" x2="20" y2="9"/>
                    <line x1="4" y1="15" x2="20" y2="15"/>
                    <line x1="10" y1="3" x2="8" y2="21"/>
                    <line x1="16" y1="3" x2="14" y2="21"/>
                  </svg>
                  <span>v{data.version}</span>
                </div>
              )}
            </div>
            <p className="text-sm text-[var(--fcp-text-secondary)] max-w-3xl leading-relaxed">
              Human-chosen conversion parameters utilized across the AWS → GCP projection pipeline.
              Each change is version-tracked and deterministically governs subsequent projection runs.
            </p>
          </div>

          <div className="flex items-center gap-2.5 shrink-0">
            <button
              type="button"
              onClick={refresh}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-bg-hover)] text-xs font-medium text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] transition-all cursor-pointer shadow-2xs"
              title="Refresh assumptions from server"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
              </svg>
              <span>Refresh</span>
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-6 p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-500 text-xs flex items-center justify-between">
            <div className="flex items-center gap-2">
              <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
              </svg>
              <span>{error}</span>
            </div>
            <button
              onClick={() => setError(null)}
              className="text-rose-500 hover:text-rose-600 font-semibold underline text-xs cursor-pointer ml-4"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Section Header with Search and Collapse All */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
          <div>
            <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--fcp-text-tertiary)]">
              Current Values
            </h2>
            <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">
              Select any assumption to customize its projection weight. All updates are logged in the version history.
            </p>
          </div>

          <div className="flex items-center gap-3">
            {/* Search filter */}
            <div className="relative">
              <input
                type="text"
                placeholder="Search assumptions…"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className="w-48 sm:w-64 pl-8 pr-3 py-1.5 text-xs rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-input-bg)] text-[var(--fcp-text-primary)] placeholder:text-[var(--fcp-text-tertiary)] shadow-2xs focus:border-[#645DF6] focus:outline-none focus:ring-2 focus:ring-[#645DF6]/20 transition-all"
              />
              <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-2.5 text-[var(--fcp-text-tertiary)]">
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="8"/>
                  <line x1="21" y1="21" x2="16.65" y2="16.65"/>
                </svg>
              </div>
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="absolute inset-y-0 right-0 flex items-center pr-2.5 text-[var(--fcp-text-tertiary)] hover:text-[var(--fcp-text-primary)] cursor-pointer"
                >
                  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <line x1="18" y1="6" x2="6" y2="18"/>
                    <line x1="6" y1="6" x2="18" y2="18"/>
                  </svg>
                </button>
              )}
            </div>

            {/* Collapse / Expand All toggle */}
            <button
              type="button"
              onClick={toggleAllCollapse}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-bg-hover)] text-xs font-medium text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] transition-all cursor-pointer shadow-2xs shrink-0"
            >
              <span>{areAllCollapsed ? 'Expand all' : 'Collapse all'}</span>
            </button>
          </div>
        </div>

        {/* Grouped Accordions */}
        <div className="space-y-4 mb-10">
          {allCategories.map((catKey) => {
            const meta = CATEGORY_META[catKey] ?? { label: catKey.replace(/_/g, ' '), desc: '' }
            const groupAssumptions = filteredAssumptions.filter(a => (a.category || 'other') === catKey)
            if (groupAssumptions.length === 0) return null

            const isCollapsed = Boolean(collapsed[catKey])

            return (
              <div
                key={catKey}
                className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-xs overflow-hidden transition-all duration-200"
              >
                {/* Accordion Header */}
                <button
                  type="button"
                  onClick={() => toggleCollapse(catKey)}
                  className="w-full px-5 py-3.5 bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-bg-hover)] flex items-center justify-between text-left transition-colors duration-150 cursor-pointer group select-none border-b border-[var(--fcp-border-subtle)]"
                  aria-expanded={!isCollapsed}
                >
                  <div className="flex items-center gap-3">
                    {/* Rotating Chevron with smooth easing */}
                    <div className="w-6 h-6 rounded-md bg-[var(--fcp-bg-raised)] border border-[var(--fcp-border-subtle)] flex items-center justify-center transition-all duration-200 group-hover:border-[var(--fcp-border-strong)]">
                      <svg
                        className={`w-3.5 h-3.5 text-[var(--facets-purple)] dark:text-[var(--facets-teal)] transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
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
                      <div className="flex items-center gap-2.5">
                        {/* High-visibility category heading in both light and dark mode */}
                        <h3 className="text-sm font-semibold text-[var(--fcp-text-primary)] tracking-wide group-hover:text-[var(--facets-purple)] dark:group-hover:text-[var(--facets-teal)] transition-colors">
                          {meta.label}
                        </h3>
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-mono font-medium bg-[var(--fcp-bg-raised)] text-[var(--fcp-text-secondary)] border border-[var(--fcp-border-subtle)]">
                          {groupAssumptions.length}
                        </span>
                      </div>
                      {meta.desc && (
                        <p className="text-xs text-[var(--fcp-text-tertiary)] mt-0.5 font-normal">
                          {meta.desc}
                        </p>
                      )}
                    </div>
                  </div>

                  <span className="text-xs font-medium text-[var(--fcp-text-tertiary)] group-hover:text-[var(--fcp-text-secondary)] transition-colors hidden sm:inline-block">
                    {isCollapsed ? 'Click to expand' : 'Click to collapse'}
                  </span>
                </button>

                {/* Animated Accordion Content */}
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
                    <table className="w-full text-left border-collapse min-w-[960px] table-fixed">
                      <colgroup>
                        <col className="w-[30%]" />
                        <col className="w-[21%]" />
                        <col className="w-[9%]" />
                        <col className="w-[7%]" />
                        <col className="w-[9%]" />
                        <col className="w-[10%]" />
                        <col className="w-[14%]" />
                      </colgroup>
                      <thead>
                        <tr className="border-b border-[var(--fcp-border-subtle)] bg-[var(--fcp-table-header-bg)] text-[var(--fcp-text-tertiary)] text-xs uppercase tracking-wider font-semibold">
                          <th className="py-3 px-5 text-left">Assumption</th>
                          <th className="py-3 px-4 text-center">Current Value</th>
                          <th className="py-3 px-3 text-center">Default</th>
                          <th className="py-3 px-3 text-center">Unit</th>
                          <th className="py-3 px-3 text-center">Allowed Range</th>
                          <th className="py-3 px-3 text-left">Last Updated</th>
                          <th className="py-3 px-5 text-right whitespace-nowrap min-w-[150px]">Action</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[var(--fcp-border-subtle)] text-xs">
                        {groupAssumptions.map((a) => {
                          const isEditing = a.key in edits
                          const editVal = getEdit(a.key)
                          const editNum = Number(editVal)
                          const inRange = !editVal || (Number.isFinite(editNum) && editNum >= a.min_value && editNum <= a.max_value)
                          const isModifiedFromDefault = a.default_value !== undefined && a.value !== a.default_value

                          return (
                            <tr
                              key={a.key}
                              className={`transition-colors duration-150 ${
                                isEditing
                                  ? 'bg-[var(--fcp-bg-raised)]'
                                  : 'hover:bg-[var(--fcp-table-row-hover)]'
                              }`}
                            >
                              {/* Assumption details structured cleanly */}
                              <td className="py-4 px-5 align-top">
                                <div className="space-y-1.5 pr-2">
                                  <div className="flex items-center gap-2 flex-wrap">
                                    <span className="font-semibold text-sm text-[var(--fcp-text-primary)]">
                                      {a.title}
                                    </span>
                                    {isModifiedFromDefault && (
                                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
                                        Customized
                                      </span>
                                    )}
                                  </div>
                                  <div>
                                    <code className="inline-block text-[11px] font-mono text-[var(--fcp-text-tertiary)] bg-[var(--fcp-bg-raised)] px-1.5 py-0.5 rounded border border-[var(--fcp-border-subtle)] select-all">
                                      {a.key}
                                    </code>
                                  </div>
                                  {a.rationale && (
                                    <p
                                      className="text-xs text-[var(--fcp-text-secondary)] leading-relaxed mt-1"
                                      title={a.rationale}
                                    >
                                      {a.rationale}
                                    </p>
                                  )}
                                </div>
                              </td>

                              {/* Current Value - Centered */}
                              <td className="py-4 px-4 align-top text-center">
                                {isEditing ? (
                                  <div className="flex items-center justify-center w-full max-w-[210px] mx-auto">
                                    {a.key === 'aurora_acu_ram_gib' ? (
                                      <div className="relative w-full">
                                        <select
                                          value={editVal}
                                          onChange={e => handleValueChange(a.key, e.target.value)}
                                          onKeyDown={e => {
                                            if (e.key === 'Enter') handleSave(a.key)
                                            if (e.key === 'Escape') handleCancel(a.key)
                                          }}
                                          autoFocus
                                          className="w-full appearance-none rounded-lg border border-[var(--fcp-border-strong)] bg-[var(--fcp-input-bg)] py-1.5 pl-3 pr-8 text-xs font-medium text-[var(--fcp-text-primary)] shadow-2xs focus:border-[#645DF6] focus:outline-none focus:ring-2 focus:ring-[#645DF6]/20 transition-all cursor-pointer"
                                        >
                                          {RATIO_OPTIONS.map(opt => (
                                            <option
                                              key={opt.value}
                                              value={opt.value}
                                              className="bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-primary)]"
                                            >
                                              {opt.label}
                                            </option>
                                          ))}
                                        </select>
                                        <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2.5 text-[var(--fcp-text-tertiary)]">
                                          <svg className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                                            <path fillRule="evenodd" d="M5.22 8.22a.75.75 0 0 1 1.06 0L10 11.94l3.72-3.72a.75.75 0 1 1 1.06 1.06l-4.25 4.25a.75.75 0 0 1-1.06 0L5.22 9.28a.75.75 0 0 1 0-1.06Z" clipRule="evenodd" />
                                          </svg>
                                        </div>
                                      </div>
                                    ) : (
                                      <div className="w-full flex flex-col items-center">
                                        <input
                                          type="number"
                                          step="any"
                                          min={a.min_value}
                                          max={a.max_value}
                                          value={editVal}
                                          onChange={e => handleValueChange(a.key, e.target.value)}
                                          onKeyDown={e => {
                                            if (e.key === 'Enter') handleSave(a.key)
                                            if (e.key === 'Escape') handleCancel(a.key)
                                          }}
                                          autoFocus
                                          className="w-28 text-center font-mono text-xs font-semibold rounded-lg border border-[var(--fcp-border-strong)] bg-[var(--fcp-input-bg)] px-2.5 py-1.5 text-[var(--fcp-text-primary)] shadow-2xs focus:border-[#645DF6] focus:outline-none focus:ring-2 focus:ring-[#645DF6]/20 transition-all"
                                        />
                                        {editVal && !inRange && (
                                          <span className="text-[10px] text-rose-500 font-medium mt-1">
                                            Range: {a.min_value}–{a.max_value}
                                          </span>
                                        )}
                                      </div>
                                    )}
                                  </div>
                                ) : (
                                  <div className="flex flex-col items-center justify-center">
                                    <span className="inline-flex items-center justify-center font-mono font-semibold text-xs px-3 py-1 rounded-md bg-[var(--facets-purple)]/10 text-[var(--facets-purple)] dark:bg-[var(--facets-teal)]/10 dark:text-[var(--facets-teal)] border border-[var(--facets-purple)]/20 dark:border-[var(--facets-teal)]/20 tabular-nums shadow-2xs">
                                      {a.key === 'aurora_acu_ram_gib' ? `1:${a.value} ratio` : a.value}
                                    </span>
                                  </div>
                                )}
                              </td>

                              {/* Default Value Column */}
                              <td className="py-4 px-3 align-top text-center">
                                <div className="flex flex-col items-center justify-center pt-1">
                                  <span className="font-mono text-xs text-[var(--fcp-text-secondary)] tabular-nums">
                                    {a.key === 'aurora_acu_ram_gib' ? `1:${a.default_value}` : a.default_value}
                                  </span>
                                  {isEditing && editVal !== String(a.default_value) && (
                                    <button
                                      type="button"
                                      onClick={() => handleResetToDefault(a.key, a.default_value)}
                                      className="text-[10px] text-[var(--facets-purple)] dark:text-[var(--facets-teal)] hover:underline mt-1 font-medium cursor-pointer"
                                      title="Reset input to system default"
                                    >
                                      Use default
                                    </button>
                                  )}
                                </div>
                              </td>

                              {/* Unit Column */}
                              <td className="py-4 px-3 align-top text-center">
                                <div className="pt-1">
                                  <span className="inline-block text-xs text-[var(--fcp-text-secondary)] font-mono">
                                    {a.unit || '—'}
                                  </span>
                                </div>
                              </td>

                              {/* Allowed Range Column */}
                              <td className="py-4 px-3 align-top text-center">
                                <div className="pt-1">
                                  <span className="inline-block font-mono text-[11px] text-[var(--fcp-text-tertiary)] bg-[var(--fcp-bg-raised)] px-2 py-0.5 rounded border border-[var(--fcp-border-subtle)] whitespace-nowrap">
                                    [{a.min_value} – {a.max_value}]
                                  </span>
                                </div>
                              </td>

                              {/* Last Updated Column */}
                              <td className="py-4 px-3 align-top text-left">
                                <div className="space-y-0.5 pt-0.5 max-w-[120px] overflow-hidden">
                                  <div className="text-xs font-medium text-[var(--fcp-text-secondary)] truncate" title={a.updated_by || 'Default'}>
                                    {a.updated_by ? a.updated_by.split('@')[0] : 'System'}
                                  </div>
                                  <div className="text-[11px] text-[var(--fcp-text-tertiary)] font-mono">
                                    {a.updated_at ? a.updated_at.split(' ')[0] : '—'}
                                  </div>
                                </div>
                              </td>

                              {/* Action Column */}
                              <td className="py-4 px-5 align-top text-right whitespace-nowrap min-w-[150px]">
                                <div className="flex items-center gap-2 justify-end pt-0.5">
                                  {isEditing ? (
                                    <>
                                      {/* Save Button */}
                                      <button
                                        type="button"
                                        onClick={() => handleSave(a.key)}
                                        disabled={!editVal || !inRange || saving === a.key}
                                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold shadow-xs transition-all duration-150 cursor-pointer disabled:cursor-not-allowed bg-[var(--facets-purple)] hover:bg-[var(--facets-purple-hover)] text-white disabled:bg-slate-200 dark:disabled:bg-slate-800 disabled:text-slate-400 dark:disabled:text-slate-500 disabled:shadow-none shrink-0"
                                        title={saving === a.key ? 'Saving…' : 'Save change (Enter)'}
                                      >
                                        {saving === a.key ? (
                                          <>
                                            <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
                                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                                            </svg>
                                            <span>Saving</span>
                                          </>
                                        ) : (
                                          <>
                                            <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                              <polyline points="20 6 9 17 4 12" />
                                            </svg>
                                            <span>Save</span>
                                          </>
                                        )}
                                      </button>

                                      {/* Cancel Button */}
                                      <button
                                        type="button"
                                        onClick={() => handleCancel(a.key)}
                                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-bg-hover)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] text-xs font-medium transition-all duration-150 shadow-2xs hover:border-[var(--fcp-border-strong)] cursor-pointer shrink-0"
                                        title="Cancel editing (Esc)"
                                      >
                                        <svg className="w-3 h-3 text-[var(--fcp-text-tertiary)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                          <line x1="18" y1="6" x2="6" y2="18" />
                                          <line x1="6" y1="6" x2="18" y2="18" />
                                        </svg>
                                        <span>Cancel</span>
                                      </button>
                                    </>
                                  ) : (
                                    <button
                                      type="button"
                                      onClick={() => handleStartEdit(a)}
                                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-bg-hover)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] text-xs font-medium transition-all duration-150 shadow-2xs hover:border-[var(--fcp-border-strong)] cursor-pointer group"
                                      title="Edit assumption value"
                                    >
                                      <svg className="w-3 h-3 text-[var(--fcp-text-tertiary)] group-hover:text-[var(--facets-purple)] dark:group-hover:text-[var(--facets-teal)] transition-colors" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>
                                        <path d="m15 5 4 4"/>
                                      </svg>
                                      <span>Edit</span>
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
                </div>
              </div>
            )
          })}
        </div>

        {/* Change History Card */}
        <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] shadow-xs overflow-hidden">
          <div className="bg-[var(--fcp-table-header-bg)] border-b border-[var(--fcp-border-subtle)] px-5 py-4 flex items-center justify-between">
            <div>
              <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--fcp-text-primary)]">
                Change History
              </h2>
              <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">
                Audit trail of historical value updates and justification logs.
              </p>
            </div>
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-mono font-medium bg-[var(--fcp-bg-raised)] text-[var(--fcp-text-secondary)] border border-[var(--fcp-border-subtle)]">
              {history.length} {history.length === 1 ? 'entry' : 'entries'}
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse min-w-[700px]">
              <thead>
                <tr className="border-b border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] text-[var(--fcp-text-tertiary)] text-xs uppercase tracking-wider font-semibold">
                  <th className="py-3 px-5">When</th>
                  <th className="py-3 px-5">Assumption</th>
                  <th className="py-3 px-5">Old → New</th>
                  <th className="py-3 px-5">Changed By</th>
                  <th className="py-3 px-5">Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--fcp-border-subtle)] text-xs">
                {history.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-8 px-5 text-center text-[var(--fcp-text-tertiary)]">
                      No changes recorded yet. Assumptions remain on system baseline.
                    </td>
                  </tr>
                ) : (
                  history.map((h) => (
                    <tr key={h.id} className="hover:bg-[var(--fcp-table-row-hover)] transition-colors duration-150">
                      <td className="py-3.5 px-5 text-[var(--fcp-text-secondary)] font-mono whitespace-nowrap">
                        {h.changed_at}
                      </td>
                      <td className="py-3.5 px-5">
                        <code className="text-xs font-mono text-[var(--fcp-text-primary)] bg-[var(--fcp-bg-raised)] px-1.5 py-0.5 rounded border border-[var(--fcp-border-subtle)]">
                          {h.key}
                        </code>
                      </td>
                      <td className="py-3.5 px-5">
                        <div className="flex items-center gap-1.5 font-mono text-xs">
                          <span className="text-[var(--fcp-text-tertiary)] line-through">{h.old_value}</span>
                          <span className="text-[var(--fcp-text-tertiary)]">→</span>
                          <span className="font-semibold text-[var(--facets-purple)] dark:text-[var(--facets-teal)]">
                            {h.new_value}
                          </span>
                        </div>
                      </td>
                      <td className="py-3.5 px-5 text-[var(--fcp-text-secondary)] font-medium">
                        {h.changed_by}
                      </td>
                      <td className="py-3.5 px-5 text-[var(--fcp-text-primary)] max-w-sm truncate" title={h.reason}>
                        {h.reason}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Layout>
  )
}
