import { apiRequest } from '@/api/client'

/** Single mail row from GET /api/sync/delta (bodies included by default). */
export interface SyncDeltaMail {
  account_id?: string | null
  email: string
  folder?: string | null
  stable_id?: string | null
  id?: string | null
  subject?: string | null
  from?: string | null
  from_addr?: string | null
  to?: string | string[] | null
  to_addrs?: string[] | null
  date?: string | null
  preview?: string | null
  body_preview?: string | null
  verification_code?: string | null
  body_text?: string | null
  body_html?: string | null
  deleted?: boolean | null
  updated_at?: string | null
  message_id?: string | null
  provider_id?: string | null
  uidvalidity?: number | null
}

/** Lightweight account code / sync status patch on delta responses. */
export interface SyncDeltaAccount {
  id?: string | null
  email?: string | null
  latest_verification_code?: string | null
  latest_code_at?: string | null
  latest_code_folder?: string | null
  last_sync_at?: string | null
  last_sync_error?: string | null
}

export interface SyncDeltaResponse {
  server_time?: string | null
  server_seq?: number | string | null
  has_more?: boolean
  mails?: SyncDeltaMail[]
  accounts?: SyncDeltaAccount[]
  /** Last keyset cursor mail id when server returns it explicitly */
  next_since_id?: string | null
}

export interface SyncStatusResponse {
  server_time?: string | null
  server_seq?: number | string | null
  mail_count?: number | null
  account_count?: number | null
  [key: string]: unknown
}

export interface PullSyncDeltaOpts {
  since?: string | null
  sinceId?: string | null
  limit?: number
  includeBody?: boolean
}

function buildQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    q.set(k, String(v))
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

/**
 * Pull one page of cloud mail delta for this device (HMAC via apiRequest).
 * GET /api/sync/delta?since=&since_id=&limit=&include_body=
 */
export async function pullSyncDelta(opts: PullSyncDeltaOpts = {}): Promise<SyncDeltaResponse> {
  const qs = buildQuery({
    since: opts.since ?? undefined,
    since_id: opts.sinceId ?? undefined,
    limit: opts.limit ?? undefined,
    include_body: opts.includeBody ? '1' : undefined,
  })
  return apiRequest<SyncDeltaResponse>(`/api/sync/delta${qs}`)
}

/** Lightweight sync status for this device. */
export async function pullSyncStatus(): Promise<SyncStatusResponse> {
  return apiRequest<SyncStatusResponse>('/api/sync/status')
}
