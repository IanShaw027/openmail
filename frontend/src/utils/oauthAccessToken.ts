export function withOAuthAccessToken(
  credential: Record<string, unknown> | null,
  accessToken?: string | null,
): Record<string, unknown> | null {
  const token = String(accessToken || '').trim()
  if (!token) return credential
  return { ...(credential || {}), access_token: token }
}

export function accessTokenFromFetchResult(result: {
  credential_updates?: { access_token?: string | null } | null
}): string | undefined {
  const token = String(result.credential_updates?.access_token || '').trim()
  return token || undefined
}
