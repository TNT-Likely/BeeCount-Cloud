import { describe, expect, it } from 'vitest'

import { splitRatioInfo } from './pages/sections/investmentsShared'
import { stockTradeAmount } from '@beecount/web-features'

describe('股票分割顯示', () => {
  it('拆股:比例 ≥ 1 顯示 1→N', () => {
    expect(splitRatioInfo(4)).toEqual({ merge: false, n: 4 })
    expect(splitRatioInfo(1.5)).toEqual({ merge: false, n: 1.5 })
    expect(splitRatioInfo(1)).toEqual({ merge: false, n: 1 })
  })

  it('反向分割:比例 < 1 顯示 N→1', () => {
    expect(splitRatioInfo(0.5)).toEqual({ merge: true, n: 2 })
    expect(splitRatioInfo(0.1)).toEqual({ merge: true, n: 10 })
    expect(splitRatioInfo(0.4)).toEqual({ merge: true, n: 2.5 })
  })

  it('不合法比例回傳 null', () => {
    expect(splitRatioInfo(0)).toBeNull()
    expect(splitRatioInfo(-2)).toBeNull()
    expect(splitRatioInfo(Number.NaN)).toBeNull()
  })

  it('分割沒有現金影響', () => {
    expect(stockTradeAmount('split', 4, 0, 0, 0, 'USD')).toBe(0)
  })
})

describe('後台資料來源錯誤訊息遮罩', () => {
  it('把 URL 內的 apikey 參數換成 ***', async () => {
    const { redactApiKeyParam } = await import('./pages/sections/AdminSecurityDataSourcePage')
    expect(
      redactApiKeyParam("Client error '401' for url 'https://api.twelvedata.com/quote?symbol=AAPL&apikey=dummy-key-1'"),
    ).toBe("Client error '401' for url 'https://api.twelvedata.com/quote?symbol=AAPL&apikey=***'")
    expect(redactApiKeyParam('no key here')).toBe('no key here')
  })
})
