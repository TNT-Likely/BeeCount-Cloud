import { useMemo, useState } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  useT,
} from '@beecount/ui'

import type { ReadBudget, WorkspaceCategory } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { CategoryIcon } from '../components/CategoryIcon'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { BudgetForm } from '../forms'
import { budgetDefaults } from '../forms'

/**
 * 单条预算的"已用 / 剩余"信息。BudgetsPanel 自己不查交易,这两个数走外部
 * 计算后塞进来 —— page 层用 fetchWorkspaceTransactions 按 budget 周期 + 分类
 * 过滤累加 expense,然后 reduce 成 map 传下来。null = 未计算或无数据。
 */
export type BudgetUsage = {
  used: number
}

type BudgetsPanelProps = {
  budgets: readonly ReadBudget[]
  /** 全量分类(workspace),用来根据 category_id 解析名称/图标。 */
  categories: readonly WorkspaceCategory[]
  /** budgetId → 已用金额。空缺视为 0。 */
  usageById: Record<string, BudgetUsage | undefined>
  /** 自定义图标预签 URL 表(从全局 AttachmentCache 拿)。 */
  iconPreviewUrlByFileId?: Record<string, string>
  /** 当前账本币种,用于 Amount 渲染。 */
  currency: string
  /** 当前 form 状态(由 page 层管理,提交后回填 budgetDefaults)。 */
  form: BudgetForm
  onFormChange: (next: BudgetForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onDelete: (budget: ReadBudget) => Promise<void> | void
  /** 当前是否处于无账本可操作状态(没选账本)。控制按钮 disabled。 */
  canManage: boolean
  /** 月剩余天数 / 日均可用 — 由 page 层计算后塞进来,匹配 mobile budget_page 的
   *  "本月还剩 X 天 · 日均可用 ¥Y" 文案。null = 不显示。 */
  totalSummary?: { daysRemaining: number; dailyAvailable: number } | null
}

/**
 * 预算管理面板 —— 对齐 mobile budget_page:
 *   - 顶部"添加预算"按钮(弹 dialog 选 total/category)
 *   - 每条预算卡:类型/名称 + 进度条 + 金额 + 已用/剩余 + edit/delete
 *   - 总预算唯一(已存在则 dialog 锁定 total 选项)
 *   - 分类预算只能选 expense kind 顶级分类(对齐 mobile)
 *
 * 进度条颜色按使用率:<70% 绿 / 70-90% 黄 / 90-100% 橙 / >=100% 红,跟 mobile
 * `BudgetProgressBar._getColor` 配色对齐。
 */
export function BudgetsPanel({
  budgets,
  categories,
  usageById,
  iconPreviewUrlByFileId,
  currency,
  form,
  onFormChange,
  onSubmit,
  onDelete,
  canManage,
  totalSummary,
}: BudgetsPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadBudget | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 已存在 total 预算:dialog 进入时如果用户尝试切到 total,禁掉(对齐
  // mobile _hasTotalBudget guard)。允许编辑当前 total budget 自身。
  const totalBudgetExists = useMemo(
    () => budgets.some((b) => b.type === 'total'),
    [budgets],
  )
  // 已被分类预算占用的 categoryId 集合(用于新建分类预算时排除)。
  const usedCategoryIds = useMemo(() => {
    const set = new Set<string>()
    for (const b of budgets) {
      if (b.type === 'category' && b.category_id) set.add(b.category_id)
    }
    return set
  }, [budgets])

  const handleOpenCreate = () => {
    onFormChange({
      ...budgetDefaults(),
      // 已有 total 时默认进入 category 模式
      type: totalBudgetExists ? 'category' : 'total',
    })
    setDialogOpen(true)
  }

  const handleOpenEdit = (budget: ReadBudget) => {
    const cat = budget.category_id
      ? categories.find((c) => c.id === budget.category_id)
      : null
    onFormChange({
      editingId: budget.id,
      type: budget.type === 'category' ? 'category' : 'total',
      category_id: budget.category_id || '',
      category_name: cat?.name || budget.category_name || '',
      amount: String(budget.amount),
      start_day: String(budget.start_day || 1),
      period: (budget.period as BudgetForm['period']) || 'monthly',
    })
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const ok = await onSubmit()
      if (ok) setDialogOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await onDelete(pendingDelete)
      setPendingDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  // 把 category picker 限制成 expense kind + 顶级分类(level=1)+ 未被占用
  // (编辑当前预算时允许保留自身)。对齐 mobile budget_edit_page._selectCategory。
  const categoryPickerRows = useMemo(() => {
    return categories.filter((c) => {
      if (c.kind !== 'expense') return false
      if (Number(c.level) !== 1) return false
      // 编辑模式下保留自身 categoryId,新建则排除已占用
      if (form.editingId && form.category_id === c.id) return true
      if (usedCategoryIds.has(c.id)) return false
      return true
    })
  }, [categories, usedCategoryIds, form.editingId, form.category_id])

  const isCategoryType = form.type === 'category'

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('budgets.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('budgets.button.create')}
        </Button>
      </div>

      {budgets.length === 0 ? (
        <EmptyState
          icon={
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="3" y="6" width="18" height="14" rx="2" />
              <path d="M3 10h18" />
            </svg>
          }
          title={t('budgets.empty')}
          description={t('budgets.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {/* total 卡片单独渲在最上,带"日均可用"hint */}
          {budgets
            .filter((b) => b.type === 'total')
            .map((b) => (
              <BudgetCard
                key={b.id}
                budget={b}
                category={null}
                used={usageById[b.id]?.used ?? 0}
                currency={currency}
                iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                summary={totalSummary || null}
                canManage={canManage}
                onEdit={() => handleOpenEdit(b)}
                onDelete={() => setPendingDelete(b)}
              />
            ))}
          {/* category 预算列表 */}
          {budgets
            .filter((b) => b.type === 'category')
            .map((b) => {
              const cat = b.category_id
                ? categories.find((c) => c.id === b.category_id)
                : null
              return (
                <BudgetCard
                  key={b.id}
                  budget={b}
                  category={cat || null}
                  used={usageById[b.id]?.used ?? 0}
                  currency={currency}
                  iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                  summary={null}
                  canManage={canManage}
                  onEdit={() => handleOpenEdit(b)}
                  onDelete={() => setPendingDelete(b)}
                />
              )
            })}
        </div>
      )}

      {/* 创建/编辑 dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('budgets.button.update') : t('budgets.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            {/* 类型选择 - 编辑模式锁定不可改 */}
            <div className="space-y-1">
              <Label>{t('budgets.field.type')}</Label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  disabled={!!form.editingId || (totalBudgetExists && form.type !== 'total')}
                  onClick={() => onFormChange({ ...form, type: 'total' })}
                  className={[
                    'flex flex-col items-center gap-1 rounded-md border px-3 py-3 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                    form.type === 'total'
                      ? 'border-primary/60 bg-primary/10 text-primary'
                      : 'border-border/60 hover:bg-accent/40',
                  ].join(' ')}
                >
                  <span className="material-symbols-outlined text-2xl">wallet</span>
                  <span>{t('budgets.type.total')}</span>
                </button>
                <button
                  type="button"
                  disabled={!!form.editingId}
                  onClick={() => onFormChange({ ...form, type: 'category' })}
                  className={[
                    'flex flex-col items-center gap-1 rounded-md border px-3 py-3 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                    form.type === 'category'
                      ? 'border-primary/60 bg-primary/10 text-primary'
                      : 'border-border/60 hover:bg-accent/40',
                  ].join(' ')}
                >
                  <span className="material-symbols-outlined text-2xl">category</span>
                  <span>{t('budgets.type.category')}</span>
                </button>
              </div>
            </div>

            {/* 分类选择 - 仅 type=category */}
            {isCategoryType ? (
              <div className="space-y-1">
                <Label>{t('budgets.field.category')}</Label>
                <button
                  type="button"
                  disabled={!!form.editingId}
                  onClick={() => setCategoryPickerOpen(true)}
                  className="flex h-10 w-full items-center justify-between gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <span className={`truncate ${form.category_name ? '' : 'text-muted-foreground'}`}>
                    {form.category_name || t('budgets.placeholder.category')}
                  </span>
                  <span className="text-xs text-muted-foreground opacity-60">▾</span>
                </button>
              </div>
            ) : null}

            <div className="space-y-1">
              <Label>{t('budgets.field.amount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                placeholder="0"
                value={form.amount}
                onChange={(e) => onFormChange({ ...form, amount: e.target.value })}
              />
            </div>

            {/* mobile budget_edit_page 把"周期 / 起始日"两个字段都隐藏了
                (默认 monthly + startDay=1),web 跟着锁定,避免双端 UI 不一致。
                form.start_day 一直保持默认 '1',提交时直接发 1 给 server。 */}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={submitting || !canManage} onClick={() => void handleSubmit()}>
              {form.editingId ? t('budgets.button.update') : t('budgets.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CategoryPickerDialog
        open={categoryPickerOpen}
        onClose={() => setCategoryPickerOpen(false)}
        kind="expense"
        rows={categoryPickerRows}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={form.category_id || undefined}
        title={t('budgets.placeholder.category')}
        onSelect={(cat) =>
          onFormChange({
            ...form,
            category_id: cat.id,
            category_name: cat.name,
          })
        }
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('budgets.delete.title')}
        description={t('budgets.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}

/**
 * 单张预算卡片。total 类型多展示一行"剩余天数 · 日均可用"hint。
 */
function BudgetCard({
  budget,
  category,
  used,
  currency,
  iconPreviewUrlByFileId,
  summary,
  canManage,
  onEdit,
  onDelete,
}: {
  budget: ReadBudget
  category: WorkspaceCategory | null
  used: number
  currency: string
  iconPreviewUrlByFileId?: Record<string, string>
  summary: { daysRemaining: number; dailyAvailable: number } | null
  canManage: boolean
  onEdit: () => void
  onDelete: () => void
}) {
  const t = useT()
  const isTotal = budget.type === 'total'
  const remaining = budget.amount - used
  const ratio = budget.amount > 0 ? Math.min(used / budget.amount, 1) : 0
  // 颜色阈值跟 mobile BudgetProgressBar._getColor 一致
  const barColor =
    ratio >= 1.0
      ? 'bg-red-700'
      : ratio >= 0.9
        ? 'bg-red-500'
        : ratio >= 0.7
          ? 'bg-orange-500'
          : 'bg-green-500'

  const title = isTotal
    ? t('budgets.label.allLedger')
    : category?.name || budget.category_name || t('budgets.label.unknownCategory')

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
        >
          {isTotal ? (
            <span className="material-symbols-outlined text-2xl">wallet</span>
          ) : (
            <CategoryIcon
              icon={category?.icon}
              iconType={category?.icon_type || 'material'}
              iconCloudFileId={category?.icon_cloud_file_id || null}
              iconPreviewUrlByFileId={iconPreviewUrlByFileId}
              size={24}
            />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold">{title}</span>
            {!budget.enabled ? (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {t('budgets.disabled')}
              </span>
            ) : null}
          </div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {t('budgets.period.monthly')}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <Amount value={budget.amount} currency={currency} size="md" bold tone="default" />
        </div>
      </div>

      {/* 进度条 */}
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full transition-all ${barColor}`}
          style={{ width: `${ratio * 100}%` }}
        />
      </div>

      <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {t('budgets.used')}{' '}
          <Amount value={used} currency={currency} size="sm" tone="default" />
        </span>
        <span>
          {t('budgets.remaining')}{' '}
          <Amount
            value={remaining}
            currency={currency}
            size="sm"
            tone={remaining >= 0 ? 'positive' : 'negative'}
          />
        </span>
      </div>

      {/* 总预算特有的"日均可用"hint */}
      {isTotal && summary ? (
        <div className="mt-2 flex items-center justify-between rounded-md bg-muted/40 px-2 py-1.5 text-xs">
          <span className="text-muted-foreground">
            {t('budgets.daysRemaining').replace('{n}', String(summary.daysRemaining))}
          </span>
          <span className="font-medium">
            {t('budgets.dailyAvailable')}{' '}
            <Amount value={summary.dailyAvailable} currency={currency} size="sm" tone="default" />
          </span>
        </div>
      ) : null}

      {/* 操作按钮 */}
      <div className="mt-3 flex items-center justify-end gap-2">
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onEdit}>
          {t('common.edit')}
        </Button>
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onDelete}>
          {t('common.delete')}
        </Button>
      </div>
    </div>
  )
}
