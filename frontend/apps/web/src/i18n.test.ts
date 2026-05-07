import {
  LOCALE_STORAGE_KEY,
  detectBrowserLocale,
  initialLocale,
  normalizeLocale,
  persistLocale
} from '@beecount/ui'
import { ApiError } from '@beecount/api-client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import en from './i18n/en'
import zhCN from './i18n/zh-CN'
import zhTW from './i18n/zh-TW'
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

/**
 * 校验三语 keys 完整对齐 —— 之前出现过 zh-CN 加了新文案 / en 漏了的情况,
 * 跑到对应 locale 时 t() 直接返回 key 字符串(像 `nav.calendar`)露馅。
 *
 * 这里把 en 当作 source of truth(`TranslationKey = keyof typeof en`),
 * 任何一边缺 key 都让 vitest 报具体差集,新加 key 时不会再漏。
 *
 * 同步原则:
 *   - 加新 key 必须三个文件都加,test 强制执行
 *   - 删 key 也必须三处一起删
 *   - 复数 / 占位符模板格式可以不同,但 key 必须存在
 */
describe('i18n parity', () => {
  const enKeys = new Set(Object.keys(en))
  const zhCNKeys = new Set(Object.keys(zhCN))
  const zhTWKeys = new Set(Object.keys(zhTW))

  const diff = (a: Set<string>, b: Set<string>) =>
    [...a].filter((k) => !b.has(k)).sort()

  const cases = [
    { name: 'zh-CN', keys: zhCNKeys },
    { name: 'zh-TW', keys: zhTWKeys },
  ] as const

  for (const { name, keys } of cases) {
    it(`${name} 包含 en 全部 key`, () => {
      const missing = diff(enKeys, keys)
      expect(
        missing,
        `${name} 缺以下 ${missing.length} 个 key(en 有但 ${name} 没):\n  ${missing.join(
          '\n  '
        )}`,
      ).toEqual([])
    })

    it(`${name} 不含 en 没有的多余 key`, () => {
      const extra = diff(keys, enKeys)
      expect(
        extra,
        `${name} 有 ${extra.length} 个 en 没有的多余 key(可能 typo / 漏删):\n  ${extra.join(
          '\n  '
        )}`,
      ).toEqual([])
    })
  }

  it('每个 locale 的所有值都是非空字符串', () => {
    for (const [name, dict] of Object.entries({ en, 'zh-CN': zhCN, 'zh-TW': zhTW })) {
      const blanks = Object.entries(dict).filter(
        ([, value]) => typeof value !== 'string' || value.trim() === '',
      )
      expect(
        blanks.map(([k]) => k),
        `${name} 有空值 / 非字符串值:\n  ${blanks.map(([k]) => k).join('\n  ')}`,
      ).toEqual([])
    }
  })
})
