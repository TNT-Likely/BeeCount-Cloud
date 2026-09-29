import { useRef, useState } from 'react'

import type { SecuritySearchItem } from '@beecount/api-client'
import { Input, Label, useT } from '@beecount/ui'

import { STOCK_MARKETS } from '../lib/investment'

/**
 * 市場下拉 + 代號輸入(含搜尋建議清單)。股票交易 dialog(InvestmentsPage)跟
 * 週期性交易的「股票定期定額」表單(RecurringRulesPanel)共用——以前後者只有
 * 一個純文字框,打代號不會出現任何候選(2026-09-29 使用者回報)。
 *
 * 跟 CategorySelector/TagSelector 同樣的分工:元件本身不呼叫 API,搜尋由呼叫
 * 方透過 `onSearch` 提供(只有呼叫方有 token)。台股(TW/TWO)搜尋不限市場,
 * 讓使用者打 0050 也能找到上櫃標的;選中結果時 `onPick` 會帶回正確的市場/
 * 幣別/名稱,由呼叫方決定要不要覆蓋自己的欄位。
 */
export function SecuritySymbolField({
  market,
  symbol,
  disabled = false,
  onMarketChange,
  onSymbolChange,
  onPick,
  onSearch,
}: {
  market: string
  symbol: string
  disabled?: boolean
  onMarketChange: (market: string) => void
  onSymbolChange: (symbol: string) => void
  onPick: (item: SecuritySearchItem) => void
  onSearch?: (query: string, market: string | null) => Promise<SecuritySearchItem[]>
}) {
  const t = useT()
  const [results, setResults] = useState<SecuritySearchItem[]>([])
  const [open, setOpen] = useState(false)
  const seq = useRef(0)

  const onInput = (value: string) => {
    onSymbolChange(value)
    const q = value.trim()
    const current = ++seq.current
    if (!q || disabled || !onSearch) {
      setResults([])
      setOpen(false)
      return
    }
    window.setTimeout(async () => {
      if (current !== seq.current) return
      try {
        const isTaiwan = market === 'TW' || market === 'TWO'
        const rows = await onSearch(q, isTaiwan ? null : market)
        if (current !== seq.current) return
        setResults(rows.slice(0, 8))
        setOpen(true)
      } catch {
        setResults([])
      }
    }, 350)
  }

  const pick = (r: SecuritySearchItem) => {
    seq.current++
    setResults([])
    setOpen(false)
    onPick(r)
  }

  return (
    <div className="grid grid-cols-3 gap-3">
      <div className="space-y-1">
        <Label>{t('investments.field.market')}</Label>
        <select
          className="flex h-10 w-full rounded-md border border-input bg-muted px-3 text-sm disabled:cursor-not-allowed disabled:opacity-60"
          value={market}
          disabled={disabled}
          onChange={(e) => onMarketChange(e.target.value)}
        >
          {STOCK_MARKETS.map((m) => (
            <option key={m.code} value={m.code}>
              {t(`investments.market.${m.code}`)}
            </option>
          ))}
        </select>
      </div>
      <div className="relative col-span-2 space-y-1">
        <Label>{t('investments.field.symbol')}</Label>
        <Input
          value={symbol}
          disabled={disabled}
          placeholder={t('investments.field.symbolSearch')}
          onChange={(e) => onInput(e.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        />
        {open && !disabled && symbol.trim() && (
          <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-md">
            {results.map((r) => (
              <button
                key={`${r.market}:${r.symbol}`}
                type="button"
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-accent"
                // onMouseDown:在 input blur 關閉清單之前先選中。
                onMouseDown={(e) => {
                  e.preventDefault()
                  pick(r)
                }}
              >
                <span>
                  <span className="font-medium">{r.symbol}</span> {r.name}
                </span>
                <span className="text-xs text-muted-foreground">
                  {t(`investments.market.${r.market}`)} · {r.currency}
                </span>
              </button>
            ))}
            {results.length === 0 && (
              <div className="px-3 py-2 text-xs text-muted-foreground">{t('investments.searchNoResult')}</div>
            )}
            <button
              type="button"
              className="w-full border-t px-3 py-2 text-left text-xs text-muted-foreground hover:bg-accent"
              onMouseDown={(e) => {
                e.preventDefault()
                seq.current++
                onSymbolChange(symbol.trim().toUpperCase())
                setOpen(false)
              }}
            >
              {t('investments.useTyped', { symbol: symbol.trim().toUpperCase() })}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
