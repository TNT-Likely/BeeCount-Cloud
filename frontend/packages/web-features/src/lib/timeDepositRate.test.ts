import { describe, expect, it } from 'vitest'

import { ratePercentFromWire, rateWireFromPercent } from './timeDepositRate'

describe('time deposit rate conversion', () => {
  it('converts wire decimal to percent string', () => {
    expect(ratePercentFromWire(0.0325)).toBe('3.25')
    expect(ratePercentFromWire(0.05)).toBe('5')
    expect(ratePercentFromWire(0.0125)).toBe('1.25')
  })

  it('returns empty string for zero / invalid wire rate', () => {
    expect(ratePercentFromWire(0)).toBe('')
    expect(ratePercentFromWire(Number.NaN)).toBe('')
  })

  it('converts percent input to wire decimal', () => {
    expect(rateWireFromPercent('3.25')).toBe(0.0325)
    expect(rateWireFromPercent('5')).toBe(0.05)
    expect(rateWireFromPercent(' 2.5 ')).toBe(0.025)
  })

  it('falls back to 0 for empty / invalid / negative input', () => {
    expect(rateWireFromPercent('')).toBe(0)
    expect(rateWireFromPercent('abc')).toBe(0)
    expect(rateWireFromPercent('-1')).toBe(0)
  })

  it('round-trips wire → percent → wire', () => {
    for (const wire of [0.0325, 0.05, 0.0125, 0.045]) {
      expect(rateWireFromPercent(ratePercentFromWire(wire))).toBeCloseTo(wire, 10)
    }
  })
})
