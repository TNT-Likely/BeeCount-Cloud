import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  createStockTrade,
  deleteStockTrade,
  fetchSecurityQuotes,
  fetchStockTrades,
  fetchWorkspaceAccounts,
  fetchWorkspaceHoldings,
  searchSecurities,
  updateAccount,
  updateStockTrade,
  type AccountHoldings,
  type Holding,
  type HoldingsSummary,
  type InvestmentSettings,
  type SecurityQuote,
  type SecuritySearchItem,
  type StockTrade,
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
  ConfirmDialog,
  DatePicker,
  STOCK_MARKETS,
  defaultMarketForCurrency,
  estimateDividend,
  formatPercent,
  formatPrice,
  formatShares,
  formatStockMoney,
  investmentDefaults,
  marketCurrency,
  percentTextToRate,
  rateToPercentText,
  securityKind,
  sellTaxRateFor,
  stockGross,
  stockTradeAmount,
  suggestFee,
  suggestSellTax,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { PendingDividendsPanel } from './PendingDividendsPanel'
import { dateValueToIso, formatQuoteTime, isoToDateValue, numText, pnlClass } from './investmentsShared'

/**
 * 投資頁(股票持股 2026-09-28,docs/STOCK_HOLDINGS_SD.md)。跟 App 功能對等:
 * 看持股/市值/損益、買進/賣出/期初持股/配股/現金股利/再投入、編輯/刪除明細、
 * 費用設定,以及待確認股利(`PendingDividendsPanel`,Phase 2)。
 *
 * 持股與市值由 server `/read/workspace/holdings` 從 stock_trade 即時算(移動
 * 平均成本法,跟 App 共用測試向量),這裡不做任何客戶端累加。帳戶是
 * user-global,同一個投資理財帳戶的明細可能散在不同帳本 —— 展開持股時跨所有
 * 帳本拉交易紀錄,編輯/刪除時用明細自己所在的帳本寫入。
 */

type TradeRef = { ledgerId: string; trade: StockTrade }

// 轉帳表單(TransactionsPage.tsx)偵測到轉入/轉出帳戶是投資理財帳戶時,會直接
// 重用這個 dialog 開買進/賣出,`initialSettlementAccountId` 帶使用者已經選好
// 的另一側帳戶當交割戶,market/symbol 留空時交回 dialog 自己的預設邏輯。
export type TradeDialogState = {
  account: WorkspaceAccount
  editing?: TradeRef
  initial?: { market?: string; symbol?: string; name?: string | null; type?: CreatableType }
  initialSettlementAccountId?: string
}

export type CreatableType = 'buy' | 'sell' | 'opening' | 'stock_dividend' | 'cash_dividend' | 'reinvest'
const CREATABLE_TYPES: CreatableType[] = ['buy', 'sell', 'opening', 'stock_dividend', 'cash_dividend', 'reinvest']

const TYPE_HINTS: Partial<Record<CreatableType, string>> = {
  opening: 'investments.tradeType.openingHint',
  stock_dividend: 'investments.tradeType.stockDividendHint',
  cash_dividend: 'investments.tradeType.cashDividendHint',
  reinvest: 'investments.tradeType.reinvestHint',
}

function holdingKey(accountId: string, market: string, symbol: string): string {
  return `${accountId}|${market}|${symbol}`
}


export function InvestmentsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, ledgers } = useLedgers()
  const { retryOnConflict } = useLedgerWrite()

  const [summary, setSummary] = usePageCache<HoldingsSummary | null>('investments:summary', null)
  const [accounts, setAccounts] = usePageCache<WorkspaceAccount[]>('investments:accounts', [])
  const [refreshing, setRefreshing] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [trades, setTrades] = useState<TradeRef[]>([])
  const [tradesLoading, setTradesLoading] = useState(false)
  const [showClosed, setShowClosed] = useState<Record<string, boolean>>({})
  const [tradeDialog, setTradeDialog] = useState<TradeDialogState | null>(null)
  const [settingsAccount, setSettingsAccount] = useState<WorkspaceAccount | null>(null)
  const [pendingDelete, setPendingDelete] = useState<TradeRef | null>(null)
  const [deleting, setDeleting] = useState(false)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  const refresh = useCallback(
    async (refreshQuotes = true) => {
      setRefreshing(true)
      try {
        const [s, a] = await Promise.all([
          fetchWorkspaceHoldings(token, { refresh: refreshQuotes }),
          fetchWorkspaceAccounts(token, { limit: 500 }),
        ])
        setSummary(s)
        setAccounts(a)
      } catch (err) {
        notifyError(err)
      } finally {
        setRefreshing(false)
      }
      // setSummary / setAccounts 来自 usePageCache,引用稳定
      // eslint-disable-next-line react-hooks/exhaustive-deps
    },
    [token, notifyError],
  )

  useEffect(() => {
    void refresh(true)
  }, [refresh])

  const loadTrades = useCallback(
    async (accountId: string, market: string, symbol: string) => {
      setTradesLoading(true)
      try {
        const perLedger = await Promise.all(
          ledgers.map((l) =>
            fetchStockTrades(token, l.ledger_id, { accountId, market, symbol })
              .then((rows) => rows.map((trade) => ({ ledgerId: l.ledger_id, trade })))
              .catch(() => [] as TradeRef[]),
          ),
        )
        const flat = perLedger.flat()
        flat.sort((a, b) => (b.trade.trade_date || '').localeCompare(a.trade.trade_date || ''))
        setTrades(flat)
      } finally {
        setTradesLoading(false)
      }
    },
    [ledgers, token],
  )

  const expandedRef = useRef(expanded)
  expandedRef.current = expanded
  const reloadAll = useCallback(async () => {
    await refresh(false)
    const key = expandedRef.current
    if (key) {
      const [accountId, market, symbol] = key.split('|')
      await loadTrades(accountId, market, symbol)
    }
  }, [refresh, loadTrades])

  useSyncRefresh(() => {
    void reloadAll()
  })

  const investmentAccounts = useMemo(
    () => accounts.filter((a) => a.account_type === 'investment'),
    [accounts],
  )
  const holdingsByAccount = useMemo(() => {
    const map = new Map<string, AccountHoldings>()
    for (const a of summary?.accounts ?? []) map.set(a.account_id, a)
    return map
  }, [summary])

  const oldestQuote = useMemo(() => {
    let oldest: string | null = null
    for (const a of summary?.accounts ?? []) {
      for (const h of a.holdings) {
        const qt = h.quote?.quote_time
        if (qt && (!oldest || qt < oldest)) oldest = qt
      }
    }
    return oldest
  }, [summary])

  const toggleHolding = (accountId: string, h: Holding) => {
    const key = holdingKey(accountId, h.market, h.symbol)
    if (expanded === key) {
      setExpanded(null)
      setTrades([])
      return
    }
    setExpanded(key)
    setTrades([])
    void loadTrades(accountId, h.market, h.symbol)
  }

  const onDeleteConfirm = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await retryOnConflict(pendingDelete.ledgerId, (base) =>
        deleteStockTrade(token, pendingDelete.ledgerId, pendingDelete.trade.id, base),
      )
      toast.success(t('investments.notice.deleted'), t('notice.success'))
      setPendingDelete(null)
      await reloadAll()
    } catch (err) {
      notifyError(err)
    } finally {
      setDeleting(false)
    }
  }

  const base = summary?.base_currency || ''

  return (
    <div className="space-y-4">
      <Card className="bc-panel">
        <CardHeader>
          <CardTitle>{t('nav.investments')}</CardTitle>
          <p className="text-sm text-muted-foreground">{t('investments.desc')}</p>
        </CardHeader>
        <CardContent>
          {summary && summary.accounts.some((a) => a.holdings.some((h) => h.shares > 0)) ? (
            <div className="rounded-lg border bg-muted/30 p-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-sm text-muted-foreground">{t('investments.card.title')}</div>
                  <div className="mt-1 text-3xl font-semibold tabular-nums">
                    {base ? formatStockMoney(summary.total_market_value, base) : '—'}
                  </div>
                  <div className={`mt-1 text-sm tabular-nums ${pnlClass(summary.total_unrealized_pnl)}`}>
                    {t('investments.unrealized')}{' '}
                    {base ? formatStockMoney(summary.total_unrealized_pnl, base, { signed: true }) : '—'}
                    {summary.total_cost > 0
                      ? ` (${formatPercent((summary.total_unrealized_pnl / summary.total_cost) * 100)})`
                      : ''}
                  </div>
                  {summary.pnl_after_sell_costs && summary.total_net_value !== undefined && (
                    <div className="mt-1 text-xs text-muted-foreground tabular-nums">
                      {t('investments.pnlAfterSellCostsNote')} · {t('investments.netValue')}{' '}
                      {base ? formatStockMoney(summary.total_net_value, base) : '—'}
                    </div>
                  )}
                  <div className="mt-1 text-xs text-muted-foreground tabular-nums">
                    {t('investments.cost')} {base ? formatStockMoney(summary.total_cost, base) : '—'} ·{' '}
                    {t('investments.card.hint')}
                  </div>
                  {summary.missing_rates.length > 0 && (
                    <div className="mt-1 text-xs text-amber-600">
                      {t('investments.missingRates', { currencies: summary.missing_rates.join(', ') })}
                    </div>
                  )}
                  {summary.stale && <div className="mt-1 text-xs text-amber-600">{t('investments.stale')}</div>}
                </div>
                <div className="flex flex-col items-end gap-2">
                  <Button variant="outline" size="sm" disabled={refreshing} onClick={() => void refresh(true)}>
                    {t('investments.refresh')}
                  </Button>
                  {oldestQuote && (
                    <span className="text-xs text-muted-foreground">
                      {t('investments.quoteAsOf', { time: formatQuoteTime(oldestQuote) })}
                    </span>
                  )}
                </div>
              </div>
            </div>
          ) : null}

          {investmentAccounts.length === 0 && (
            <p className="text-sm text-muted-foreground">{t('investments.empty')}</p>
          )}
        </CardContent>
      </Card>

      {investmentAccounts.length > 0 && <PendingDividendsPanel accounts={accounts} onConfirmed={reloadAll} />}

      {investmentAccounts.map((account) => {
        const data = holdingsByAccount.get(account.id)
        const open = (data?.holdings ?? [])
          .filter((h) => h.shares > 0)
          .sort((a, b) => (b.market_value ?? b.total_cost) - (a.market_value ?? a.total_cost))
        const closed = (data?.holdings ?? []).filter((h) => h.shares <= 0)
        const mvEntries = Object.entries(data?.market_value_by_currency ?? {})
        return (
          <Card key={account.id} className="bc-panel">
            <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
              <div>
                <CardTitle className="text-base">{account.name}</CardTitle>
                <div className="mt-1 text-sm tabular-nums text-muted-foreground">
                  {mvEntries.length > 0
                    ? mvEntries
                        .map(([ccy, mv]) => {
                          const cost = data?.cost_by_currency[ccy] ?? 0
                          return `${t('investments.marketValue')} ${formatStockMoney(mv, ccy)} · ${t('investments.cost')} ${formatStockMoney(cost, ccy)}`
                        })
                        .join('  |  ')
                    : account.currency}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button size="sm" onClick={() => setTradeDialog({ account })}>
                  {t('investments.button.addTrade')}
                </Button>
                <Button size="sm" variant="outline" onClick={() => setSettingsAccount(account)}>
                  {t('investments.button.feeSettings')}
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {open.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t('investments.noHoldings')}</p>
              ) : (
                <HoldingsTable
                  holdings={open}
                  accountId={account.id}
                  expanded={expanded}
                  trades={trades}
                  tradesLoading={tradesLoading}
                  onToggle={(h) => toggleHolding(account.id, h)}
                  onEditTrade={(ref) => setTradeDialog({ account, editing: ref })}
                  onDeleteTrade={(ref) => setPendingDelete(ref)}
                  onQuickTrade={(h, type) =>
                    setTradeDialog({
                      account,
                      initial: { market: h.market, symbol: h.symbol, name: h.security_name, type },
                    })
                  }
                />
              )}
              {closed.length > 0 && (
                <div className="mt-3">
                  <button
                    type="button"
                    className="text-sm text-muted-foreground hover:underline"
                    onClick={() => setShowClosed((s) => ({ ...s, [account.id]: !s[account.id] }))}
                  >
                    {showClosed[account.id] ? '▾' : '▸'} {t('investments.closed', { count: closed.length })}
                  </button>
                  {showClosed[account.id] && (
                    <HoldingsTable
                      holdings={closed}
                      accountId={account.id}
                      expanded={expanded}
                      trades={trades}
                      tradesLoading={tradesLoading}
                      onToggle={(h) => toggleHolding(account.id, h)}
                      onEditTrade={(ref) => setTradeDialog({ account, editing: ref })}
                      onDeleteTrade={(ref) => setPendingDelete(ref)}
                    />
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        )
      })}

      {tradeDialog && (
        <StockTradeDialog
          state={tradeDialog}
          accounts={accounts}
          holdings={holdingsByAccount.get(tradeDialog.account.id)?.holdings ?? []}
          activeLedgerId={activeLedgerId}
          onClose={() => setTradeDialog(null)}
          onSaved={async () => {
            setTradeDialog(null)
            toast.success(t('investments.notice.saved'), t('notice.success'))
            await reloadAll()
          }}
        />
      )}

      {settingsAccount && (
        <InvestmentSettingsDialog
          account={settingsAccount}
          accounts={accounts}
          activeLedgerId={activeLedgerId}
          onClose={() => setSettingsAccount(null)}
          onSaved={async () => {
            setSettingsAccount(null)
            toast.success(t('investments.notice.settingsSaved'), t('notice.success'))
            await refresh(false)
          }}
        />
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title={t('investments.confirm.deleteTitle')}
        description={t('investments.confirm.deleteDesc')}
        confirmText={t('common.delete')}
        cancelText={t('dialog.cancel')}
        loading={deleting}
        onCancel={() => !deleting && setPendingDelete(null)}
        onConfirm={() => void onDeleteConfirm()}
      />
    </div>
  )
}

function HoldingsTable({
  holdings,
  accountId,
  expanded,
  trades,
  tradesLoading,
  onToggle,
  onEditTrade,
  onDeleteTrade,
  onQuickTrade,
}: {
  holdings: Holding[]
  accountId: string
  expanded: string | null
  trades: TradeRef[]
  tradesLoading: boolean
  onToggle: (h: Holding) => void
  onEditTrade: (ref: TradeRef) => void
  onDeleteTrade: (ref: TradeRef) => void
  onQuickTrade?: (h: Holding, type: CreatableType) => void
}) {
  const t = useT()
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs text-muted-foreground">
          <tr className="border-b">
            <th className="py-2 text-left font-medium">{t('investments.col.symbol')}</th>
            <th className="py-2 text-right font-medium">{t('investments.col.shares')}</th>
            <th className="py-2 text-right font-medium">{t('investments.col.avgCost')}</th>
            <th className="py-2 text-right font-medium">{t('investments.col.price')}</th>
            <th className="py-2 text-right font-medium">{t('investments.col.marketValue')}</th>
            <th className="py-2 text-right font-medium" title={t('investments.col.netValueHint')}>
              {t('investments.col.netValue')}
            </th>
            <th className="py-2 text-right font-medium">{t('investments.col.pnl')}</th>
          </tr>
        </thead>
        <tbody>
          {holdings.map((h) => {
            const key = holdingKey(accountId, h.market, h.symbol)
            const isOpen = h.shares > 0
            const ccy = h.currency || ''
            const day = h.quote?.change_percent ?? null
            return (
              <HoldingRowGroup
                key={key}
                h={h}
                ccy={ccy}
                day={day}
                isOpen={isOpen}
                expanded={expanded === key}
                trades={trades}
                tradesLoading={tradesLoading}
                onToggle={() => onToggle(h)}
                onEditTrade={onEditTrade}
                onDeleteTrade={onDeleteTrade}
                onQuickTrade={onQuickTrade ? (type) => onQuickTrade(h, type) : undefined}
              />
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function HoldingRowGroup({
  h,
  ccy,
  day,
  isOpen,
  expanded,
  trades,
  tradesLoading,
  onToggle,
  onEditTrade,
  onDeleteTrade,
  onQuickTrade,
}: {
  h: Holding
  ccy: string
  day: number | null
  isOpen: boolean
  expanded: boolean
  trades: TradeRef[]
  tradesLoading: boolean
  onToggle: () => void
  onEditTrade: (ref: TradeRef) => void
  onDeleteTrade: (ref: TradeRef) => void
  onQuickTrade?: (type: CreatableType) => void
}) {
  const t = useT()
  return (
    <>
      <tr className="cursor-pointer border-b hover:bg-accent/40" onClick={onToggle}>
        <td className="py-2">
          <div className="font-medium">
            {h.symbol} <span className="font-normal text-muted-foreground">{h.security_name || ''}</span>
          </div>
          <div className="text-xs text-muted-foreground">
            {t(`investments.market.${h.market}`)}
            {h.quote?.session ? ` · ${h.quote.session === 'close' ? '●' : '○'}` : ''}
          </div>
        </td>
        <td className="py-2 text-right tabular-nums">{isOpen ? formatShares(h.shares) : '0'}</td>
        <td className="py-2 text-right tabular-nums">{isOpen ? formatPrice(h.avg_cost) : '—'}</td>
        <td className="py-2 text-right tabular-nums">
          {h.quote?.price !== null && h.quote?.price !== undefined ? (
            <>
              {formatPrice(h.quote.price)}
              {day !== null && <div className={`text-xs ${pnlClass(day)}`}>{formatPercent(day)}</div>}
            </>
          ) : (
            '—'
          )}
        </td>
        <td className="py-2 text-right tabular-nums">
          {h.market_value !== null ? formatStockMoney(h.market_value, ccy) : '—'}
        </td>
        <td className="py-2 text-right tabular-nums">
          {isOpen && h.net_value !== null && h.net_value !== undefined ? formatStockMoney(h.net_value, ccy) : '—'}
        </td>
        <td className={`py-2 text-right tabular-nums ${pnlClass(isOpen ? h.unrealized_pnl : h.realized_pnl)}`}>
          {isOpen
            ? h.unrealized_pnl !== null
              ? `${formatStockMoney(h.unrealized_pnl, ccy, { signed: true })}${
                  h.unrealized_pnl_percent !== null ? ` (${formatPercent(h.unrealized_pnl_percent)})` : ''
                }`
              : '—'
            : `${t('investments.realized')} ${formatStockMoney(h.realized_pnl, ccy, { signed: true })}`}
          {isOpen && h.unrealized_pnl !== null && h.pnl_after_sell_costs && (
            <div className="text-xs text-muted-foreground">{t('investments.pnlAfterSellCostsNote')}</div>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b bg-muted/20">
          <td colSpan={7} className="px-2 py-3">
            <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span>
                {t('investments.realized')}{' '}
                <span className={pnlClass(h.realized_pnl)}>{formatStockMoney(h.realized_pnl, ccy, { signed: true })}</span>
              </span>
              {h.dividends > 0 && (
                <span>
                  {t('investments.dividends')} {formatStockMoney(h.dividends, ccy)}
                </span>
              )}
              {isOpen && h.est_sell_fee !== null && h.est_sell_fee !== undefined && (
                <span>
                  {t('investments.estSellFee')} {formatStockMoney(h.est_sell_fee, ccy)} · {t('investments.estSellTax')}{' '}
                  {formatStockMoney(h.est_sell_tax ?? 0, ccy)}
                </span>
              )}
              {onQuickTrade && (
                <span className="ml-auto flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => onQuickTrade('buy')}>
                    {t('investments.tradeType.buy')}
                  </Button>
                  {isOpen && (
                    <Button size="sm" variant="outline" onClick={() => onQuickTrade('sell')}>
                      {t('investments.tradeType.sell')}
                    </Button>
                  )}
                </span>
              )}
            </div>
            {tradesLoading ? (
              <div className="text-xs text-muted-foreground">…</div>
            ) : (
              <table className="w-full text-xs">
                <tbody>
                  {trades.map((ref) => {
                    const tr = ref.trade
                    const editable = CREATABLE_TYPES.includes(tr.trade_type as CreatableType)
                    return (
                      <tr key={tr.id} className="border-t">
                        <td className="py-1.5">{isoToDateValue(tr.trade_date)}</td>
                        <td className="py-1.5">{t(`investments.tradeType.${tr.trade_type}`)}</td>
                        <td className="py-1.5 text-right tabular-nums">
                          {formatShares(tr.shares)}
                          {tr.price !== null && tr.trade_type !== 'stock_dividend' ? ` @ ${formatPrice(tr.price)}` : ''}
                        </td>
                        <td className="py-1.5 text-right tabular-nums">
                          {tr.amount ? formatStockMoney(tr.amount, tr.currency) : ''}
                        </td>
                        <td className="py-1.5 pl-3 text-muted-foreground">{tr.note || ''}</td>
                        <td className="py-1.5 text-right">
                          {editable && (
                            <span className="flex justify-end gap-1">
                              <Button size="sm" variant="ghost" onClick={() => onEditTrade(ref)}>
                                {t('common.edit')}
                              </Button>
                              <Button size="sm" variant="ghost" onClick={() => onDeleteTrade(ref)}>
                                {t('common.delete')}
                              </Button>
                            </span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

export function StockTradeDialog({
  state,
  accounts,
  holdings,
  activeLedgerId,
  onClose,
  onSaved,
}: {
  state: TradeDialogState
  accounts: WorkspaceAccount[]
  holdings: Holding[]
  activeLedgerId: string | null
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { retryOnConflict } = useLedgerWrite()
  const { account, editing, initial, initialSettlementAccountId } = state
  const settings = account.investment_settings ?? null
  const editingTrade = editing?.trade

  const [tradeType, setTradeType] = useState<CreatableType>(
    (editingTrade?.trade_type as CreatableType) || initial?.type || 'buy',
  )
  const [market, setMarket] = useState<string>(
    editingTrade?.market || initial?.market || settings?.market || defaultMarketForCurrency(account.currency),
  )
  const [currency, setCurrency] = useState<string>(
    (editingTrade?.currency || marketCurrency(editingTrade?.market || initial?.market || settings?.market || defaultMarketForCurrency(account.currency)) || account.currency || '').toUpperCase(),
  )
  const [symbol, setSymbol] = useState(editingTrade?.symbol || initial?.symbol || '')
  const [name, setName] = useState(editingTrade?.security_name || initial?.name || '')
  const [shares, setShares] = useState(numText(editingTrade?.shares))
  const [price, setPrice] = useState(numText(editingTrade?.price))
  const [fee, setFee] = useState(numText(editingTrade?.fee))
  const [tax, setTax] = useState(numText(editingTrade?.tax))
  const [feeEdited, setFeeEdited] = useState(Boolean(editingTrade))
  const [taxEdited, setTaxEdited] = useState(Boolean(editingTrade))
  const [settlementId, setSettlementId] = useState<string>(
    editingTrade ? '' : initialSettlementAccountId || settings?.settlementAccountId || '',
  )
  const [settlementAmount, setSettlementAmount] = useState('')
  const [tradeDate, setTradeDate] = useState(isoToDateValue(editingTrade?.trade_date))
  const [note, setNote] = useState(editingTrade?.note || '')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [results, setResults] = useState<SecuritySearchItem[]>([])
  const [searchOpen, setSearchOpen] = useState(false)
  const searchSeq = useRef(0)
  // 選/輸入代號後自動帶入的現價;使用者自己改價格後就不再覆蓋。
  const [prefilledQuote, setPrefilledQuote] = useState<SecurityQuote | null>(null)
  const [priceEdited, setPriceEdited] = useState(Boolean(editingTrade))
  const quoteSeq = useRef(0)

  // 需要選「交割/入帳帳戶」的類型;reinvest 入投資理財帳戶本身。
  const isCash = tradeType === 'buy' || tradeType === 'sell' || tradeType === 'cash_dividend'
  const isDividend = tradeType === 'cash_dividend'
  const settlement = accounts.find((a) => a.id === settlementId)
  const receiving = tradeType === 'reinvest' ? account : isCash ? settlement : undefined
  const crossCurrency = Boolean(receiving && (receiving.currency || '').toUpperCase() !== currency)
  const sharesNum = Number(shares) || 0
  const priceNum = Number(price) || 0
  const gross = stockGross(sharesNum, priceNum, currency)
  // 期初持股填的是成本、股利填的是每股股利,只有買/賣/再投入帶現價。
  const usesMarketPrice = tradeType === 'buy' || tradeType === 'sell' || tradeType === 'reinvest'

  // 編輯既有買進/賣出:從綁定的轉帳交易推回交割帳戶/交割金額。
  useEffect(() => {
    if (!editingTrade?.tx_id || !editing) return
    let cancelled = false
    void (async () => {
      try {
        const { fetchWorkspaceTransactions } = await import('@beecount/api-client')
        const page = await fetchWorkspaceTransactions(token, { txSyncId: editingTrade.tx_id!, limit: 1 })
        const tx = page.items[0] as unknown as Record<string, unknown> | undefined
        if (!tx || cancelled) return
        const from = (tx.from_account_id as string) || (tx.account_id as string) || ''
        const to = (tx.to_account_id as string) || ''
        const type = editingTrade.trade_type
        const sid = type === 'buy' ? from : type === 'cash_dividend' || type === 'reinvest' ? (tx.account_id as string) || '' : to
        if (type !== 'reinvest') setSettlementId(sid)
        const acc = type === 'reinvest' ? account : accounts.find((a) => a.id === sid)
        if (acc && (acc.currency || '').toUpperCase() !== currency) {
          const v = type === 'sell' ? (tx.to_amount as number) : (tx.amount as number)
          if (typeof v === 'number') setSettlementAmount(numText(v))
        }
      } catch {
        // 找不到綁定交易時讓使用者自己選交割帳戶
      }
    })()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 成交金額變了就重算建議手續費/稅(使用者手動改過的欄位不動)。
  useEffect(() => {
    if (tradeType === 'stock_dividend') return
    if (isDividend) {
      // 現金股利:手續費 = 股利手續費,稅 = 預扣稅 + 二代健保(同 server 估算)。
      const est = estimateDividend({ market, currency, shares: sharesNum, cashPerShare: priceNum, settings })
      if (!feeEdited) setFee(est.fee > 0 ? numText(est.fee) : '')
      if (!taxEdited) setTax(est.tax > 0 ? numText(est.tax) : '')
      return
    }
    if (!feeEdited) setFee(gross > 0 ? numText(suggestFee(gross, settings, market, currency)) : '')
    if (!taxEdited) {
      const suggested = tradeType === 'sell' ? suggestSellTax(gross, settings, market, currency, symbol) : 0
      setTax(gross > 0 && suggested > 0 ? numText(suggested) : '')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gross, tradeType, market, currency, symbol])

  // 帶入目前報價(新增時、價格還沒自己改過)。代號停止輸入 0.6 秒後才抓。
  useEffect(() => {
    if (editing || priceEdited || !usesMarketPrice) return
    const sym = symbol.trim().toUpperCase()
    if (!sym) return
    const seq = ++quoteSeq.current
    const timer = window.setTimeout(async () => {
      try {
        const [q] = await fetchSecurityQuotes(token, [`${market}:${sym}`])
        if (seq !== quoteSeq.current || !q || q.price === null) return
        setPrice(numText(q.price))
        setPrefilledQuote(q)
        if (!name.trim() && q.name) setName(q.name)
      } catch {
        // 抓不到報價就讓使用者自己填
      }
    }, 600)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, symbol, tradeType, priceEdited])

  // 現金股利預填持有股數。
  useEffect(() => {
    if (!isDividend || editing || shares) return
    const h = holdings.find((x) => x.market === market && x.symbol === symbol.trim().toUpperCase())
    if (h && h.shares > 0) setShares(numText(h.shares))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDividend, market, symbol])

  useEffect(() => {
    if (!usesMarketPrice && prefilledQuote && !priceEdited) {
      setPrice('')
      setPrefilledQuote(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [usesMarketPrice])

  // 代號/市場真的換了才清掉上一檔帶入的價格(從搜尋結果點同一檔時保留)。
  const clearPrefilledPrice = (nextMarket: string, nextSymbol: string) => {
    if (!prefilledQuote || priceEdited) return
    if (prefilledQuote.market === nextMarket.toUpperCase() && prefilledQuote.symbol === nextSymbol.trim().toUpperCase()) {
      return
    }
    setPrice('')
    setPrefilledQuote(null)
  }

  const onSymbolChange = (value: string) => {
    setSymbol(value)
    clearPrefilledPrice(market, value)
    const q = value.trim()
    const seq = ++searchSeq.current
    if (!q || editing) {
      setResults([])
      return
    }
    window.setTimeout(async () => {
      if (seq !== searchSeq.current) return
      try {
        const isTaiwan = market === 'TW' || market === 'TWO'
        const rows = await searchSecurities(token, q, isTaiwan ? null : market)
        if (seq !== searchSeq.current) return
        setResults(rows.slice(0, 8))
        setSearchOpen(true)
      } catch {
        setResults([])
      }
    }, 350)
  }

  const pickResult = (r: SecuritySearchItem) => {
    clearPrefilledPrice(r.market, r.symbol)
    setMarket(r.market)
    setCurrency(r.currency.toUpperCase())
    setSymbol(r.symbol)
    setName(r.name)
    setResults([])
    setSearchOpen(false)
  }

  const heldShares = useMemo(() => {
    const h = holdings.find((x) => x.market === market && x.symbol === symbol.trim().toUpperCase())
    const base = h?.shares ?? 0
    return editingTrade?.trade_type === 'sell' ? base + editingTrade.shares : base
  }, [holdings, market, symbol, editingTrade])

  const secAmount = stockTradeAmount(tradeType, sharesNum, priceNum, Number(fee) || 0, Number(tax) || 0, currency)

  // 「證交稅率 0.1%(ETF)」:台股依代號判斷普通股/ETF/債券 ETF。
  const sellTaxHint = (() => {
    const rate = `${Number((sellTaxRateFor(settings, market, symbol) * 100).toFixed(4))}%`
    if (market !== 'TW' && market !== 'TWO') return t('investments.sellTaxRateHint', { rate })
    return t('investments.sellTaxRateHintWithKind', {
      rate,
      kind: t(`investments.securityKind.${securityKind(market, symbol)}`),
    })
  })()

  const onSave = async () => {
    const sym = symbol.trim().toUpperCase()
    if (!sym) return toast.error(t('investments.error.symbolRequired'), t('notice.error'))
    if (!(sharesNum > 0)) return toast.error(t('investments.error.sharesRequired'), t('notice.error'))
    if (tradeType !== 'stock_dividend' && !price.trim()) {
      return toast.error(t('investments.error.priceRequired'), t('notice.error'))
    }
    if (isCash && !settlementId) {
      return toast.error(
        t(isDividend ? 'investments.error.receivingRequired' : 'investments.error.settlementRequired'),
        t('notice.error'),
      )
    }
    if (crossCurrency && !(Number(settlementAmount) > 0)) {
      return toast.error(t('investments.error.settlementAmountRequired'), t('notice.error'))
    }
    if (tradeType === 'sell' && sharesNum > heldShares + 1e-6) {
      return toast.error(t('investments.error.oversell', { held: formatShares(heldShares) }), t('notice.error'))
    }
    const ledgerId = editing?.ledgerId || activeLedgerId
    if (!ledgerId) return toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
    setSaving(true)
    try {
      if (editing) {
        await retryOnConflict(ledgerId, (base) =>
          updateStockTrade(token, ledgerId, editing.trade.id, base, {
            shares: sharesNum,
            price: priceNum,
            fee: Number(fee) || 0,
            tax: Number(tax) || 0,
            trade_date: dateValueToIso(tradeDate),
            security_name: name.trim() || null,
            note: note.trim() || null,
            ...(isCash ? { settlement_account_id: settlementId } : {}),
            ...(crossCurrency ? { settlement_amount: Number(settlementAmount) } : {}),
          }),
        )
      } else {
        await retryOnConflict(ledgerId, (base) =>
          createStockTrade(token, ledgerId, base, {
            account_id: account.id,
            trade_type: tradeType,
            market,
            symbol: sym,
            security_name: name.trim() || null,
            shares: sharesNum,
            price: tradeType === 'stock_dividend' ? 0 : priceNum,
            fee: tradeType === 'stock_dividend' ? 0 : Number(fee) || 0,
            tax: tradeType === 'sell' || isDividend ? Number(tax) || 0 : 0,
            currency,
            trade_date: dateValueToIso(tradeDate),
            settlement_account_id: isCash ? settlementId : null,
            settlement_amount: crossCurrency ? Number(settlementAmount) : null,
            note: note.trim() || null,
          }),
        )
      }
      await onSaved()
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
            {editing ? t('investments.dialog.editTitle') : t('investments.dialog.createTitle')} · {account.name}
          </DialogTitle>
        </DialogHeader>
        <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
          <div className="flex flex-wrap gap-2">
            {(editing ? [tradeType] : CREATABLE_TYPES).map((type) => (
              <Button
                key={type}
                size="sm"
                variant={tradeType === type ? 'default' : 'outline'}
                disabled={Boolean(editing)}
                onClick={() => setTradeType(type)}
              >
                {t(`investments.tradeType.${type}`)}
              </Button>
            ))}
          </div>
          {TYPE_HINTS[tradeType] && (
            <p className="text-xs text-muted-foreground">{t(TYPE_HINTS[tradeType]!)}</p>
          )}
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1">
              <Label>{t('investments.field.market')}</Label>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-muted px-3 text-sm"
                value={market}
                disabled={Boolean(editing)}
                onChange={(e) => {
                  setMarket(e.target.value)
                  setCurrency(marketCurrency(e.target.value) || currency)
                }}
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
                disabled={Boolean(editing)}
                placeholder={t('investments.field.symbolSearch')}
                onChange={(e) => onSymbolChange(e.target.value)}
                onFocus={() => results.length > 0 && setSearchOpen(true)}
              />
              {searchOpen && !editing && symbol.trim() && (
                <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-md">
                  {results.map((r) => (
                    <button
                      key={`${r.market}:${r.symbol}`}
                      type="button"
                      className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-accent"
                      onClick={() => pickResult(r)}
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
                    onClick={() => {
                      setSymbol(symbol.trim().toUpperCase())
                      setSearchOpen(false)
                    }}
                  >
                    {t('investments.useTyped', { symbol: symbol.trim().toUpperCase() })}
                  </button>
                </div>
              )}
            </div>
          </div>
          <div className="space-y-1">
            <Label>{t('investments.field.name')}</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('investments.field.shares')}</Label>
              <Input inputMode="decimal" value={shares} onChange={(e) => setShares(e.target.value)} />
            </div>
            {tradeType !== 'stock_dividend' && (
              <div className="space-y-1">
                <Label>
                  {t(isDividend ? 'investments.field.dividendPerShare' : 'investments.field.price', { currency })}
                </Label>
                <Input
                  inputMode="decimal"
                  value={price}
                  onChange={(e) => {
                    setPriceEdited(true)
                    setPrefilledQuote(null)
                    setPrice(e.target.value)
                  }}
                />
              </div>
            )}
          </div>
          {prefilledQuote && usesMarketPrice && (
            <div className="-mt-2 text-xs text-muted-foreground">
              {t('investments.pricePrefilled', {
                when: `${prefilledQuote.session === 'close' ? t('investments.quoteClose') : t('investments.quoteIntraday')} ${formatQuoteTime(prefilledQuote.quote_time || prefilledQuote.fetched_at)}`,
              })}
            </div>
          )}
          {tradeType !== 'stock_dividend' && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>{t('investments.field.fee')}</Label>
                <Input
                  inputMode="decimal"
                  value={fee}
                  onChange={(e) => {
                    setFeeEdited(true)
                    setFee(e.target.value)
                  }}
                />
              </div>
              {(tradeType === 'sell' || isDividend) && (
                <div className="space-y-1">
                  <Label>{t(isDividend ? 'investments.field.dividendTax' : 'investments.field.tax')}</Label>
                  <Input
                    inputMode="decimal"
                    value={tax}
                    onChange={(e) => {
                      setTaxEdited(true)
                      setTax(e.target.value)
                    }}
                  />
                  {tradeType === 'sell' && <div className="text-xs text-muted-foreground">{sellTaxHint}</div>}
                </div>
              )}
            </div>
          )}
          {tradeType !== 'stock_dividend' && (
            <div className="flex items-center justify-between rounded-md bg-muted/40 px-3 py-2 text-sm">
              <span className="text-xs text-muted-foreground">{t('investments.field.feeHint')}</span>
              <span className="font-semibold tabular-nums">
                {t(
                  isDividend ? 'investments.dividendNet' : tradeType === 'sell' ? 'investments.netProceeds' : 'investments.totalCost',
                )}{' '}
                {formatStockMoney(secAmount, currency)}
              </span>
            </div>
          )}
          {isCash && (
            <div className="space-y-1">
              <Label>{t(isDividend ? 'investments.field.receivingAccount' : 'investments.field.settlementAccount')}</Label>
              <button
                type="button"
                onClick={() => setPickerOpen(true)}
                className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
              >
                <span className={`flex-1 truncate ${settlement ? '' : 'text-muted-foreground'}`}>
                  {settlement
                    ? `${settlement.name} · ${settlement.currency}`
                    : t(isDividend ? 'investments.error.receivingRequired' : 'investments.error.settlementRequired')}
                </span>
                <span className="text-xs text-muted-foreground opacity-60">▾</span>
              </button>
            </div>
          )}
          {crossCurrency && receiving && (
            <div className="space-y-1">
              <Label>{t('investments.field.settlementAmount', { currency: receiving.currency || '' })}</Label>
              <Input
                inputMode="decimal"
                value={settlementAmount}
                onChange={(e) => setSettlementAmount(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">{t('investments.field.settlementAmountHint')}</p>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('investments.field.tradeDate')}</Label>
              <DatePicker value={tradeDate} onChange={setTradeDate} />
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
          <Button disabled={saving} onClick={() => void onSave()}>
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
      <AccountPickerDialog
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        accounts={accounts.filter((a) => a.id !== account.id)}
        value={settlement?.name || ''}
        title={t('investments.field.settlementAccount')}
        onSelect={(row) => {
          setSettlementId(row.id)
          setPickerOpen(false)
        }}
      />
    </Dialog>
  )
}

const SETTINGS_FIELDS: {
  key: keyof InvestmentSettings
  labelKey: string
  percent: boolean
  /** 只有台股有:證交稅依標的類型不同(依代號判斷,見 securityKind)。 */
  twOnly?: boolean
}[] = [
  { key: 'feeRate', labelKey: 'investments.settings.feeRate', percent: true },
  { key: 'feeDiscount', labelKey: 'investments.settings.feeDiscount', percent: true },
  { key: 'feeMin', labelKey: 'investments.settings.feeMin', percent: false },
  { key: 'sellTaxRate', labelKey: 'investments.settings.sellTaxRate', percent: true },
  { key: 'etfSellTaxRate', labelKey: 'investments.settings.etfSellTaxRate', percent: true, twOnly: true },
  { key: 'bondEtfSellTaxRate', labelKey: 'investments.settings.bondEtfSellTaxRate', percent: true, twOnly: true },
  { key: 'dividendFeeFixed', labelKey: 'investments.settings.dividendFeeFixed', percent: false },
  { key: 'dividendFeeRate', labelKey: 'investments.settings.dividendFeeRate', percent: true },
  { key: 'dividendWithholdingRate', labelKey: 'investments.settings.dividendWithholdingRate', percent: true },
  { key: 'nhiSupplementRate', labelKey: 'investments.settings.nhiSupplementRate', percent: true },
  { key: 'nhiThreshold', labelKey: 'investments.settings.nhiThreshold', percent: false },
]

function InvestmentSettingsDialog({
  account,
  accounts,
  activeLedgerId,
  onClose,
  onSaved,
}: {
  account: WorkspaceAccount
  accounts: WorkspaceAccount[]
  activeLedgerId: string | null
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { retryOnConflict } = useLedgerWrite()
  const initial = account.investment_settings ?? {}
  const [market, setMarket] = useState<string>(initial.market || defaultMarketForCurrency(account.currency))
  const [values, setValues] = useState<Record<string, string>>(() => {
    const out: Record<string, string> = {}
    for (const f of SETTINGS_FIELDS) {
      const v = initial[f.key] as number | undefined
      out[f.key] = f.percent ? rateToPercentText(v) : v === undefined ? '' : String(v)
    }
    return out
  })
  const [reinvest, setReinvest] = useState(Boolean(initial.reinvestDividends))
  const [pnlAfterSellCosts, setPnlAfterSellCosts] = useState(initial.pnlAfterSellCosts !== false)
  const [settlementId, setSettlementId] = useState(initial.settlementAccountId || '')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const defaults = investmentDefaults(market)
  const settlement = accounts.find((a) => a.id === settlementId)
  const isTw = market === 'TW' || market === 'TWO'
  const visibleFields = SETTINGS_FIELDS.filter((f) => !f.twOnly || isTw)

  const onSave = async () => {
    if (!activeLedgerId) return toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
    const next: InvestmentSettings = { market }
    for (const f of visibleFields) {
      const raw = values[f.key] ?? ''
      const parsed = f.percent ? percentTextToRate(raw) : raw.trim() ? Number(raw) : undefined
      if (parsed !== undefined && Number.isFinite(parsed)) {
        ;(next as Record<string, unknown>)[f.key] = parsed
      }
    }
    if (reinvest) next.reinvestDividends = true
    // 預設就是開,關掉才存。
    if (!pnlAfterSellCosts) next.pnlAfterSellCosts = false
    if (settlementId) next.settlementAccountId = settlementId
    setSaving(true)
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateAccount(token, activeLedgerId, account.id, base, { investment_settings: next }),
      )
      await onSaved()
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
          <DialogTitle>{t('investments.settings.title', { account: account.name })}</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">{t('investments.settings.desc')}</p>
        <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('investments.settings.defaultMarket')}</Label>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-muted px-3 text-sm"
                value={market}
                onChange={(e) => setMarket(e.target.value)}
              >
                {STOCK_MARKETS.map((m) => (
                  <option key={m.code} value={m.code}>
                    {t(`investments.market.${m.code}`)}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label>{t('investments.field.settlementAccount')}</Label>
              <button
                type="button"
                onClick={() => setPickerOpen(true)}
                className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm"
              >
                <span className={`flex-1 truncate ${settlement ? '' : 'text-muted-foreground'}`}>
                  {settlement?.name || '—'}
                </span>
                <span className="text-xs text-muted-foreground opacity-60">▾</span>
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {visibleFields.map((f) => {
              const d = defaults[f.key as keyof typeof defaults] as number
              const defaultText = f.percent ? `${rateToPercentText(d)}%` : String(d)
              const labelKey = isTw && f.key === 'sellTaxRate' ? 'investments.settings.sellTaxRateStock' : f.labelKey
              return (
                <div key={f.key} className="space-y-1">
                  <Label>{t(labelKey)}</Label>
                  <Input
                    inputMode="decimal"
                    value={values[f.key] ?? ''}
                    placeholder={defaultText}
                    onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                  />
                  <p className="text-xs text-muted-foreground">
                    {t('investments.settings.marketDefault', { value: defaultText })}
                  </p>
                </div>
              )
            })}
          </div>
          {isTw && <p className="text-xs text-muted-foreground">{t('investments.settings.sellTaxKindHint')}</p>}
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={pnlAfterSellCosts}
              onChange={(e) => setPnlAfterSellCosts(e.target.checked)}
            />
            <span>
              {t('investments.settings.pnlAfterSellCosts')}
              <span className="block text-xs text-muted-foreground">
                {t('investments.settings.pnlAfterSellCostsDesc')}
              </span>
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={reinvest} onChange={(e) => setReinvest(e.target.checked)} />
            {t('investments.settings.reinvestDividends')}
          </label>
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
      <AccountPickerDialog
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        accounts={accounts.filter((a) => a.id !== account.id)}
        value={settlement?.name || ''}
        allowNone
        noneLabel="—"
        title={t('investments.field.settlementAccount')}
        onSelect={(row) => {
          setSettlementId(row.id)
          setPickerOpen(false)
        }}
      />
    </Dialog>
  )
}
