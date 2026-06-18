import { useEffect, useState } from 'react'
import { submitContactInterest } from '../api/contact'

export function ContactCard() {
  const [message, setMessage] = useState('')
  const [open, setOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await submitContactInterest(message.trim())
      setSent(true)
      setOpen(false)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  if (sent) {
    return (
      <div className="bg-white/[0.02] border border-white/10 rounded-lg p-4">
        <p className="text-xs uppercase tracking-wider text-[#00C2BB] mb-1">Request submitted</p>
        <p className="text-xs text-gray-400 leading-relaxed">
          Thanks for reaching out. Our team will review your request and get back to you shortly!
        </p>
      </div>
    )
  }

  return (
    <>
      <div className="bg-white/[0.02] border border-white/10 rounded-lg p-4 space-y-3">
        <div>
          <p className="text-xs uppercase tracking-wider text-[#645DF6] mb-1">Need help?</p>
          <p className="text-xs text-gray-400 leading-relaxed">
            Need help understanding your cost analysis or want a more detailed infrastructure and cost assessment? Drop a note and our team will get in touch with you.
          </p>
        </div>
        <button
          onClick={() => setOpen(true)}
          className="w-full py-2 rounded-lg text-xs font-medium text-white
            bg-gradient-to-r from-[#645DF6] to-[#00C2BB] transition">
          Contact us
        </button>
      </div>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm px-4"
          onClick={() => !submitting && setOpen(false)}>
          <div
            className="w-full max-w-xl bg-[#0a0a0f] border border-white/10 rounded-2xl shadow-2xl p-6 space-y-4"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-wider text-[#645DF6] mb-1">Need help?</p>
                <h2 className="text-lg font-semibold text-white">Tell us what you're looking for</h2>
                <p className="text-sm text-gray-400 leading-relaxed mt-2">
                  Need help understanding your cost analysis or want a more detailed infrastructure and cost assessment? Drop a note below and our team will get in touch with you.
                </p>
              </div>
              <button
                onClick={() => !submitting && setOpen(false)}
                className="text-gray-500 hover:text-gray-300 text-xl leading-none"
                aria-label="Close">
                ×
              </button>
            </div>

            <textarea
              autoFocus
              value={message}
              onChange={e => setMessage(e.target.value)}
              placeholder="What's the context? What kind of help would be most useful?"
              rows={8}
              className="w-full bg-white/5 border border-white/20 rounded-lg px-3 py-3 text-sm outline-none focus:border-[#645DF6] resize-y"
            />

            {error && <p className="text-xs text-orange-400">{error}</p>}

            <div className="flex items-center justify-end gap-3">
              <button
                onClick={() => !submitting && setOpen(false)}
                disabled={submitting}
                className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition
                  disabled:opacity-40 disabled:cursor-not-allowed">
                Cancel
              </button>
              <button
                onClick={submit}
                disabled={submitting}
                className="px-5 py-2 rounded-lg text-sm font-medium text-white
                  bg-gradient-to-r from-[#645DF6] to-[#00C2BB]
                  disabled:opacity-40 disabled:cursor-not-allowed transition">
                {submitting ? 'Sending…' : 'Submit request'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
