import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

import { Card, CardContent, useT } from '@beecount/ui'
import type { ImportStats } from '@beecount/api-client'

interface Props {
  stats: ImportStats
}

/**
 * 预览统计卡 —— 顶部 3 个数字 + 「将创建/合并」分四组 + 告警折叠区。
 * 设计:.docs/web-ledger-import.md §2.6
 */
export function ImportStatsCard({ stats }: Props) {
  const t = useT()
  const dateRange =
    stats.time_range_start && stats.time_range_end
      ? `${formatDate(stats.time_range_start)} ~ ${formatDate(stats.time_range_end)}`
      : t('common.dash')
  const totalAmount = formatAmount(stats.total_signed_amount)

  return (
    <Card className="bc-panel">
      <CardContent className="space-y-4 p-5">
        {/* 顶部 3 数字 */}
        <div className="grid gap-2 sm:grid-cols-3">
          <Stat label={t('import.stats.totalRows')} value={`${stats.total_rows}`} />
          <Stat label={t('import.stats.timeRange')} value={dateRange} />
          <Stat label={t('import.stats.totalSigned')} value={totalAmount} />
        </div>

        {/* 「将创建/合并」分组 */}
        <div className="rounded-lg border border-border/50 bg-muted/10 px-4 py-3">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('import.stats.willCreate.title')}
          </p>
          <div className="space-y-2 text-[12px]">
            <EntityLine
              label={t('import.stats.entity.account')}
              newNames={stats.accounts.new_names}
              matchedNames={stats.accounts.matched_names}
            />
            <EntityLine
              label={t('import.stats.entity.category')}
              newNames={stats.categories.new_names}
              matchedNames={stats.categories.matched_names}
            />
            <EntityLine
              label={t('import.stats.entity.tag')}
              newNames={stats.tags.new_names}
              matchedNames={stats.tags.matched_names}
            />
            <div className="border-t border-border/40 pt-2">
              <p className="font-medium">
                {t('import.stats.entity.transaction')} —{' '}
                {t('import.stats.txTotal', { count: stats.total_rows })}
              </p>
              <ul className="ml-4 mt-1 space-y-0.5 text-muted-foreground">
                <li>
                  {t('import.stats.tx.expense', {
                    count: stats.by_type.expense_count,
                    amount: formatAmount(stats.by_type.expense_total),
                  })}
                </li>
                <li>
                  {t('import.stats.tx.income', {
                    count: stats.by_type.income_count,
                    amount: formatAmount(stats.by_type.income_total),
                  })}
                </li>
                <li>
                  {t('import.stats.tx.transfer', {
                    count: stats.by_type.transfer_count,
                  })}
                </li>
                {stats.skipped_dedup > 0 ? (
                  <li className="text-amber-600 dark:text-amber-400">
                    {t('import.stats.tx.dedupSkip', { count: stats.skipped_dedup })}
                  </li>
                ) : null}
              </ul>
            </div>
          </div>
        </div>

        {/* 告警折叠 */}
        {stats.parse_warnings_total > 0 ? (
          <Collapsible
            title={t('import.stats.warnings.title', { count: stats.parse_warnings_total })}
            tone="warning"
          >
            <ul className="max-h-48 space-y-1 overflow-y-auto text-[11px] text-muted-foreground">
              {stats.parse_warnings.map((w, idx) => (
                <li key={idx}>
                  <span className="font-mono text-amber-600 dark:text-amber-400">
                    L{w.row_number}
                  </span>{' '}
                  · {w.code} · {w.message}
                </li>
              ))}
              {stats.parse_warnings_total > stats.parse_warnings.length ? (
                <li>… {t('import.stats.warnings.more', {
                  count: stats.parse_warnings_total - stats.parse_warnings.length,
                })}</li>
              ) : null}
            </ul>
          </Collapsible>
        ) : null}

        {/* 错误折叠(预览阶段:必填字段缺失等) */}
        {stats.parse_errors_total > 0 ? (
          <Collapsible
            title={t('import.stats.errors.title', { count: stats.parse_errors_total })}
            tone="error"
          >
            <ul className="max-h-48 space-y-1 overflow-y-auto text-[11px] text-destructive">
              {stats.parse_errors.map((e, idx) => (
                <li key={idx}>
                  <span className="font-mono">L{e.row_number}</span> · {e.code}
                  {e.field_name ? ` (${e.field_name})` : ''} · {e.message}
                </li>
              ))}
              {stats.parse_errors_total > stats.parse_errors.length ? (
                <li>… {t('import.stats.errors.more', {
                  count: stats.parse_errors_total - stats.parse_errors.length,
                })}</li>
              ) : null}
            </ul>
          </Collapsible>
        ) : null}
      </CardContent>
    </Card>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border/50 bg-card px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-1 text-base font-semibold tabular-nums">{value}</p>
    </div>
  )
}

function EntityLine({
  label,
  newNames,
  matchedNames,
}: {
  label: string
  newNames: string[]
  matchedNames: string[]
}) {
  const t = useT()
  const [expanded, setExpanded] = useState(false)
  const summary = `${t('import.stats.entity.summary', {
    new: newNames.length,
    matched: matchedNames.length,
  })}`
  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-2 rounded px-1 py-0.5 text-left transition hover:bg-muted/40"
      >
        <span>
          {label}: {summary}
        </span>
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3 w-3 text-muted-foreground" />
        )}
      </button>
      {expanded ? (
        <div className="ml-4 mt-1 space-y-1">
          {newNames.length > 0 ? (
            <p className="text-emerald-600 dark:text-emerald-400">
              + {newNames.slice(0, 10).join(', ')}
              {newNames.length > 10 ? ` … +${newNames.length - 10}` : ''}
            </p>
          ) : null}
          {matchedNames.length > 0 ? (
            <p className="text-muted-foreground">
              ✓ {matchedNames.slice(0, 10).join(', ')}
              {matchedNames.length > 10 ? ` … +${matchedNames.length - 10}` : ''}
            </p>
          ) : null}
          {newNames.length === 0 && matchedNames.length === 0 ? (
            <p className="text-muted-foreground">—</p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function Collapsible({
  title,
  tone,
  children,
}: {
  title: string
  tone: 'warning' | 'error'
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const toneClass =
    tone === 'error'
      ? 'border-destructive/40 bg-destructive/5'
      : 'border-amber-500/40 bg-amber-500/5'
  return (
    <div className={`rounded-lg border ${toneClass} px-3 py-2`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-2 text-left text-[12px] font-medium"
      >
        <span>{title}</span>
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
      </button>
      {open ? <div className="mt-2">{children}</div> : null}
    </div>
  )
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  } catch {
    return iso
  }
}

function formatAmount(raw: string): string {
  const num = Number(raw)
  if (Number.isNaN(num)) return raw
  return num.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}
