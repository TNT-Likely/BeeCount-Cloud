import type { ReadTimeDeposit } from '@beecount/api-client'
import { Dialog, DialogContent, DialogHeader, DialogTitle, useT } from '@beecount/ui'
import { PiggyBank } from 'lucide-react'

interface Props {
  deposit: ReadTimeDeposit | null
  /** 首次按 id 拉数据的 loading 态(弹窗已开但数据还没到)。 */
  loading?: boolean
  onClose: () => void
}

/**
 * 定期存款详情(0032)—— 流水里的利息收入点「定期存款」chip 打开。
 *
 * 只读展示:本金/利率/期限/起息到期日/状态/已收利息/到息计划。数据由
 * GlobalEntityDialogs 按 sync_id 现拉(调用方手上只有 id),加载中显示占位。
 */
export function TimeDepositDetailDialog({ deposit, loading, onClose }: Props) {
  const t = useT()
  const statusKey = deposit ? `timeDeposits.status.${deposit.status}` : ''
  return (
    <Dialog open={Boolean(deposit) || Boolean(loading)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-lg flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex flex-row items-center gap-3 border-b border-border/60 px-6 py-4">
          <DialogTitle className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-sky-500/15">
              <PiggyBank className="h-3.5 w-3.5 text-sky-600" />
            </span>
            <span className="truncate">
              {deposit?.account_name || t('accountType.time_deposit')}
            </span>
          </DialogTitle>
          {deposit ? (
            <span className="ml-auto shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
              {t(statusKey)}
            </span>
          ) : null}
        </DialogHeader>

        {deposit ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="grid grid-cols-3 gap-3 border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
              <StatCell
                label={t('timeDeposits.field.principal')}
                value={formatMoney(deposit.principal)}
              />
              <StatCell
                label={t('timeDeposits.expectedInterest')}
                value={formatMoney(deposit.expected_interest)}
              />
              <StatCell
                label={t('timeDeposits.interestReceived')}
                value={formatMoney(deposit.interest_received ?? 0)}
                tone="income"
              />
            </div>

            <div className="space-y-2.5 px-6 py-4 text-sm">
              <Row label={t('timeDeposits.field.startDate')} value={formatDate(deposit.start_date)} />
              <Row
                label={t('timeDeposits.field.maturityDate')}
                value={
                  <span className="flex items-center gap-2">
                    {formatDate(deposit.maturity_date)}
                    {deposit.status === 'holding' && deposit.days_remaining > 0 ? (
                      <span className="text-[11px] text-muted-foreground">
                        {t('timeDeposits.daysRemaining').replace(
                          '{n}',
                          String(deposit.days_remaining)
                        )}
                      </span>
                    ) : null}
                  </span>
                }
              />
              <Row
                label={t('timeDeposits.termLabel')}
                value={`${deposit.term_months} ${t('timeDeposits.months')}`}
              />
              <Row
                label={t('timeDeposits.rateLabel')}
                value={`${(deposit.annual_rate * 100).toFixed(2)}%`}
              />
              <Row
                label={t('timeDeposits.field.interestMode')}
                value={
                  deposit.interest_mode === 'compound'
                    ? t('timeDeposits.interestMode.compound')
                    : t('timeDeposits.interestMode.simple')
                }
              />
              <Row
                label={t('timeDeposits.field.account')}
                value={deposit.account_name || t('timeDeposits.noAccount')}
              />
              {deposit.auto_renew ? (
                <Row label={t('timeDeposits.field.autoRenew')} value={t('timeDeposits.autoRenew')} />
              ) : null}
              {deposit.status === 'withdrawn' ? (
                <>
                  <Row
                    label={t('timeDeposits.field.withdrawnAt')}
                    value={formatDate(deposit.withdrawn_at)}
                  />
                  <Row
                    label={t('timeDeposits.field.actualInterest')}
                    value={formatMoney(deposit.actual_interest ?? 0)}
                  />
                </>
              ) : null}
              {deposit.interest_tx_count ? (
                <Row
                  label={t('timeDeposits.interestReceived')}
                  value={`${formatMoney(deposit.interest_received ?? 0)} · ${t(
                    'timeDeposits.interestTxCount'
                  ).replace('{n}', String(deposit.interest_tx_count))}`}
                />
              ) : null}
              {deposit.note ? (
                <Row label={t('timeDeposits.field.note')} value={deposit.note} />
              ) : null}
            </div>

            {deposit.payout_plans && deposit.payout_plans.length > 0 ? (
              <div className="border-t border-border/60 px-6 py-4">
                <div className="mb-2 flex items-center justify-between text-sm font-medium">
                  <span>{t('timeDeposits.field.payoutPlans')}</span>
                  <span className="text-muted-foreground">
                    {t('timeDeposits.payoutPlans.total').replace(
                      '{total}',
                      formatMoney(deposit.payout_plans_total ?? 0)
                    )}
                  </span>
                </div>
                <div className="space-y-1.5">
                  {deposit.payout_plans.map((plan, i) => (
                    <div
                      key={`${plan.date}-${i}`}
                      className="flex items-center justify-between gap-3 text-sm"
                    >
                      <span className="font-mono tabular-nums text-muted-foreground">
                        {formatDate(plan.date)}
                      </span>
                      <span className="flex items-center gap-2">
                        {plan.note ? (
                          <span className="text-[11px] text-muted-foreground">{plan.note}</span>
                        ) : null}
                        <span className="font-mono tabular-nums">
                          {formatMoney(plan.amount)}
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="px-6 py-10 text-center text-sm text-muted-foreground">
            {t('common.loading')}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="text-right font-mono tabular-nums">{value}</span>
    </div>
  )
}

function StatCell({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'income'
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div
        className={`mt-0.5 font-mono text-base font-bold tabular-nums ${
          tone === 'income' ? 'text-income' : ''
        }`}
      >
        {value}
      </div>
    </div>
  )
}

function formatMoney(value: number): string {
  return (Number(value) || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/** 起息日/到期日:00:00 的老数据只显示日期,带时间的显示到分钟。 */
function formatDate(value: string | null | undefined): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value.slice(0, 10)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const base = `${d.getFullYear()}-${mm}-${dd}`
  const hh = d.getHours()
  const mi = d.getMinutes()
  if (!hh && !mi) return base
  return `${base} ${String(hh).padStart(2, '0')}:${String(mi).padStart(2, '0')}`
}
