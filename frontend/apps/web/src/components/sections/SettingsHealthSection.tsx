import { type ReactNode } from 'react'
import {
  Activity,
  BookOpen,
  Database,
  FolderTree,
  Receipt,
  RefreshCcw,
  Tag,
  Users,
  Wallet,
  Wifi,
} from 'lucide-react'

import type {
  AdminHealth,
  AdminOverview,
  RagIndexStatus,
} from '@beecount/api-client'
import {
  Badge,
  Button,
  Card,
  CardContent,
  useT,
} from '@beecount/ui'
import { formatIsoDateTime, formatIsoDateTimeLocal } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { getRagLatestState, shouldShowRagUpdate } from '../../lib/ragStatus'

interface Props {
  adminHealth: AdminHealth | null
  adminOverview: AdminOverview | null
  ragStatus: RagIndexStatus | null
  isRefreshingRag: boolean
  onRefresh: () => void
  onRefreshRag: () => void
}

/**
 * 设置 - 健康 section —— 从 AppPage.tsx 抽出。
 * 顶部 hero:系统状态 + DB / 在线用户 / 时间。
 * 管理员可见下方使用概览 6 张统计卡。
 */
export function SettingsHealthSection({
  adminHealth,
  adminOverview,
  ragStatus,
  isRefreshingRag,
  onRefresh,
  onRefreshRag,
}: Props) {
  const t = useT()
  const { isAdmin } = useAuth()
  const healthy = adminHealth?.status === 'ok'
  const showRagUpdate = isAdmin && shouldShowRagUpdate(ragStatus)

  return (
    <div className="space-y-6">
      <Card className="bc-panel overflow-hidden">
        <CardContent className="p-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="relative flex h-14 w-14 items-center justify-center">
                <span
                  className={`absolute inset-0 animate-ping rounded-full ${
                    healthy ? 'bg-emerald-500/30' : 'bg-rose-500/30'
                  }`}
                />
                <span
                  className={`relative flex h-12 w-12 items-center justify-center rounded-full ${
                    healthy
                      ? 'bg-emerald-500/15 text-emerald-500'
                      : 'bg-rose-500/15 text-rose-500'
                  }`}
                >
                  <Activity className="h-6 w-6" />
                </span>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wider text-muted-foreground">
                  {t('ops.health.title')}
                </p>
                <p className="text-2xl font-semibold">
                  {adminHealth?.status === 'ok'
                    ? t('ops.health.statusRunning')
                    : adminHealth?.status || '—'}
                </p>
              </div>
            </div>
            <Button size="sm" variant="outline" onClick={onRefresh}>
              <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
              {t('ops.health.button.refresh')}
            </Button>
          </div>

          {adminHealth ? (
            <div className="mt-6 grid gap-3 sm:grid-cols-3">
              <HealthMetaTile
                icon={<Database className="h-4 w-4" />}
                label={t('ops.health.meta.db')}
                value={adminHealth.db || '—'}
              />
              <HealthMetaTile
                icon={<Wifi className="h-4 w-4" />}
                label={t('ops.health.meta.online')}
                value={String(adminHealth.online_ws_users)}
              />
              <HealthMetaTile
                icon={<Activity className="h-4 w-4" />}
                label={t('ops.health.meta.time')}
                value={formatIsoDateTime(adminHealth.time)}
              />
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card className="bc-panel">
        <CardContent className="p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <BookOpen className="h-4 w-4 text-muted-foreground" />
                <p className="text-sm font-semibold">{t('ops.rag.title')}</p>
                {ragStatus ? <RagLatestBadge ragStatus={ragStatus} t={t} /> : null}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                {ragStatus
                  ? `${ragStatus.embedding_model || '—'} · ${ragStatus.source}`
                  : t('ops.rag.unavailable')}
              </p>
            </div>
            {showRagUpdate ? (
              <Button size="sm" variant="outline" disabled={isRefreshingRag} onClick={onRefreshRag}>
                <RefreshCcw className="mr-1.5 h-3.5 w-3.5" />
                {isRefreshingRag ? t('ops.rag.refreshing') : t('ops.rag.updateNow')}
              </Button>
            ) : null}
          </div>
          {ragStatus ? (
            <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-border/50 bg-muted/30 px-3 py-2.5 text-xs">
              <RagSummaryItem
                label={t('ops.rag.meta.zh')}
                value={formatIsoDateTimeLocal(ragStatus.languages.zh?.build_time || '') || '—'}
              />
              <RagSummaryItem
                label={t('ops.rag.meta.en')}
                value={formatIsoDateTimeLocal(ragStatus.languages.en?.build_time || '') || '—'}
              />
              <RagSummaryItem
                label={t('ops.rag.meta.chunks')}
                value={`${ragStatus.languages.zh?.chunk_count || 0} / ${ragStatus.languages.en?.chunk_count || 0}`}
              />
            </div>
          ) : null}
          {ragStatus?.last_error ? (
            <p className="mt-4 text-xs text-amber-600 dark:text-amber-400">
              {t('ops.rag.lastError')}: {ragStatus.last_error}
            </p>
          ) : null}
        </CardContent>
      </Card>

      {isAdmin && adminOverview ? (
        <div>
          <div className="mb-3 flex items-center gap-2">
            <h3 className="text-sm font-medium">{t('ops.health.overviewTitle')}</h3>
            <span className="text-xs text-muted-foreground">{t('ops.health.overviewHint')}</span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <OverviewStatCard
              icon={<Users className="h-5 w-5" />}
              accentClass="from-blue-500/15 to-blue-500/5 text-blue-500"
              label={t('ops.health.stat.users')}
              value={adminOverview.users_total}
              sub={t('ops.health.stat.usersEnabled', { n: adminOverview.users_enabled_total })}
            />
            <OverviewStatCard
              icon={<BookOpen className="h-5 w-5" />}
              accentClass="from-violet-500/15 to-violet-500/5 text-violet-500"
              label={t('ops.health.stat.ledgers')}
              value={adminOverview.ledgers_total}
            />
            <OverviewStatCard
              icon={<Receipt className="h-5 w-5" />}
              accentClass="from-emerald-500/15 to-emerald-500/5 text-emerald-500"
              label={t('ops.health.stat.transactions')}
              value={adminOverview.transactions_total}
            />
            <OverviewStatCard
              icon={<Wallet className="h-5 w-5" />}
              accentClass="from-amber-500/15 to-amber-500/5 text-amber-500"
              label={t('ops.health.stat.accounts')}
              value={adminOverview.accounts_total}
            />
            <OverviewStatCard
              icon={<FolderTree className="h-5 w-5" />}
              accentClass="from-cyan-500/15 to-cyan-500/5 text-cyan-500"
              label={t('ops.health.stat.categories')}
              value={adminOverview.categories_total}
            />
            <OverviewStatCard
              icon={<Tag className="h-5 w-5" />}
              accentClass="from-pink-500/15 to-pink-500/5 text-pink-500"
              label={t('ops.health.stat.tags')}
              value={adminOverview.tags_total}
            />
          </div>
        </div>
      ) : null}

      {isAdmin && !adminOverview ? (
        <Card className="bc-panel">
          <CardContent className="py-6">
            <p className="text-center text-sm text-muted-foreground">{t('table.empty')}</p>
          </CardContent>
        </Card>
      ) : null}

    </div>
  )
}

function RagLatestBadge({
  ragStatus,
  t,
}: {
  ragStatus: RagIndexStatus
  t: ReturnType<typeof useT>
}) {
  const latestState = getRagLatestState(ragStatus)
  const config =
    latestState === 'latest'
      ? { label: t('ops.rag.badge.latest'), className: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400' }
      : latestState === 'outdated'
        ? { label: t('ops.rag.badge.outdated'), className: 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400' }
        : latestState === 'failed'
          ? { label: t('ops.rag.badge.failed'), className: 'border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-400' }
          : { label: t('ops.rag.badge.pending'), className: 'border-border/60 bg-muted text-muted-foreground' }
  return (
    <Badge variant="outline" className={`h-5 gap-1 px-1.5 text-[10px] font-medium ${config.className}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {config.label}
    </Badge>
  )
}

function RagSummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-1.5 whitespace-nowrap">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium text-foreground">{value}</span>
    </div>
  )
}

/** Health hero 下方的 meta 方块(DB / 在线 / 时间):icon chip + label + value。 */
function HealthMetaTile({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: string
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border/50 bg-muted/30 px-3 py-2.5">
      <div className="flex h-8 w-8 items-center justify-center rounded-md bg-background text-muted-foreground">
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
        <p className="truncate text-sm font-medium" title={value}>
          {value}
        </p>
      </div>
    </div>
  )
}

/** 使用概览里的统计卡:左上角彩色 icon 块 + 标签 + 大数字 + 可选辅助行。 */
function OverviewStatCard({
  icon,
  label,
  value,
  sub,
  accentClass,
}: {
  icon: ReactNode
  label: string
  value: number
  sub?: string
  accentClass: string
}) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-border/60 bg-card p-4 transition-shadow hover:shadow-md">
      <div
        className={`absolute -right-8 -top-8 h-24 w-24 rounded-full bg-gradient-to-br ${accentClass} opacity-50 blur-2xl`}
        aria-hidden
      />
      <div className="relative flex items-start gap-3">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br ${accentClass}`}>
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className="mt-0.5 text-2xl font-bold tabular-nums">{value.toLocaleString()}</p>
          {sub ? <p className="mt-0.5 text-[11px] text-muted-foreground">{sub}</p> : null}
        </div>
      </div>
    </div>
  )
}
