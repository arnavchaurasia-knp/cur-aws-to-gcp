interface StatusBadgeProps {
  status: 'ok' | 'warn' | 'fail' | 'unknown' | string
  size?: 'sm' | 'md'
}

export function StatusBadge({ status, size = 'sm' }: Readonly<StatusBadgeProps>) {
  let label = 'Not checked yet'
  let colorClasses =
    'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-300 dark:border-gray-700'
  let dotClass = 'bg-gray-400'

  switch (status) {
    case 'ok':
      label = 'Healthy'
      colorClasses =
        'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800/60'
      dotClass = 'bg-emerald-500'
      break
    case 'warn':
      label = 'Needs attention'
      colorClasses =
        'bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-800/60'
      dotClass = 'bg-amber-500'
      break
    case 'fail':
      label = 'Not working'
      colorClasses =
        'bg-rose-50 dark:bg-rose-950/40 text-rose-800 dark:text-rose-300 border-rose-200 dark:border-rose-800/60'
      dotClass = 'bg-rose-500'
      break
    case 'running':
      label = 'Running'
      colorClasses =
        'bg-blue-50 dark:bg-blue-950/40 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-800/60'
      dotClass = 'bg-blue-500 animate-pulse'
      break
    case 'retrying':
    case 'backing_off':
      label = 'Retrying'
      colorClasses =
        'bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-800/60'
      dotClass = 'bg-amber-500'
      break
    case 'escalated':
      label = 'Escalated'
      colorClasses =
        'bg-purple-50 dark:bg-purple-950/40 text-purple-800 dark:text-purple-300 border-purple-200 dark:border-purple-800/60'
      dotClass = 'bg-purple-500'
      break
    case 'paused':
      label = 'Paused'
      colorClasses =
        'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border-slate-300 dark:border-slate-700'
      dotClass = 'bg-slate-400'
      break
    case 'partial':
      label = 'Partial'
      colorClasses =
        'bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-800/60'
      dotClass = 'bg-amber-500'
      break
    case 'success':
      label = 'Success'
      colorClasses =
        'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800/60'
      dotClass = 'bg-emerald-500'
      break
    case 'failed':
      label = 'Failed'
      colorClasses =
        'bg-rose-50 dark:bg-rose-950/40 text-rose-800 dark:text-rose-300 border-rose-200 dark:border-rose-800/60'
      dotClass = 'bg-rose-500'
      break
    case 'idle':
      label = 'Idle'
      colorClasses =
        'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-300 dark:border-gray-700'
      dotClass = 'bg-gray-400'
      break
    case 'skipped':
      label = 'Skipped'
      colorClasses =
        'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-300 dark:border-gray-700'
      dotClass = 'bg-gray-400'
      break
  }

  const padding = size === 'md' ? 'px-2.5 py-1 text-xs' : 'px-2 py-0.5 text-[11px]'

  return (
    <span
      className={`inline-flex items-center gap-1.5 font-medium rounded-full border ${padding} ${colorClasses}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dotClass}`} />
      {label}
    </span>
  )
}
