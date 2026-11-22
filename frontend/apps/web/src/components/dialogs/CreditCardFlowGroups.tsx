import { useEffect, useMemo, useRef, useState } from 'react'

import type {
  AttachmentRef,
  WorkspaceTag,
  WorkspaceTransaction,
} from '@beecount/api-client'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
  useT,
} from '@beecount/ui'
import { TransactionList } from '@beecount/web-features'
import { CalendarDays } from 'lucide-react'

type ViewMode = 'month' | 'cycle'

type CardAccountForGroups = {
  id: string
  name: string
  billing_day?: unknown
  currency?: string | null
}

interface FlowGroup {
  key: string
  sortKey: string
  label: string
  count: number
  expense: number
  income: number
  balance: number
  items: WorkspaceTransaction[]
}

interface Props {
  account: CardAccountForGroups
  transactions: WorkspaceTransaction[]
  tags: WorkspaceTag[]
  currency: string
  noteDisplayMode: 'category' | 'note'
  showLedger: boolean
  /** 是否允许「按账期」切换。信用卡 true;普通账户 false(只按月)。 */
  allowCycle?: boolean
  onSelect?: (tx: WorkspaceTransaction) => void
  onPreviewAttachment?: (refs: AttachmentRef[], startIndex: number) => Promise<void>
  resolveAttachmentPreviewUrl?: (ref: AttachmentRef) => Promise<string | null>
}

const pad = (n: number) => String(n).padStart(2, '0')

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

function parseDate(value: string): { y: number; m: number; d: number } | null {
  const match = value?.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!match) return null
  return { y: Number(match[1]), m: Number(match[2]), d: Number(match[3]) }
}

function shiftMonth(year: number, month: number, delta: number) {
  const total = year * 12 + (month - 1) + delta
  return { y: Math.floor(total / 12), m: (total % 12) + 1 }
}

/** 账单日可能是数字(1-31)或 ISO 字符串,统一提取"日号"。 */
function extractBillingDay(value: unknown): number | null {
  if (value == null || value === '') return null
  if (typeof value === 'number' && Number.isFinite(value)) {
    const d = Math.trunc(value)
    return d >= 1 && d <= 31 ? d : null
  }
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (/^\d+$/.test(trimmed)) {
      const d = Number(trimmed)
      return d >= 1 && d <= 31 ? d : null
    }
    // 兼容历史脏数据:旧版把日号当 Unix 秒数写入 => 1970-01-01T00:00:20。
    const legacy = trimmed.match(/^1970-01-01T00:00:(\d{1,2})/)
    if (legacy) {
      const legacyDay = Number(legacy[1])
      if (legacyDay >= 1 && legacyDay <= 31) return legacyDay
    }
    const match = trimmed.match(/^\d{4}-\d{2}-(\d{2})/)
    if (match) {
      const d = Number(match[1])
      return d >= 1 && d <= 31 ? d : null
    }
  }
  return null
}

/** 从当前账户视角判断一笔流水是"支出"还是"收入"(信用卡:消费=支出,还款/退款=收入)。 */
function flowDirection(
  tx: WorkspaceTransaction,
  account: CardAccountForGroups,
): 'expense' | 'income' {
  if (tx.tx_type === 'income' || tx.tx_type === 'refund') return 'income'
  if (tx.tx_type === 'expense') return 'expense'
  // transfer:转入该卡=还款(收入),从该卡转出=支出
  const toCard =
    (account.id && tx.to_account_id === account.id) ||
    Boolean(tx.to_account_name && tx.to_account_name === account.name)
  const fromCard =
    (account.id && tx.from_account_id === account.id) ||
    Boolean(tx.from_account_name && tx.from_account_name === account.name)
  if (toCard && !fromCard) return 'income'
  return 'expense'
}

/**
 * 信用卡详情弹窗的流水分组视图。
 *
 * - 支持「按月 / 按账期」切换,各自汇总每一组的支出/收入/结余;
 * - 账期以账单日 billing_day 划分:账单日当天及之前的消费计入本月账单,
 *   账单日之后的消费计入下月账单;未设置账单日则退化为自然月;
 * - 每组可折叠,默认展开最近一组(切换方式后同样展开新的最近一组);
 * - 组内流水行点击跳原交易详情。
 */
export function CreditCardFlowGroups({
  account,
  transactions,
  tags,
  currency,
  noteDisplayMode,
  showLedger,
  allowCycle = true,
  onSelect,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
}: Props) {
  const t = useT()
  const [viewMode, setViewMode] = useState<ViewMode>('month')
  const [openGroup, setOpenGroup] = useState<string>('')
  const billingDay = extractBillingDay(account.billing_day)

  const groups = useMemo<FlowGroup[]>(() => {
    const bucket = new Map<string, { sortKey: string; label: string; items: WorkspaceTransaction[] }>()

    for (const tx of transactions) {
      const parsed = parseDate(tx.happened_at)
      if (!parsed) continue
      let groupKey: string
      let sortKey: string
      let label: string

      if (viewMode === 'month' || !billingDay) {
        groupKey = `${parsed.y}-${pad(parsed.m)}`
        sortKey = groupKey
        label = t('accounts.card.monthRange', { year: String(parsed.y), month: pad(parsed.m) })
      } else {
        // 账单日当天及之前 -> 本月账单;之后 -> 下月账单
        const cycle = parsed.d <= billingDay
          ? { y: parsed.y, m: parsed.m }
          : shiftMonth(parsed.y, parsed.m, 1)
        const prev = shiftMonth(cycle.y, cycle.m, -1)
        const startDay = billingDay + 1 <= daysInMonth(prev.y, prev.m) ? billingDay + 1 : 1
        const endDay = Math.min(billingDay, daysInMonth(cycle.y, cycle.m))
        groupKey = `c-${cycle.y}-${pad(cycle.m)}`
        sortKey = `${cycle.y}-${pad(cycle.m)}`
        label = t('accounts.card.cycleRange', {
          year: String(cycle.y),
          start: `${pad(prev.m)}-${pad(startDay)}`,
          end: `${pad(cycle.m)}-${pad(endDay)}`,
        })
      }

      let entry = bucket.get(groupKey)
      if (!entry) {
        entry = { sortKey, label, items: [] }
        bucket.set(groupKey, entry)
      }
      entry.items.push(tx)
    }

    return Array.from(bucket.entries())
      .map(([key, entry]): FlowGroup => {
        let expense = 0
        let income = 0
        const items = [...entry.items].sort(
          (a, b) => (b.happened_at || '').localeCompare(a.happened_at || ''),
        )
        for (const tx of items) {
          const amount = Number(tx.native_amount ?? tx.amount ?? 0)
          if (flowDirection(tx, account) === 'income') income += amount
          else expense += amount
        }
        return {
          key,
          sortKey: entry.sortKey,
          label: entry.label,
          count: items.length,
          expense,
          income,
          balance: income - expense,
          items,
        }
      })
      .sort((a, b) => b.sortKey.localeCompare(a.sortKey))
  }, [transactions, viewMode, billingDay, account, t])

  // 数据到达或切换查看方式后,自动展开最近一组。用 signature 判断"分组结构变化",
  // 避免用户手动折叠后又被引擎强制展开。
  const signature = `${viewMode}|${groups[0]?.key ?? ''}`
  const lastSignatureRef = useRef('')
  useEffect(() => {
    if (groups.length > 0 && signature !== lastSignatureRef.current) {
      lastSignatureRef.current = signature
      setOpenGroup(groups[0].key)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, groups.length])

  const fmt = (v: number) =>
    `${currency} ${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

  const modeLabel = (mode: ViewMode) =>
    t(mode === 'month' ? 'accounts.card.byMonth' : 'accounts.card.byCycle')

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* 查看方式切换 + 账期口径提示(非信用卡只允许按月,隐藏切换) */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 px-6 py-2.5">
        <div className="flex items-center gap-2">
          {allowCycle ? (
            <>
              <span className="text-xs text-muted-foreground">{t('accounts.card.viewMode')}</span>
              <div
                role="tablist"
                aria-label={t('accounts.card.viewMode')}
                className="inline-flex rounded-md border border-border/60 bg-muted/30 p-0.5 text-xs"
              >
                {(['month', 'cycle'] as ViewMode[]).map((mode) => {
                  const active = mode === viewMode
                  return (
                    <button
                      key={mode}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      onClick={() => setViewMode(mode)}
                      className={`rounded px-2.5 py-1 transition-colors ${
                        active
                          ? 'bg-background font-semibold text-foreground shadow-sm'
                          : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {modeLabel(mode)}
                    </button>
                  )
                })}
              </div>
            </>
          ) : (
            <span className="text-xs text-muted-foreground">{t('accounts.card.byMonth')}</span>
          )}
        </div>
        {allowCycle ? (
          <span className="text-[11px] text-muted-foreground">
            {viewMode === 'cycle' && !billingDay
              ? t('accounts.card.noBillingDay')
              : viewMode === 'cycle' && billingDay
                ? t('accounts.card.cycleHint', { day: String(billingDay) })
                : null}
          </span>
        ) : null}
      </div>

      {/* 分组折叠列表 */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {groups.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted-foreground">
            {t('transactions.empty.forAccount.title')}
          </div>
        ) : (
          <Accordion
            type="single"
            collapsible
            value={openGroup}
            onValueChange={setOpenGroup}
            className="w-full"
          >
            {groups.map((group) => (
              <AccordionItem key={group.key} value={group.key} className="rounded-lg border border-border/60 bg-card px-3 mb-2 last:mb-0">
                <AccordionTrigger className="py-3 hover:no-underline" title={t('accounts.card.expandTip')}>
                  <div className="flex w-full min-w-0 flex-col gap-2 pr-2">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2">
                        <CalendarDays className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <span className="truncate text-sm font-semibold">{group.label}</span>
                        <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[11px] font-normal text-muted-foreground">
                          {t('accounts.card.txCount', { count: String(group.count) })}
                        </span>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                      <span className="text-muted-foreground">
                        {t('accounts.card.expense')}{' '}
                        <span className="font-mono font-medium tabular-nums text-expense">{fmt(group.expense)}</span>
                      </span>
                      <span className="text-muted-foreground">
                        {t('accounts.card.income')}{' '}
                        <span className="font-mono font-medium tabular-nums text-income">{fmt(group.income)}</span>
                      </span>
                      <span className="text-muted-foreground">
                        {t('accounts.card.balance')}{' '}
                        <span
                          className={`font-mono font-semibold tabular-nums ${
                            group.balance >= 0 ? 'text-income' : 'text-expense'
                          }`}
                        >
                          {fmt(group.balance)}
                        </span>
                      </span>
                    </div>
                  </div>
                </AccordionTrigger>
                <AccordionContent className="pb-2">
                  <div className="rounded-md border border-border/40 bg-muted/10">
                    <TransactionList
                      items={group.items}
                      tags={tags}
                      variant="compact"
                      onSelect={onSelect as never}
                      onPreviewAttachment={onPreviewAttachment as never}
                      resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl as never}
                      showLedger={showLedger}
                      noteDisplayMode={noteDisplayMode}
                      currentAccountId={account.id}
                    />
                  </div>
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        )}
      </div>
    </div>
  )
}
