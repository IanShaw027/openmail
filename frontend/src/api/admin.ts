import { apiRequest } from '@/api/client'

export type LicenseDeviceUse = {
  device_id: string
  first_seen_at: string
  last_seen_at: string
}

export type IssuedLicense = {
  id: string
  token: string
  note: string | null
  created_at: string
  created_by: string
  revoked_at: string | null
  device_count: number
  last_used_at: string | null
  devices: LicenseDeviceUse[]
}

export async function listAdminLicenses(): Promise<IssuedLicense[]> {
  const res = await apiRequest<{ licenses?: IssuedLicense[] }>('/api/admin/licenses', {
    timeoutMs: 15_000,
  })
  return Array.isArray(res?.licenses) ? res.licenses : []
}

export async function issueAdminLicense(note?: string): Promise<IssuedLicense> {
  return apiRequest<IssuedLicense>('/api/admin/licenses', {
    method: 'POST',
    body: { note: note?.trim() || null },
    timeoutMs: 15_000,
  })
}

export async function revokeAdminLicense(id: string): Promise<IssuedLicense> {
  return apiRequest<IssuedLicense>(`/api/admin/licenses/${id}/revoke`, {
    method: 'POST',
    body: {},
    timeoutMs: 15_000,
  })
}
