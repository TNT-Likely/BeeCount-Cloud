import { describe, expect, it } from 'vitest'
import { attachmentDateRange } from './lib/attachmentGallery'
import { parseRoute, routePath } from './state/router'

describe('attachment date and route contracts', () => {
  it('uses local calendar boundaries with an exclusive next month', () => {
    const range = attachmentDateRange(2024, 2)
    expect(new Date(range.dateFrom!)).toEqual(new Date(2024, 1, 1))
    expect(new Date(range.dateTo!)).toEqual(new Date(2024, 2, 1))
  })
  it('allows an entire year and all dates', () => {
    expect(attachmentDateRange(null, null)).toEqual({})
    const range = attachmentDateRange(2026, null)
    expect(new Date(range.dateFrom!)).toEqual(new Date(2026, 0, 1))
    expect(new Date(range.dateTo!)).toEqual(new Date(2027, 0, 1))
  })
  it('recognizes the attachments route without treating it as a legacy ledger', () => {
    expect(parseRoute('/app/attachments')).toEqual({ kind: 'app', ledgerId: '', section: 'attachments' })
    expect(routePath({ kind: 'app', ledgerId: '', section: 'attachments' })).toBe('/app/attachments')
  })
})
