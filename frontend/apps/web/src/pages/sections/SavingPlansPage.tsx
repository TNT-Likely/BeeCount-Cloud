import { useSearchParams } from 'react-router-dom'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PiggyBank, Plus, Target, CheckCircle2, XCircle, Archive, Trash2, Repeat, Pencil, Search } from 'lucide-react'

import {
  type PlanStatus,
  type PlanType,
  type SavingPlan,
  type WorkspaceTransaction,
  abandonPlan,
  archivePlan,
  completePlan,
  contributeToPlan,
  createSavingPlan,
  deleteContribution,
  deleteSavingPlan,
  fetchContributions,
  fetchReadAccounts,
  fetchSavingPlans,
  fetchWorkspaceTransactions,
  type SavingContribution,
  updateContribution,
  updateSavingPlan,
  uploadAttachment,
} from '@beecount/api-client'
import {
  useT,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
  Button,
  Input,
  Textarea,
  Label,
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@beecount/ui'
import { DatePicker } from '../../components/DatePicker'
import { LinkedTxList, SectionPageHeader } from '@beecount/web-features'
import { formatBalanceCompact, formatIsoDateTimeLocal, fromLocalDateTimeInput, toLocalDateTimeInput } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { localizeError } from '../../i18n/errors'
import { openTxDetailBySyncId, onOpenDetailSavingPlan } from '../../lib/txDialogEvents'

const PLAN_TYPES: PlanType[] = ['free', 'fixed', 'wish']
const PLAN_STATUSES: PlanStatus[] = ['active', 'completed', 'abandoned', 'archived']

function statusColor(status: PlanStatus): string {
  if (status === 'completed') return 'text-emerald-600'
  if (status === 'abandoned' || status === 'archived') return 'text-muted-foreground'
  return 'text-primary'
}

/** 状态徽章底色(跟定期存款详情页同款胶囊样式)。 */
function statusBadgeClass(status: PlanStatus): string {
  if (status === 'completed') return 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
  if (status === 'active') return 'bg-primary/15 text-primary'
  if (status === 'abandoned') return 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
  return 'bg-muted text-muted-foreground'
}

/** 计划类型图标。 */
function planTypeIcon(planType: PlanType) {
  if (planType === 'fixed') return <Repeat className="h-5 w-5" />
  if (planType === 'wish') return <Target className="h-5 w-5" />
  return <PiggyBank className="h-5 w-5" />
}

/** 统计小卡(与联系人详情页同款版式)。 */
function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-base font-semibold">{value}</div>
      {hint ? <div className="mt-0.5 text-[10px] text-muted-foreground">{hint}</div> : null}
    </div>
  )
}

/**
 * 储蓄罐详情弹窗。
 *
 * 版式对齐「联系人详情弹窗」:头部固定(icon + 名称 + 类型/状态),
 * 中间区独立滚动(统计 → 计划信息 → 存入记录),底部操作栏固定可见。
 *
 * 统计维度与联系人**不同** —— 联系人是「人」的维度(借出/借入/应收/应付),
 * 储蓄罐是「目标」的维度(目标 / 已存 / 还差 / 存入笔数)。
 *
 * 流水互相跳转:存入记录下的关联交易可点开原始交易详情;交易详情的
 * 「关联记录」又能跳回本弹窗(见 txDialogEvents.onOpenDetailSavingPlan)。
 */
function SavingPlanDetailDialog({
  open,
  plan,
  currency,
  ledgerId,
  refreshKey,
  onOpenChange,
  onContribute,
  onComplete,
  onAbandon,
  onArchive,
  onDelete,
  onEditPlan,
  onEditContribution,
  onDeleteContribution,
}: {
  open: boolean
  plan: SavingPlan | null
  currency: string
  ledgerId: string | null
  /** 存入成功后自增,强制重新拉取存入记录。 */
  refreshKey: number
  onOpenChange: (v: boolean) => void
  onContribute: (plan: SavingPlan) => void
  onComplete: (plan: SavingPlan) => void
  onAbandon: (plan: SavingPlan) => void
  onArchive: (plan: SavingPlan) => void
  onDelete: (plan: SavingPlan) => void
  /** 编辑计划(打开新建/编辑共用的表单弹窗)。 */
  onEditPlan: (plan: SavingPlan) => void
  /** 修改一笔存入记录。 */
  onEditContribution: (c: SavingContribution) => void
  /** 删除一笔存入记录。 */
  onDeleteContribution: (c: SavingContribution) => void
}) {
  const t = useT()
  const { token } = useAuth()
  const [contributions, setContributions] = useState<SavingContribution[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !token || !plan) {
      setContributions([])
      return
    }
    let cancelled = false
    setLoading(true)
    fetchContributions(token, plan.id)
      .then((rows) => {
        if (!cancelled) setContributions(rows)
      })
      .catch(() => {
        if (!cancelled) setContributions([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, token, plan, refreshKey])

  // 统计维度:目标口径(而非联系人的人口径)。
  const stats = useMemo(() => {
    if (!plan) return null
    const saved = plan.saved_amount
    const target = plan.target_amount
    const remaining = Math.max(0, target - saved)
    const monthly = plan.monthly_amount && plan.monthly_amount > 0 ? plan.monthly_amount : null
    // 按当前月存额度,还需多少个月才能达标(无月存额度则不估)。
    const monthsLeft = monthly && remaining > 0 ? Math.ceil(remaining / monthly) : null
    return { saved, target, remaining, monthly, monthsLeft, count: contributions.length }
  }, [plan, contributions.length])

  if (!plan || !stats) return null

  const cur = plan.currency_code || currency

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-border/60 bg-gradient-to-br from-muted/40 to-transparent px-6 py-4">
          <DialogTitle className="flex items-center gap-3">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              {planTypeIcon(plan.plan_type)}
            </span>
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-lg font-semibold">{plan.name}</span>
              <span className="mt-0.5 flex items-center gap-1.5 text-[11px] font-normal uppercase tracking-[0.18em] text-primary">
                <span>{t(`saving.type.${plan.plan_type}`)}</span>
                <span className="text-muted-foreground/60">·</span>
                <span className={`rounded-full px-1.5 py-0.5 normal-case tracking-normal ${statusBadgeClass(plan.status)}`}>
                  {t(`saving.status.${plan.status}`)}
                </span>
              </span>
            </div>
          </DialogTitle>
        </DialogHeader>

        {/* 中间区独立滚动:统计 / 计划信息 / 存入记录可变长,头尾保持固定可见。 */}
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-4">
          {/* 统计维度:目标 / 已存 / 还差 / 存入笔数(储蓄罐专属口径)。 */}
          <div>
            <div className="mb-2 text-sm font-medium">{t('saving.detail.summary')}</div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label={t('saving.detail.target')} value={formatBalanceCompact(stats.target, cur)} />
              <Stat
                label={t('saving.detail.saved')}
                value={formatBalanceCompact(stats.saved, cur)}
                hint={`${Math.round(plan.progress)}%`}
              />
              <Stat label={t('saving.detail.remaining')} value={formatBalanceCompact(stats.remaining, cur)} />
              <Stat
                label={t('saving.detail.count')}
                value={String(stats.count)}
                hint={stats.monthly ? `${t('saving.detail.monthly')} ${formatBalanceCompact(stats.monthly, cur)}` : undefined}
              />
            </div>
            {/* 进度条 */}
            <div className="mt-3">
              <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
                <span>{t('saving.progress')}</span>
                <span className="font-mono tabular-nums">{Math.round(plan.progress)}%</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${Math.min(100, plan.progress)}%` }}
                />
              </div>
              {stats.monthsLeft !== null ? (
                <p className="mt-1.5 text-[11px] text-muted-foreground">
                  {t('saving.detail.monthsLeft', { months: stats.monthsLeft })}
                </p>
              ) : null}
            </div>
          </div>

          {/* 计划信息 */}
          <div>
            <div className="mb-2 text-sm font-medium">{t('saving.detail.info')}</div>
            <div className="grid grid-cols-1 gap-3 rounded-lg border border-border/60 p-3 text-sm sm:grid-cols-2">
              <div>
                <span className="text-muted-foreground">{t('saving.field.planType')}</span>
                <div className="mt-0.5 font-medium">{t(`saving.type.${plan.plan_type}`)}</div>
              </div>
              <div>
                <span className="text-muted-foreground">{t('saving.field.account')}</span>
                <div className="mt-0.5 font-medium">
                  {plan.account_name || t('saving.detail.noAccount')}
                </div>
              </div>
              <div>
                <span className="text-muted-foreground">{t('saving.field.deadline')}</span>
                <div className="mt-0.5 font-medium">
                  {plan.deadline ? formatIsoDateTimeLocal(plan.deadline).slice(0, 10) : t('saving.detail.noDeadline')}
                </div>
              </div>
              <div>
                <span className="text-muted-foreground">{t('saving.field.monthlyAmount')}</span>
                <div className="mt-0.5 font-medium">
                  {plan.monthly_amount ? formatBalanceCompact(plan.monthly_amount, cur) : '—'}
                </div>
              </div>
              {plan.note ? (
                <div className="sm:col-span-2">
                  <span className="text-muted-foreground">{t('saving.field.note')}</span>
                  <div className="mt-0.5 whitespace-pre-wrap">{plan.note}</div>
                </div>
              ) : null}
            </div>
          </div>

          {/* 存入记录(时间轴)+ 关联交易(可点开原始流水) */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium">{t('saving.contributions')}</span>
              <span className="text-xs text-muted-foreground">
                {t('saving.detail.count')}: {stats.count}
              </span>
            </div>
            {loading && contributions.length === 0 ? (
              <div className="py-6 text-center text-sm text-muted-foreground">{t('notice.loading')}</div>
            ) : contributions.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t('saving.contributionsEmpty')}</p>
            ) : (
              <div className="divide-y divide-border rounded-lg border border-border">
                {contributions.map((c) => {
                  const changed = Math.abs(c.effective_amount - c.amount) > 0.005
                  // 存入流水(扣款侧,支出)与收款流水(入账侧,收入)合并展示,
                  // 用 role 区分徽标,方便一眼看出这笔钱从哪来、到哪去。
                  const receiveIds = new Set((c.receive_tx_sync_details ?? []).map((t) => t.sync_id))
                  const linked = [
                    ...(c.tx_sync_details ?? []).map((t) => ({ ...t, role: 'debit' as const })),
                    ...(c.receive_tx_sync_details ?? []).map((t) => ({ ...t, role: 'receive' as const })),
                  ]
                  return (
                    <div key={c.id} className="px-3 py-2">
                      <div className="flex items-start justify-between gap-3">
                        <span
                          className={`min-w-0 text-sm ${c.tx_sync_id ? 'cursor-pointer transition-colors hover:text-primary' : ''}`}
                          onClick={(e) => {
                            // 点标题区跳主流水;点关联交易行/操作按钮由各自处理。
                            if ((e.target as HTMLElement).closest('li')) return
                            if ((e.target as HTMLElement).closest('button')) return
                            if (!c.tx_sync_id) return
                            openTxDetailBySyncId(token, c.tx_sync_id, ledgerId ?? undefined, () =>
                              onOpenChange(false),
                            )
                          }}
                          title={c.tx_sync_id ? (t('deposits.detail.viewFlow') as string) : undefined}
                        >
                          <span className="font-mono tabular-nums text-xs text-muted-foreground">
                            {formatIsoDateTimeLocal(c.happened_at)}
                          </span>
                          {c.note ? (
                            <span className="ml-2 text-xs text-muted-foreground">{c.note}</span>
                          ) : null}
                          {changed ? (
                            <span className="ml-2 text-xs text-amber-600">{t('saving.contributionsAdjusted')}</span>
                          ) : null}
                        </span>
                        <div className="flex shrink-0 items-center gap-1">
                          <span className="font-mono tabular-nums">
                            {changed ? (
                              <>
                                <span className="text-muted-foreground line-through">
                                  {formatBalanceCompact(c.amount, c.currency_code || cur)}
                                </span>
                                <span className="ml-1">
                                  {formatBalanceCompact(c.effective_amount, c.currency_code || cur)}
                                </span>
                              </>
                            ) : (
                              formatBalanceCompact(c.effective_amount, c.currency_code || cur)
                            )}
                          </span>
                          {/* 修改 / 删除这笔存入记录 */}
                          <button
                            type="button"
                            aria-label={t('saving.contribution.edit') as string}
                            title={t('saving.contribution.edit') as string}
                            className="rounded p-1 text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary"
                            onClick={(e) => {
                              e.stopPropagation()
                              onEditContribution(c)
                            }}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            aria-label={t('saving.contribution.delete') as string}
                            title={t('saving.contribution.delete') as string}
                            className="rounded p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                            onClick={(e) => {
                              e.stopPropagation()
                              onDeleteContribution(c)
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                      {/* 关联交易:可点开原始交易详情(与联系人详情同款互相跳转)。 */}
                      {linked.length > 0 ? (
                        <div className="mt-2">
                          <p className="mb-1 text-[11px] text-muted-foreground">{t('linkedTx.title')}</p>
                          <LinkedTxList
                            details={linked}
                            searchable={false}
                            renderBadge={(tx) => (
                              <span
                                className={`shrink-0 rounded px-1 py-0.5 text-[10px] font-medium ${
                                  receiveIds.has(tx.sync_id)
                                    ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                                    : 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                                }`}
                              >
                                {receiveIds.has(tx.sync_id)
                                  ? t('saving.detail.receiveTag')
                                  : t('saving.detail.debitTag')}
                              </span>
                            )}
                            onSelect={(tx) =>
                              openTxDetailBySyncId(token, tx.sync_id, ledgerId ?? undefined, () =>
                                onOpenChange(false),
                              )
                            }
                          />
                        </div>
                      ) : null}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        {/* 底部操作栏固定可见,不随内容滚动。 */}
        <div className="shrink-0 border-t border-border/60 px-6 py-3">
          <div className="flex flex-wrap gap-2">
            {plan.status === 'active' ? (
              <>
                <Button onClick={() => onContribute(plan)}>
                  <PiggyBank className="mr-1 h-4 w-4" />
                  {t('saving.button.contribute')}
                </Button>
                <Button variant="outline" onClick={() => onComplete(plan)}>
                  <CheckCircle2 className="mr-1 h-4 w-4" />
                  {t('saving.button.complete')}
                </Button>
                <Button variant="ghost" onClick={() => onAbandon(plan)}>
                  <XCircle className="mr-1 h-4 w-4" />
                  {t('saving.button.abandon')}
                </Button>
              </>
            ) : null}
            {plan.status !== 'archived' ? (
              <Button variant="ghost" onClick={() => onArchive(plan)}>
                <Archive className="mr-1 h-4 w-4" />
                {t('saving.button.archive')}
              </Button>
            ) : null}
            <Button variant="ghost" onClick={() => onEditPlan(plan)}>
              <Pencil className="mr-1 h-4 w-4" />
              {t('saving.button.edit')}
            </Button>
            <Button
              variant="ghost"
              className="text-destructive hover:text-destructive"
              onClick={() => onDelete(plan)}
            >
              <Trash2 className="mr-1 h-4 w-4" />
              {t('saving.button.delete')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 选择一笔已有交易(单选)—— 储蓄罐「关联已有交易」用。
 *
 * 与物品页的 ExistingTxPicker 同思路,但储蓄罐一笔存入只对应**一笔**流水,
 * 所以这里单选即可(选中第二笔会替换第一笔)。
 */
function ExistingTxPicker({
  open,
  onOpenChange,
  token,
  ledgerId,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  token: string
  ledgerId: string
  onConfirm: (tx: WorkspaceTransaction | null) => void
}) {
  const t = useT()
  const [query, setQuery] = useState('')
  const [rows, setRows] = useState<WorkspaceTransaction[]>([])
  const [loading, setLoading] = useState(false)

  const search = useCallback(async () => {
    if (!token || !ledgerId) return
    setLoading(true)
    try {
      const page = await fetchWorkspaceTransactions(token, {
        ledgerId,
        q: query.trim() || undefined,
        limit: 30,
      })
      setRows(page.items || [])
    } catch {
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [token, ledgerId, query])

  useEffect(() => {
    if (open) void search()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('saving.contribute.pickExisting')}</DialogTitle>
        </DialogHeader>
        <div className="flex gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') search() }}
            placeholder={t('common.search') as string}
          />
          <Button variant="outline" onClick={search} disabled={loading}>
            <Search className="h-4 w-4" />
          </Button>
        </div>
        <div className="divide-y rounded-lg border">
          {rows.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-muted-foreground">
              {loading ? t('common.loading') : t('members.detail.empty')}
            </p>
          ) : (
            rows.map((tx) => (
              <button
                key={tx.id}
                type="button"
                className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left transition-colors hover:bg-primary/5"
                onClick={() => {
                  onConfirm(tx)
                  onOpenChange(false)
                }}
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">
                    {tx.category_name || tx.note || '—'}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {formatIsoDateTimeLocal(tx.happened_at)}
                    {tx.account_name ? ` · ${tx.account_name}` : ''}
                  </span>
                </span>
                <span className="shrink-0 font-mono tabular-nums text-sm font-semibold">
                  {formatBalanceCompact(Number(tx.amount || 0), tx.currency_code || undefined)}
                </span>
              </button>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function SavingPlansPage() {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId, ledgers, setActiveLedgerId, refreshLedgers, currency } = useLedgers()
  // 若当前账本 id 无效(例如 localStorage 缓存了旧 id),自动切换到第一个有效账本
  // 优先使用有效的账本 id；localStorage 中的 stale id 直接丢弃。
  // 注意:read 接口的路径参数是 ledger **external_id**(形如 ledger_xxx),
  // 这里不能用内部 UUID 兜底 —— 否则 fetchReadAccounts 会 404,
  // 存入弹窗的「扣款账户」下拉就会是空的(表现为"打不开/选不了账户")。
  const validLedgerId = (ledgers.find(l => l.ledger_id === activeLedgerId)?.ledger_id)
    || (ledgers[0]?.ledger_id)
    || activeLedgerId
    || ''

  const [plans, setPlans] = useState<SavingPlan[]>([])
  const plansRef = useRef<SavingPlan[]>(plans)
  useEffect(() => { plansRef.current = plans }, [plans])
  const [loading, setLoading] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [detailPlan, setDetailPlan] = useState<SavingPlan | null>(null)
  // 关联记录跳转(0043):?plan_id=xxx → 自动打开该储蓄罐详情。
  const [searchParams] = useSearchParams()
  const presetPlanId = searchParams.get('plan_id')
  // 存入成功后自增,强制详情弹窗重新拉取存入记录。
  const [detailRefreshKey, setDetailRefreshKey] = useState(0)
  const [error, setError] = useState<string | null>(null)
  // 状态筛选('all' = 全部)
  const [statusFilter, setStatusFilter] = useState<PlanStatus | 'all'>('all')
  // 删除确认
  const [deleteTarget, setDeleteTarget] = useState<SavingPlan | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  // 扣款账户候选(存入一笔时选,自动生成支出交易)
  const [accounts, setAccounts] = useState<{ id: string; name: string }[]>([])

  // create form
  const [name, setName] = useState('')
  const [target, setTarget] = useState('')
  const [deadline, setDeadline] = useState('')
  const [planType, setPlanType] = useState<PlanType>('free')
  const [monthlyAmount, setMonthlyAmount] = useState('')
  const [accountId, setAccountId] = useState('')
  const [accountName, setAccountName] = useState('')
  const [note, setNote] = useState('')
  // 编辑模式:非 null 表示正在编辑已有计划(与新建共用同一个表单弹窗)。
  const [editingPlan, setEditingPlan] = useState<SavingPlan | null>(null)
  const [savingPlanBusy, setSavingPlanBusy] = useState(false)

  // 存入记录(contribution)编辑 / 删除
  const [contribEditOpen, setContribEditOpen] = useState(false)
  const [contribEditTarget, setContribEditTarget] = useState<SavingContribution | null>(null)
  const [contribEditAmount, setContribEditAmount] = useState('')
  const [contribEditAt, setContribEditAt] = useState('')
  const [contribEditNote, setContribEditNote] = useState('')
  const [contribEditCategory, setContribEditCategory] = useState('')
  const [contribEditExcludeStats, setContribEditExcludeStats] = useState(false)
  const [contribEditExcludeBudget, setContribEditExcludeBudget] = useState(false)
  const [contribEditBusy, setContribEditBusy] = useState(false)
  // 删除存入记录确认
  const [contribDeleteTarget, setContribDeleteTarget] = useState<SavingContribution | null>(null)
  const [contribDeleteBusy, setContribDeleteBusy] = useState(false)

  // contribute form
  const [contributeOpen, setContributeOpen] = useState(false)
  const [contributePlan, setContributePlan] = useState<SavingPlan | null>(null)
  const [contributeAmount, setContributeAmount] = useState('')
  const [contributeNote, setContributeNote] = useState('')
  // 存入时间(datetime-local,秒精度)→ 提交时转 UTC ISO
  const [contributeAt, setContributeAt] = useState('')
  // 扣款账户:空串 = 不自动扣款,仅记录存入
  const [contributeAccountId, setContributeAccountId] = useState('')
  // 收款账户:空串 = 不生成收入交易
  const [contributeReceiveAccountId, setContributeReceiveAccountId] = useState('')
  // 记账方式(0057):new = 新建流水;existing = 关联一笔已存在的流水。
  const [contributeMode, setContributeMode] = useState<'new' | 'existing'>('new')
  // 关联已有交易时选中的那笔流水。
  const [contributeExistingTx, setContributeExistingTx] = useState<WorkspaceTransaction | null>(null)
  const [txPickerOpen, setTxPickerOpen] = useState(false)
  const [contributeCategory, setContributeCategory] = useState('')
  const [contributeExcludeStats, setContributeExcludeStats] = useState(false)
  const [contributeExcludeBudget, setContributeExcludeBudget] = useState(false)
  const [contributeFiles, setContributeFiles] = useState<File[]>([])
  const [contributeBusy, setContributeBusy] = useState(false)

  const load = useCallback(async () => {
    if (!token) return
    // 修复 stale 账本 id:若当前 activeLedgerId 不在 ledgers 列表中,切换到第一个有效账本并等待重渲染
    if (ledgers.length > 0 && activeLedgerId && !ledgers.some(l => l.ledger_id === activeLedgerId)) {
      try { Object.keys(localStorage).filter(k => k.startsWith('beecount.active-ledger.')).forEach(k => localStorage.removeItem(k)) } catch {}
      setActiveLedgerId(ledgers[0].ledger_id)
      return
    }
    const lid = validLedgerId
    if (!lid) {
      // ledgers 尚未加载,触发拉取后等待
      refreshLedgers().catch(() => {})
      return
    }
    setLoading(true)
    try {
      const rows = await fetchSavingPlans(token, lid)
      setPlans(rows)
      // 详情弹窗开着时同步刷新它 —— 否则改/删存入记录后,弹窗头部的
      // 「已存金额 / 还差金额 / 进度」还是旧值(它们来自 plan.saved_amount)。
      setDetailPlan((prev) => {
        if (!prev) return prev
        return rows.find((p) => p.id === prev.id) ?? prev
      })
      setError(null)
    } catch (e: any) {
      setError(localizeError(e, t))
    } finally {
      setLoading(false)
    }
  }, [token, activeLedgerId, ledgers, validLedgerId, setActiveLedgerId, refreshLedgers, t])

  useEffect(() => {
    // 清理 localStorage 中缓存的 stale 账本 id
    try { Object.keys(localStorage).filter(k => k.startsWith('beecount.active-ledger.')).forEach(k => localStorage.removeItem(k)) } catch {}
    // 确保 ledgers 已加载
    if (ledgers.length === 0) {
      refreshLedgers().then(() => load()).catch(() => {})
    } else {
      load()
    }
  }, [load, ledgers.length, refreshLedgers])

  // 扣款账户候选:存入一笔时选,选定即自动生成支出交易并扣减该账户
  useEffect(() => {
    if (!token || !validLedgerId) return
    let cancelled = false
    fetchReadAccounts(token, validLedgerId)
      .then((rows) => {
        if (cancelled) return
        setAccounts(rows.map((a) => ({ id: a.id, name: a.name })))
      })
      .catch(() => {
        if (!cancelled) setAccounts([])
      })
    return () => { cancelled = true }
  }, [token, validLedgerId])

  const resetForm = () => {
    setName('')
    setTarget('')
    setDeadline('')
    setPlanType('free')
    setMonthlyAmount('')
    setNote('')
  }

  /** 打开「新建计划」弹窗(清空表单)。 */
  const openCreate = () => {
    setEditingPlan(null)
    resetForm()
    setDialogOpen(true)
  }

  /** 打开「编辑计划」弹窗:把已有计划的值填进表单。 */
  const openEditPlan = (plan: SavingPlan) => {
    setEditingPlan(plan)
    setName(plan.name)
    setTarget(String(plan.target_amount))
    setDeadline(plan.deadline ? String(plan.deadline).slice(0, 10) : '')
    setPlanType(plan.plan_type)
    setMonthlyAmount(plan.monthly_amount ? String(plan.monthly_amount) : '')
    setAccountId(plan.account_id || '')
    setAccountName(plan.account_name || '')
    setNote(plan.note || '')
    setDialogOpen(true)
  }

  const handleSavePlan = async () => {
    if (!token || !name.trim() || !Number(target)) return
    setSavingPlanBusy(true)
    try {
      if (editingPlan) {
        // 编辑模式:只传要改的字段(PATCH 语义)。
        // monthly_amount / note 传 null 显式清空(后端 PATCH 用 None 覆盖)。
        const updated = await updateSavingPlan(token, editingPlan.id, {
          name: name.trim(),
          target_amount: Number(target),
          deadline: deadline ? new Date(deadline).toISOString() : null,
          plan_type: planType,
          monthly_amount: monthlyAmount ? Number(monthlyAmount) : null,
          account_id: accountId || null,
          account_name: accountName || null,
          note: note.trim() || null,
        })
        // 详情弹窗开着时同步刷新它(否则头部/统计还是旧值)。
        if (detailPlan?.id === updated.id) setDetailPlan(updated)
      } else {
        await createSavingPlan(token, {
          ledger_id: validLedgerId,
          name: name.trim(),
          target_amount: Number(target),
          deadline: deadline ? new Date(deadline).toISOString() : null,
          plan_type: planType,
          monthly_amount: monthlyAmount ? Number(monthlyAmount) : null,
          account_id: accountId || null,
          account_name: accountName || null,
          note: note.trim() || null,
        })
      }
      setDialogOpen(false)
      setEditingPlan(null)
      resetForm()
      load()
    } catch (e) {
      setError(localizeError(e, t))
    } finally {
      setSavingPlanBusy(false)
    }
  }

  /** 打开「编辑存入记录」弹窗:预填金额/时间/备注/分类/统计开关。 */
  const openEditContribution = (c: SavingContribution) => {
    setContribEditTarget(c)
    setContribEditAmount(String(c.amount))
    setContribEditAt(toLocalDateTimeInput(c.happened_at))
    setContribEditNote(c.note || '')
    setContribEditCategory(c.category_name || '')
    setContribEditExcludeStats(Boolean(c.exclude_from_stats))
    setContribEditExcludeBudget(Boolean(c.exclude_from_budget))
    setContribEditOpen(true)
  }

  /** 提交存入记录修改。 */
  const handleSaveContribution = async () => {
    if (!token || !contribEditTarget || !Number(contribEditAmount)) return
    setContribEditBusy(true)
    try {
      await updateContribution(token, contribEditTarget.id, {
        amount: Number(contribEditAmount),
        happened_at: fromLocalDateTimeInput(contribEditAt),
        note: contribEditNote.trim(),
        category_name: contribEditCategory.trim(),
        exclude_from_stats: contribEditExcludeStats,
        exclude_from_budget: contribEditExcludeBudget,
      })
      setContribEditOpen(false)
      setContribEditTarget(null)
      // 刷新详情弹窗里的存入记录 + 计划已存金额/进度。
      setDetailRefreshKey((k) => k + 1)
      await load()
    } catch (e) {
      setError(localizeError(e, t))
    } finally {
      setContribEditBusy(false)
    }
  }

  /** 删除一笔存入记录(后端会一并删掉它生成的交易流水)。 */
  const handleDeleteContribution = async () => {
    if (!token || !contribDeleteTarget) return
    setContribDeleteBusy(true)
    try {
      await deleteContribution(token, contribDeleteTarget.id)
      setContribDeleteTarget(null)
      setDetailRefreshKey((k) => k + 1)
      await load()
    } catch (e) {
      setError(localizeError(e, t))
    } finally {
      setContribDeleteBusy(false)
    }
  }

  const openDetail = (plan: SavingPlan) => {
    setDetailPlan(plan)
  }

  /** 按 id 打开储蓄罐详情(交易详情「关联记录」跳回时用)。列表未就绪时直接拉接口。 */
  const openDetailById = useCallback(
    (id: string) => {
      if (!id) return
      const found = plansRef.current.find((p) => p.id === id)
      if (found) {
        setDetailPlan(found)
        return
      }
      if (!token || !validLedgerId) return
      // 列表尚未加载完成 → 直接按账本拉一次,避免只靠轮询等列表就绪。
      void fetchSavingPlans(token, validLedgerId)
        .then((rows) => {
          const hit = rows.find((p) => p.id === id)
          if (hit) {
            setPlans(rows)
            setDetailPlan(hit)
          }
        })
        .catch(() => undefined)
    },
    [token, validLedgerId],
  )

  // 关联记录跳转(0043):拉起指定储蓄罐的详情。
  const openedPlanRef = useRef<string | null>(null)
  useEffect(() => {
    if (!token || !presetPlanId || !validLedgerId) return
    if (openedPlanRef.current === presetPlanId) return
    openedPlanRef.current = presetPlanId
    void fetchSavingPlans(token, validLedgerId)
      .then((rows) => {
        const hit = rows.find((p) => p.id === presetPlanId)
        if (hit) return openDetail(hit)
        return undefined
      })
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, presetPlanId, validLedgerId])

  // 交易详情「关联记录」跳回储蓄罐(同页兜底;跨页走上面的 URL 参数通道)。
  useEffect(() => {
    return onOpenDetailSavingPlan((id) => openDetailById(id))
  }, [openDetailById])

  /** 打开「存入一笔」对话框:预填时间与扣款账户(计划已绑账户时优先)。 */
  const openContribute = (plan: SavingPlan) => {
    setContributePlan(plan)
    setContributeAmount('')
    setContributeNote('')
    setContributeAt(toLocalDateTimeInput(new Date().toISOString()))
    setContributeAccountId(plan.account_id || '')
    setContributeReceiveAccountId('')
    setContributeCategory('')
    setContributeExcludeStats(false)
    setContributeExcludeBudget(false)
    setContributeFiles([])
    setContributeMode('new')
    setContributeExistingTx(null)
    setContributeOpen(true)
  }

  const handleContribute = async () => {
    if (!token || !contributePlan || !Number(contributeAmount)) return
    setContributeBusy(true)
    try {
      // 1) 先上传附件,拿到 file_id / sha256 再随交易一起落库
      const attachments: Record<string, unknown>[] = []
      for (let i = 0; i < contributeFiles.length; i++) {
        const out = await uploadAttachment(token, {
          ledger_id: validLedgerId,
          file: contributeFiles[i],
        })
        attachments.push({
          fileName: out.file_name,
          originalName: out.file_name,
          fileSize: out.size,
          sortOrder: i,
          cloudFileId: out.file_id,
          cloudSha256: out.sha256,
        })
      }

      // 2) 提交存入记录
      //    - 关联已有交易(existing):传 tx_sync_id,后端直接绑定那笔既有流水,
      //      不再生成新流水;金额以那笔流水为准。
      //    - 新建流水(new):传账户。后端按「只选转出 / 只选转入 / 两个都选」
      //      分别记一笔支出 / 收入 / **转账**(0057)。
      await contributeToPlan(token, contributePlan.id, {
        amount:
          contributeMode === 'existing' && contributeExistingTx
            ? Number(contributeExistingTx.amount || 0)
            : Number(contributeAmount),
        note: contributeNote.trim() || null,
        happened_at: fromLocalDateTimeInput(contributeAt),
        account_id: contributeMode === 'existing' ? null : contributeAccountId || null,
        receive_account_id:
          contributeMode === 'existing' ? null : contributeReceiveAccountId || null,
        tx_sync_id:
          contributeMode === 'existing' ? contributeExistingTx?.id ?? null : null,
        category_name: contributeCategory.trim() || null,
        exclude_from_stats: contributeExcludeStats,
        exclude_from_budget: contributeExcludeBudget,
        attachments,
      })
      setContributeOpen(false)
      setContributeAmount('')
      setContributeNote('')
      setContributeFiles([])
      setContributeExistingTx(null)
      const refreshed = await fetchSavingPlans(token, validLedgerId)
      setPlans(refreshed)
      const updated = refreshed.find((p) => p.id === contributePlan.id)
      if (updated && detailPlan?.id === updated.id) {
        setDetailPlan(updated)
        setDetailRefreshKey((k) => k + 1)
      }
    } catch (e) {
      setError(localizeError(e, t))
    } finally {
      setContributeBusy(false)
    }
  }

  const handleComplete = async (plan: SavingPlan) => {
    if (!token) return
    try {
      const updated = await completePlan(token, plan.id)
      // 详情弹窗开着时同步刷新头部状态徽章(否则仍显示「进行中」)。
      if (detailPlan?.id === updated.id) setDetailPlan(updated)
      load()
    } catch (e) {
      setError(localizeError(e, t))
    }
  }

  const handleAbandon = async (plan: SavingPlan) => {
    if (!token) return
    try {
      const updated = await abandonPlan(token, plan.id)
      if (detailPlan?.id === updated.id) setDetailPlan(updated)
      load()
    } catch (e) {
      setError(localizeError(e, t))
    }
  }

  const handleArchive = async (plan: SavingPlan) => {
    if (!token) return
    try {
      await archivePlan(token, plan.id)
      setDetailPlan(null)
      load()
    } catch (e) {
      setError(localizeError(e, t))
    }
  }

  const handleDelete = async () => {
    if (!token || !deleteTarget) return
    setDeleteBusy(true)
    try {
      await deleteSavingPlan(token, deleteTarget.id)
      setDeleteTarget(null)
      setDetailPlan(null)
      load()
    } catch (e) {
      setError(localizeError(e, t))
    } finally {
      setDeleteBusy(false)
    }
  }

  // 根据状态筛选计划
  const visiblePlans = statusFilter === 'all'
    ? plans
    : plans.filter((p) => p.status === statusFilter)

  return (
    <div className="space-y-4">
      <SectionPageHeader
        section="saving-plans"
        title={t('saving.title')}
        subtitle={t('saving.subtitle')}
        actions={
          <Button onClick={openCreate}>
            <Plus className="mr-1 h-4 w-4" />
            {t('saving.button.create')}
          </Button>
        }
      />

      {error && <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</div>}

      {/* 状态筛选 */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">{t('saving.filter.label')}</span>
        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as PlanStatus | 'all')}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('saving.filter.all')}</SelectItem>
            {PLAN_STATUSES.map((s) => (
              <SelectItem key={s} value={s}>
                {t(`saving.status.${s}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {loading && <div className="py-8 text-center text-sm text-muted-foreground">…</div>}

      <div className="grid gap-3 md:grid-cols-2">
        {visiblePlans.map((p) => (
          <div
            key={p.id}
            role="button"
            tabIndex={0}
            className="w-full cursor-pointer rounded-lg border p-4 text-left transition hover:border-primary/50"
            onClick={() => openDetail(p)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                openDetail(p)
              }
            }}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="flex min-w-0 items-center gap-2 font-medium">
                <Target className="h-4 w-4 shrink-0" />
                <span className="truncate">{p.name}</span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                <span className={`text-xs ${statusColor(p.status)}`}>{t(`saving.status.${p.status}`)}</span>
                <button
                  type="button"
                  aria-label={t('saving.button.edit') as string}
                  title={t('saving.button.edit') as string}
                  className="rounded p-1 text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary"
                  onClick={(e) => {
                    e.stopPropagation()
                    openEditPlan(p)
                  }}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
              </span>
            </div>
            <div className="mt-2 flex items-baseline justify-between text-sm">
              <span className="font-mono">{p.saved_amount.toLocaleString()}</span>
              <span className="text-muted-foreground">/ {p.target_amount.toLocaleString()}</span>
            </div>
            <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${Math.min(100, p.progress)}%` }}
              />
            </div>
            <div className="mt-1 flex items-center justify-between text-xs text-muted-foreground">
              <span>{p.progress}%</span>
              {p.deadline && <span>{new Date(p.deadline).toLocaleDateString()}</span>}
            </div>
          </div>
        ))}
      </div>

      {!loading && visiblePlans.length === 0 && (
        <div className="rounded-lg border border-dashed py-12 text-center">
          <PiggyBank className="mx-auto h-10 w-10 text-muted-foreground/40" />
          <p className="mt-3 text-sm text-muted-foreground">{t('saving.empty')}</p>
          <p className="mt-1 text-xs text-muted-foreground/70">{t('saving.emptyHint')}</p>
        </div>
      )}

      {/* 新建 / 编辑计划(共用同一个弹窗,editingPlan 非 null = 编辑模式) */}
      <Dialog
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open)
          if (!open) setEditingPlan(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editingPlan ? t('saving.button.edit') : t('saving.button.create')}
            </DialogTitle>
            <DialogDescription>{t('saving.subtitle')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>{t('saving.field.name')}</Label>
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div>
              <Label>{t('saving.field.target')}</Label>
              <Input type="number" value={target} onChange={(e) => setTarget(e.target.value)} />
            </div>
            <div>
              <Label>{t('saving.field.deadline')}</Label>
              <DatePicker
  value={deadline}
  onChange={(v) => setDeadline(v)}
  label={t('saving.field.deadline')}
/>
            </div>
            <div>
              <Label>{t('saving.field.planType')}</Label>
              <Select value={planType} onValueChange={(v) => setPlanType(v as PlanType)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PLAN_TYPES.map((tp) => (
                    <SelectItem key={tp} value={tp}>
                      {t(`saving.type.${tp}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {planType === 'fixed' && (
              <div>
                <Label>{t('saving.field.monthlyAmount')}</Label>
                <Input type="number" value={monthlyAmount} onChange={(e) => setMonthlyAmount(e.target.value)} />
              </div>
            )}
            <div>
              <Label>{t('saving.field.note')}</Label>
              <Textarea value={note} onChange={(e) => setNote(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setDialogOpen(false)
                setEditingPlan(null)
              }}
            >
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => void handleSavePlan()}
              disabled={!name.trim() || !Number(target) || savingPlanBusy}
            >
              {t('common.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 存入一笔 */}
      <Dialog open={contributeOpen} onOpenChange={setContributeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('saving.button.contribute')}</DialogTitle>
            <DialogDescription>{contributePlan?.name}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            {/* 记账方式(0057):新建流水 / 关联已有交易。 */}
            <div>
              <Label>{t('saving.contribute.mode')}</Label>
              <Select
                value={contributeMode}
                onValueChange={(v) => {
                  setContributeMode(v as 'new' | 'existing')
                  if (v === 'existing') {
                    // 关联已有交易时不再选账户(避免重复记账)。
                    setContributeAccountId('')
                    setContributeReceiveAccountId('')
                  }
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="new">{t('saving.contribute.modeNew')}</SelectItem>
                  <SelectItem value="existing">{t('saving.contribute.modeExisting')}</SelectItem>
                </SelectContent>
              </Select>
              <p className="mt-1 text-xs text-muted-foreground">{t('saving.contribute.modeHint')}</p>
            </div>

            {contributeMode === 'existing' ? (
              <>
                <div>
                  <Label>{t('saving.contribute.existingPicked')}</Label>
                  {contributeExistingTx ? (
                    <div className="rounded-md border border-border bg-muted/30 px-3 py-2">
                      <div className="truncate text-sm font-medium">
                        {contributeExistingTx.category_name || contributeExistingTx.note || '—'}
                      </div>
                      <div className="mt-0.5 truncate text-xs text-muted-foreground">
                        {formatIsoDateTimeLocal(contributeExistingTx.happened_at)}
                        {contributeExistingTx.account_name ? ` · ${contributeExistingTx.account_name}` : ''}
                        {' · '}
                        {formatBalanceCompact(
                          Number(contributeExistingTx.amount || 0),
                          contributeExistingTx.currency_code || undefined,
                        )}
                      </div>
                    </div>
                  ) : (
                    <p className="rounded-md border border-dashed border-border px-3 py-2 text-sm text-muted-foreground">
                      {t('saving.contribute.existingEmpty')}
                    </p>
                  )}
                  <div className="mt-2 flex gap-2">
                    <Button variant="outline" onClick={() => setTxPickerOpen(true)}>
                      <Search className="mr-1 h-4 w-4" />
                      {t('saving.contribute.pickExisting')}
                    </Button>
                    {contributeExistingTx ? (
                      <Button variant="ghost" onClick={() => setContributeExistingTx(null)}>
                        {t('confirm.cancel')}
                      </Button>
                    ) : null}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t('saving.contribute.existingHint')}
                    {contributeExistingTx ? ` · ${t('saving.contribute.existingAmountFromTx')}` : ''}
                  </p>
                </div>
                <div>
                  <Label>{t('saving.field.note')}</Label>
                  <Input value={contributeNote} onChange={(e) => setContributeNote(e.target.value)} />
                </div>
              </>
            ) : (
              <>
            <div>
              <Label>{t('saving.field.target')}</Label>
              <Input type="number" value={contributeAmount} onChange={(e) => setContributeAmount(e.target.value)} />
            </div>
            <div>
              <Label>{t('saving.contribute.time')}</Label>
              <Input type="datetime-local" step="1" value={contributeAt} onChange={(e) => setContributeAt(e.target.value)} />
            </div>
            <div>
              <Label>{t('saving.contribute.debitAccount')}</Label>
              <Select value={contributeAccountId || 'none'} onValueChange={(v) => setContributeAccountId(v === 'none' ? '' : v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t('saving.contribute.debitAccountNone')}</SelectItem>
                  {accounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="mt-1 text-xs text-muted-foreground">{t('saving.contribute.debitAccountHint')}</p>
            </div>
            <div>
              <Label>{t('saving.contribute.receiveAccount')}</Label>
              <Select
                value={contributeReceiveAccountId || 'none'}
                onValueChange={(v) => setContributeReceiveAccountId(v === 'none' ? '' : v)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t('saving.contribute.receiveAccountNone')}</SelectItem>
                  {accounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="mt-1 text-xs text-muted-foreground">{t('saving.contribute.receiveAccountHint')}</p>
            </div>
            <p className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
              {t('saving.contribute.txHint')}
            </p>
            {/* 双账户 = 一笔转账(0057):明确告诉用户不会拆成支出+收入。 */}
            {contributeAccountId && contributeReceiveAccountId ? (
              <p className="rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-xs text-primary">
                {t('saving.contribute.transferHint')}
              </p>
            ) : null}
            <div>
              <Label>{t('saving.contribute.category')}</Label>
              <Input
                value={contributeCategory}
                onChange={(e) => setContributeCategory(e.target.value)}
                placeholder="储蓄"
              />
            </div>
            <div>
              <Label>{t('saving.field.note')}</Label>
              <Input value={contributeNote} onChange={(e) => setContributeNote(e.target.value)} />
            </div>
            <div>
              <Label>{t('saving.contribute.attachments')}</Label>
              <input
                type="file"
                accept="image/*"
                multiple
                onChange={(e) => setContributeFiles(Array.from(e.target.files ?? []))}
                className="block w-full text-sm text-muted-foreground file:mr-3 file:rounded-md file:border file:border-border file:bg-background file:px-3 file:py-1.5 file:text-sm file:font-medium"
              />
              <p className="mt-1 text-xs text-muted-foreground">{t('saving.contribute.attachmentsHint')}</p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={contributeExcludeStats}
                onChange={(e) => setContributeExcludeStats(e.target.checked)}
                className="h-4 w-4 rounded border-border"
              />
              {t('saving.contribute.excludeFromStats')}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={contributeExcludeBudget}
                onChange={(e) => setContributeExcludeBudget(e.target.checked)}
                className="h-4 w-4 rounded border-border"
              />
              {t('saving.contribute.excludeFromBudget')}
            </label>
              </>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setContributeOpen(false)} disabled={contributeBusy}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={handleContribute}
              disabled={
                contributeBusy ||
                (contributeMode === 'existing'
                  ? !contributeExistingTx
                  : !Number(contributeAmount))
              }
            >
              {contributeBusy ? t('saving.contribute.uploading') : t('common.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 计划详情 —— 头部固定 / 中间独立滚动 / 底部操作栏固定(与联系人详情同款版式)。
          统计维度为「目标」口径(目标/已存/还差/存入笔数),与联系人的「人」口径不同。
          存入记录下的关联交易可点开原始流水,交易详情的关联记录又能跳回本弹窗。 */}
      <SavingPlanDetailDialog
        open={!!detailPlan}
        plan={detailPlan}
        currency={currency}
        ledgerId={validLedgerId}
        refreshKey={detailRefreshKey}
        onOpenChange={(v) => !v && setDetailPlan(null)}
        onContribute={openContribute}
        onComplete={(plan) => void handleComplete(plan)}
        onAbandon={(plan) => void handleAbandon(plan)}
        onArchive={(plan) => void handleArchive(plan)}
        onDelete={(plan) => setDeleteTarget(plan)}
        onEditPlan={(plan) => {
          // 打开编辑弹窗前先关详情,避免两个弹窗叠在一起。
          setDetailPlan(null)
          openEditPlan(plan)
        }}
        onEditContribution={(c) => openEditContribution(c)}
        onDeleteContribution={(c) => setContribDeleteTarget(c)}
      />

      {/* 关联已有交易的选择器(单选)。 */}
      <ExistingTxPicker
        open={txPickerOpen}
        onOpenChange={setTxPickerOpen}
        token={token || ''}
        ledgerId={validLedgerId}
        onConfirm={(tx) => {
          setContributeExistingTx(tx)
          if (tx) {
            // 金额以那笔流水为准,同步到表单(后端也会用流水金额派生)。
            setContributeAmount(String(Number(tx.amount || 0)))
            setContributeAt(toLocalDateTimeInput(tx.happened_at))
          }
        }}
      />

      {/* 编辑存入记录 */}
      <Dialog open={contribEditOpen} onOpenChange={setContribEditOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('saving.contribution.edit')}</DialogTitle>
            <DialogDescription>{contribEditTarget?.note || t('saving.contributions')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>{t('saving.field.target')}</Label>
              <Input
                type="number"
                value={contribEditAmount}
                onChange={(e) => setContribEditAmount(e.target.value)}
              />
            </div>
            <div>
              <Label>{t('saving.contribute.time')}</Label>
              <Input
                type="datetime-local"
                step="1"
                value={contribEditAt}
                onChange={(e) => setContribEditAt(e.target.value)}
              />
            </div>
            <div>
              <Label>{t('saving.contribute.category')}</Label>
              <Input
                value={contribEditCategory}
                onChange={(e) => setContribEditCategory(e.target.value)}
              />
            </div>
            <div>
              <Label>{t('saving.field.note')}</Label>
              <Input value={contribEditNote} onChange={(e) => setContribEditNote(e.target.value)} />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={contribEditExcludeStats}
                onChange={(e) => setContribEditExcludeStats(e.target.checked)}
                className="h-4 w-4 rounded border-border"
              />
              {t('saving.contribute.excludeFromStats')}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={contribEditExcludeBudget}
                onChange={(e) => setContribEditExcludeBudget(e.target.checked)}
                className="h-4 w-4 rounded border-border"
              />
              {t('saving.contribute.excludeFromBudget')}
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setContribEditOpen(false)} disabled={contribEditBusy}>
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => void handleSaveContribution()}
              disabled={!Number(contribEditAmount) || contribEditBusy}
            >
              {t('common.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除存入记录确认 */}
      <Dialog
        open={!!contribDeleteTarget}
        onOpenChange={(open) => !open && !contribDeleteBusy && setContribDeleteTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('saving.contribution.deleteConfirm.title')}</DialogTitle>
            <DialogDescription>{t('saving.contribution.deleteConfirm.desc')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setContribDeleteTarget(null)}
              disabled={contribDeleteBusy}
            >
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleDeleteContribution()}
              disabled={contribDeleteBusy}
            >
              <Trash2 className="mr-1 h-4 w-4" />
              {t('saving.button.deleteConfirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <Dialog open={!!deleteTarget} onOpenChange={(open) => !open && !deleteBusy && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('saving.confirmDelete.title')}</DialogTitle>
            <DialogDescription>{t('saving.confirmDelete.desc')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={deleteBusy}>
              {t('common.cancel')}
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleteBusy}>
              <Trash2 className="mr-1 h-4 w-4" />
              {t('saving.button.deleteConfirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
