import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createBudget,
  deleteBudget,
  fetchReadBudgets,
  fetchWorkspaceCategories,
  fetchWorkspaceTransactions,
  updateBudget,
  type ReadBudget,
  type WorkspaceCategory,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import {
  BudgetsPanel,
  budgetDefaults,
  type BudgetForm,
  type BudgetUsage,
} from '@beecount/web-features'

import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

/**
 * 预算页 —— budgets 按账本取(账本级实体);categories 是 user-global,
 * 跨账本共享。新增字段:
 *   - 用 fetchWorkspaceTransactions 拉本预算周期内 expense 累加 used
 *   - 调 createBudget / updateBudget / deleteBudget(对齐 mobile 能力)
 */

/**
 * 给定 startDay,算出当前期间的 [start, end)。仅支持 monthly(其他 period
 * 现在 mobile 没真用,默认值 monthly,先按 monthly 算 used)。
 */
function currentMonthRange(startDay: number, now = new Date()): { start: Date; end: Date } {
  const day = Math.max(1, Math.min(28, Math.round(startDay || 1)))
  // 当天 < startDay → 期间是上个月 startDay 到本月 startDay
  // 当天 >= startDay → 期间是本月 startDay 到下个月 startDay
  const today = now.getDate()
  let start: Date
  let end: Date
  if (today >= day) {
    start = new Date(now.getFullYear(), now.getMonth(), day, 0, 0, 0, 0)
    end = new Date(now.getFullYear(), now.getMonth() + 1, day, 0, 0, 0, 0)
  } else {
    start = new Date(now.getFullYear(), now.getMonth() - 1, day, 0, 0, 0, 0)
    end = new Date(now.getFullYear(), now.getMonth(), day, 0, 0, 0, 0)
  }
  return { start, end }
}

export function BudgetsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency } = useLedgers()
  const { previewMap: iconPreviewByFileId, ensureLoadedMany } = useAttachmentCache()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const bucket = activeLedgerId || '__none__'
  const [budgets, setBudgets] = usePageCache<ReadBudget[]>(`budgets:${bucket}:rows`, [])
  const [categories, setCategories] = usePageCache<WorkspaceCategory[]>(
    'budgets:categories',
    [],
  )
  const [usageById, setUsageById] = useState<Record<string, BudgetUsage | undefined>>({})
  const [form, setForm] = useState<BudgetForm>(budgetDefaults())

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t],
  )

  /**
   * 拉本期间 expense 交易,按 budget 分组累加 used。
   * - total budget:全部 expense 累加(可选 ledger filter)
   * - category budget:按 category_sync_id 过滤
   *
   * 这里走"按 budget 各自周期分别 fetch"的简化策略,因为 mobile 也是 per-budget
   * 算 usage(repository.getBudgetUsage),不复用同一份 tx。budgets 数量通常很少
   * (1 个 total + 几个 category),fetch 数次问题不大。
   */
  const refreshUsages = useCallback(
    async (budgetRows: ReadBudget[], catRows: WorkspaceCategory[]) => {
      if (!activeLedgerId || budgetRows.length === 0) {
        setUsageById({})
        return
      }
      const next: Record<string, BudgetUsage> = {}
      // 并行 fetch 各预算 used。失败的桶 used=0,不阻塞其它。
      await Promise.all(
        budgetRows.map(async (b) => {
          try {
            const startDay = Math.max(1, Math.min(28, Number(b.start_day || 1)))
            const { start, end } = currentMonthRange(startDay)
            // category 预算需要 categorySyncId 过滤,total 不传分类。
            const categorySyncId = b.type === 'category' ? b.category_id || undefined : undefined
            // expense type only。fetchWorkspaceTransactions 已支持
            // amount range / date range / categorySyncId。
            const page = await fetchWorkspaceTransactions(token, {
              ledgerId: activeLedgerId,
              txType: 'expense',
              categorySyncId,
              dateFrom: start.toISOString(),
              dateTo: end.toISOString(),
              limit: 1000, // 单期间一般 < 1000 条;超出再分页
            })
            const used = page.items.reduce((acc, tx) => acc + Math.abs(Number(tx.amount || 0)), 0)
            next[b.id] = { used }
          } catch (_err) {
            next[b.id] = { used: 0 }
          }
        }),
      )
      setUsageById(next)
    },
    [token, activeLedgerId],
  )

  const refresh = useCallback(async () => {
    if (!activeLedgerId) {
      setBudgets([])
      setUsageById({})
      return
    }
    try {
      const [b, c] = await Promise.all([
        fetchReadBudgets(token, activeLedgerId),
        fetchWorkspaceCategories(token, {}),
      ])
      setBudgets(b)
      setCategories(c)
      void refreshUsages(b, c)
    } catch (err) {
      notifyError(err)
    }
    // setBudgets / setCategories 来自 usePageCache,引用稳定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, notifyError, refreshUsages])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  useEffect(() => {
    const ids = categories
      .map((c) => c.icon_cloud_file_id || '')
      .filter((v) => v.trim().length > 0)
    if (ids.length > 0) ensureLoadedMany(ids)
  }, [categories, ensureLoadedMany])

  // 总预算的"日均可用 / 剩余天数",对齐 mobile budget_page.dart 算法。
  const totalSummary = useMemo(() => {
    const total = budgets.find((b) => b.type === 'total')
    if (!total) return null
    const startDay = Math.max(1, Math.min(28, Number(total.start_day || 1)))
    const { end } = currentMonthRange(startDay)
    const now = new Date()
    const msPerDay = 1000 * 60 * 60 * 24
    const daysRemaining = Math.max(0, Math.ceil((end.getTime() - now.getTime()) / msPerDay))
    const used = usageById[total.id]?.used ?? 0
    const remaining = Math.max(0, total.amount - used)
    const dailyAvailable = daysRemaining > 0 ? remaining / daysRemaining : 0
    return { daysRemaining, dailyAvailable }
  }, [budgets, usageById])

  const onSubmit = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const amount = Number((form.amount || '').toString().trim())
    if (!Number.isFinite(amount) || amount <= 0) {
      toast.error(t('budgets.error.amountInvalid'), t('notice.error'))
      return false
    }
    if (form.type === 'category' && !form.category_id.trim()) {
      toast.error(t('budgets.error.categoryRequired'), t('notice.error'))
      return false
    }
    const startDay = Math.round(Number(form.start_day || '1'))
    if (!Number.isFinite(startDay) || startDay < 1 || startDay > 28) {
      toast.error(t('budgets.error.startDayInvalid'), t('notice.error'))
      return false
    }
    try {
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateBudget(token, activeLedgerId, form.editingId!, base, {
            amount,
            period: form.period,
            start_day: startDay,
          }),
        )
        notifySuccess(t('budgets.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createBudget(token, activeLedgerId, base, {
            type: form.type,
            category_id: form.type === 'category' ? form.category_id : null,
            amount,
            period: form.period,
            start_day: startDay,
          }),
        )
        notifySuccess(t('budgets.notice.created'))
      }
      setForm(budgetDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onDelete = async (budget: ReadBudget): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteBudget(token, activeLedgerId, budget.id, base),
      )
      notifySuccess(t('budgets.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('nav.budgets')}</CardTitle>
      </CardHeader>
      <CardContent>
        {!activeLedgerId ? (
          <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
        ) : (
          <BudgetsPanel
            budgets={budgets}
            categories={categories}
            usageById={usageById}
            iconPreviewUrlByFileId={iconPreviewByFileId}
            currency={currency}
            form={form}
            onFormChange={setForm}
            onSubmit={onSubmit}
            onDelete={onDelete}
            canManage={Boolean(activeLedgerId)}
            totalSummary={totalSummary}
          />
        )}
      </CardContent>
    </Card>
  )
}
