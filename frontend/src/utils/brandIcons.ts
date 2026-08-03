/**
 * Mail-brand marks for console chips.
 * Thin adapter over the unified brand logo registry (`brandLogos.ts`).
 */

import type { MailBrand } from '@/utils/domainBrand'
import {
  type LogoSvgPart,
  logoAccent,
  logoParts,
  normalizeBrandId,
} from '@/utils/brandLogos'

export type BrandSvgPart = LogoSvgPart

/** SVG path parts for a mail brand (registry-backed). */
export function brandSvgParts(brand?: string | null): BrandSvgPart[] {
  return logoParts(brand)
}

/** @deprecated prefer brandSvgParts */
export function brandSvgPath(brand?: string | null): string {
  const parts = brandSvgParts(brand)
  return parts[0]?.d || ''
}

export function brandAccent(brand?: string | null): string {
  return logoAccent(brand)
}

export function isKnownBrand(brand?: string | null): brand is MailBrand {
  const id = normalizeBrandId(brand)
  return [
    'microsoft',
    'gmail',
    'qq',
    'netease',
    'yahoo',
    'icloud',
    'aliyun',
    'mailcom',
    'gmx',
    'proton',
    'zoho',
    'cf_temp',
    'duckmail',
    'http_api',
    'other',
  ].includes(id)
}
