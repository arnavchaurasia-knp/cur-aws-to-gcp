export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

async function readError(res: Response): Promise<ApiError> {
  const text = await res.text().catch(() => '')
  let msg = text || `${res.status} ${res.statusText}`
  try {
    const j = JSON.parse(text)
    if (j && typeof j.error === 'string') msg = j.error
    else if (j && typeof j.reason === 'string') msg = j.reason
  } catch {
    /* not JSON — keep text */
  }
  if (text.trimStart().startsWith('<')) {
    msg =
      'The server answered with a web page instead of data. The backend is probably not running or is an older build — restart it.'
  }
  return new ApiError(res.status, msg)
}

export async function getJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) throw await readError(res)
  const ct = res.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    if (res.status === 204) return undefined as T
    throw new ApiError(
      res.status,
      'The server answered with a web page instead of data. Restart the backend.'
    )
  }
  return res.json() as Promise<T>
}

export async function sendJSON<T>(
  method: 'POST' | 'PUT' | 'DELETE',
  url: string,
  body?: unknown
): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw await readError(res)
  const ct = res.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    if (res.status === 204) return undefined as T
    throw new ApiError(
      res.status,
      'The server answered with a web page instead of data. Restart the backend.'
    )
  }
  return res.json() as Promise<T>
}
