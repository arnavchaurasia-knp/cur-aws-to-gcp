import { createPortal } from 'react-dom'
import { useEffect, useState, useMemo } from 'react'
import { Layout } from '../components/Layout'
import { listUploadedBills, previewUploadedBill, deleteUploadedBill, type UploadedBill, type BillPreviewResponse } from '../api/admin'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { toast } from 'sonner'
import { triggerNotificationRefresh } from '../lib/notificationsContext'
import type { UserInfo } from '../api/auth'
import { useTitle } from '../lib/useTitle'
import { BackButton } from '../components/BackButton'

function fmtBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

export function Bills({ user }: Readonly<{ user: UserInfo }>) {
  useTitle('Uploaded Bills — admin')
  const [bills, setBills] = useState<UploadedBill[]>([])
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)

  // Preview modal state
  const [previewModalOpen, setPreviewModalOpen] = useState(false)
  const [previewData, setPreviewData] = useState<BillPreviewResponse | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [selectedBill, setSelectedBill] = useState<UploadedBill | null>(null)

  const [deleteTarget, setDeleteTarget] = useState<UploadedBill | null>(null)
  const [deleting, setDeleting] = useState(false)

  const isAdmin = Boolean(user?.is_admin)

  const handleDeleteBill = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteUploadedBill(deleteTarget.content_hash)
      toast.success(`Bill "${deleteTarget.file_name || deleteTarget.content_hash.slice(0, 12)}" deleted`)
      setDeleteTarget(null)
      await refreshBills()
      triggerNotificationRefresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete bill')
    } finally {
      setDeleting(false)
    }
  }

  const refreshBills = async () => {
    setError(null)
    setLoading(true)
    try {
      const res = await listUploadedBills()
      setBills(res.bills || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to load bills')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refreshBills()
  }, [])

  // Close preview modal on Escape key
  useEffect(() => {
    if (!previewModalOpen) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPreviewModalOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [previewModalOpen])


  const handleOpenPreview = async (bill: UploadedBill) => {
    setSelectedBill(bill)
    setPreviewModalOpen(true)
    setPreviewLoading(true)
    setPreviewError(null)
    setPreviewData(null)

    // For non-PDF files, fetch structured lines preview
    const isPdf = bill.input_ext?.toLowerCase() === '.pdf' || bill.file_name?.toLowerCase().endsWith('.pdf')
    if (isPdf) {
      setPreviewLoading(false)
      return
    }

    try {
      const data = await previewUploadedBill(bill.content_hash)
      setPreviewData(data)
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : 'Failed to preview file contents')
    } finally {
      setPreviewLoading(false)
    }
  }

  const filteredBills = useMemo(() => {
    if (!search.trim()) return bills
    const q = search.toLowerCase().trim()
    return bills.filter(
      b =>
        b.file_name?.toLowerCase().includes(q) ||
        b.content_hash?.toLowerCase().includes(q) ||
        b.input_ext?.toLowerCase().includes(q)
    )
  }, [bills, search])

  return (
    <Layout user={user}>
      <div className="max-w-6xl mx-auto px-6 py-8">
        <BackButton fallback="/admin" />
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-white">Uploaded Bills</h1>
            <p className="text-sm text-gray-400 mt-1">
              All AWS bill files stored in the database. Download original bills or view their contents and schemas.
            </p>
          </div>
        </div>

        {error && (
          <div className="mb-6 p-3 rounded-lg bg-orange-950/40 border border-orange-500/40 text-orange-400 text-xs">
            {error}
          </div>
        )}

        {/* Filter and stats toolbar */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 mb-4">
          <div className="relative flex-1 max-w-md">
            <span className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-500">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
            </span>
            <input
              type="text"
              placeholder="Search by filename or content hash..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full pl-9 pr-4 py-2 bg-white/[0.03] border border-white/10 rounded-lg text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-[#00C2BB]/60 focus:ring-1 focus:ring-[#00C2BB]/60 transition"
            />
          </div>

          <button
            onClick={refreshBills}
            disabled={loading}
            className="inline-flex items-center justify-center gap-1.5 px-3 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg text-xs font-medium text-gray-300 transition cursor-pointer"
          >
            <svg
              className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            Refresh List
          </button>
        </div>

        {/* Bills Table */}
        <div className="border border-white/10 rounded-lg overflow-hidden bg-black/30">
          <div className="bg-white/[0.02] border-b border-white/10 px-5 py-4 flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">Database Bills</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                {filteredBills.length} of {bills.length} bill{bills.length !== 1 ? 's' : ''} shown
              </p>
            </div>
            <span className="text-[11px] font-mono text-gray-400 bg-white/5 px-2.5 py-1 rounded border border-white/10">
              Total Storage: {fmtBytes(bills.reduce((acc, b) => acc + (b.file_size || 0), 0))}
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-white/10 bg-white/[0.02]">
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">File Name</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Type</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Size</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5">Uploaded At</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5 font-mono">Content Hash</th>
                  <th className="pb-3 pt-4 text-gray-400 text-xs px-5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {filteredBills.length === 0 ? (
                  <tr>
                    <td className="px-5 py-8 text-center text-gray-500 text-xs" colSpan={6}>
                      {bills.length === 0 ? 'No bills uploaded yet.' : 'No bills matching your search filter.'}
                    </td>
                  </tr>
                ) : (
                  filteredBills.map(b => (
                    <tr key={b.content_hash} className="hover:bg-white/[0.02] transition-colors group">
                      <td className="px-5 py-4 text-gray-200 font-medium flex items-center gap-2">
                        <svg
                          className="w-4 h-4 text-[#00C2BB] shrink-0"
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={1.8}
                            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                          />
                        </svg>
                        <span className="truncate max-w-[280px]" title={b.file_name}>
                          {b.file_name || 'unnamed_bill'}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-gray-400 text-xs">
                        <span className="px-2 py-0.5 rounded bg-white/5 border border-white/10 uppercase text-[10px] font-mono tracking-wide">
                          {b.input_ext?.replace('.', '') || 'UNKNOWN'}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-gray-400 text-xs whitespace-nowrap">{fmtBytes(b.file_size)}</td>
                      <td className="px-5 py-4 text-gray-400 text-xs whitespace-nowrap">
                        {new Date(b.created_at).toLocaleString()}
                      </td>
                      <td
                        className="px-5 py-4 text-gray-400 text-xs font-mono truncate max-w-[150px]"
                        title={b.content_hash}
                      >
                        {b.content_hash.slice(0, 12)}…{b.content_hash.slice(-4)}
                      </td>
                      <td className="px-5 py-4 text-right whitespace-nowrap">
                        <div className="flex items-center justify-end gap-2">
                          {/* View Button */}
                          <button
                            onClick={() => handleOpenPreview(b)}
                            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-white/5 hover:bg-[#00C2BB]/20 hover:text-[#00C2BB] text-gray-300 text-xs font-medium border border-white/10 hover:border-[#00C2BB]/40 transition cursor-pointer"
                            title="View / inspect bill"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth={2}
                                d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
                              />
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth={2}
                                d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"
                              />
                            </svg>
                            View
                          </button>

                          {/* Direct Download Button */}
                          <a
                            href={`/api/admin/bills/${encodeURIComponent(b.content_hash)}/download`}
                            download={b.file_name || undefined}
                            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-[#00C2BB]/10 hover:bg-[#00C2BB]/20 text-[#00C2BB] text-xs font-medium border border-[#00C2BB]/30 hover:border-[#00C2BB]/60 transition cursor-pointer"
                            title="Download original file"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth={2}
                                d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                              />
                            </svg>
                            Download
                          </a>

                          {/* Delete Button */}
                          {isAdmin && (
                            <button
                              type="button"
                              onClick={() => setDeleteTarget(b)}
                              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-red-500/10 hover:bg-red-500/20 text-red-400 hover:text-red-300 text-xs font-medium border border-red-500/30 hover:border-red-500/50 transition cursor-pointer"
                              title="Delete bill"
                            >
                              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                              </svg>
                              Delete
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {!isAdmin && (
          <div className="mt-6 p-4 rounded-lg bg-amber-950/20 border border-amber-500/30 text-amber-400 text-xs">
            <p className="font-medium">Admin access required</p>
            <p className="text-[10px] text-amber-400/70 mt-1">
              Only administrators can view and download database bills and manage notification settings.
            </p>
          </div>
        )}
      </div>

      {/* Bill Preview Modal */}
      {previewModalOpen && selectedBill && createPortal(
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center p-4 sm:p-6 bg-black/80 backdrop-blur-md animate-in fade-in duration-150"
          onClick={(e) => { if (e.target === e.currentTarget) setPreviewModalOpen(false) }}
          role="dialog"
          aria-modal="true"
        >
          <div className="relative w-full max-w-5xl max-h-[90vh] bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-default)] rounded-2xl shadow-2xl flex flex-col overflow-hidden text-[var(--fcp-text-primary)]">
            {/* Modal Header */}
            <div className="px-6 py-4 border-b border-[var(--fcp-border-subtle)] flex items-center justify-between bg-[var(--fcp-bg-surface)] shrink-0">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-[#00C2BB]/10 border border-[#00C2BB]/30 flex items-center justify-center text-[#00C2BB] shrink-0">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                    />
                  </svg>
                </div>
                <div>
                  <h3 className="text-base font-semibold text-[var(--fcp-text-primary)] truncate max-w-lg">
                    {selectedBill.file_name}
                  </h3>
                  <div className="flex items-center gap-3 text-xs text-[var(--fcp-text-secondary)] mt-0.5">
                    <span>Size: {fmtBytes(selectedBill.file_size)}</span>
                    <span>•</span>
                    <span className="font-mono text-[11px]">{selectedBill.content_hash.slice(0, 20)}…</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <a
                  href={`/api/admin/bills/${encodeURIComponent(selectedBill.content_hash)}/view`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1.5 rounded-lg bg-[var(--fcp-bg-elevated)] hover:bg-[var(--fcp-bg-hover)] text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] text-xs font-medium border border-[var(--fcp-border-subtle)] inline-flex items-center gap-1.5 transition cursor-pointer"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                    />
                  </svg>
                  Open Raw
                </a>
                <a
                  href={`/api/admin/bills/${encodeURIComponent(selectedBill.content_hash)}/download`}
                  download={selectedBill.file_name || undefined}
                  className="px-3 py-1.5 rounded-lg bg-[#00C2BB] hover:bg-[#00C2BB]/90 text-black text-xs font-semibold inline-flex items-center gap-1.5 transition cursor-pointer"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
                    />
                  </svg>
                  Download File
                </a>
                <button
                  type="button"
                  onClick={() => setPreviewModalOpen(false)}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 hover:text-red-300 border border-red-500/20 text-xs font-semibold transition cursor-pointer ml-1"
                  title="Close preview (Esc)"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                  <span>Close</span>
                </button>
              </div>
            </div>

            {/* Modal Content */}
            <div className="flex-1 overflow-y-auto p-6 bg-[var(--fcp-bg-surface)]">
              {previewLoading ? (
                <div className="flex flex-col items-center justify-center py-20 text-[var(--fcp-text-secondary)] space-y-3">
                  <svg className="w-8 h-8 animate-spin text-[#00C2BB]" fill="none" viewBox="0 0 24 24">
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                    />
                    <path
                      className="opacity-75"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                    />
                  </svg>
                  <p className="text-sm">Reading bill sample lines...</p>
                </div>
              ) : previewError ? (
                <div className="p-4 rounded-xl bg-orange-950/30 border border-orange-500/30 text-orange-300 text-sm">
                  <p className="font-semibold mb-1">Unable to preview bill content</p>
                  <p className="text-xs text-orange-400/80">{previewError}</p>
                </div>
              ) : selectedBill.input_ext?.toLowerCase() === '.pdf' ||
                selectedBill.file_name?.toLowerCase().endsWith('.pdf') ? (
                <div className="h-[65vh] w-full rounded-lg overflow-hidden border border-[var(--fcp-border-subtle)]">
                  <iframe
                    src={`/api/admin/bills/${encodeURIComponent(selectedBill.content_hash)}/view`}
                    className="w-full h-full bg-white"
                    title="PDF Viewer"
                  />
                </div>
              ) : previewData?.lines && previewData.lines.length > 0 ? (
                <div className="space-y-3">
                  <div className="flex items-center justify-between text-xs text-[var(--fcp-text-secondary)] px-1">
                    <span>
                      Showing first {previewData.lines.length} lines of file
                      {previewData.is_truncated && ' (truncated for quick inspection)'}
                    </span>
                    <span className="font-mono text-[var(--fcp-text-tertiary)]">{selectedBill.input_ext}</span>
                  </div>

                  <div className="rounded-xl border border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] p-4 overflow-x-auto font-mono text-xs leading-relaxed max-h-[60vh] select-text">
                    <table className="w-full border-collapse">
                      <tbody>
                        {previewData.lines.map((line, idx) => (
                          <tr
                            key={idx}
                            className={`transition-colors ${
                              idx === 0
                                ? 'text-[#00C2BB] font-semibold bg-[var(--fcp-bg-elevated)]'
                                : 'hover:bg-[var(--fcp-bg-hover)]'
                            }`}
                          >
                            <td className="pr-4 py-1 text-[var(--fcp-text-tertiary)] select-none text-right font-mono text-[11px] w-12 border-r border-[var(--fcp-border-subtle)]">
                              {idx + 1}
                            </td>
                            <td className="pl-4 py-1 whitespace-pre break-all font-mono text-[var(--fcp-text-primary)]">
                              {line}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
                <div className="text-center py-16 text-[var(--fcp-text-secondary)] text-sm">
                  No preview text available. You can download the file directly via the button above.
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-3 border-t border-[var(--fcp-border-subtle)] bg-[var(--fcp-bg-surface)] flex items-center justify-between shrink-0">
              <span className="text-xs text-[var(--fcp-text-secondary)]">
                Press <kbd className="px-1.5 py-0.5 rounded bg-white/10 border border-white/10 font-mono text-[10px] text-[var(--fcp-text-primary)]">Esc</kbd> or click outside to exit
              </span>
              <button
                type="button"
                onClick={() => setPreviewModalOpen(false)}
                className="px-4 py-1.5 rounded-lg bg-[var(--fcp-bg-elevated)] hover:bg-[var(--fcp-bg-hover)] text-[var(--fcp-text-primary)] border border-[var(--fcp-border-subtle)] text-xs font-medium transition cursor-pointer"
              >
                Close Preview
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
        title="Delete Bill"
        description={`Are you sure you want to delete "${deleteTarget?.file_name || deleteTarget?.content_hash?.slice(0, 12)}"? This will permanently remove the stored bill file and database record.`}
        confirmLabel="Delete bill"
        destructive
        loading={deleting}
        onConfirm={handleDeleteBill}
      />
    </Layout>
  )
}
