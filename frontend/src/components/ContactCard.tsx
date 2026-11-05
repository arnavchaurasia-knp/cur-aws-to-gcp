import { useEffect, useState } from 'react'

export function ContactCard() {
  const [message, setMessage] = useState('')
  const [open, setOpen] = useState(false)
  const [sent, setSent] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const submit = () => {
    const to = 'arnav.chaurasia@facets.cloud'
    const subject = 'Contact Request from Facets Cost Projection'
    const bodyText = message.trim() || 'Hello Facets Team,\n\nI would like more information on the infrastructure cost projection.'

    // Directly open Gmail compose URL in a new tab with to, subject, and message body prefilled
    const gmailUrl = `https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(to)}&su=${encodeURIComponent(subject)}&body=${encodeURIComponent(bodyText)}`
    window.open(gmailUrl, '_blank', 'noopener,noreferrer')

    setSent(true)
    setOpen(false)
  }

  if (sent) {
    return (
      <div className="bg-[var(--fcp-bg-surface)] border border-[#00C2BB]/40 rounded-xl p-4 anim-scale-in w-full overflow-hidden shadow-sm">
        <p className="text-xs uppercase tracking-wider text-[#00C2BB] mb-1 font-semibold">Gmail Opened</p>
        <p className="text-xs text-[var(--fcp-text-secondary)] leading-relaxed">
          Gmail compose has opened in a new tab addressed to{' '}
          <strong className="text-[var(--fcp-text-primary)]">arnav.chaurasia@facets.cloud</strong> with your subject and message prefilled.
        </p>
        <button
          type="button"
          onClick={() => { setSent(false); setMessage('') }}
          className="mt-2.5 text-[11px] text-[#00C2BB] hover:underline cursor-pointer"
        >
          Send another message
        </button>
      </div>
    )
  }

  return (
    <>
      <div className="bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] rounded-xl p-4 space-y-3 card-lift w-full overflow-hidden shadow-sm min-w-0">
        <div>
          <p className="text-xs uppercase tracking-wider text-[#645DF6] mb-1 font-semibold">Need help?</p>
          <p className="text-xs text-[var(--fcp-text-secondary)] leading-relaxed break-words max-w-full">
            Need help understanding your cost analysis or want a more detailed infrastructure and cost assessment? Drop a note and our team will get in touch with you.
          </p>
        </div>
        <button
          type="button"
          data-contact-btn
          onClick={() => setOpen(true)}
          style={{ color: '#ffffff', cursor: 'pointer' }}
          className="btn-shimmer w-full py-2.5 rounded-lg text-xs font-semibold !text-white cursor-pointer shadow-sm hover:opacity-95 transition-all flex items-center justify-center gap-1.5 select-none"
        >
          <svg className="w-3.5 h-3.5 shrink-0 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
          <span style={{ color: '#ffffff' }} className="!text-white font-semibold pointer-events-none">Contact Us</span>
        </button>
      </div>

      {open && (
        <div
          role="presentation"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm px-4 anim-fade-in"
          onClick={() => setOpen(false)}
          onKeyDown={e => { if (e.key === 'Escape') setOpen(false) }}>
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Contact us"
            className="w-full max-w-xl bg-[var(--fcp-bg-surface)] border border-[var(--fcp-border-subtle)] rounded-2xl shadow-2xl p-6 space-y-4 anim-scale-in text-[var(--fcp-text-primary)]"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-wider text-[#645DF6] mb-1 font-semibold">Need help?</p>
                <h2 className="text-lg font-semibold text-[var(--fcp-text-primary)]">Tell us what you're looking for</h2>
                <p className="text-sm text-[var(--fcp-text-secondary)] leading-relaxed mt-2">
                  Need help understanding your cost analysis or want a more detailed infrastructure and cost assessment? Drop a note below and our team will get in touch with you.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="text-[var(--fcp-text-tertiary)] hover:text-[var(--fcp-text-primary)] text-xl leading-none transition-colors duration-150 cursor-pointer"
                aria-label="Close">
                ×
              </button>
            </div>

            <textarea
              autoFocus
              value={message}
              onChange={e => setMessage(e.target.value)}
              placeholder="What's the context? What kind of help would be most useful?"
              maxLength={2000}
              rows={8}
              className="w-full bg-[var(--fcp-input-bg)] border border-[var(--fcp-input-border)] text-[var(--fcp-text-primary)] placeholder-[var(--fcp-text-tertiary)] rounded-lg px-3 py-3 text-sm outline-none focus:border-[#645DF6] transition-colors duration-150 resize-y"
            />

            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="px-4 py-2 text-sm text-[var(--fcp-text-secondary)] hover:text-[var(--fcp-text-primary)] transition-colors duration-150 cursor-pointer">
                Cancel
              </button>
              <button
                type="button"
                onClick={submit}
                style={{ color: '#ffffff' }}
                className="btn-shimmer px-5 py-2 rounded-lg text-sm font-semibold !text-white cursor-pointer shadow-sm hover:opacity-95 transition-all">
                <span style={{ color: '#ffffff' }} className="!text-white font-semibold">Submit Request</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
