export function relativeTime(isoOrSqlite?: string | null): string {
  if (!isoOrSqlite) return 'never'

  let date: Date
  // Handle SQLite timestamp: "YYYY-MM-DD HH:MM:SS" as UTC
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(isoOrSqlite)) {
    date = new Date(isoOrSqlite.replace(' ', 'T') + 'Z')
  } else {
    date = new Date(isoOrSqlite)
  }

  if (Number.isNaN(date.getTime())) return isoOrSqlite

  const now = Date.now()
  const diffMs = now - date.getTime()
  if (diffMs < 0) return 'just now'

  const diffSec = Math.floor(diffMs / 1000)
  if (diffSec < 60) return 'just now'

  const diffMin = Math.floor(diffSec / 60)
  if (diffMin === 1) return '1 min ago'
  if (diffMin < 60) return `${diffMin} min ago`

  const diffHr = Math.floor(diffMin / 60)
  if (diffHr === 1) return '1 hour ago'
  if (diffHr < 24) return `${diffHr} hours ago`

  const diffDays = Math.floor(diffHr / 24)
  if (diffDays === 1) return '1 day ago'
  return `${diffDays} days ago`
}

export function formatBytes(bytes: number): string {
  if (bytes <= 0 || Number.isNaN(bytes)) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const val = bytes / Math.pow(1024, i)
  return `${val.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

export function formatDuration(sec: number): string {
  if (sec <= 0 || Number.isNaN(sec)) return '0s'
  if (sec < 60) return `${Math.round(sec)}s`
  const min = Math.floor(sec / 60)
  const remSec = Math.round(sec % 60)
  if (min < 60) {
    return remSec > 0 ? `${min}m ${remSec}s` : `${min}m`
  }
  const hr = Math.floor(min / 60)
  const remMin = min % 60
  return remMin > 0 ? `${hr}h ${remMin}m` : `${hr}h`
}
