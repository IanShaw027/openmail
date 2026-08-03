import { deviceHeaders, deviceHeadersAsync } from '@/utils/device'

const base = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

/** Default client timeout for long proxy fetch/send (ms). */
export const DEFAULT_API_TIMEOUT_MS = 55_000

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(message: string, status: number, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

export class TimeoutError extends Error {
  constructor(message = 'Request timed out') {
    super(message)
    this.name = 'TimeoutError'
  }
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  /** Abort if no response within this many ms (default: none unless set). */
  timeoutMs?: number
}

function detailMessage(data: unknown, fallback: string): string {
  if (typeof data === 'object' && data && data !== null) {
    const d = data as Record<string, unknown>
    if (typeof d.detail === 'string') return d.detail
    if (Array.isArray(d.detail)) {
      return d.detail
        .map((item) => {
          if (typeof item === 'object' && item && 'msg' in item) {
            return String((item as { msg: unknown }).msg)
          }
          return String(item)
        })
        .join('; ')
    }
    if (typeof d.message === 'string') return d.message
  }
  return fallback
}

function mergeAbortSignals(signals: Array<AbortSignal | undefined | null>): AbortSignal | undefined {
  const list = signals.filter((s): s is AbortSignal => Boolean(s))
  if (!list.length) return undefined
  if (list.length === 1) return list[0]
  // AbortSignal.any is widely available in modern browsers
  const anyFn = (AbortSignal as unknown as { any?: (s: AbortSignal[]) => AbortSignal }).any
  if (typeof anyFn === 'function') return anyFn(list)
  const ctrl = new AbortController()
  for (const s of list) {
    if (s.aborted) {
      ctrl.abort(s.reason)
      return ctrl.signal
    }
    s.addEventListener('abort', () => ctrl.abort(s.reason), { once: true })
  }
  return ctrl.signal
}

export async function apiRequest<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { body, headers: extraHeaders, timeoutMs, signal: userSignal, ...rest } = options
  const headers = new Headers(extraHeaders)

  const bodyText = body === undefined ? undefined : JSON.stringify(body)

  // Device / license (+ optional HMAC proof when vault unlocked)
  try {
    const method = String(rest.method || 'GET').toUpperCase()
    const pathForSign = path.startsWith('http')
      ? `${new URL(path).pathname}${new URL(path).search}`
      : path.startsWith('/')
        ? path
        : `/${path}`
    // Same wire string as fetch body so SHA-256 matches server-side hash
    const dh = await deviceHeadersAsync(method, pathForSign, bodyText ?? '')
    for (const [k, v] of Object.entries(dh)) {
      if (!headers.has(k)) headers.set(k, v)
    }
  } catch {
    try {
      const dh = deviceHeaders()
      for (const [k, v] of Object.entries(dh)) {
        if (!headers.has(k)) headers.set(k, v)
      }
    } catch {
      /* ignore */
    }
  }

  if (body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const url = path.startsWith('http')
    ? path
    : `${base}${path.startsWith('/') ? path : `/${path}`}`

  let timeoutCtrl: AbortController | undefined
  let timeoutId: ReturnType<typeof setTimeout> | undefined
  if (timeoutMs != null && timeoutMs > 0) {
    timeoutCtrl = new AbortController()
    timeoutId = setTimeout(() => timeoutCtrl!.abort(new TimeoutError()), timeoutMs)
  }
  const signal = mergeAbortSignals([userSignal, timeoutCtrl?.signal])

  try {
    const res = await fetch(url, {
      ...rest,
      signal,
      headers,
      credentials: 'include',
      body: bodyText,
    })

    const text = await res.text()
    let data: unknown = null
    if (text) {
      try {
        data = JSON.parse(text)
      } catch {
        data = text
      }
    }

    if (!res.ok) {
      throw new ApiError(detailMessage(data, res.statusText || 'Request failed'), res.status, data)
    }

    return data as T
  } catch (e) {
    if (e instanceof TimeoutError) throw e
    if (e instanceof DOMException && e.name === 'AbortError') {
      // Distinguish our timeout abort vs external cancel
      if (timeoutCtrl?.signal.aborted) {
        throw new TimeoutError()
      }
      throw e
    }
    // Some browsers surface abort reason
    if (e instanceof Error && e.name === 'AbortError') {
      if (timeoutCtrl?.signal.aborted) throw new TimeoutError()
      throw e
    }
    throw e
  } finally {
    if (timeoutId != null) clearTimeout(timeoutId)
  }
}

export function getApiBase(): string {
  return base
}

export function isAbortError(e: unknown): boolean {
  return (
    (e instanceof DOMException && e.name === 'AbortError') ||
    (e instanceof Error && e.name === 'AbortError')
  )
}

export function isTimeoutError(e: unknown): boolean {
  return e instanceof TimeoutError || (e instanceof Error && e.name === 'TimeoutError')
}
