import { ArrowRight, TrendingDown, TrendingUp, Users } from 'lucide-react'

import type { ReadLedger } from '@beecount/api-client'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  useT
} from '@beecount/ui'
import { Amount, formatIsoDateTime } from '@beecount/web-features'

import { useLedgers } from '../../context/LedgersContext'

interface Props {
  onSelect: (ledgerId: string) => void
}

/**
 * 账本列表 section —— 从 AppPage.tsx 抽出独立组件。
 *
 * 信息密度分三层:
 *   - 头部:首字母色块 avatar(名字哈希稳定色) + 大号账本名 + 徽章
 *   - 统计:tx 数 / 收入 / 支出 三栏(有上下标指引方向)
 *   - 底部:净值(醒目)+ 最近更新时间 + 箭头图标
 *
 * 点击整张卡片切到该账本的 overview;active ledger 有明显高亮边框。
 */
export function LedgersSection({ onSelect }: Props) {
  const t = useT()
  const { ledgers, activeLedgerId } = useLedgers()

  return (
    <div className="space-y-4">
      <Card className="bc-panel">
        <CardHeader>
          <CardTitle>{t('ledgers.title')}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-4 text-xs text-muted-foreground">
            {t('ledgers.subtitle')}
          </p>
          {ledgers.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('ledgers.empty')}</p>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {ledgers.map((ledger) => (
                <LedgerCard
                  key={ledger.ledger_id}
                  ledger={ledger}
                  isActive={activeLedgerId === ledger.ledger_id}
                  onSelect={() => onSelect(ledger.ledger_id)}
                  roleLabel={roleLabelOf(ledger.role, t)}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function roleLabelOf(role: ReadLedger['role'], t: (key: string) => string): string {
  if (role === 'owner') return t('ledgers.role.owner')
  if (role === 'editor') return t('ledgers.role.editor')
  return t('ledgers.role.viewer')
}

/** 按名字稳定哈希到 9 个主题色里的一个,让每个账本在卡片上有可辨识的 accent。 */
const ACCENT_PALETTE = [
  { bg: 'from-amber-400/20 to-amber-500/5', solid: 'bg-amber-500', text: 'text-amber-600 dark:text-amber-400' },
  { bg: 'from-sky-400/20 to-sky-500/5', solid: 'bg-sky-500', text: 'text-sky-600 dark:text-sky-400' },
  { bg: 'from-violet-400/20 to-violet-500/5', solid: 'bg-violet-500', text: 'text-violet-600 dark:text-violet-400' },
  { bg: 'from-emerald-400/20 to-emerald-500/5', solid: 'bg-emerald-500', text: 'text-emerald-600 dark:text-emerald-400' },
  { bg: 'from-rose-400/20 to-rose-500/5', solid: 'bg-rose-500', text: 'text-rose-600 dark:text-rose-400' },
  { bg: 'from-cyan-400/20 to-cyan-500/5', solid: 'bg-cyan-500', text: 'text-cyan-600 dark:text-cyan-400' },
  { bg: 'from-fuchsia-400/20 to-fuchsia-500/5', solid: 'bg-fuchsia-500', text: 'text-fuchsia-600 dark:text-fuchsia-400' },
  { bg: 'from-teal-400/20 to-teal-500/5', solid: 'bg-teal-500', text: 'text-teal-600 dark:text-teal-400' },
  { bg: 'from-indigo-400/20 to-indigo-500/5', solid: 'bg-indigo-500', text: 'text-indigo-600 dark:text-indigo-400' }
]

function accentFor(name: string) {
  let h = 0
  for (let i = 0; i < name.length; i += 1) {
    h = (h * 31 + name.charCodeAt(i)) | 0
  }
  return ACCENT_PALETTE[Math.abs(h) % ACCENT_PALETTE.length]
}

interface LedgerCardProps {
  ledger: ReadLedger
  isActive: boolean
  roleLabel: string
  onSelect: () => void
}

function LedgerCard({ ledger, isActive, roleLabel, onSelect }: LedgerCardProps) {
  const t = useT()
  const accent = accentFor(ledger.ledger_name || '?')
  const initial = (ledger.ledger_name || '?').trim().slice(0, 1).toUpperCase()

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`group relative overflow-hidden rounded-2xl border text-left transition hover:-translate-y-0.5 hover:shadow-lg ${
        isActive
          ? 'border-primary/60 shadow-md ring-1 ring-primary/20'
          : 'border-border/60'
      }`}
    >
      {/* 顶部渐变条:按名字哈希到 palette,让每个账本有颜色 signature */}
      <div className={`absolute inset-x-0 top-0 h-1 ${accent.solid}`} />

      {/* Header 区:avatar + name + badges */}
      <div
        className={`flex items-start gap-3 bg-gradient-to-br px-4 pb-3 pt-4 ${accent.bg}`}
      >
        <div
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-lg font-bold text-white shadow-sm ${accent.solid}`}
          aria-hidden
        >
          {initial}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">
                {ledger.ledger_name || '—'}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px]">
                <span className="rounded bg-background/80 px-1.5 py-0.5 font-mono text-muted-foreground">
                  {ledger.currency}
                </span>
                <span className="text-muted-foreground">·</span>
                <span className={`font-medium ${accent.text}`}>{roleLabel}</span>
                {ledger.is_shared ? (
                  <span className="inline-flex items-center gap-0.5 rounded bg-primary/15 px-1.5 py-0.5 text-primary">
                    <Users className="h-2.5 w-2.5" />
                    {ledger.member_count || 1}
                  </span>
                ) : null}
              </div>
            </div>
            <ArrowRight
              className="mt-1 h-4 w-4 shrink-0 text-muted-foreground transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-primary"
              aria-hidden
            />
          </div>
        </div>
      </div>

      {/* Stats 区:tx / income / expense 三栏 */}
      <div className="grid grid-cols-3 gap-2 border-t border-border/40 bg-card px-4 py-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('ledgers.col.tx')}
          </div>
          <div className="mt-0.5 font-mono text-sm font-semibold tabular-nums">
            {ledger.transaction_count.toLocaleString()}
          </div>
        </div>
        <div>
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            <TrendingUp className="h-2.5 w-2.5 text-income" />
            {t('ledgers.col.income')}
          </div>
          <Amount
            value={ledger.income_total}
            currency={ledger.currency}
            size="xs"
            bold
            className="mt-0.5 text-income"
          />
        </div>
        <div>
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            <TrendingDown className="h-2.5 w-2.5 text-expense" />
            {t('ledgers.col.expense')}
          </div>
          <Amount
            value={ledger.expense_total}
            currency={ledger.currency}
            size="xs"
            bold
            className="mt-0.5 text-expense"
          />
        </div>
      </div>

      {/* Footer:balance + updated */}
      <div className="flex items-end justify-between border-t border-border/40 bg-muted/20 px-4 py-2.5">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('ledgers.col.balance')}
          </div>
          <Amount
            value={ledger.balance}
            currency={ledger.currency}
            size="md"
            bold
            tone={ledger.balance < 0 ? 'negative' : 'default'}
            className="mt-0.5"
          />
        </div>
        <div className="text-right text-[10px] text-muted-foreground">
          <div>{t('ledgers.col.updatedAt')}</div>
          <div className="mt-0.5 font-mono">
            {formatIsoDateTime(ledger.updated_at)}
          </div>
        </div>
      </div>
    </button>
  )
}
