import { useEffect, useMemo, useState } from 'react'
import type { BackupInfo, DataStoreDetail, StoreHealth } from '../../api/health'
import { getDataStore, listBackups, restoreBackup, updateDataStore } from '../../api/health'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { formatBytes, relativeTime } from './format'

interface StoreEditorProps {
  storeKey: string | null
  onClose: () => void
  onSaved: (store: StoreHealth) => void
}

export function StoreEditor({ storeKey, onClose, onSaved }: Readonly<StoreEditorProps>) {
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState<DataStoreDetail | null>(null)
  const [content, setContent] = useState('')
  const [originalContent, setOriginalContent] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [confirmSaveOpen, setConfirmSaveOpen] = useState(false)

  // Backups
  const [backups, setBackups] = useState<BackupInfo[]>([])
  const [loadingBackups, setLoadingBackups] = useState(false)
  const [confirmRestoreName, setConfirmRestoreName] = useState<string | null>(null)
  const [restoring, setRestoring] = useState(false)

  useEffect(() => {
    if (!storeKey) return
    setLoading(true)
    setError(null)

    getDataStore(storeKey)
      .then((d) => {
        setDetail(d)
        setContent(d.content || '')
        setOriginalContent(d.content || '')
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => setLoading(false))

    // Load backups
    setLoadingBackups(true)
    listBackups(storeKey)
      .then((res) => setBackups(res.backups || []))
      .catch(() => setBackups([]))
      .finally(() => setLoadingBackups(false))
  }, [storeKey])

  // Live JSON validator
  const jsonStatus = useMemo(() => {
    if (!content.trim()) {
      return { valid: false, message: 'Content is empty' }
    }
    try {
      JSON.parse(content)
      return { valid: true, message: 'Valid JSON' }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      return { valid: false, message: msg }
    }
  }, [content])

  const isDirty = content !== originalContent

  const handleSave = async () => {
    if (!storeKey || !jsonStatus.valid) return
    setSaving(true)
    setError(null)
    try {
      const res = await updateDataStore(storeKey, content)
      setOriginalContent(content)
      setConfirmSaveOpen(false)
      onSaved(res.store)

      // Refresh backups
      listBackups(storeKey).then((b) => setBackups(b.backups || []))
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const handleDownload = () => {
    if (!detail) return
    const blob = new Blob([content], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = detail.file || `${storeKey}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleRestore = async (name: string) => {
    if (!storeKey) return
    setRestoring(true)
    setError(null)
    try {
      const res = await restoreBackup(storeKey, name)
      setConfirmRestoreName(null)
      onSaved(res.store)
      // Reload store content
      const d = await getDataStore(storeKey)
      setDetail(d)
      setContent(d.content || '')
      setOriginalContent(d.content || '')
      // Reload backups
      const b = await listBackups(storeKey)
      setBackups(b.backups || [])
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setRestoring(false)
    }
  }

  if (!storeKey) return null

  const isOrderSensitive =
    storeKey === 'service_map' ||
    storeKey === 'service_specs' ||
    storeKey === 'pricing_matrix' ||
    storeKey === 'review_config'

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex justify-end anim-fade-in">
      <div className="w-full max-w-4xl bg-[var(--fcp-bg-surface)] h-full shadow-2xl flex flex-col overflow-hidden border-l border-[var(--fcp-border-subtle)]">
        {/* Header */}
        <div className="p-4 border-b border-[var(--fcp-border-subtle)] flex items-center justify-between gap-3">
          <div>
            <h3 className="text-base font-bold text-[color:var(--fcp-text-primary)]">
              {detail?.title || storeKey}
            </h3>
            <div className="text-xs text-[color:var(--fcp-text-muted)] font-mono">{detail?.file}</div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleDownload}
              disabled={loading}
              className="px-3 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] text-xs font-medium text-[color:var(--fcp-text-secondary)] hover:bg-[var(--fcp-hover-overlay)] cursor-pointer transition-colors"
            >
              Download
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-gray-500 hover:text-gray-900 dark:hover:text-white hover:bg-[var(--fcp-hover-overlay)] cursor-pointer text-sm"
              title="Close"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Content body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {detail?.description && (
            <p className="text-xs text-[color:var(--fcp-text-secondary)] leading-relaxed">
              {detail.description}
            </p>
          )}

          {isOrderSensitive && (
            <div className="p-3.5 rounded-xl bg-amber-50 dark:bg-amber-950/30 border border-amber-300 dark:border-amber-800/60 text-amber-900 dark:text-amber-200 text-xs">
              <strong>Order matters:</strong> Changes here affect every future projection. Rules are evaluated top
              to bottom — first match wins. Do not rearrange rules unless intentionally changing precedence.
            </div>
          )}

          {error && (
            <div className="p-3 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 text-rose-800 dark:text-rose-200 text-xs">
              {error}
            </div>
          )}

          {loading ? (
            <div className="py-20 text-center text-xs text-[color:var(--fcp-text-muted)]">
              Loading datastore content...
            </div>
          ) : !detail?.editable ? (
            <div className="space-y-4">
              <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-subtle)] text-xs space-y-2">
                <div className="font-semibold text-[color:var(--fcp-text-primary)]">Read-only file</div>
                <p className="text-[color:var(--fcp-text-secondary)]">
                  {detail?.detail || 'This file is generated or binary and cannot be edited by hand in the app.'}
                </p>
                <div className="pt-2 font-mono text-[11px] text-[color:var(--fcp-text-muted)]">
                  Size: {formatBytes(detail?.size_bytes || 0)}
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="font-semibold text-[color:var(--fcp-text-primary)]">JSON Content</span>
                <span
                  className={`font-mono text-[11px] flex items-center gap-1.5 ${
                    jsonStatus.valid ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'
                  }`}
                >
                  <span>{jsonStatus.valid ? '✓' : '✗'}</span>
                  <span className="truncate max-w-md">{jsonStatus.message}</span>
                </span>
              </div>

              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={18}
                spellCheck={false}
                className="w-full p-3 font-mono text-xs rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-subtle)] text-[color:var(--fcp-text-primary)] focus:outline-none focus:ring-1 focus:ring-indigo-500 whitespace-pre leading-relaxed resize-y"
              />

              <div className="flex items-center justify-between pt-2">
                <div className="text-[11px] text-[color:var(--fcp-text-muted)]">
                  {isDirty ? 'Unsaved changes' : 'No changes'} · {content.length.toLocaleString()} characters
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setContent(originalContent)}
                    disabled={!isDirty || saving}
                    className="px-3 py-1.5 rounded-lg border border-[var(--fcp-border-subtle)] text-xs font-medium text-[color:var(--fcp-text-secondary)] hover:bg-[var(--fcp-hover-overlay)] disabled:opacity-40 cursor-pointer transition-colors"
                  >
                    Discard Changes
                  </button>

                  <button
                    onClick={() => setConfirmSaveOpen(true)}
                    disabled={!isDirty || !jsonStatus.valid || saving}
                    className="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs font-semibold shadow-xs cursor-pointer transition-colors"
                  >
                    Save Changes
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Version History */}
          {detail?.editable && (
            <div className="pt-6 border-t border-[var(--fcp-border-subtle)] space-y-3">
              <h4 className="text-sm font-bold text-[color:var(--fcp-text-primary)]">Version History & Backups</h4>
              {loadingBackups ? (
                <div className="text-xs text-[color:var(--fcp-text-muted)]">Loading backups...</div>
              ) : backups.length === 0 ? (
                <div className="p-4 rounded-xl border border-[var(--fcp-border-subtle)] text-xs text-[color:var(--fcp-text-muted)] text-center">
                  No previous backups found for this file. A backup is created automatically whenever you save.
                </div>
              ) : (
                <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] divide-y divide-[var(--fcp-border-subtle)]">
                  {backups.map((b) => (
                    <div key={b.name} className="p-3 flex items-center justify-between text-xs gap-3">
                      <div>
                        <div className="font-semibold text-[color:var(--fcp-text-primary)]">
                          {relativeTime(b.created_at)}{' '}
                          <span className="text-[10px] font-normal uppercase px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300">
                            {b.kind}
                          </span>
                        </div>
                        <div className="text-[11px] font-mono text-[color:var(--fcp-text-muted)]">
                          {b.name} · {formatBytes(b.size_bytes)}
                        </div>
                      </div>

                      <button
                        onClick={() => setConfirmRestoreName(b.name)}
                        disabled={restoring || saving}
                        className="px-3 py-1 rounded-lg border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] hover:bg-[var(--fcp-hover-overlay)] text-xs font-medium text-[color:var(--fcp-text-primary)] cursor-pointer"
                      >
                        Restore
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Save Confirm Dialog */}
      <ConfirmDialog
        open={confirmSaveOpen}
        onOpenChange={setConfirmSaveOpen}
        title="Save Changes to Datastore?"
        description="A backup of the current file version will be created automatically before saving. You can restore it anytime from the version history."
        confirmLabel="Save"
        loading={saving}
        onConfirm={handleSave}
      />

      {/* Restore Confirm Dialog */}
      <ConfirmDialog
        open={confirmRestoreName !== null}
        onOpenChange={(open) => !open && setConfirmRestoreName(null)}
        title="Restore Datastore Backup?"
        description={`This will restore the file to the state saved in "${confirmRestoreName}". A backup of the current state will be created first.`}
        confirmLabel="Restore"
        destructive
        loading={restoring}
        onConfirm={() => {
          if (confirmRestoreName) return handleRestore(confirmRestoreName)
        }}
      />
    </div>
  )
}
