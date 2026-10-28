import type { AttachmentRef } from '@beecount/api-client'
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
import { TransactionList, BankIconBadge, accountDisplayName, creditCardAmounts, formatMonthDay, hasAlias, statementPeriod } from '@beecount/web-features'
import { Banknote, Calendar as CalendarIcon, CreditCard } from 'lucide-react'

import { useAuth } from '../../context/AuthContext'
import type { DetailScope } from '../../lib/txDialogEvents'
import { DetailScopeToggle } from './DetailScopeToggle'

type AccountWithStats = ReadAccount & {
  tx_count?: number | null
  income_total?: number | null
  expense_total?: number | null
  balance?: number | null
}

interface Props {
  account: AccountWithStats | null
  scope: DetailScope
  onScopeChange: (next: DetailScope) => void
  transactions: WorkspaceTransaction[]
  total: number
  offset: number
  loading: boolean
  tags: WorkspaceTag[]
  onClose: () => void
  onLoadMore: (accountSyncId: string, offset: number) => void
  onPreviewAttachment?: (refs: AttachmentRef[], startIndex: number) => Promise<void>
  resolveAttachmentPreviewUrl?: (att: AttachmentRef) => Promise<string | null>
}

/** 点账户卡片弹出的详情:顶部账户名 + 当前余额/累计收入/累计支出 + 交易列表(无限滚动加载)。 */
export function AccountDetailDialog({
  account,
  scope,
  onScopeChange,
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
  const { profileMe } = useAuth()
  const noteDisplayMode = profileMe?.appearance?.note_display_mode ?? 'category'
  return (
    <Dialog open={Boolean(account)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex flex-row items-center justify-between gap-3 border-b border-border/60 px-6 py-4">
          <div className="min-w-0">
            <DialogTitle className="truncate">
              {account ? accountDisplayName(account) : ''}
            </DialogTitle>
            {/* 0027:设了别名时补一行原名,避免"这到底是哪张卡"的困惑 */}
            {account && hasAlias(account) ? (
              <p className="truncate text-xs text-muted-foreground" title={account.name}>
                {account.name}
              </p>
            ) : null}
          </div>
          <DetailScopeToggle value={scope} onChange={onScopeChange} className="shrink-0" />
        </DialogHeader>
        {account ? (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* 统计:优先 server 返回的 balance/income/expense,缺失时兜底 initial_balance.
                注意:这里的统计来自 props 上的 account 实体,本身是按上层 scope
                取的(GlobalEntityDialogs / AccountsPage 通过 fetchWorkspaceAccounts
                的 ledgerId 参数控制)。弹窗顶部 scope 切换只影响交易列表过滤,
                上面这块 KPI 沿用打开时的快照,不跟随 scope 实时切换 —
                跟 mobile 端 account_detail_page 行为一致。 */}
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
                  if (!loading) onLoadMore(account.id, offset)
                }}
                onPreviewAttachment={onPreviewAttachment as never}
                resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl as never}
                emptyTitle={t('transactions.empty.forAccount.title')}
                showLedger={scope === 'all'}
                noteDisplayMode={noteDisplayMode}
                currentAccountId={account.id}
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

  // 信用卡按负债展示:只显当前欠款(= -balance,余额为负=欠款),不显示
  // 累计收入/支出(信用卡入账是还款/退款,非收入);额度/可用/账单在下方
  // AccountCardInfo。对齐 mobile account_detail_page。
  if ((account.account_type || '') === 'credit_card') {
    const amounts = creditCardAmounts(balance)
    return (
      <div className="border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t(amounts.overpayment > 0 ? 'accounts.credit.overpayment' : 'accounts.bankcard.currentOwed')}
        </div>
        <div className="mt-0.5 font-mono text-lg font-bold tabular-nums text-expense">
          {fmt(amounts.overpayment > 0 ? amounts.overpayment : amounts.debt)} {account.currency}
        </div>
      </div>
    )
  }

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
  const cardNumber = account.card_number?.trim() || ''
  // 完整卡号脱敏展示:保留前 4 后 4,中间打码(16 位 → **** **** **** 1234)。
  const maskedCard = cardNumber
    ? (() => {
        const digits = cardNumber.replace(/\D/g, '')
        if (digits.length <= 8) return cardNumber
        const head = digits.slice(0, 4)
        const tail = digits.slice(-4)
        const mid = '*'.repeat(Math.max(digits.length - 8, 0))
        return `${head} ${mid} ${tail}`
      })()
    : ''
  const creditLimit = isCreditCard ? account.credit_limit : null
  const billingDay = isCreditCard ? account.billing_day : null
  const paymentDueDay = isCreditCard ? account.payment_due_day : null

  // 信用卡已用额度 = -balance(余额为负表示欠款),剩余额度 = limit - used。
  // 这是粗略估算 — 没考虑账单周期,只看终身累计。但作为"大致还能刷多少"
  // 的信号已经够用,后续要精确版本应该按 billing_day 分账期算。
  //
  // 为什么用 balance 而不是 expense_total - income_total:后者会漏掉"储蓄卡
  // 转账到信用卡还款"这种 transfer-in 操作 —— 转账既不是 expense 也不是
  // income,只在 backend 的 balance 计算里被加回去(workspace.py 的
  // transfer_to bucket)。balance 已经统一包含初始余额 + 全部 income /
  // expense / transfer-in / transfer-out,跟 mobile 端
  // `getCreditCardUsedAmount` (balance < 0 ? -balance : 0) 完全一致。
  // 修复 issue #26:储蓄卡转账到信用卡额度没恢复。
  const balance = account.balance ?? account.initial_balance ?? 0
  const remaining = creditCardAmounts(balance, creditLimit).estimatedAvailable

  // 没有任何要展示的就直接不渲染(用户没填这些字段时)
  if (
    !bankName &&
    !cardLastFour &&
    !cardNumber &&
    creditLimit === null &&
    !billingDay &&
    !paymentDueDay
  ) {
    return null
  }

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  // 0027:本期账单区间 —— 账单日 + 「出账日计入当期」推导。账单日没填时为 null。
  const period = isCreditCard
    ? statementPeriod(billingDay, paymentDueDay, account.billing_day_in_current)
    : null
  const periodText = period
    ? `${formatMonthDay(period.start)} ~ ${formatMonthDay(period.end)}`
    : ''

  return (
    <div className="border-b border-border/60 bg-muted/10 px-6 py-3">
      {/* 第一行:银行图标 + 银行 + 完整卡号(脱敏)/ 卡号末 4 位 */}
      {(bankName || cardLastFour || cardNumber) ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {account.icon ? (
            <BankIconBadge icon={account.icon} size={20} radius={6} />
          ) : isCreditCard ? (
            <CreditCard className="h-4 w-4 text-muted-foreground" />
          ) : (
            <Banknote className="h-4 w-4 text-muted-foreground" />
          )}
          <span className="font-medium">{bankName || t('detail.account.bankUnknown')}</span>
          {maskedCard ? (
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
              {maskedCard}
            </span>
          ) : cardLastFour ? (
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
                  ? t('accounts.credit.estimateHint') + ' ' + t('detail.account.remaining', { value: fmt(remaining) })
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
          {/* 0027:本期账单区间(由账单日 + 出账日计入当期推导) */}
          {period ? (
            <CardInfoItem
              label={t('detail.account.statementPeriod')}
              value={periodText}
              hint={
                period.daysToDue !== null && paymentDueDay
                  ? t('detail.account.daysUntil', { days: period.daysToDue })
                  : t(
                      account.billing_day_in_current === false
                        ? 'accounts.credit.billingInNext'
                        : 'accounts.credit.billingInCurrent'
                    )
              }
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
