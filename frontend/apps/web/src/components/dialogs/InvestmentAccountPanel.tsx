import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  deleteStockTrade,
  fetchReadRecurringRules,
  fetchStockTrades,
  fetchWorkspaceAccounts,
  fetchWorkspaceHoldings,
  type AccountHoldings,
  type Holding,
  type ReadRecurringRule,
  type WorkspaceAccount,
} from '@beecount/api-client'
import { Button, useT, useToast } from '@beecount/ui'
import { ConfirmDialog, formatPercent, formatStockMoney } from '@beecount/web-features'

import { useLedgerWrite } from '../../app/useLedgerWrite'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import {
  DcaPlanList,
  HoldingsTable,
  InvestmentSettingsDialog,
  StockTradeDialog,
  holdingKey,
  type TradeDialogState,
  type TradeRef,
} from '../../pages/sections/InvestmentsPage'
import { pnlClass } from '../../pages/sections/investmentsShared'
import { routePath } from '../../state/router'

/**
 * 帳戶詳情彈窗裡「投資理財(股票)帳戶」專用的內容(取代一般帳戶的
 * 餘額/收入/支出統計 + 調整餘額按鈕)。
 *
 * 投資理財帳戶的餘額是「持股成本的帳面數」(買進時由交割帳戶轉進來),手動調整
 * 餘額會讓金額跟持股對不上,所以這裡完全不提供調整餘額;改成顯示持股市值/
 * 成本/未實現損益、持股清單(可展開看明細、編輯、刪除)、買進/賣出/定期定額/
 * 期初持股/費用設定,跟投資頁的帳戶卡片功能一致。
 */
export function InvestmentAccountPanel({ accountId }: { accountId: string }) {
  const t = useT()
  const toast = useToast()
  const navigate = useNavigate()
  const { token } = useAuth()
  const { activeLedgerId, ledgers } = useLedgers()
  const { retryOnConflict } = useLedgerWrite()

  const [accounts, setAccounts] = useState<WorkspaceAccount[]>([])
  const [data, setData] = useState<AccountHoldings | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [trades, setTrades] = useState<TradeRef[]>([])
  const [tradesLoading, setTradesLoading] = useState(false)
  const [showClosed, setShowClosed] = useState(false)
  const [dcaRules, setDcaRules] = useState<ReadRecurringRule[]>([])
  const [tradeDialog, setTradeDialog] = useState<TradeDialogState | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<TradeRef | null>(null)
  const [deleting, setDeleting] = useState(false)

  const account = useMemo(() => accounts.find((a) => a.id === accountId) ?? null, [accounts, accountId])

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  const load = useCallback(async () => {
    try {
      const [summary, accs, rules] = await Promise.all([
        fetchWorkspaceHoldings(token, { refresh: false }),
        fetchWorkspaceAccounts(token, { limit: 500 }),
        activeLedgerId
          ? fetchReadRecurringRules(token, activeLedgerId).catch(() => [] as ReadRecurringRule[])
          : Promise.resolve([] as ReadRecurringRule[]),
      ])
      setData(summary.accounts.find((a) => a.account_id === accountId) ?? null)
      setAccounts(accs)
      setDcaRules(rules.filter((r) => r.kind === 'stock_dca' && r.to_account_id === accountId))
    } catch (err) {
      notifyError(err)
    } finally {
      setLoaded(true)
    }
  }, [token, accountId, activeLedgerId, notifyError])

  useEffect(() => {
    setLoaded(false)
    setExpanded(null)
    setTrades([])
    void load()
  }, [load])

  const loadTrades = useCallback(
    async (market: string, symbol: string) => {
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
    [ledgers, token, accountId],
  )

  const reloadAll = useCallback(async () => {
    await load()
    if (expanded) {
      const [, market, symbol] = expanded.split('|')
      await loadTrades(market, symbol)
    }
  }, [load, loadTrades, expanded])

  useSyncRefresh(() => {
    void reloadAll()
  })

  const toggleHolding = (h: Holding) => {
    const key = holdingKey(accountId, h.market, h.symbol)
    if (expanded === key) {
      setExpanded(null)
      setTrades([])
      return
    }
    setExpanded(key)
    setTrades([])
    void loadTrades(h.market, h.symbol)
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

  const open = useMemo(
    () =>
      (data?.holdings ?? [])
        .filter((h) => h.shares > 0)
        .sort((a, b) => (b.market_value ?? b.total_cost) - (a.market_value ?? a.total_cost)),
    [data],
  )
  const closed = useMemo(() => (data?.holdings ?? []).filter((h) => h.shares <= 0), [data])
  const currencies = Object.keys(data?.market_value_by_currency ?? {})

  const openTrade = (initial?: TradeDialogState['initial']) => account && setTradeDialog({ account, initial })

  return (
    <div className="space-y-4 border-b border-border/60 px-6 py-4">
      {/* 市值 / 成本 / 未實現損益(每個幣別一列) */}
      {currencies.length > 0 ? (
        <div className="space-y-2">
          {currencies.map((ccy) => {
            const mv = data?.market_value_by_currency[ccy] ?? 0
            const cost = data?.cost_by_currency[ccy] ?? 0
            const pnl = mv - cost
            return (
              <div key={ccy} className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <div className="text-xs text-muted-foreground">{t('investments.marketValue')}</div>
                  <div className="mt-1 text-lg font-semibold tabular-nums">{formatStockMoney(mv, ccy)}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">{t('investments.cost')}</div>
                  <div className="mt-1 text-lg font-semibold tabular-nums">{formatStockMoney(cost, ccy)}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">{t('investments.unrealized')}</div>
                  <div className={`mt-1 text-lg font-semibold tabular-nums ${pnlClass(pnl)}`}>
                    {formatStockMoney(pnl, ccy, { signed: true })}
                    {cost > 0 ? (
                      <span className="ml-1 text-xs font-normal">({formatPercent((pnl / cost) * 100)})</span>
                    ) : null}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={!account} onClick={() => openTrade({ type: 'buy' })}>
          {t('investments.tradeType.buy')}
        </Button>
        <Button size="sm" variant="outline" disabled={!account || open.length === 0} onClick={() => openTrade({ type: 'sell' })}>
          {t('investments.tradeType.sell')}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!account}
          onClick={() => account && setTradeDialog({ account, mode: 'dca' })}
        >
          {t('investments.button.addDca')}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!account}
          onClick={() => account && setTradeDialog({ account, batchOpening: true })}
        >
          {t('investments.button.openingBatch')}
        </Button>
        <Button size="sm" variant="ghost" disabled={!account} onClick={() => setSettingsOpen(true)}>
          {t('investments.button.feeSettings')}
        </Button>
      </div>

      {!loaded ? (
        <p className="text-sm text-muted-foreground">…</p>
      ) : open.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('investments.noHoldings')}</p>
      ) : (
        <HoldingsTable
          holdings={open}
          accountId={accountId}
          expanded={expanded}
          trades={trades}
          tradesLoading={tradesLoading}
          onToggle={toggleHolding}
          onEditTrade={(ref) => account && setTradeDialog({ account, editing: ref })}
          onDeleteTrade={(ref) => setPendingDelete(ref)}
          onQuickTrade={(h, type) =>
            account &&
            setTradeDialog(
              type === 'dca'
                ? { account, mode: 'dca', initial: { market: h.market, symbol: h.symbol, name: h.security_name } }
                : { account, initial: { market: h.market, symbol: h.symbol, name: h.security_name, type } },
            )
          }
        />
      )}

      <DcaPlanList
        rules={dcaRules}
        onManage={() => navigate(routePath({ kind: 'app', ledgerId: '', section: 'recurring-rules' }))}
      />

      {closed.length > 0 ? (
        <div>
          <button
            type="button"
            className="text-sm text-muted-foreground hover:underline"
            onClick={() => setShowClosed((v) => !v)}
          >
            {showClosed ? '▾' : '▸'} {t('investments.closed', { count: closed.length })}
          </button>
          {showClosed ? (
            <HoldingsTable
              holdings={closed}
              accountId={accountId}
              expanded={expanded}
              trades={trades}
              tradesLoading={tradesLoading}
              onToggle={toggleHolding}
              onEditTrade={(ref) => account && setTradeDialog({ account, editing: ref })}
              onDeleteTrade={(ref) => setPendingDelete(ref)}
            />
          ) : null}
        </div>
      ) : null}

      <p className="text-xs text-muted-foreground">{t('investments.detail.noAdjustHint')}</p>

      {tradeDialog ? (
        <StockTradeDialog
          state={tradeDialog}
          accounts={accounts}
          holdings={data?.holdings ?? []}
          activeLedgerId={activeLedgerId}
          onClose={() => setTradeDialog(null)}
          onSaved={async () => {
            setTradeDialog(null)
            toast.success(t('investments.notice.saved'), t('notice.success'))
            await reloadAll()
          }}
        />
      ) : null}

      {settingsOpen && account ? (
        <InvestmentSettingsDialog
          account={account}
          accounts={accounts}
          activeLedgerId={activeLedgerId}
          onClose={() => setSettingsOpen(false)}
          onSaved={async () => {
            setSettingsOpen(false)
            toast.success(t('investments.notice.settingsSaved'), t('notice.success'))
            await load()
          }}
        />
      ) : null}

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
