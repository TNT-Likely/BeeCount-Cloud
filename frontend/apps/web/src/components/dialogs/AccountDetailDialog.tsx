import type {
  ReadAccount,
  WorkspaceTag,
  WorkspaceTransaction
} from '@beecount/api-client'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  useT
} from '@beecount/ui'
import { TransactionList } from '@beecount/web-features'
import { Banknote, Calendar as CalendarIcon, CreditCard } from 'lucide-react'

type AccountWithStats = ReadAccount & {
  tx_count?: number | null
  income_total?: number | null
  expense_total?: number | null
  balance?: number | null
}

interface Props {
  account: AccountWithStats | null
  transactions: WorkspaceTransaction[]
  total: number
  offset: number
  loading: boolean
  tags: WorkspaceTag[]
  onClose: () => void
  onLoadMore: (accountName: string, offset: number) => void
  onPreviewAttachment?: (ctx: unknown) => void
  resolveAttachmentPreviewUrl?: (att: unknown) => string | null
}

/** 点账户卡片弹出的详情:顶部账户名 + 当前余额/累计收入/累计支出 + 交易列表(无限滚动加载)。 */
export function AccountDetailDialog({
  account,
  transactions,
  total,
  offset,
  loading,
  tags,
  onClose,
  onLoadMore,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
}: Props) {
  const t = useT()
  return (
    <Dialog open={Boolean(account)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="truncate">{account?.name || ''}</DialogTitle>
        </DialogHeader>
        {account ? (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* 统计:优先 server 返回的 balance/income/expense,缺失时兜底 initial_balance */}
            <AccountStatsHeader account={account} t={t} />

            {/* 信用卡 / 银行卡专属信息:bank_name / 卡号末 4 / 信用额度 /
                账单日 / 还款日 + 倒计时。普通账户类型不渲染。 */}
            <AccountCardInfo account={account} t={t} />

            <div className="min-h-0 flex-1 overflow-y-auto">
              <TransactionList
                items={transactions}
                tags={tags}
                variant="compact"
                loading={loading}
                hasMore={transactions.length < total}
                onLoadMore={() => {
                  if (!loading) onLoadMore(account.name, offset)
                }}
                onPreviewAttachment={onPreviewAttachment as never}
                resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl as never}
                emptyTitle={t('transactions.empty.forAccount.title')}
              />
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function AccountStatsHeader({
  account,
  t,
}: {
  account: AccountWithStats
  t: (key: string) => string
}) {
  const hasServerStats = typeof account.balance === 'number'
  const balance = hasServerStats ? account.balance! : account.initial_balance ?? 0
  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return (
    <div className="grid grid-cols-3 gap-3 border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.currentBalance')}
        </div>
        <div
          className={`mt-0.5 font-mono text-base font-bold tabular-nums ${
            balance >= 0 ? 'text-foreground' : 'text-expense'
          }`}
        >
          {fmt(balance)}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.accumIncome')}
        </div>
        <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-income">
          {fmt(account.income_total ?? 0)}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.accumExpense')}
        </div>
        <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-expense">
          {fmt(account.expense_total ?? 0)}
        </div>
      </div>
    </div>
  )
}

/**
 * 信用卡 / 银行卡专属信息卡片。
 *
 * 展示规则:
 *   - bank_card: 银行 + 卡号末 4 位(2 项中只要任一有值就显示)
 *   - credit_card: 上面 2 项 + 信用额度 + 账单日 + 还款日 + 倒计时
 *   - 其它账户类型(cash / alipay / etc):整块不渲染
 *
 * 倒计时算法:今天日号 ≤ 目标日号 → 目标日号 - 今天日号;否则 → 跨月,
 * (本月剩余天数) + 目标日号。"今天"取本地时区,因为还款日是法律日历日,
 * 不需要 UTC。
 */
function AccountCardInfo({
  account,
  t,
}: {
  account: AccountWithStats
  t: (key: string, params?: Record<string, string | number>) => string
}) {
  const accountType = account.account_type || ''
  const isCreditCard = accountType === 'credit_card'
  const isBankOrCredit = isCreditCard || accountType === 'bank_card'
  if (!isBankOrCredit) return null

  const bankName = account.bank_name?.trim() || ''
  const cardLastFour = account.card_last_four?.trim() || ''
  const creditLimit = isCreditCard ? account.credit_limit : null
  const billingDay = isCreditCard ? account.billing_day : null
  const paymentDueDay = isCreditCard ? account.payment_due_day : null

  // 信用卡已用额度 = 累计支出 - 累计收入(还款)。剩余额度 = limit - used。
  // 这是粗略估算 — 没考虑账单周期,只看终身累计。但作为"大致还能刷多少"
  // 的信号已经够用,后续要精确版本应该按 billing_day 分账期算。
  const used =
    typeof creditLimit === 'number'
      ? Math.max(0, (account.expense_total ?? 0) - (account.income_total ?? 0))
      : null
  const remaining =
    typeof creditLimit === 'number' && used !== null
      ? Math.max(0, creditLimit - used)
      : null

  // 没有任何要展示的就直接不渲染(用户没填这些字段时)
  if (
    !bankName &&
    !cardLastFour &&
    creditLimit === null &&
    !billingDay &&
    !paymentDueDay
  ) {
    return null
  }

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  return (
    <div className="border-b border-border/60 bg-muted/10 px-6 py-3">
      {/* 第一行:银行 + 卡号末 4 位 + 类型 icon */}
      {(bankName || cardLastFour) ? (
        <div className="flex items-center gap-2 text-sm">
          {isCreditCard ? (
            <CreditCard className="h-4 w-4 text-muted-foreground" />
          ) : (
            <Banknote className="h-4 w-4 text-muted-foreground" />
          )}
          <span className="font-medium">{bankName || t('detail.account.bankUnknown')}</span>
          {cardLastFour ? (
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
              •••• {cardLastFour}
            </span>
          ) : null}
        </div>
      ) : null}

      {/* 信用卡:额度 + 账单日 + 还款日 + 倒计时 */}
      {isCreditCard ? (
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
          {creditLimit !== null && creditLimit !== undefined ? (
            <CardInfoItem
              label={t('detail.account.creditLimit')}
              value={fmt(creditLimit)}
              hint={
                remaining !== null
                  ? t('detail.account.remaining', { value: fmt(remaining) })
                  : undefined
              }
            />
          ) : null}
          {billingDay ? (
            <CardInfoItem
              label={t('detail.account.billingDay')}
              value={t('detail.account.dayOfMonth', { day: billingDay })}
              hint={t('detail.account.daysUntil', { days: daysUntilDay(billingDay) })}
            />
          ) : null}
          {paymentDueDay ? (
            <CardInfoItem
              label={t('detail.account.paymentDueDay')}
              value={t('detail.account.dayOfMonth', { day: paymentDueDay })}
              hint={t('detail.account.daysUntil', { days: daysUntilDay(paymentDueDay) })}
              urgent={daysUntilDay(paymentDueDay) <= 3}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function CardInfoItem({
  label,
  value,
  hint,
  urgent,
}: {
  label: string
  value: string
  hint?: string
  urgent?: boolean
}) {
  return (
    <div>
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        <CalendarIcon className="h-3 w-3" />
        <span>{label}</span>
      </div>
      <div className="mt-0.5 text-sm font-medium tabular-nums">{value}</div>
      {hint ? (
        <div
          className={`text-[10px] tabular-nums ${
            urgent ? 'text-expense font-semibold' : 'text-muted-foreground'
          }`}
        >
          {hint}
        </div>
      ) : null}
    </div>
  )
}

/**
 * 今天到目标日号还有多少天。
 *   - 今天 ≤ 目标 → 本月内,直接差值
 *   - 今天 > 目标 → 跨月,本月剩余 + 目标日号
 *
 * 目标日号超过当月最大天数(比如 31 号但 2 月)按当月最后一天兜底,跟
 * mobile 端 AccountDetailPage 算法对齐。
 */
function daysUntilDay(targetDay: number): number {
  if (!Number.isFinite(targetDay) || targetDay < 1 || targetDay > 31) return 0
  const now = new Date()
  const today = now.getDate()
  if (today <= targetDay) {
    // 同月内 — 验证当月有这一天(2 月没有 31 号)
    const lastDayThisMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()
    const effective = Math.min(targetDay, lastDayThisMonth)
    return effective - today
  }
  // 跨月 — 算本月剩余 + 下月目标日(下月可能也没那一天)
  const lastDayThisMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()
  const lastDayNextMonth = new Date(now.getFullYear(), now.getMonth() + 2, 0).getDate()
  const effective = Math.min(targetDay, lastDayNextMonth)
  return (lastDayThisMonth - today) + effective
}
