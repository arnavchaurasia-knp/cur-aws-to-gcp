import * as React from 'react'
import * as AlertDialogPrimitive from '@radix-ui/react-alert-dialog'

export interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: React.ReactNode
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
  loading?: boolean
  requireTypedWord?: string // e.g. "STOP"
  onConfirm: () => void | Promise<void>
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  destructive = false,
  loading = false,
  requireTypedWord,
  onConfirm,
}: ConfirmDialogProps) {
  const [typedInput, setTypedInput] = React.useState('')
  const cancelRef = React.useRef<HTMLButtonElement>(null)

  // Reset typed input whenever dialog opens
  React.useEffect(() => {
    if (open) {
      setTypedInput('')
    }
  }, [open])

  const isTypedMatch = !requireTypedWord || typedInput.trim() === requireTypedWord

  const handleAction = async (e: React.MouseEvent) => {
    e.preventDefault()
    if (!isTypedMatch || loading) return
    await onConfirm()
  }

  return (
    <AlertDialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialogPrimitive.Portal>
        <AlertDialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm anim-fade-in" />
        <AlertDialogPrimitive.Content
          onOpenAutoFocus={(e) => {
            // Focus cancel by default so Enter key does NOT accidentally fire confirm
            e.preventDefault()
            cancelRef.current?.focus()
          }}
          className="fixed left-[50%] top-[50%] z-50 w-full max-w-md translate-x-[-50%] translate-y-[-50%] rounded-xl border border-white/10 bg-[#12121a] p-6 shadow-2xl anim-scale-in text-white focus:outline-none"
        >
          <AlertDialogPrimitive.Title className="text-lg font-semibold tracking-tight text-white mb-2">
            {title}
          </AlertDialogPrimitive.Title>

          <AlertDialogPrimitive.Description className="text-sm text-gray-300 leading-relaxed mb-4">
            {description}
          </AlertDialogPrimitive.Description>

          {requireTypedWord && (
            <div className="mb-5 space-y-2">
              <label htmlFor="confirm-typed-input" className="block text-xs text-gray-400">
                Type <strong className="text-red-400 font-mono font-bold select-all">{requireTypedWord}</strong> to confirm:
              </label>
              <input
                id="confirm-typed-input"
                type="text"
                value={typedInput}
                onChange={(e) => setTypedInput(e.target.value)}
                placeholder={requireTypedWord}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white font-mono placeholder:text-gray-600 focus:border-red-500/50"
                autoComplete="off"
                disabled={loading}
              />
            </div>
          )}

          <div className="flex items-center justify-end gap-3 mt-6">
            <AlertDialogPrimitive.Cancel asChild>
              <button
                ref={cancelRef}
                type="button"
                disabled={loading}
                className="px-4 py-2 text-sm text-gray-300 hover:text-white bg-white/5 hover:bg-white/10 rounded-lg transition-colors duration-150 disabled:opacity-40 cursor-pointer"
              >
                {cancelLabel}
              </button>
            </AlertDialogPrimitive.Cancel>

            <AlertDialogPrimitive.Action asChild>
              <button
                type="button"
                onClick={handleAction}
                disabled={!isTypedMatch || loading}
                className={`px-4 py-2 text-sm font-semibold rounded-lg transition-colors duration-150 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer ${
                  destructive
                    ? 'bg-red-600 hover:bg-red-500 text-white'
                    : 'bg-[#00C2BB] hover:bg-[#00d8d0] text-black'
                }`}
              >
                {loading ? 'Processing…' : confirmLabel}
              </button>
            </AlertDialogPrimitive.Action>
          </div>
        </AlertDialogPrimitive.Content>
      </AlertDialogPrimitive.Portal>
    </AlertDialogPrimitive.Root>
  )
}
