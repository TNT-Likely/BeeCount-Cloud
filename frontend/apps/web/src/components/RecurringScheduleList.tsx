import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeftRight, CircleDollarSign, Ellipsis, ReceiptText } from 'lucide-react'
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
import {
  initialRecurringView,
  lastPostingDate,
  recurringSubtitle,
  recurringViewFor as viewFor,
  recurringViews as views,
  type RecurringView as View,
  perPeriodAmount,
  recurringCivilDate as civilDate,
  recurringUtcDay as utcDay,
  shortPostingDate,
} from '../lib/recurringPresentation'
import './RecurringScheduleList.css'

const palette = [
  'bg-amber-500/10 text-amber-700 dark:text-amber-300',
  'bg-violet-500/10 text-violet-700 dark:text-violet-300',
  'bg-teal-500/10 text-teal-700 dark:text-teal-300',
  'bg-sky-500/10 text-sky-700 dark:text-sky-300',
]
const viewKey = (view: View) => (view === 'active' ? 'recurring.inProgress' : 'recurring.' + view)
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
  ledgerName,
  currency,
  busy,
  onEdit,
  onToggle,
  onDelete,
}: {
  items: RecurringRuleRow[]
  ledger: string
  ledgerName: string
  currency: string
  busy: boolean
  onEdit: (row: RecurringRuleRow) => void
  onToggle: (row: RecurringRuleRow) => void
  onDelete: (row: RecurringRuleRow) => void
}) {
  const { locale, t } = useLocale()
  const [view, setView] = useState<View>(() => initialRecurringView(items))
  const selectedFilter = useRef<HTMLButtonElement>(null)
  const { bundle } = useSharedLedgerResources(ledger)
  const { previewMap, ensureLoadedMany } = useAttachmentCache()
  useEffect(() => {
    ensureLoadedMany(
      (bundle?.categories ?? []).flatMap((c) => (c.icon_cloud_file_id ? [c.icon_cloud_file_id] : [])),
    )
  }, [bundle, ensureLoadedMany])
  const categories = useMemo(() => new Map(bundle?.categories.map((c) => [c.sync_id, c])), [bundle])
  const accounts = useMemo(() => new Map(bundle?.accounts.map((a) => [a.sync_id, a])), [bundle])
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
            ref={view === key ? selectedFilter : undefined}
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
              className={'rounded px-1.5 text-xs tabular-nums ' + (view === key ? 'bg-primary/15' : 'bg-muted')}
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
          </div>
          <ul className="recurring-entries text-card-foreground">
            {entries.map(({ row, date }) => {
              const rule = row.data.rule,
                tx = rule.transaction,
                editing = row.can_edit && row.status !== 'executing'
              const cat = categories.get(tx.category_id || '')
              const category =
                cat?.name ||
                tx.category ||
                (tx.tx_type === 'transfer' ? t('recurring.transfer') : t('overview.recent.uncategorized'))
              function accountDetails(id?: string | null, name?: string | null) {
                const named =
                  !id && name ? bundle?.accounts.filter((account) => account.name === name) : undefined
                const entry = id ? accounts.get(id) : named?.length === 1 ? named[0] : undefined
                const referenced = Boolean(id || name)
                return {
                  name:
                    entry?.name ||
                    name ||
                    (referenced
                      ? bundle
                        ? t('recurring.accountUnavailable')
                        : '…'
                      : t('recurring.noAccount')),
                  currency: entry ? entry.currency : referenced ? undefined : null,
                }
              }
              const source =
                tx.tx_type === 'transfer'
                  ? accountDetails(tx.from_account_id || tx.account_id, tx.from_account || tx.account)
                  : accountDetails(tx.account_id, tx.account)
              const account =
                tx.tx_type === 'transfer'
                  ? source.name + ' → ' + accountDetails(tx.to_account_id, tx.to_account).name
                  : source.name
              const note = tx.note?.trim()
              const today = civilDate(new Date(), rule.time_zone)
              const delta = date ? Math.round((+utcDay(date) - +utcDay(today)) / 86400000) : null
              const relative =
                delta === null
                  ? ''
                  : new Intl.RelativeTimeFormat(locale, {
                      numeric: Math.abs(delta) < 2 ? 'auto' : 'always',
                    }).format(delta, 'day')
              const time =
                row.next_due_at &&
                rule.starts_at.length > 10 &&
                new Intl.DateTimeFormat(locale, {
                  hour: '2-digit',
                  minute: '2-digit',
                  timeZone: rule.time_zone,
                }).format(new Date(row.next_due_at))
              const previous = lastPostingDate(row)
              const amount = perPeriodAmount(tx, currency, locale, source.currency)
              const amountLabel = t(
                {
                  expense: 'recurring.perExpense',
                  income: 'recurring.perIncome',
                  transfer: 'recurring.perTransfer',
                }[tx.tx_type],
              )
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
              const subtitle = recurringSubtitle(rule.name, category, note)
              const enabled = row.status === 'active' || row.status === 'executing'
              return (
                <li key={row.id} aria-label={rule.name} className="recurring-bill-card" data-state={row.status}>
                  <div className="flex min-h-10 items-center gap-2.5 sm:gap-3">
                    <button
                      type="button"
                      disabled={!editing || busy}
                      aria-label={editing ? t('recurring.editNamed', { name: rule.name }) : undefined}
                      onClick={() => onEdit(row)}
                      className="flex min-w-0 flex-1 items-center gap-2.5 rounded-md text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-default sm:gap-3"
                    >
                      <span
                        className={
                          'recurring-bill-badge grid size-9 shrink-0 place-items-center rounded-xl sm:size-10 ' +
                          tone
                        }
                        aria-hidden="true"
                      >
                        {cat?.icon || cat?.icon_cloud_file_id ? (
                          <CategoryIcon
                            icon={cat.icon}
                            iconType={cat.icon_type}
                            iconCloudFileId={cat.icon_cloud_file_id}
                            iconPreviewUrlByFileId={previewMap}
                            size={20}
                          />
                        ) : (
                          <Icon size={20} />
                        )}
                      </span>
                      <span className="min-w-0">
                        <strong
                          className="block truncate text-[15px] font-semibold leading-snug sm:text-base"
                          title={rule.name}
                        >
                          {rule.name}
                        </strong>
                        {subtitle && (
                          <span
                            className="mt-0.5 line-clamp-2 break-words text-xs leading-snug text-muted-foreground"
                            title={subtitle}
                          >
                            {subtitle}
                          </span>
                        )}
                      </span>
                    </button>
                    <div className="flex shrink-0 items-center gap-0.5">
                      {row.status !== 'completed' && (
                        <button
                          type="button"
                          role="switch"
                          aria-checked={enabled}
                          aria-label={t('recurring.toggleNamed', { name: rule.name })}
                          disabled={!editing || busy}
                          className="group grid h-9 w-10 shrink-0 place-items-center rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-default disabled:opacity-60"
                          onClick={(event) => {
                            if (event.detail === 0) selectedFilter.current?.focus()
                            onToggle(row)
                          }}
                        >
                          <span
                            aria-hidden="true"
                            className="inline-flex h-[18px] w-8 items-center rounded-full bg-border p-0.5 transition-colors group-aria-checked:bg-primary"
                          >
                            <span className="size-3.5 rounded-full bg-card shadow-sm transition-transform group-aria-checked:translate-x-3.5" />
                          </span>
                        </button>
                      )}
                      {editing && (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <button
                              type="button"
                              className="grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
                              aria-label={t('recurring.actions', { name: rule.name })}
                              disabled={busy}
                            >
                              <Ellipsis size={18} />
                            </button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onSelect={() => onEdit(row)}>{t('common.edit')}</DropdownMenuItem>
                            <DropdownMenuItem onSelect={() => onDelete(row)} className="text-destructive">
                              {t('common.delete')}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
                    </div>
                  </div>
                  <dl className="recurring-bill-figures">
                    <div>
                      <dt className="text-xs text-muted-foreground">{amountLabel}</dt>
                      <dd>
                        <strong
                          className={'recurring-bill-value ' + (tx.tx_type === 'income' ? 'text-income' : '')}
                        >
                          {amount.original}
                        </strong>
                        {amount.converted && (
                          <small className="mt-0.5 block text-[11px] tabular-nums text-muted-foreground">
                            ≈ {amount.converted}
                          </small>
                        )}
                      </dd>
                    </div>
                    <div className="recurring-bill-next">
                      <dt className="text-xs text-muted-foreground">
                        {t(enabled ? 'recurring.nextPosting' : 'recurring.postingStatus')}
                        {enabled && (row.status === 'executing' || relative) && (
                          <>
                            {' · '}
                            <span
                              className={
                                delta === 0 || delta === 1 ? 'text-amber-700 dark:text-amber-300' : undefined
                              }
                            >
                              {row.status === 'executing' ? t('recurring.executing') : relative}
                            </span>
                          </>
                        )}
                      </dt>
                      <dd
                        className="recurring-bill-value recurring-bill-due"
                        title={enabled && date ? date + ' · ' + rule.time_zone : undefined}
                      >
                        {enabled && date ? (
                          <time dateTime={row.next_local || row.next_due_at || date}>
                            {shortPostingDate(date, today, locale)}
                            {time && ' ' + time}
                          </time>
                        ) : enabled ? (
                          '—'
                        ) : (
                          t(viewKey(view))
                        )}
                      </dd>
                    </div>
                  </dl>
                  <footer className="mt-auto border-t border-border pt-2.5">
                    <dl className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                      <div>
                        <dt className="sr-only">{t('recurring.frequency')}</dt>
                        <dd className="text-[13px] font-medium">{period(rule, t, locale)}</dd>
                      </div>
                      <div className="flex items-baseline gap-1 text-xs text-muted-foreground">
                        <dt className={previous ? undefined : 'sr-only'}>{t('recurring.lastPosting')}</dt>
                        <dd>
                          {previous ? (
                            <time dateTime={previous}>{shortPostingDate(previous, today, locale)}</time>
                          ) : (
                            t(previous === null ? 'recurring.noHistory' : 'recurring.historyUnavailable')
                          )}
                        </dd>
                      </div>
                    </dl>
                    <p className="mt-1 break-words text-xs text-muted-foreground">
                      {[ledgerName, account, currency].join(' · ')}
                    </p>
                  </footer>
                </li>
              )
            })}
          </ul>
        </section>
      ))}
    </>
  )
}
