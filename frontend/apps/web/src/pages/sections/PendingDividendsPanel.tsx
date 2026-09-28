import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  confirmPendingDividend,
  dismissPendingDividend,
  fetchPendingDividends,
  restorePendingDividend,
  type PendingDividend,
  type WorkspaceAccount,
} from '@beecount/api-client'
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
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
  AccountPickerDialog,
  DatePicker,
  currencyDecimals,
  formatPrice,
  formatShares,
  formatStockMoney,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { dateValueToIso, numText } from './investmentsShared'

/**
 * 待確認股利(股票持股 Phase 2,docs/STOCK_HOLDINGS_SD.md §7)。server 在除息日後
 * 依「除息日前一天」的持股建 pending_dividends 並發通知;這裡讓使用者確認實收
 * 金額(或再投入)後由 server 建 income 交易 + 明細,或忽略。App 端
 * `lib/pages/investment/pending_dividends_page.dart` 功能對等。
 */
export function PendingDividendsPanel({
  accounts,
  onConfirmed,
}: {
  accounts: WorkspaceAccount[]
  onConfirmed: () => Promise<void>
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const [items, setItems] = usePageCache<PendingDividend[]>('investments:pendingDividends', [])
  const [dismissed, setDismissed] = useState<PendingDividend[] | null>(null)
  const [confirming, setConfirming] = useState<PendingDividend | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setItems(await fetchPendingDividends(token, 'pending'))
    } catch {
      // 舊版 server 沒有這個端點 → 不顯示區塊
      setItems([])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => {
    void load()
  }, [load])
  useSyncRefresh(() => {
    void load()
  })

  const loadDismissed = async () => {
    try {
      setDismissed(await fetchPendingDividends(token, 'dismissed'))
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    }
  }

  const onDismiss = async (item: PendingDividend) => {
    setBusyId(item.id)
    try {
      await dismissPendingDividend(token, item.id)
      toast.success(t('investments.dividend.dismissed'), t('notice.success'))
      await load()
      if (dismissed) await loadDismissed()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setBusyId(null)
    }
  }

  const onRestore = async (item: PendingDividend) => {
    setBusyId(item.id)
    try {
      await restorePendingDividend(token, item.id)
      await Promise.all([load(), loadDismissed()])
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setBusyId(null)
    }
  }

  if (items.length === 0 && dismissed === null) {
    return (
      <div className="text-right">
        <button type="button" className="text-xs text-muted-foreground hover:underline" onClick={() => void loadDismissed()}>
          {t('investments.dividend.showDismissed')}
        </button>
      </div>
    )
  }

  return (
    <Card className="bc-panel">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">{t('investments.dividend.pendingTitle', { count: items.length })}</CardTitle>
        <button
          type="button"
          className="text-xs text-muted-foreground hover:underline"
          onClick={() => (dismissed ? setDismissed(null) : void loadDismissed())}
        >
          {dismissed ? t('investments.dividend.hideDismissed') : t('investments.dividend.showDismissed')}
        </button>
      </CardHeader>
      <CardContent className="space-y-2">
        {items.length === 0 && <p className="text-sm text-muted-foreground">{t('investments.dividend.none')}</p>}
        {items.map((item) => (
          <DividendRow key={item.id} item={item}>
            <Button size="sm" disabled={busyId === item.id} onClick={() => setConfirming(item)}>
              {t('investments.dividend.confirm')}
            </Button>
            <Button size="sm" variant="outline" disabled={busyId === item.id} onClick={() => void onDismiss(item)}>
              {t('investments.dividend.dismiss')}
            </Button>
          </DividendRow>
        ))}
        {dismissed && (
          <div className="border-t pt-2">
            <p className="mb-2 text-xs text-muted-foreground">{t('investments.dividend.dismissedTitle')}</p>
            {dismissed.length === 0 && <p className="text-xs text-muted-foreground">—</p>}
            {dismissed.map((item) => (
              <DividendRow key={item.id} item={item} muted>
                <Button size="sm" variant="ghost" disabled={busyId === item.id} onClick={() => void onRestore(item)}>
                  {t('investments.dividend.restore')}
                </Button>
              </DividendRow>
            ))}
          </div>
        )}
      </CardContent>
      {confirming && (
        <ConfirmDividendDialog
          item={confirming}
          accounts={accounts}
          onClose={() => setConfirming(null)}
          onConfirmed={async () => {
            setConfirming(null)
            toast.success(t('investments.dividend.confirmed'), t('notice.success'))
            await Promise.all([load(), onConfirmed()])
          }}
        />
      )}
    </Card>
  )
}

function DividendRow({ item, muted, children }: { item: PendingDividend; muted?: boolean; children: React.ReactNode }) {
  const t = useT()
  const ccy = item.currency || ''
  return (
    <div className={`flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2 ${muted ? 'opacity-70' : ''}`}>
      <div className="min-w-0">
        <div className="font-medium">
          {item.symbol} <span className="font-normal text-muted-foreground">{item.security_name || ''}</span>
          <span className="ml-2 text-xs font-normal text-muted-foreground">{item.account_name || ''}</span>
        </div>
        <div className="text-xs text-muted-foreground tabular-nums">
          {t('investments.dividend.exDate', { date: item.ex_date })}
          {item.pay_date ? ` · ${t('investments.dividend.payDate', { date: item.pay_date })}` : ''}
          {` · ${formatShares(item.shares)} ${t('investments.dividend.sharesUnit')}`}
          {item.cash_per_share > 0 ? ` · ${t('investments.dividend.perShare', { amount: formatPrice(item.cash_per_share) })}` : ''}
          {item.est_stock_shares > 0 ? ` · ${t('investments.dividend.stockShares', { shares: formatShares(item.est_stock_shares) })}` : ''}
        </div>
      </div>
      <div className="flex items-center gap-2">
        {item.est_net > 0 && (
          <span className="text-sm font-semibold tabular-nums text-income">
            {t('investments.dividend.estNet')} {formatStockMoney(item.est_net, ccy)}
          </span>
        )}
        {children}
      </div>
    </div>
  )
}

function ConfirmDividendDialog({
  item,
  accounts,
  onClose,
  onConfirmed,
}: {
  item: PendingDividend
  accounts: WorkspaceAccount[]
  onClose: () => void
  onConfirmed: () => Promise<void>
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { retryOnConflict } = useLedgerWrite()
  const ccy = (item.currency || '').toUpperCase()
  const hasCash = item.cash_per_share > 0
  const hasStock = item.stock_per_share > 0

  const [mode, setMode] = useState<'cash' | 'reinvest'>(item.reinvest_default ? 'reinvest' : 'cash')
  const [perShare, setPerShare] = useState(numText(item.cash_per_share))
  const [fee, setFee] = useState(numText(item.est_fee))
  const [tax, setTax] = useState(numText(item.est_tax))
  const [receivingId, setReceivingId] = useState(item.settlement_account_id || '')
  const [receivedAmount, setReceivedAmount] = useState('')
  const [reinvestPrice, setReinvestPrice] = useState(numText(item.quote_price))
  const [reinvestShares, setReinvestShares] = useState('')
  const [reinvestSharesEdited, setReinvestSharesEdited] = useState(false)
  const [reinvestFee, setReinvestFee] = useState('')
  const [stockShares, setStockShares] = useState(numText(item.est_stock_shares))
  const [date, setDate] = useState(item.pay_date || item.ex_date)
  const [note, setNote] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [saving, setSaving] = useState(false)

  const investmentAccount = accounts.find((a) => a.id === item.account_id)
  const receiving = mode === 'reinvest' ? investmentAccount : accounts.find((a) => a.id === receivingId)
  const crossCurrency = Boolean(hasCash && receiving && (receiving.currency || '').toUpperCase() !== ccy)
  const decimals = currencyDecimals(ccy)
  const gross = useMemo(() => {
    const raw = item.shares * (Number(perShare) || 0)
    return decimals === 0 ? Math.floor(raw + 1e-9) : Math.round(raw * 100) / 100
  }, [item.shares, perShare, decimals])
  const net = Math.max(gross - (Number(fee) || 0) - (Number(tax) || 0), 0)

  // 再投入股數預設 = 實收 ÷ 價格(台股捨去到整股,其它到 6 位小數)。
  useEffect(() => {
    if (reinvestSharesEdited) return
    const price = Number(reinvestPrice) || 0
    if (!(price > 0) || !(net > 0)) return setReinvestShares('')
    const raw = net / price
    const isTw = item.market === 'TW' || item.market === 'TWO'
    setReinvestShares(numText(isTw ? Math.floor(raw) : Math.floor(raw * 1e6) / 1e6))
  }, [net, reinvestPrice, reinvestSharesEdited, item.market])

  const reinvestCost = (Number(reinvestShares) || 0) * (Number(reinvestPrice) || 0) + (Number(reinvestFee) || 0)

  const onSubmit = async () => {
    if (!item.ledger_id) return
    if (hasCash && mode === 'cash' && !receivingId) {
      return toast.error(t('investments.error.receivingRequired'), t('notice.error'))
    }
    if (hasCash && mode === 'reinvest' && !(Number(reinvestShares) > 0 && Number(reinvestPrice) > 0)) {
      return toast.error(t('investments.dividend.error.reinvestRequired'), t('notice.error'))
    }
    if (crossCurrency && !(Number(receivedAmount) > 0)) {
      return toast.error(t('investments.error.settlementAmountRequired'), t('notice.error'))
    }
    const ledgerId = item.ledger_id
    setSaving(true)
    try {
      await retryOnConflict(ledgerId, (base) =>
        confirmPendingDividend(token, item.id, base, {
          mode,
          cash_per_share: Number(perShare) || 0,
          fee: Number(fee) || 0,
          tax: Number(tax) || 0,
          settlement_account_id: mode === 'cash' ? receivingId : null,
          settlement_amount: crossCurrency ? Number(receivedAmount) : null,
          reinvest_shares: mode === 'reinvest' ? Number(reinvestShares) : null,
          reinvest_price: mode === 'reinvest' ? Number(reinvestPrice) : null,
          reinvest_fee: mode === 'reinvest' ? Number(reinvestFee) || 0 : null,
          stock_shares: hasStock ? Number(stockShares) || 0 : 0,
          trade_date: dateValueToIso(date),
          note: note.trim() || null,
        }),
      )
      await onConfirmed()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(next) => !next && !saving && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t('investments.dividend.confirmTitle')} · {item.symbol} {item.security_name || ''}
          </DialogTitle>
        </DialogHeader>
        <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
          <div className="rounded-md bg-muted/40 px-3 py-2 text-xs text-muted-foreground tabular-nums">
            {item.account_name || ''} · {t('investments.dividend.exDate', { date: item.ex_date })} ·{' '}
            {t('investments.dividend.recordShares', { shares: formatShares(item.shares) })}
          </div>
          {hasCash && (
            <>
              <div className="flex gap-2">
                {(['cash', 'reinvest'] as const).map((m) => (
                  <Button key={m} size="sm" variant={mode === m ? 'default' : 'outline'} onClick={() => setMode(m)}>
                    {t(m === 'cash' ? 'investments.dividend.modeCash' : 'investments.dividend.modeReinvest')}
                  </Button>
                ))}
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div className="space-y-1">
                  <Label>{t('investments.field.dividendPerShare', { currency: ccy })}</Label>
                  <Input inputMode="decimal" value={perShare} onChange={(e) => setPerShare(e.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label>{t('investments.field.fee')}</Label>
                  <Input inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label>{t('investments.field.dividendTax')}</Label>
                  <Input inputMode="decimal" value={tax} onChange={(e) => setTax(e.target.value)} />
                </div>
              </div>
              <div className="flex items-center justify-between rounded-md bg-muted/40 px-3 py-2 text-sm">
                <span className="text-xs text-muted-foreground">
                  {t('investments.dividend.gross')} {formatStockMoney(gross, ccy)}
                </span>
                <span className="font-semibold tabular-nums">
                  {t('investments.dividendNet')} {formatStockMoney(net, ccy)}
                </span>
              </div>
              {mode === 'cash' ? (
                <div className="space-y-1">
                  <Label>{t('investments.field.receivingAccount')}</Label>
                  <button
                    type="button"
                    onClick={() => setPickerOpen(true)}
                    className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                  >
                    <span className={`flex-1 truncate ${receiving ? '' : 'text-muted-foreground'}`}>
                      {receiving ? `${receiving.name} · ${receiving.currency}` : t('investments.error.receivingRequired')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
              ) : (
                <div className="grid grid-cols-3 gap-3">
                  <div className="space-y-1">
                    <Label>{t('investments.dividend.reinvestPrice', { currency: ccy })}</Label>
                    <Input inputMode="decimal" value={reinvestPrice} onChange={(e) => setReinvestPrice(e.target.value)} />
                  </div>
                  <div className="space-y-1">
                    <Label>{t('investments.dividend.reinvestShares')}</Label>
                    <Input
                      inputMode="decimal"
                      value={reinvestShares}
                      onChange={(e) => {
                        setReinvestSharesEdited(true)
                        setReinvestShares(e.target.value)
                      }}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label>{t('investments.field.fee')}</Label>
                    <Input inputMode="decimal" value={reinvestFee} onChange={(e) => setReinvestFee(e.target.value)} />
                  </div>
                  <p className="col-span-3 text-xs text-muted-foreground">
                    {t('investments.dividend.reinvestHint', { amount: formatStockMoney(reinvestCost, ccy) })}
                  </p>
                </div>
              )}
              {crossCurrency && receiving && (
                <div className="space-y-1">
                  <Label>{t('investments.dividend.receivedAmount', { currency: receiving.currency || '' })}</Label>
                  <Input inputMode="decimal" value={receivedAmount} onChange={(e) => setReceivedAmount(e.target.value)} />
                </div>
              )}
            </>
          )}
          {hasStock && (
            <div className="space-y-1">
              <Label>{t('investments.dividend.stockSharesField')}</Label>
              <Input inputMode="decimal" value={stockShares} onChange={(e) => setStockShares(e.target.value)} />
              <p className="text-xs text-muted-foreground">
                {t('investments.dividend.stockSharesHint', { ratio: numText(item.stock_per_share) })}
              </p>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('investments.dividend.dateField')}</Label>
              <DatePicker value={date} onChange={setDate} />
            </div>
            <div className="space-y-1">
              <Label>{t('investments.field.note')}</Label>
              <Input value={note} onChange={(e) => setNote(e.target.value)} />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button disabled={saving} onClick={() => void onSubmit()}>
            {t('investments.dividend.confirmSubmit')}
          </Button>
        </DialogFooter>
      </DialogContent>
      <AccountPickerDialog
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        accounts={accounts}
        value={receiving?.name || ''}
        title={t('investments.field.receivingAccount')}
        onSelect={(row) => {
          setReceivingId(row.id)
          setPickerOpen(false)
        }}
      />
    </Dialog>
  )
}
