import { useState, type ReactNode } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Database,
  FolderTree,
  Info,
  Receipt,
  RefreshCcw,
  RefreshCw,
  Tag,
  Users,
  Wallet,
  Wifi,
} from 'lucide-react'

import type {
  AdminHealth,
  AdminIntegrityIssue,
  AdminIntegrityIssueSample,
  AdminIntegrityScan,
  AdminOverview,
} from '@beecount/api-client'
import {
  Button,
  Card,
  CardContent,
  useT,
} from '@beecount/ui'
import { formatIsoDateTime } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'

interface Props {
  adminHealth: AdminHealth | null
  adminOverview: AdminOverview | null
  integrity: AdminIntegrityScan | null
  integrityLoading: boolean
  onRefresh: () => void
  onRefreshIntegrity: () => void
  onJumpToSample: (issueType: string, sample: AdminIntegrityIssueSample) => void
}

/**
 * 设置 - 健康 section —— 从 AppPage.tsx 抽出。
 * 顶部 hero:系统状态 + DB / 在线用户 / 时间。
 * 管理员可见下方使用概览 6 张统计卡。
 */
export function SettingsHealthSection({
  adminHealth,
  adminOverview,
  integrity,
  integrityLoading,
  onRefresh,
  onRefreshIntegrity,
  onJumpToSample,
}: Props) {
  const t = useT()
  const { isAdmin } = useAuth()
  const healthy = adminHealth?.status === 'ok'

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

      {/* Admin only:数据完整性扫描 */}
      {isAdmin ? (
        <IntegrityPanel
          scan={integrity}
          loading={integrityLoading}
          onRefresh={onRefreshIntegrity}
          onJumpToSample={onJumpToSample}
          t={t}
        />
      ) : null}
    </div>
  )
}

/** 数据完整性扫描面板 - 嵌入在 health 页底部(admin only)。 */
function IntegrityPanel({
  scan,
  loading,
  onRefresh,
  onJumpToSample,
  t,
}: {
  scan: AdminIntegrityScan | null
  loading: boolean
  onRefresh: () => void
  onJumpToSample: (issueType: string, sample: AdminIntegrityIssueSample) => void
  t: (key: string, params?: Record<string, string | number>) => string
}) {
  const grouped = groupByIssueType(scan?.issues || [])
  return (
    <div>
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-medium">{t('admin.integrity.title')}</h3>
          {scan ? (
            <span className="text-xs text-muted-foreground">
              {t('admin.integrity.notice.done', {
                ledgers: scan.ledgers_total,
                issues: scan.issues_total,
              })}
            </span>
          ) : null}
        </div>
        <Button size="sm" variant="outline" onClick={onRefresh} disabled={loading}>
          <RefreshCw className={`mr-1 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
          {loading ? t('admin.integrity.scanning') : t('admin.integrity.runScan')}
        </Button>
      </div>

      {scan && scan.issues.length === 0 ? (
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4 text-center">
          <p className="text-sm text-emerald-700 dark:text-emerald-400">
            ✓ {t('admin.integrity.allClean')}
          </p>
        </div>
      ) : null}

      <div className="space-y-3">
        {grouped.map(({ type, total, issues }) => (
          <Card key={type} className="bc-panel">
            <CardContent className="p-4">
              <div className="flex items-center justify-between gap-3 border-b border-border/40 pb-3">
                <div className="flex items-center gap-2">
                  {SEVERITY_BY_TYPE[type] === 'warn' ? (
                    <AlertTriangle className="h-4 w-4 text-amber-500" />
                  ) : (
                    <Info className="h-4 w-4 text-muted-foreground" />
                  )}
                  <h4 className="text-sm font-semibold">
                    {t(`admin.integrity.issue.${type}`)}
                  </h4>
                </div>
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium tabular-nums text-muted-foreground">
                  {total}
                </span>
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                {t(`admin.integrity.issue.${type}.help`)}
              </p>
              <div className="mt-3 space-y-2">
                {issues.map((iss) => (
                  <IssueRow
                    key={`${iss.issue_type}-${iss.ledger_id}`}
                    issue={iss}
                    onJumpToSample={(sample) => onJumpToSample(iss.issue_type, sample)}
                    t={t}
                  />
                ))}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}

function IssueRow({
  issue,
  t,
  onJumpToSample,
}: {
  issue: AdminIntegrityIssue
  t: (key: string, params?: Record<string, string | number>) => string
  onJumpToSample: (sample: AdminIntegrityIssueSample) => void
}) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="rounded-lg border border-border/40 bg-muted/20 px-3 py-2.5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">
            {issue.ledger_name || '(未命名账本)'}{' '}
            {issue.owner_email ? (
              <span className="text-xs text-muted-foreground">
                · {issue.owner_email}
              </span>
            ) : null}
          </div>
          <div className="text-xs text-muted-foreground">
            {t('admin.integrity.row.count', { count: issue.count })}
          </div>
        </div>
        {issue.samples.length > 0 ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-xs text-primary hover:underline"
          >
            {expanded
              ? t('admin.integrity.row.hideSamples')
              : t('admin.integrity.row.showSamples')}
          </button>
        ) : null}
      </div>
      {expanded ? (
        <div className="mt-2 space-y-1 border-t border-border/40 pt-2">
          {issue.samples.map((sample) => (
            <button
              key={sample.sync_id}
              type="button"
              onClick={() => onJumpToSample(sample)}
              className="group flex w-full items-center justify-between gap-2 rounded border border-border/40 bg-background px-2 py-1.5 text-left text-[11px] transition hover:border-primary/40 hover:bg-primary/5"
              title={t('admin.integrity.row.jumpHint')}
            >
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium text-foreground">
                    {sample.label}
                  </span>
                  <span className="truncate font-mono text-muted-foreground">
                    {sample.sync_id.slice(0, 8)}…
                  </span>
                </div>
                {sample.extra ? (
                  <div className="truncate font-mono text-[10px] text-muted-foreground/70">
                    {Object.entries(sample.extra)
                      .filter(([, v]) => v !== null && v !== undefined)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(' · ')}
                  </div>
                ) : null}
              </div>
              <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground transition group-hover:text-primary" />
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function groupByIssueType(
  issues: AdminIntegrityIssue[],
): Array<{ type: string; total: number; issues: AdminIntegrityIssue[] }> {
  const map = new Map<string, AdminIntegrityIssue[]>()
  for (const iss of issues) {
    const list = map.get(iss.issue_type) || []
    list.push(iss)
    map.set(iss.issue_type, list)
  }
  return Array.from(map.entries())
    .map(([type, group]) => ({
      type,
      total: group.reduce((s, g) => s + g.count, 0),
      issues: group,
    }))
    .sort((a, b) => {
      const sa = SEVERITY_BY_TYPE[a.type] === 'warn' ? 1 : 0
      const sb = SEVERITY_BY_TYPE[b.type] === 'warn' ? 1 : 0
      return sb - sa || b.total - a.total
    })
}

const SEVERITY_BY_TYPE: Record<string, 'warn' | 'info'> = {
  orphan_tx_category: 'warn',
  orphan_tx_account: 'warn',
  orphan_tx_from_account: 'warn',
  orphan_tx_to_account: 'warn',
  future_tx: 'warn',
  zero_amount_tx: 'warn',
  orphan_attachment: 'warn',
  unused_category: 'info',
  unused_account: 'info',
  unused_tag: 'info',
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
