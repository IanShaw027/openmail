/**
 * 2FA service / issuer marks — adapter over unified `brandLogos` registry.
 */

import {
  type LogoSvgPart,
  logoAccent,
  logoParts,
  normalizeBrandId,
} from '@/utils/brandLogos'

export type ServiceSvgPart = LogoSvgPart

/** Normalize logo id / issuer string → registry id */
export function normalizeServiceLogoId(raw?: string | null): string {
  return normalizeBrandId(raw)
}

export function serviceSvgParts(logoOrIssuer?: string | null): ServiceSvgPart[] {
  return logoParts(logoOrIssuer)
}

export function serviceAccent(logoOrIssuer?: string | null): string {
  return logoAccent(logoOrIssuer)
}
