import {
  LOCALE_STORAGE_KEY,
  detectBrowserLocale,
  initialLocale,
  normalizeLocale,
  persistLocale
} from '@beecount/ui'
import { ApiError } from '@beecount/api-client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { localizeError } from './i18n/errors'
import { formatAmountCny, formatIsoDateTime } from './i18n/format'

describe('i18n locale runtime', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('normalizes locale values', () => {
    expect(normalizeLocale('zh-HK')).toBe('zh-TW')
    expect(normalizeLocale('zh-CN')).toBe('zh-CN')
    expect(normalizeLocale('en-US')).toBe('en')
  })

  it('detects browser locale and persists to localStorage', () => {
    const store = new Map<string, string>()
    vi.stubGlobal('navigator', {
      language: 'zh-HK',
      languages: ['zh-HK', 'en-US']
    })
    vi.stubGlobal('window', {
      localStorage: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => {
          store.set(key, value)
        }
      }
    })

    expect(detectBrowserLocale()).toBe('zh-TW')
    expect(initialLocale()).toBe('zh-TW')

    persistLocale('en')
    expect(store.get(LOCALE_STORAGE_KEY)).toBe('en')
    expect(initialLocale()).toBe('en')
  })
})

describe('i18n error mapping and formatting', () => {
  const t = (key: string) => key

  it('maps known API error codes to localized keys', () => {
    const err = new ApiError('invalid', {
      status: 401,
      code: 'AUTH_INVALID_CREDENTIALS'
    })
    expect(localizeError(err, t)).toBe('error.AUTH_INVALID_CREDENTIALS')
  })

  it('maps write conflict with params', () => {
    const err = new ApiError('write conflict', {
      status: 409,
      code: 'WRITE_CONFLICT',
      latestChangeId: 21,
      latestServerTimestamp: '2026-02-25T10:00:00Z'
    })
    expect(localizeError(err, t)).toContain('error.WRITE_CONFLICT')
  })

  it('formats amount and datetime in fixed mode', () => {
    expect(formatAmountCny(12.5)).toBe('CNY 12.50')
    expect(formatIsoDateTime('2026-02-25T10:20:30Z')).toBe('2026-02-25 10:20:30')
  })
})
