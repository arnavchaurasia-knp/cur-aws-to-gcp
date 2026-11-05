import React from 'react'

interface ToggleSwitchProps {
  id?: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  label?: React.ReactNode
  description?: React.ReactNode
  size?: 'sm' | 'md'
  color?: 'teal' | 'indigo'
  className?: string
}

export function ToggleSwitch({
  id,
  checked,
  onChange,
  disabled = false,
  label,
  description,
  size = 'md',
  color = 'teal',
  className = '',
}: ToggleSwitchProps) {
  const isSm = size === 'sm'

  const activeColor = color === 'indigo' ? 'bg-[#645DF6]' : 'bg-[#00C2BB]'

  const trackClasses = `
    relative inline-flex shrink-0 cursor-pointer rounded-full transition-colors duration-200 ease-in-out
    focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-[#00C2BB]
    ${disabled ? 'opacity-40 cursor-not-allowed' : ''}
    ${checked ? activeColor : 'bg-gray-300 dark:bg-white/20 hover:bg-gray-400/80 dark:hover:bg-white/30'}
    ${isSm ? 'h-4 w-7' : 'h-6 w-11'}
  `

  const thumbClasses = `
    pointer-events-none inline-block rounded-full bg-white shadow-md transform ring-0 transition duration-200 ease-in-out
    ${isSm ? 'h-3 w-3 mt-0.5 ml-0.5' : 'h-5 w-5 mt-0.5 ml-0.5'}
    ${checked ? (isSm ? 'translate-x-3' : 'translate-x-5') : 'translate-x-0'}
  `

  const switchButton = (
    <button
      type="button"
      id={id}
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={trackClasses}
    >
      <span className="sr-only">{typeof label === 'string' ? label : 'Toggle'}</span>
      <span aria-hidden="true" className={thumbClasses} />
    </button>
  )

  if (!label && !description) {
    return <div className={`inline-flex items-center ${className}`}>{switchButton}</div>
  }

  return (
    <div className={`flex items-start justify-between gap-4 ${className}`}>
      <div
        className={`cursor-pointer select-none ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
        onClick={() => !disabled && onChange(!checked)}
      >
        {label && (
          <span className="text-sm font-medium text-[var(--fcp-text-primary)] block">
            {label}
          </span>
        )}
        {description && (
          <p className="text-xs text-[var(--fcp-text-secondary)] mt-0.5">
            {description}
          </p>
        )}
      </div>
      <div className="pt-0.5">{switchButton}</div>
    </div>
  )
}
