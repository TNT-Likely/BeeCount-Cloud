import { useCallback, useEffect, useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import {
  fetchMemberStats,
  resolveApiUrl,
  type MemberStatItem,
  type MemberStatScope,
  type MemberStatsResponse,
} from '@beecount/api-client'
import { Amount } from '@beecount/web-features'
import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
  useToast,
} from '@beecount/ui'
import { localizeError } from '../i18n/errors'

import { useAuth } from '../context/AuthContext'

interface Props {
  open: boolean
  onOpenChange: (next: boolean) => void
  ledgerId: string
  ledgerName: string
}

// 成员配色 — 用稳定的 hash → palette index 保证同一 user_id 跨 chart 颜色一致。
const MEMBER_PALETTE = [
  '#3b82f6', // blue
  '#10b981', // emerald
  '#f59e0b', // amber
  '#ef4444', // red
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#14b8a6', // teal
  '#f97316', // orange
  '#06b6d4', // cyan
  '#84cc16', // lime
]
function colorForUserId(userId: string): string {
  let h = 0
  for (let i = 0; i < userId.length; i += 1) h = (h * 31 + userId.charCodeAt(i)) | 0
  return MEMBER_PALETTE[Math.abs(h) % MEMBER_PALETTE.length]
}

/**
 * 共享账本成员收支统计 — 图表版。仅在 LedgerCard 上独立入口触发,跟"成员/邀请"
 * 对话框解耦。
 *
 * 内容:
 *  - ScopeSwitcher(本月 / 本年 / 全部)
 *  - 三个 KPI(总收入 / 总支出 / 总笔数)
 *  - 横向柱图:每个成员的收入(绿)/ 支出(红)
 *  - 饼图:支出占比
 *  - 详细列表:头像 + 名字 + 笔数 + 收入 + 支出
 */
export function SharedLedgerStatsDialog({
  open,
  onOpenChange,
  ledgerId,
  ledgerName,
}: Props) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()

  const [data, setData] = useState<MemberStatsResponse | null>(null)
  const [scope, setScope] = useState<MemberStatScope>('month')
  const [loading, setLoading] = useState(false)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  const load = useCallback(
    async (nextScope: MemberStatScope) => {
      if (!token || !ledgerId) return
      setLoading(true)
      try {
        const result = await fetchMemberStats(token, ledgerId, {
          scope: nextScope,
          tzOffsetMinutes: -new Date().getTimezoneOffset(),
        })
        setData(result)
      } catch (err) {
        notifyError(err)
        setData(null)
      } finally {
        setLoading(false)
      }
    },
    [token, ledgerId, notifyError],
  )

  // 仅依赖原始值,避免 toast / t 不稳定引用引发的抖动(SharedLedgerManageDialog
  // 已踩过这个坑)。
  useEffect(() => {
    if (open) void load(scope)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, token, ledgerId, scope])

  const items: MemberStatItem[] = data?.items ?? []
  const currency = data?.ledger_currency || 'CNY'

  const summary = useMemo(() => {
    let income = 0
    let expense = 0
    let tx = 0
    for (const it of items) {
      income += it.income_total
      expense += it.expense_total
      tx += it.tx_count
    }
    return { income, expense, tx }
  }, [items])

  // 柱图数据:按支出降序,每个成员一行;income & expense 两根柱并排。
  // recharts 用 Bar 默认 vertical(竖向),用 layout="vertical" 切横向更适合
  // 成员名(可能长)展示。
  const barData = useMemo(
    () =>
      items
        .map((it) => ({
          userId: it.user_id,
          name: nameOf(it),
          income: it.income_total,
          expense: it.expense_total,
          color: colorForUserId(it.user_id),
        }))
        .sort((a, b) => b.expense - a.expense),
    [items],
  )

  const pieData = useMemo(() => {
    const withExpense = items.filter((it) => it.expense_total > 0)
    return withExpense.map((it) => ({
      userId: it.user_id,
      name: nameOf(it),
      value: it.expense_total,
      color: colorForUserId(it.user_id),
    }))
  }, [items])

  const showFirstSpinner = loading && !data

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span>📊 {ledgerName} · {t('sharedLedger.statsTitle')}</span>
            {loading && data ? (
              <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
            ) : null}
          </DialogTitle>
        </DialogHeader>

        {/* Scope switcher */}
        <div className="flex justify-end">
          <div className="inline-flex overflow-hidden rounded-md border border-border/60 text-xs">
            {(['month', 'year', 'all'] as MemberStatScope[]).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setScope(s)}
                className={`px-3 py-1 transition ${
                  s === scope
                    ? 'bg-primary/15 font-medium text-primary'
                    : 'text-muted-foreground hover:bg-muted/50'
                }`}
              >
                {t(`sharedLedger.statsScope.${s}`)}
              </button>
            ))}
          </div>
        </div>

        {showFirstSpinner ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">
            {t('sharedLedger.statsEmpty')}
          </p>
        ) : (
          <div className="space-y-4">
            {/* KPI row */}
            <div className="grid grid-cols-3 gap-3">
              <KpiCard
                label={t('sharedLedger.statsTotalIncome')}
                value={summary.income}
                currency={currency}
                tone="positive"
              />
              <KpiCard
                label={t('sharedLedger.statsTotalExpense')}
                value={summary.expense}
                currency={currency}
                tone="negative"
              />
              <KpiCardSimple
                label={t('sharedLedger.statsTotalTx')}
                value={String(summary.tx)}
              />
            </div>

            {/* Charts */}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <ChartCard title={t('sharedLedger.statsBarChartTitle')}>
                <ResponsiveContainer width="100%" height={Math.max(180, barData.length * 36 + 40)}>
                  <BarChart
                    data={barData}
                    layout="vertical"
                    margin={{ top: 8, right: 16, bottom: 4, left: 4 }}
                  >
                    <CartesianGrid horizontal={false} strokeDasharray="3 3" stroke="#94a3b8" strokeOpacity={0.18} />
                    <XAxis type="number" tick={{ fontSize: 11 }} />
                    <YAxis
                      type="category"
                      dataKey="name"
                      tick={{ fontSize: 11 }}
                      width={80}
                    />
                    <Tooltip
                      formatter={(value, key) => {
                        const v = typeof value === 'number' ? value : Number(value) || 0
                        const k = String(key)
                        return [
                          formatCurrency(v, currency),
                          k === 'income'
                            ? t('sharedLedger.statsIncome')
                            : t('sharedLedger.statsExpense'),
                        ]
                      }}
                      labelStyle={{ fontSize: 11 }}
                      contentStyle={{ fontSize: 12 }}
                    />
                    <Legend
                      wrapperStyle={{ fontSize: 11 }}
                      formatter={(v: string) =>
                        v === 'income'
                          ? t('sharedLedger.statsIncome')
                          : t('sharedLedger.statsExpense')
                      }
                    />
                    <Bar dataKey="income" fill="#10b981" radius={[0, 3, 3, 0]} />
                    <Bar dataKey="expense" fill="#ef4444" radius={[0, 3, 3, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard title={t('sharedLedger.statsPieChartTitle')}>
                {pieData.length === 0 ? (
                  <p className="flex h-[220px] items-center justify-center text-xs text-muted-foreground">
                    {t('sharedLedger.statsNoExpenseChart')}
                  </p>
                ) : (
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie
                        data={pieData}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={48}
                        outerRadius={80}
                        paddingAngle={2}
                      >
                        {pieData.map((entry) => (
                          <Cell key={entry.userId} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(value, _name, item) => {
                          const v = typeof value === 'number' ? value : Number(value) || 0
                          const name =
                            (item as { payload?: { name?: string } })?.payload?.name ?? ''
                          return [formatCurrency(v, currency), name]
                        }}
                        labelStyle={{ fontSize: 11 }}
                        contentStyle={{ fontSize: 12 }}
                      />
                      <Legend
                        wrapperStyle={{ fontSize: 11 }}
                        formatter={(v: string) => v}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                )}
              </ChartCard>
            </div>

            {/* Detail list */}
            <div>
              <h3 className="mb-2 text-sm font-semibold">
                {t('sharedLedger.members')}
              </h3>
              <div className="space-y-1">
                {items.map((s) => {
                  const avatarUrl = resolveApiUrl(s.avatar_url)
                  const name = nameOf(s)
                  const sharePct =
                    summary.expense > 0
                      ? (s.expense_total / summary.expense) * 100
                      : 0
                  return (
                    <div
                      key={s.user_id}
                      className="flex items-center gap-3 rounded border border-border/40 bg-background/40 px-3 py-2"
                    >
                      <span
                        className="block h-2 w-2 shrink-0 rounded-full"
                        style={{ background: colorForUserId(s.user_id) }}
                      />
                      {avatarUrl ? (
                        <img
                          src={avatarUrl}
                          alt={name}
                          className="h-7 w-7 rounded-full object-cover"
                        />
                      ) : (
                        <span className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-primary/20 text-xs font-semibold text-primary">
                          {(name[0] || '?').toUpperCase()}
                        </span>
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">
                          {name}
                        </div>
                        <div className="text-[10px] text-muted-foreground">
                          {s.tx_count} {t('sharedLedger.statsTxCount')}
                          {summary.expense > 0 ? (
                            <span className="ml-2">
                              {t('sharedLedger.statsShare')}{' '}
                              {sharePct.toFixed(1)}%
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-0.5 text-xs">
                        <div className="flex items-center gap-1">
                          <span className="text-muted-foreground">
                            {t('sharedLedger.statsIncome')}
                          </span>
                          <Amount
                            value={s.income_total}
                            currency={currency}
                            size="sm"
                            tone="positive"
                            bold
                          />
                        </div>
                        <div className="flex items-center gap-1">
                          <span className="text-muted-foreground">
                            {t('sharedLedger.statsExpense')}
                          </span>
                          <Amount
                            value={s.expense_total}
                            currency={currency}
                            size="sm"
                            tone="negative"
                            bold
                          />
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.close')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function nameOf(it: MemberStatItem): string {
  if (it.display_name && it.display_name.trim().length > 0) return it.display_name
  if (it.email && it.email.length > 0) return it.email.split('@')[0]
  return it.user_id.slice(0, 6)
}

function formatCurrency(value: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    }).format(value)
  } catch {
    return `${currency} ${value.toFixed(2)}`
  }
}

function KpiCard({
  label,
  value,
  currency,
  tone,
}: {
  label: string
  value: number
  currency: string
  tone: 'positive' | 'negative'
}) {
  return (
    <div className="rounded border border-border/40 bg-background/40 p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-1">
        <Amount value={value} currency={currency} size="lg" tone={tone} bold />
      </div>
    </div>
  )
}

function KpiCardSimple({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border/40 bg-background/40 p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold tabular-nums">
        {value}
      </div>
    </div>
  )
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-border/40 bg-background/40 p-3">
      <div className="mb-2 text-xs font-medium text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  )
}
