import { useEffect, useMemo, useState } from 'react'
import { ArrowLeftRight, Check, CircleDollarSign, Ellipsis, Pause, ReceiptText } from 'lucide-react'
import type { RecurringRuleRow, RuleConfig } from '@beecount/api-client'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  useLocale,
  useT,
} from '@beecount/ui'
import { CategoryIcon } from '@beecount/web-features'
import { useSharedLedgerResources } from '../context/SharedLedgerResourcesContext'
import { useAttachmentCache } from '../context/AttachmentCacheContext'
import './RecurringScheduleList.css'

type View = 'active' | 'paused' | 'completed'
const palette = [
  'bg-amber-500/10 text-amber-700 dark:text-amber-300',
  'bg-violet-500/10 text-violet-700 dark:text-violet-300',
  'bg-teal-500/10 text-teal-700 dark:text-teal-300',
  'bg-sky-500/10 text-sky-700 dark:text-sky-300',
]
const views: View[] = ['active', 'paused', 'completed']
const viewKey = (view: View) => (view === 'active' ? 'recurring.inProgress' : 'recurring.' + view)
const viewFor = (row: RecurringRuleRow): View => (row.status === 'executing' ? 'active' : row.status)
const civilDate = (instant: Date, zone: string) =>
  new Intl.DateTimeFormat('sv-SE', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    timeZone: zone,
  }).format(instant)
const utcDay = (date: string) => new Date(date + 'T12:00:00Z')

function period(rule: RuleConfig, t: ReturnType<typeof useT>, locale: string) {
  const anchor = utcDay(rule.starts_at.slice(0, 10))
  const day = rule.day_of_month ?? anchor.getUTCDate()
  const interval = t('recurring.every', { n: rule.interval, unit: t('recurring.' + rule.frequency) })
  if (rule.frequency === 'monthly')
    return rule.interval === 1 ? t('recurring.monthOn', { day }) : interval + ' · ' + day
  if (rule.frequency === 'weekly') {
    const weekday = rule.day_of_week ?? (anchor.getUTCDay() || 7)
    const label = new Intl.DateTimeFormat(locale, { weekday: 'long', timeZone: 'UTC' }).format(
      Date.UTC(2024, 0, weekday),
    )
    return rule.interval === 1
      ? t('recurring.weekOn', { day: locale.startsWith('zh') ? label.slice(-1) : label })
      : interval + ' · ' + label
  }
  if (rule.frequency === 'yearly') {
    const month = rule.month_of_year ?? anchor.getUTCMonth() + 1
    // Show the requested anchor even when individual short months clamp the occurrence.
    const date = new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric', timeZone: 'UTC' })
      .formatToParts(Date.UTC(2024, month - 1, 1))
      .map((p) => (p.type === 'day' ? String(day) : p.value))
      .join('')
    return rule.interval === 1 ? t('recurring.yearOn', { date }) : interval + ' · ' + date
  }
  return interval
}

export function RecurringScheduleList({
  items,
  ledger,
  currency,
  busy,
  onEdit,
  onToggle,
  onDelete,
}: {
  items: RecurringRuleRow[]
  ledger: string
  currency: string
  busy: boolean
  onEdit: (row: RecurringRuleRow) => void
  onToggle: (row: RecurringRuleRow) => void
  onDelete: (row: RecurringRuleRow) => void
}) {
  const { locale, t } = useLocale()
  const [view, setView] = useState<View>('active')
  const { bundle } = useSharedLedgerResources(ledger)
  const { previewMap, ensureLoadedMany } = useAttachmentCache()
  useEffect(() => {
    ensureLoadedMany(
      (bundle?.categories ?? []).flatMap((c) => (c.icon_cloud_file_id ? [c.icon_cloud_file_id] : [])),
    )
  }, [bundle, ensureLoadedMany])
  const categories = useMemo(() => new Map(bundle?.categories.map((c) => [c.sync_id, c])), [bundle])
  const accounts = useMemo(() => new Map(bundle?.accounts.map((a) => [a.sync_id, a.name])), [bundle])
  const groups = useMemo(() => {
    const filtered = items
      .filter((row) => viewFor(row) === view)
      .map((row) => ({
        row,
        date:
          row.next_local?.slice(0, 10) ||
          (row.next_due_at ? civilDate(new Date(row.next_due_at), row.data.rule.time_zone) : ''),
      }))
    filtered.sort(
      (a, b) =>
        (view === 'active' ? a.date.localeCompare(b.date) : 0) ||
        a.row.data.rule.name.localeCompare(b.row.data.rule.name, locale),
    )
    const result = new Map<string, typeof filtered>()
    for (const item of filtered) {
      const key = view === 'active' ? item.date.slice(0, 7) : ''
      const group = result.get(key) ?? []
      group.push(item)
      result.set(key, group)
    }
    return result
  }, [items, locale, view])
  return (
    <>
      <div role="group" aria-label={t('nav.recurring')} className="flex gap-5 border-b border-border">
        {views.map((key) => (
          <button
            key={key}
            type="button"
            aria-pressed={view === key}
            onClick={() => setView(key)}
            className={
              'relative inline-flex items-center gap-1.5 pb-3 pt-2 text-sm ' +
              (view === key
                ? 'font-semibold text-foreground after:absolute after:inset-x-0 after:-bottom-px after:h-[3px] after:rounded-full after:bg-primary'
                : 'text-muted-foreground')
            }
          >
            {t(viewKey(key))}
            <span
              className={
                'rounded px-1.5 text-xs tabular-nums ' + (view === key ? 'bg-primary/15' : 'bg-muted')
              }
            >
              {items.filter((r) => viewFor(r) === key).length}
            </span>
          </button>
        ))}
      </div>
      {!groups.size && (
        <p className="py-14 text-center text-sm text-muted-foreground">
          {items.length ? t('recurring.emptyStatus', { status: t(viewKey(view)) }) : t('recurring.empty')}
        </p>
      )}
      {Array.from(groups, ([month, entries]) => (
        <section key={month || view} className="recurring-month mt-5">
          <div className="mb-2 flex items-baseline gap-2">
            <h2 className="font-semibold">
              {!month
                ? t(viewKey(view))
                : locale.startsWith('zh')
                  ? Number(month.slice(-2)) + ' ' + t('recurring.monthly')
                  : new Intl.DateTimeFormat(locale, { month: 'long', timeZone: 'UTC' }).format(
                      utcDay(month + '-01'),
                    )}
            </h2>
            {month && <span className="text-xs text-muted-foreground">{month.slice(0, 4)}</span>}
            <span className="recurring-amount-label ml-auto mr-10 text-xs text-muted-foreground">
              {t('recurring.perBill')}
            </span>
          </div>
          <ul className="recurring-entries text-card-foreground">
            {entries.map(({ row, date }) => {
              const rule = row.data.rule,
                tx = rule.transaction,
                editing = row.can_edit && row.status !== 'executing'
              const cat = categories.get(tx.category_id || '')
              const category = cat?.name ?? tx.category ?? t('recurring.' + tx.tx_type)
              const account =
                tx.tx_type === 'transfer'
                  ? [
                      accounts.get(tx.from_account_id || '') || tx.from_account,
                      accounts.get(tx.to_account_id || '') || tx.to_account,
                    ]
                      .filter(Boolean)
                      .join(' → ')
                  : accounts.get(tx.account_id || '') || tx.account
              const delta = date
                ? Math.round((+utcDay(date) - +utcDay(civilDate(new Date(), rule.time_zone))) / 86400000)
                : 0
              const shortDate =
                date &&
                (delta === 0 || delta === 1
                  ? new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(delta, 'day')
                  : new Intl.DateTimeFormat(locale, { weekday: 'short', timeZone: 'UTC' }).format(
                      utcDay(date),
                    ))
              const amount = new Intl.NumberFormat(locale, {
                style: 'currency',
                currency: tx.currency || currency,
                currencyDisplay: tx.currency && tx.currency !== currency ? 'code' : 'narrowSymbol',
                signDisplay: tx.tx_type === 'income' ? 'always' : 'auto',
                maximumFractionDigits: 8,
              }).format(tx.tx_type === 'expense' ? -tx.amount : tx.amount)
              const tone =
                tx.tx_type === 'income'
                  ? 'bg-income/10 text-income'
                  : palette[Array.from(category).reduce((n, c) => n + c.charCodeAt(0), 0) % palette.length]
              const Icon =
                tx.tx_type === 'transfer'
                  ? ArrowLeftRight
                  : tx.tx_type === 'income'
                    ? CircleDollarSign
                    : ReceiptText
              const caption =
                row.status === 'executing'
                  ? t('recurring.executing')
                  : [category, account].filter(Boolean).join(' · ')
              return (
                <li
                  key={row.id}
                  className="grid min-h-[68px] grid-cols-[36px_minmax(0,1fr)_auto_26px] items-center gap-1.5 py-2 sm:grid-cols-[58px_minmax(0,1fr)_auto_32px] sm:gap-3"
                >
                  <div
                    className={
                      'flex min-h-10 flex-col items-center justify-center border-r border-border leading-none ' +
                      (view === 'active' && (delta === 0 || delta === 1)
                        ? 'text-amber-700 dark:text-amber-300'
                        : 'text-muted-foreground')
                    }
                    title={view === 'active' ? date + ' · ' + rule.time_zone : t(viewKey(view))}
                  >
                    {view === 'active' && date ? (
                      <>
                        <span className="text-xl font-semibold tabular-nums">{date.slice(-2)}</span>
                        <span className="mt-1 text-[10px]">{shortDate}</span>
                      </>
                    ) : view === 'paused' ? (
                      <Pause size={16} />
                    ) : view === 'completed' ? (
                      <Check size={16} />
                    ) : (
                      <span>—</span>
                    )}
                  </div>
                  <button
                    type="button"
                    disabled={!editing || busy}
                    onClick={() => onEdit(row)}
                    className="flex min-w-0 items-center gap-2 text-left disabled:cursor-default sm:gap-3"
                  >
                    <span
                      className={
                        'hidden size-9 shrink-0 items-center justify-center rounded-xl sm:flex ' + tone
                      }
                    >
                      {cat?.icon || cat?.icon_cloud_file_id ? (
                        <CategoryIcon
                          icon={cat.icon}
                          iconType={cat.icon_type}
                          iconCloudFileId={cat.icon_cloud_file_id}
                          iconPreviewUrlByFileId={previewMap}
                          size={18}
                        />
                      ) : (
                        <Icon size={17} />
                      )}
                    </span>
                    <span className="min-w-0">
                      <strong className="block truncate text-sm font-semibold sm:text-base" title={rule.name}>
                        {rule.name}
                      </strong>
                      <span className="mt-0.5 block truncate text-xs text-muted-foreground" title={caption}>
                        {caption}
                      </span>
                    </span>
                  </button>
                  <div className="text-right">
                    <strong
                      className={
                        'block whitespace-nowrap text-sm font-semibold tabular-nums sm:text-base ' +
                        (tx.tx_type === 'income' ? 'text-income' : '')
                      }
                    >
                      {amount}
                    </strong>
                    <span className="text-xs text-muted-foreground">{period(rule, t, locale)}</span>
                  </div>
                  {editing && (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button
                          className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted"
                          aria-label={t('recurring.actions', { name: rule.name })}
                          disabled={busy}
                        >
                          <Ellipsis size={17} />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onSelect={() => onEdit(row)}>{t('common.edit')}</DropdownMenuItem>
                        {row.status !== 'completed' && (
                          <DropdownMenuItem onSelect={() => onToggle(row)}>
                            {t(row.status === 'paused' ? 'recurring.resume' : 'recurring.pause')}
                          </DropdownMenuItem>
                        )}
                        <DropdownMenuItem onSelect={() => onDelete(row)} className="text-destructive">
                          {t('common.delete')}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      ))}
    </>
  )
}
