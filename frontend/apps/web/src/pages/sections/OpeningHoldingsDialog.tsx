import { useMemo, useRef, useState } from 'react'

import {
  createStockTrade,
  fetchSecurityQuotes,
  type Holding,
  type WorkspaceAccount,
} from '@beecount/api-client'
import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  useT,
  useToast,
} from '@beecount/ui'
import {
  DatePicker,
  STOCK_MARKETS,
  defaultMarketForCurrency,
  formatShares,
  formatStockMoney,
  marketCurrency,
  openingTotalCost,
  openingTradeFromCost,
  parseOpeningHoldingsText,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { dateValueToIso, numText, todayDateValue } from './investmentsShared'

type Row = { id: number; symbol: string; name: string; autoName: string | null; shares: string; cost: string }

/**
 * 批次新增期初持股(2026-09-30,STOCK_HOLDINGS_SD §10.3)。開始記帳前就買過
 * 很多次的股票不用逐筆補記:照券商「庫存」頁每一檔填股數 + 平均成本(或
 * 總成本),每檔存成一筆 `opening` 明細(沒有金流)。也可以把 Excel/Google
 * 試算表的「代號 股數 成本」整段貼上,解析規則見 `parseOpeningHoldingsText`
 * (跟 App 同一套)。入口:投資頁帳戶卡「期初持股」、股票交易 dialog 選
 * 「期初持股」後的「一次新增多檔」。
 */
export function OpeningHoldingsDialog({
  account,
  holdings,
  activeLedgerId,
  onClose,
  onSaved,
}: {
  account: WorkspaceAccount
  holdings: Holding[]
  activeLedgerId: string | null
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { retryOnConflict } = useLedgerWrite()
  const settings = account.investment_settings ?? null
  const [market, setMarket] = useState<string>(settings?.market || defaultMarketForCurrency(account.currency))
  const currency = (marketCurrency(market) || account.currency || '').toUpperCase()
  const [date, setDate] = useState(todayDateValue())
  const [costIsTotal, setCostIsTotal] = useState(false)
  const nextId = useRef(1)
  const newRow = (): Row => ({ id: nextId.current++, symbol: '', name: '', autoName: null, shares: '', cost: '' })
  const [rows, setRows] = useState<Row[]>(() => [newRow()])
  const [pasteText, setPasteText] = useState('')
  const [saving, setSaving] = useState(false)

  const heldByKey = useMemo(() => {
    const m = new Map<string, number>()
    for (const h of holdings) {
      if (h.account_id === account.id && h.shares > 0) m.set(`${h.market}:${h.symbol}`, h.shares)
    }
    return m
  }, [holdings, account.id])

  const isBlank = (r: Row) => !r.symbol.trim() && !r.name.trim() && !r.shares.trim() && !r.cost.trim()
  const rowTotal = (r: Row) => {
    const shares = Number(r.shares)
    const cost = Number(r.cost)
    if (!(shares > 0) || !(cost > 0)) return 0
    return openingTotalCost(shares, cost, costIsTotal, currency)
  }

  const patchRow = (id: number, patch: Partial<Row>) =>
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch } : r)))

  /** 代號→名稱:一次查多檔報價,名稱空白或還是上次自動帶入的才覆蓋。 */
  const lookupNames = async (targets: { id: number; symbol: string }[], mkt: string) => {
    const keys = [...new Set(targets.map((x) => `${mkt}:${x.symbol.trim().toUpperCase()}`).filter((k) => !k.endsWith(':')))]
    if (keys.length === 0) return
    try {
      const quotes = await fetchSecurityQuotes(token, keys)
      const byKey = new Map(quotes.map((q) => [`${q.market}:${q.symbol}`, q]))
      setRows((rs) =>
        rs.map((r) => {
          if (!targets.some((x) => x.id === r.id)) return r
          const q = byKey.get(`${mkt}:${r.symbol.trim().toUpperCase()}`)
          const current = r.name.trim()
          if (current && current !== r.autoName) return r
          const name = q?.name?.trim() || ''
          if (!name && !current) return r
          return { ...r, name, autoName: name || null }
        }),
      )
    } catch {
      // 查不到報價就讓使用者自己填名稱
    }
  }

  const applyPaste = () => {
    const result = parseOpeningHoldingsText(pasteText)
    if (result.lines.length === 0) {
      toast.error(t('investments.opening.pasteEmpty'), t('notice.error'))
      return
    }
    const added: Row[] = result.lines.map((l) => ({
      ...newRow(),
      symbol: l.symbol,
      name: l.name || '',
      shares: numText(l.shares),
      cost: numText(l.cost),
    }))
    setRows((rs) => [...rs.filter((r) => !isBlank(r)), ...added])
    setPasteText('')
    toast.success(
      t('investments.opening.pasted', { count: result.lines.length, skipped: result.skipped }),
      t('notice.success'),
    )
    void lookupNames(added, market)
  }

  const filled = rows.filter((r) => rowTotal(r) > 0)
  const grandTotal = filled.reduce((s, r) => s + rowTotal(r), 0)

  const onSave = async () => {
    const toSave = rows.filter((r) => !isBlank(r))
    if (toSave.length === 0) return toast.error(t('investments.opening.nothing'), t('notice.error'))
    const bad = toSave.find((r) => !r.symbol.trim() || !(Number(r.shares) > 0) || !(Number(r.cost) > 0))
    if (bad) {
      return toast.error(t('investments.opening.rowInvalid', { row: rows.indexOf(bad) + 1 }), t('notice.error'))
    }
    const ledgerId = activeLedgerId
    if (!ledgerId) return toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
    setSaving(true)
    const savedIds: number[] = []
    try {
      for (const r of toSave) {
        const shares = Number(r.shares)
        const tr = openingTradeFromCost(shares, Number(r.cost), costIsTotal, currency)
        await retryOnConflict(ledgerId, (base) =>
          createStockTrade(token, ledgerId, base, {
            account_id: account.id,
            trade_type: 'opening',
            market,
            symbol: r.symbol.trim().toUpperCase(),
            security_name: r.name.trim() || null,
            shares,
            price: tr.price,
            fee: tr.fee,
            tax: 0,
            currency,
            trade_date: dateValueToIso(date),
            settlement_account_id: null,
            settlement_amount: null,
            note: null,
          }),
        )
        savedIds.push(r.id)
      }
      // 成功提示由 onSaved(投資頁共用的「已儲存交易」)負責,這裡不重複跳。
      await onSaved()
    } catch (err) {
      // 已存的幾檔從清單拿掉,免得重按儲存又存一次。
      setRows((rs) => {
        const left = rs.filter((r) => !savedIds.includes(r.id))
        return left.length ? left : [newRow()]
      })
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSaving(false)
    }
  }

  const costLabel = costIsTotal ? t('investments.opening.totalCost') : t('investments.field.avgCost')

  return (
    <Dialog open onOpenChange={(next) => !next && !saving && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            {t('investments.opening.title')} · {account.name}
          </DialogTitle>
        </DialogHeader>
        <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
          <p className="text-xs text-muted-foreground">{t('investments.opening.intro')}</p>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1">
              <Label>{t('investments.field.market')}</Label>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-muted px-3 text-sm"
                value={market}
                onChange={(e) => {
                  setMarket(e.target.value)
                  void lookupNames(rows, e.target.value)
                }}
              >
                {STOCK_MARKETS.map((m) => (
                  <option key={m.code} value={m.code}>
                    {t(`investments.market.${m.code}`)} · {m.currency}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label>{t('investments.opening.asOf')}</Label>
              <DatePicker value={date} onChange={setDate} />
            </div>
            <div className="space-y-1">
              <Label>{t('investments.opening.costMode')}</Label>
              <div className="flex gap-2">
                {[false, true].map((total) => (
                  <Button
                    key={String(total)}
                    size="sm"
                    className="flex-1"
                    variant={costIsTotal === total ? 'default' : 'outline'}
                    onClick={() => setCostIsTotal(total)}
                  >
                    {total ? t('investments.opening.totalCost') : t('investments.field.avgCost')}
                  </Button>
                ))}
              </div>
            </div>
          </div>

          <div className="space-y-1">
            <Label>{t('investments.opening.paste')}</Label>
            <textarea
              className="flex min-h-[72px] w-full rounded-md border border-input bg-muted px-3 py-2 font-mono text-xs"
              placeholder={'0050\t1000\t120.5\n2330\t50\t580'}
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
            />
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs text-muted-foreground">{t('investments.opening.pasteHint')}</p>
              <Button size="sm" variant="outline" disabled={!pasteText.trim()} onClick={applyPaste}>
                {t('investments.opening.pasteApply')}
              </Button>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="w-8 py-1">#</th>
                  <th className="py-1 pr-2">{t('investments.field.symbol')}</th>
                  <th className="py-1 pr-2">{t('investments.field.name')}</th>
                  <th className="py-1 pr-2">{t('investments.field.shares')}</th>
                  <th className="py-1 pr-2">
                    {costLabel}（{currency}）
                  </th>
                  <th className="py-1 pr-2 text-right">{t('investments.opening.rowTotal')}</th>
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const total = rowTotal(r)
                  const held = heldByKey.get(`${market}:${r.symbol.trim().toUpperCase()}`)
                  return (
                    <tr key={r.id} className="align-top">
                      <td className="py-1 text-xs text-muted-foreground">{i + 1}</td>
                      <td className="py-1 pr-2">
                        <Input
                          value={r.symbol}
                          onChange={(e) => patchRow(r.id, { symbol: e.target.value.toUpperCase() })}
                          onBlur={() => void lookupNames([r], market)}
                        />
                      </td>
                      <td className="py-1 pr-2">
                        <Input value={r.name} onChange={(e) => patchRow(r.id, { name: e.target.value })} />
                      </td>
                      <td className="py-1 pr-2">
                        <Input inputMode="decimal" value={r.shares} onChange={(e) => patchRow(r.id, { shares: e.target.value })} />
                        {held !== undefined && (
                          <p className="mt-0.5 text-[11px] text-muted-foreground">
                            {t('investments.opening.rowHeld', { shares: formatShares(held) })}
                          </p>
                        )}
                      </td>
                      <td className="py-1 pr-2">
                        <Input inputMode="decimal" value={r.cost} onChange={(e) => patchRow(r.id, { cost: e.target.value })} />
                      </td>
                      <td className="py-1 pr-2 pt-3 text-right tabular-nums">
                        {total > 0 ? formatStockMoney(total, currency) : '—'}
                      </td>
                      <td className="py-1">
                        <button
                          type="button"
                          aria-label={t('common.delete')}
                          className="mt-2 text-muted-foreground hover:text-foreground"
                          onClick={() =>
                            setRows((rs) => {
                              const left = rs.filter((x) => x.id !== r.id)
                              return left.length ? left : [newRow()]
                            })
                          }
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between">
            <Button size="sm" variant="outline" onClick={() => setRows((rs) => [...rs, newRow()])}>
              {t('investments.opening.addRow')}
            </Button>
            {filled.length > 0 && (
              <span className="text-sm font-medium">
                {t('investments.opening.summary', {
                  count: filled.length,
                  total: formatStockMoney(grandTotal, currency),
                })}
              </span>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button disabled={saving} onClick={() => void onSave()}>
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
