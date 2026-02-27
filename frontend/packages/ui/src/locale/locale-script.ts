export const LOCALE_STORAGE_KEY = 'beecount.locale'

export const SUPPORTED_LOCALES = ['zh-CN', 'zh-TW', 'en'] as const
export type Locale = (typeof SUPPORTED_LOCALES)[number]

export function normalizeLocale(raw: string | null | undefined): Locale {
  if (!raw) return 'en'
  const input = raw.toLowerCase()
  if (input === 'zh-tw' || input === 'zh-hk' || input === 'zh-mo') return 'zh-TW'
  if (input.startsWith('zh')) return 'zh-CN'
  return 'en'
}

export function detectBrowserLocale(): Locale {
  if (typeof navigator === 'undefined') return 'en'
  const candidates: Array<string | null | undefined> = [
    navigator.language,
    ...(Array.isArray(navigator.languages) ? navigator.languages : [])
  ]
  for (const candidate of candidates) {
    if (candidate) return normalizeLocale(candidate)
  }
  return 'en'
}

export function initialLocale(): Locale {
  if (typeof window === 'undefined') return 'en'
  const raw = window.localStorage.getItem(LOCALE_STORAGE_KEY)
  return raw ? normalizeLocale(raw) : detectBrowserLocale()
}

export function persistLocale(locale: Locale): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(LOCALE_STORAGE_KEY, locale)
}

export function applyDocumentLocale(locale: Locale): void {
  if (typeof document === 'undefined') return
  document.documentElement.setAttribute('lang', locale)
}
