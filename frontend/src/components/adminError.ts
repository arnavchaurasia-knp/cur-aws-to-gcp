import { ApiError } from '../api/http'

export interface AdminApiErrorState {
  endpoint: string
  status: number
  message: string
}

export function parseAdminApiError(
  err: unknown,
  endpoint: string,
  onNavigateLogin?: () => void
): AdminApiErrorState {
  if (err instanceof ApiError) {
    if (err.status === 401) {
      if (onNavigateLogin) {
        onNavigateLogin()
      } else if (typeof window !== 'undefined') {
        window.location.replace('/login')
      }
      return { endpoint, status: 401, message: 'Session expired. Redirecting to login…' }
    }
    if (err.status === 403) {
      return {
        endpoint,
        status: 403,
        message: `Permission denied accessing ${endpoint}. Admin privileges required.`
      }
    }
    return {
      endpoint,
      status: err.status,
      message: `${endpoint} failed (${err.status}): ${err.message}`
    }
  }
  const message = err instanceof Error ? err.message : String(err)
  return { endpoint, status: 0, message: `${endpoint} failed: ${message}` }
}
