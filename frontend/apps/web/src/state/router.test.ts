import { describe, expect, it } from 'vitest'

import { parseRoute, routePath } from './router'

describe('router path mapping', () => {
  it('parses login path', () => {
    expect(parseRoute('/login')).toEqual({ kind: 'login' })
    expect(parseRoute('/')).toEqual({ kind: 'login' })
  })

  it('parses app route with section', () => {
    expect(parseRoute('/app/ledger-1/transactions')).toEqual({
      kind: 'app',
      ledgerId: 'ledger-1',
      section: 'transactions'
    })
expect(parseRoute('/app/workspace/transactions')).toEqual({
      kind: 'app',
      ledgerId: '',
      section: 'transactions'
    })
    expect(parseRoute('/app/transactions')).toEqual({
      kind: 'app',
      ledgerId: '',
      section: 'transactions'
    })
    expect(parseRoute('/app/settings/health')).toEqual({
      kind: 'app',
      ledgerId: '',
      section: 'settings-health'
    })
  })

  // 0021:定期存款 / 归物两个新 section 的 parse + routePath 往返。
  it('parses time-deposits and item-returns sections', () => {
    expect(parseRoute('/app/time-deposits')).toEqual({
      kind: 'app',
      ledgerId: '',
      section: 'time-deposits'
    })
    expect(parseRoute('/app/item-returns')).toEqual({
      kind: 'app',
      ledgerId: '',
      section: 'item-returns'
    })
    expect(routePath({ kind: 'app', ledgerId: '', section: 'time-deposits' })).toBe(
      '/app/time-deposits'
    )
    expect(routePath({ kind: 'app', ledgerId: '', section: 'item-returns' })).toBe(
      '/app/item-returns'
    )
  })

  // 0022:退款 section 的 parse + routePath 往返。
  it('parses refunds section', () => {
    expect(parseRoute('/app/refunds')).toEqual({
      kind: 'app',
      ledgerId: '',
      section: 'refunds'
    })
    expect(routePath({ kind: 'app', ledgerId: '', section: 'refunds' })).toBe(
      '/app/refunds'
    )
  })

  it('falls back to overview for unknown section', () => {
    expect(parseRoute('/app/ledger-1/unknown')).toEqual({
      kind: 'app',
      ledgerId: 'ledger-1',
      section: 'transactions'
    })
  })

  it('creates path from app route', () => {
    expect(
      routePath({
        kind: 'app',
        ledgerId: 'ledger a',
        section: 'settings-devices'
      })
    ).toBe('/app/settings/devices')
    expect(
      routePath({
        kind: 'app',
        ledgerId: 'ledger a',
        section: 'settings-health'
      })
    ).toBe('/app/settings/health')
  })
})
