/** Device transfer (QR) API — server holds opaque ciphertext only. */

import { apiRequest } from '@/api/client'

export type TransferDirection = 'to_guest' | 'to_host'

export interface TransferCreateOut {
  code: string
  claim_token: string
  expires_at: number
  direction: TransferDirection
  qr_path: string
}

export interface TransferStatusOut {
  code: string
  status: string
  direction: TransferDirection
  label: string
  expires_at: number
  guest_device_id?: string | null
  has_blob?: boolean
}

export async function transferCreate(body: {
  blob: string
  host_device_id: string
  direction: TransferDirection
  label?: string
}): Promise<TransferCreateOut> {
  return apiRequest<TransferCreateOut>('/api/transfer/create', {
    method: 'POST',
    body,
    timeoutMs: 30_000,
  })
}

export async function transferClaim(body: {
  code: string
  guest_device_id: string
}): Promise<{
  claim_token: string
  status: string
  direction: TransferDirection
  label: string
  expires_at: number
  host_hint: string
}> {
  return apiRequest('/api/transfer/claim', {
    method: 'POST',
    body,
    timeoutMs: 15_000,
  })
}

export async function transferStatus(
  code: string,
  claimToken?: string,
): Promise<TransferStatusOut> {
  const q = claimToken ? `?claim_token=${encodeURIComponent(claimToken)}` : ''
  return apiRequest<TransferStatusOut>(`/api/transfer/status/${encodeURIComponent(code)}${q}`, {
    timeoutMs: 10_000,
  })
}

export async function transferApprove(body: {
  code: string
  claim_token: string
  host_device_id: string
  approve: boolean
}): Promise<TransferStatusOut> {
  return apiRequest('/api/transfer/approve', {
    method: 'POST',
    body,
    timeoutMs: 15_000,
  })
}

export async function transferDownload(body: {
  code: string
  guest_device_id: string
}): Promise<{ blob: string; direction: TransferDirection; label: string }> {
  return apiRequest('/api/transfer/download', {
    method: 'POST',
    body,
    timeoutMs: 60_000,
  })
}
