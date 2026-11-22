import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  deleteTransaction,
  fetchFlowLinks,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  type FlowLink,
  type WorkspaceAccount,
  type WorkspaceCategory,
  type WorkspaceTag,
  type WorkspaceTransaction,
} from '@beecount/api-client'
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

import { RefundDialog } from '@beecount/web-features'

import { useLedgerWrite } from '../app/useLedgerWrite'
import { useAttachmentPreview } from '../context/AttachmentPreviewContext'
import { useAttachmentCache } from '../context/AttachmentCacheContext'
import { useAuth } from '../context/AuthContext'
import { useLock } from '../context/LockContext'
import { useLedgers } from '../context/LedgersContext'
import {
  dispatchOpenDetailContact,
  dispatchOpenDetailDeposit,
  dispatchOpenDetailLoan,
  dispatchOpenDetailMember,
  dispatchOpenDetailReimbursement,
  dispatchOpenDetailSavingPlan,
  dispatchOpenDetailTx,
  dispatchOpenEditCategory,
  dispatchOpenEditTx,
  onOpenDetailAccount,
  onOpenDetailCategory,
  onOpenDetailTag,
  onOpenDetailTx,
  type DetailScope,
} from '../lib/txDialogEvents'
import { dispatchTxRefresh } from '../lib/txRefreshEvents'
import { localizeError } from '../i18n/errors'
import { AccountDetailDialog } from './dialogs/AccountDetailDialog'
import { CategoryDetailDialog } from './dialogs/CategoryDetailDialog'
import { TagDetailDialog } from './dialogs/TagDetailDialog'
import { TransactionDetailDialog } from './dialogs/TransactionDetailDialog'

const DETAIL_PAGE_SIZE = 50
// 信用卡详情按「按月 / 按账期」分组汇总,需要一次拉足够多的流水才能算准每组汇总。
// 用 1000(server 硬上限)一次拉完,不再走 50 条无限滚动。
const CREDIT_CARD_DETAIL_LIMIT = 1000
// CategoryDetail 的 stats batch 上限。单账本单分类的交易量上限,950 以下场景
// 拉一次就能拿全;超过则在 UI 提示用户精度被截断。1000 是 server 的硬上限,
// 留 50 缓冲避免边界 off-by-one。
const CATEGORY_STATS_LIMIT = 1000

/**
 * 全局实体详情弹窗容器 — 监听 4 类 detail 事件,在 AppShell 顶层渲染对应弹窗。
 *
 * 之前各 *Page 自己管自己的详情弹窗,导致跨页打开(例如 health 页点 sample
 * 想看交易详情)必须先跳到目标页才能渲染,体验割裂。
 *
 * 集中管理后:
 *  - 任意页点 dispatchOpenDetailX(entity) → 弹窗在当前页面就开
 *  - tx detail 是轻量(只展示字段)直接渲染
 *  - account / category / tag 的弹窗内部带交易列表,这里在事件触发时
 *    懒拉对应交易数据
 *  - 详情 → 编辑链路:点编辑按钮 → 跳到对应 page + 派发 openEditX 事件,
 *    page 端的编辑弹窗接管
 */
export function GlobalEntityDialogs() {
  const navigate = useNavigate()
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { isTxLocked } = useLock()
  const { activeLedgerId, currentLedger, currency: activeCurrency } = useLedgers()
  const { previewMap: iconPreviewByFileId, resolvePreview } = useAttachmentCache()
  const previewAttachment = useAttachmentPreview()
  const { retryOnConflict } = useLedgerWrite()

  // 4 个独立 state — 互不影响,可同时打开(不太可能但理论支持)
  const [tx, setTx] = useState<WorkspaceTransaction | null>(null)
  // 退款(0025):从 tx 详情弹窗「申请退款」按钮进入,关闭详情后打开退款弹窗。
  const [refundOriginal, setRefundOriginal] = useState<WorkspaceTransaction | null>(null)
  // 退款(0025):退款详情里展示/跳转用的「原消费流水」对象(按 refund_of_sync_id 拉取)。
  const [originalTx, setOriginalTx] = useState<WorkspaceTransaction | null>(null)
  // 合并还款(0039):被合并的各笔原始借款流水(按 merged_from_sync_ids 批量拉取)。
  const [mergedOriginals, setMergedOriginals] = useState<Record<string, WorkspaceTransaction>>({})
  // 关联记录(0043):引用这笔流水的业务实体(借款/储蓄罐/物品/报销/定期存款)。
  const [flowLinks, setFlowLinks] = useState<FlowLink[]>([])
  // 取消退款:待确认删除的退款流水(点击详情「取消退款」后填入,走二次确认)。
  const [cancelRefundTarget, setCancelRefundTarget] = useState<WorkspaceTransaction | null>(null)
  const [cancelRefundSaving, setCancelRefundSaving] = useState(false)

  const [account, setAccount] = useState<WorkspaceAccount | null>(null)
  const [accountScope, setAccountScope] = useState<DetailScope>('current')
  const [accountTxs, setAccountTxs] = useState<WorkspaceTransaction[]>([])
  const [accountTotal, setAccountTotal] = useState(0)
  const [accountOffset, setAccountOffset] = useState(0)
  const [accountLoading, setAccountLoading] = useState(false)

  const [category, setCategory] = useState<WorkspaceCategory | null>(null)
  const [categoryScope, setCategoryScope] = useState<DetailScope>('current')
  const [categoryTxs, setCategoryTxs] = useState<WorkspaceTransaction[]>([])
  const [categoryTotal, setCategoryTotal] = useState(0)
  const [categoryOffset, setCategoryOffset] = useState(0)
  const [categoryLoading, setCategoryLoading] = useState(false)
  // Stats 批量数据 — 跟 categoryScope 联动:current 限当前账本,all 跨账本。
  const [categoryStatsTxs, setCategoryStatsTxs] = useState<WorkspaceTransaction[]>([])
  const [categoryStatsLoading, setCategoryStatsLoading] = useState(false)
  const [categoryStatsTruncated, setCategoryStatsTruncated] = useState(false)

  const [tag, setTag] = useState<WorkspaceTag | null>(null)
  const [tagScope, setTagScope] = useState<DetailScope>('current')
  const [tagTxs, setTagTxs] = useState<WorkspaceTransaction[]>([])
  const [tagTotal, setTagTotal] = useState(0)
  const [tagOffset, setTagOffset] = useState(0)
  const [tagLoading, setTagLoading] = useState(false)

  // 共享 tags 字典 — 4 个详情弹窗里 TransactionList 渲染 tag chip 都要
  const [tagsDict, setTagsDict] = useState<WorkspaceTag[]>([])

  // 监听 tx detail
  useEffect(() => {
    return onOpenDetailTx((next) => {
      setTx(next)
      // 退款(0025):拉取关联的原消费流水,供详情里展示「原消费流水」并跳转。
      if (next?.tx_type === 'refund' && next.refund_of_sync_id) {
        void fetchWorkspaceTransactions(token, {
          ledgerId: next.ledger_id || undefined,
          txSyncId: next.refund_of_sync_id,
          limit: 1,
        })
          .then((page) => setOriginalTx(page.items[0] || null))
          .catch(() => setOriginalTx(null))
      } else {
        setOriginalTx(null)
      }
      // 合并还款(0039):拉取被合并的各笔原始借款流水,供详情里展示「查看原单」。
      const mergedIds = next?.merged_from_sync_ids ?? []
      if (mergedIds.length > 0) {
        void Promise.all(
          mergedIds.map((sid) =>
            fetchWorkspaceTransactions(token, {
              ledgerId: next.ledger_id || undefined,
              txSyncId: sid,
              limit: 1,
            })
              .then((page) => [sid, page.items[0]] as const)
              .catch(() => [sid, null] as const),
          ),
        )
          .then((pairs) => {
            const map: Record<string, WorkspaceTransaction> = {}
            for (const [sid, item] of pairs) {
              if (item) map[sid] = item
            }
            setMergedOriginals(map)
          })
          .catch(() => setMergedOriginals({}))
      } else {
        setMergedOriginals({})
      }
      // 关联记录(0043):拉取引用这笔流水的业务实体(借款/储蓄罐/物品/报销/存款),
      // 供详情里展示可跳转的关联入口。
      if (next?.id) {
        void fetchFlowLinks(token, next.id, next.ledger_id || undefined)
          .then((res) => setFlowLinks(res.links || []))
          .catch(() => setFlowLinks([]))
      } else {
        setFlowLinks([])
      }
      // 顺手拉一份 tags 字典,详情弹窗里的 tag chip 要按 color 渲染
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 })
          .then(setTagsDict)
          .catch(() => undefined)
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const loadAccountTxs = useCallback(
    async (accountName: string, scope: DetailScope, offset: number, limit = DETAIL_PAGE_SIZE) => {
      setAccountLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          accountName,
          ledgerId: scope === 'current' ? activeLedgerId || undefined : undefined,
          limit,
          offset,
        })
        setAccountTxs((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
        setAccountTotal(page.total)
        setAccountOffset(offset + page.items.length)
      } catch {
        // 静默,弹窗里展示空 list 即可
      } finally {
        setAccountLoading(false)
      }
    },
    [token, activeLedgerId],
  )

  /**
   * 信用卡专用:循环分页拉该账户的**全部**流水,用于按月/按账期分组汇总。
   *
   * 不能用单次 limit=1000 了事 —— 南京信用卡有 1284 笔,2023-05-30 的转账
   * 恰好排在第 1284 位,单次截断后就"查不到"了。这里按顺序翻页直到取完,
   * 保证任何历史流水都进入分组。
   */
  const loadAllAccountTxs = useCallback(
    async (accountName: string, scope: DetailScope) => {
      setAccountLoading(true)
      try {
        const ledgerId = scope === 'current' ? activeLedgerId || undefined : undefined
        const accumulated: WorkspaceTransaction[] = []
        let offset = 0
        // 保险上限:防止异常 total 导致死循环(单账本流水量远低于此)。
        const MAX_ROUNDS = 50
        for (let round = 0; round < MAX_ROUNDS; round++) {
          const page = await fetchWorkspaceTransactions(token, {
            accountName,
            ledgerId,
            limit: CREDIT_CARD_DETAIL_LIMIT,
            offset,
          })
          accumulated.push(...page.items)
          setAccountTotal(page.total)
          offset += page.items.length
          // 取完:本页为空 或 已累积到 total
          if (page.items.length === 0 || accumulated.length >= page.total) break
        }
        setAccountTxs(accumulated)
        setAccountOffset(offset)
      } catch {
        setAccountTxs([])
      } finally {
        setAccountLoading(false)
      }
    },
    [token, activeLedgerId],
  )

  // 监听 account detail
  useEffect(() => {
    return onOpenDetailAccount((acc, defaultScope) => {
      setAccount(acc)
      setAccountScope(defaultScope)
      setAccountTxs([])
      setAccountTotal(0)
      setAccountOffset(0)
      // 所有账户详情都按「按月分组」展示流水,需拉全量保证汇总准确;
      // 信用卡在此基础上还支持按账期切换。
      void loadAllAccountTxs(acc.name, defaultScope)
      // 同时拉一份 tags 字典(如还没拉)
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 }).then(setTagsDict).catch(() => undefined)
      }
    })
  }, [loadAccountTxs, loadAllAccountTxs, token, tagsDict.length])

  const handleAccountScopeChange = useCallback(
    (next: DetailScope) => {
      if (!account || next === accountScope) return
      setAccountScope(next)
      setAccountTxs([])
      setAccountTotal(0)
      setAccountOffset(0)
      void loadAllAccountTxs(account.name, next)
    },
    [account, accountScope, loadAllAccountTxs],
  )

  const loadCategoryTxs = useCallback(
    async (categorySyncId: string, scope: DetailScope, offset: number) => {
      setCategoryLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          categorySyncId,
          ledgerId: scope === 'current' ? activeLedgerId || undefined : undefined,
          limit: DETAIL_PAGE_SIZE,
          offset,
        })
        setCategoryTxs((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
        setCategoryTotal(page.total)
        setCategoryOffset(offset + page.items.length)
      } catch {
        // ignore
      } finally {
        setCategoryLoading(false)
      }
    },
    [token, activeLedgerId],
  )

  /** 拉一次大批量用于客户端聚合 KPI / 趋势 / Top。scope=current 限定当前账本,
   *  scope=all 跨账本。cap=1000 — 超过显示截断提示,KPI 仍可用但精度下降。 */
  const loadCategoryStats = useCallback(
    async (categorySyncId: string, scope: DetailScope) => {
      setCategoryStatsLoading(true)
      setCategoryStatsTruncated(false)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          categorySyncId,
          ledgerId: scope === 'current' ? activeLedgerId || undefined : undefined,
          limit: CATEGORY_STATS_LIMIT,
          offset: 0,
        })
        setCategoryStatsTxs(page.items)
        setCategoryStatsTruncated(page.total > page.items.length)
      } catch {
        setCategoryStatsTxs([])
      } finally {
        setCategoryStatsLoading(false)
      }
    },
    [token, activeLedgerId],
  )

  // 监听 category detail
  useEffect(() => {
    return onOpenDetailCategory((cat, defaultScope) => {
      setCategory(cat)
      setCategoryScope(defaultScope)
      setCategoryTxs([])
      setCategoryTotal(0)
      setCategoryOffset(0)
      setCategoryStatsTxs([])
      setCategoryStatsTruncated(false)
      void loadCategoryTxs(cat.id, defaultScope, 0)
      void loadCategoryStats(cat.id, defaultScope)
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 }).then(setTagsDict).catch(() => undefined)
      }
    })
  }, [loadCategoryTxs, loadCategoryStats, token, tagsDict.length])

  const handleCategoryScopeChange = useCallback(
    (next: DetailScope) => {
      if (!category || next === categoryScope) return
      setCategoryScope(next)
      setCategoryTxs([])
      setCategoryTotal(0)
      setCategoryOffset(0)
      setCategoryStatsTxs([])
      setCategoryStatsTruncated(false)
      void loadCategoryTxs(category.id, next, 0)
      void loadCategoryStats(category.id, next)
    },
    [category, categoryScope, loadCategoryTxs, loadCategoryStats],
  )

  const loadTagTxs = useCallback(
    async (tagSyncId: string, scope: DetailScope, offset: number) => {
      setTagLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          tagSyncId,
          ledgerId: scope === 'current' ? activeLedgerId || undefined : undefined,
          limit: DETAIL_PAGE_SIZE,
          offset,
        })
        setTagTxs((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
        setTagTotal(page.total)
        setTagOffset(offset + page.items.length)
      } catch {
        // ignore
      } finally {
        setTagLoading(false)
      }
    },
    [token, activeLedgerId],
  )

  // 监听 tag detail
  useEffect(() => {
    return onOpenDetailTag((nextTag, defaultScope) => {
      setTag(nextTag)
      setTagScope(defaultScope)
      setTagTxs([])
      setTagTotal(0)
      setTagOffset(0)
      void loadTagTxs(nextTag.id, defaultScope, 0)
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 }).then(setTagsDict).catch(() => undefined)
      }
    })
  }, [loadTagTxs, token, tagsDict.length])

  const handleTagScopeChange = useCallback(
    (next: DetailScope) => {
      if (!tag || next === tagScope) return
      setTagScope(next)
      setTagTxs([])
      setTagTotal(0)
      setTagOffset(0)
      void loadTagTxs(tag.id, next, 0)
    },
    [tag, tagScope, loadTagTxs],
  )

  // 详情 → 编辑:派发事件到 GlobalEditDialogs(任何页都挂载,事件总能被
  // 接住,无需跳页)。Category 编辑暂时还要 fallback 跳页(分类编辑表单
  // 依赖 inline icon picker,数据流复杂,后续单独全局化)。
  const handleEditTx = useCallback(
    (target: WorkspaceTransaction) => {
      setTx(null)
      dispatchOpenEditTx(target)
    },
    [],
  )

  // 取消退款:删除该退款流水即可 —— 后端 refunded_amount 是读时按退款流水
  // 聚合的,删除后原单已退金额自动回落、重新可退,无需额外改原单。
  const handleConfirmCancelRefund = useCallback(async () => {
    if (!cancelRefundTarget || !token) return
    const ledgerId = cancelRefundTarget.ledger_id
    if (!ledgerId) return
    if (isTxLocked(cancelRefundTarget.happened_at)) {
      toast.error(t('settings.lock.txBlocked'), t('notice.error'))
      return
    }
    setCancelRefundSaving(true)
    try {
      await retryOnConflict(ledgerId, (base) =>
        deleteTransaction(token, ledgerId, cancelRefundTarget.id, base),
      )
      setCancelRefundTarget(null)
      setTx(null)
      setOriginalTx(null)
      dispatchTxRefresh()
      toast.success(t('refund.cancel.success'), t('notice.success'))
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setCancelRefundSaving(false)
    }
  }, [cancelRefundTarget, token, retryOnConflict, toast, t, isTxLocked])

  // 退款(0025):从退款详情「查看原单」跳转 —— 拉原消费流水并替换当前详情。
  const handleOpenOriginal = useCallback(
    (syncId: string, ledgerId: string) => {
      if (!syncId) return
      void fetchWorkspaceTransactions(token, {
        ledgerId: ledgerId || undefined,
        txSyncId: syncId,
        limit: 1,
      })
        .then((page) => {
          const orig = page.items[0]
          if (orig) {
            setTx(orig)
            setOriginalTx(null)
          }
        })
        .catch(() => undefined)
    },
    [token],
  )

  // 关联记录(0043):跳转到关联业务实体详情。
  // 统一用「导航到目标页 + 带实体 id 的 URL 参数」,由目标页读取参数自动
  // 打开对应详情弹窗 —— 这样链接可分享、可刷新、可后退。
  const handleOpenFlowLink = useCallback(
    (link: FlowLink) => {
      const target = link.target
      if (!target?.page) return
      const params = new URLSearchParams()
      if (target.loan_id) params.set('loan_id', target.loan_id)
      if (target.plan_id) params.set('plan_id', target.plan_id)
      if (target.item_id) params.set('item_id', target.item_id)
      if (target.reimbursement_id) params.set('reimbursement_id', target.reimbursement_id)
      if (target.reimbursement_item_id) {
        params.set('reimbursement_item_id', target.reimbursement_item_id)
      }
      if (target.deposit_id) params.set('deposit_id', target.deposit_id)
      if (target.contact_id) params.set('contact_id', target.contact_id)
      if (target.member_id) params.set('member_id', target.member_id)
      setTx(null)
      navigate(`/app/${target.page}?${params.toString()}`)
      // 同页面跳回时目标组件不重挂载、URL 参数 effect 的 ref guard 会拦截,
      // 额外派发事件兜底:目标页监听后直接打开对应详情弹窗。
      if (target.loan_id) setTimeout(() => dispatchOpenDetailLoan(target.loan_id as string), 60)
      if (target.deposit_id) setTimeout(() => dispatchOpenDetailDeposit(target.deposit_id as string), 60)
      if (target.contact_id) setTimeout(() => dispatchOpenDetailContact(target.contact_id as string), 60)
      if (target.plan_id) setTimeout(() => dispatchOpenDetailSavingPlan(target.plan_id as string), 60)
      if (target.member_id) setTimeout(() => dispatchOpenDetailMember(target.member_id as string), 60)
      if (target.reimbursement_id) {
        setTimeout(
          () => dispatchOpenDetailReimbursement({ kind: 'reimbursement', id: target.reimbursement_id as string }),
          60,
        )
      }
      if (target.reimbursement_item_id) {
        setTimeout(
          () => dispatchOpenDetailReimbursement({ kind: 'item', id: target.reimbursement_item_id as string }),
          60,
        )
      }
    },
    [navigate],
  )

  const handleEditCategory = useCallback(    (cat: WorkspaceCategory) => {
      setCategory(null)
      dispatchOpenEditCategory(cat)
    },
    [],
  )

  const handleJumpToTransactions = useCallback(
    (cat: WorkspaceCategory) => {
      const scopeAtJump = categoryScope
      setCategory(null)
      // 跳过去的 TransactionsPage ledger filter 跟当前 scope 对齐:current
      // 时带 ledger=...,all 时不限制账本,避免用户切到「全部账本」却跳进单
      // 账本列表造成迷惑。
      const params = new URLSearchParams()
      params.set('q', cat.name)
      if (scopeAtJump === 'current' && activeLedgerId) {
        params.set('ledger', activeLedgerId)
      }
      navigate(`/app/transactions?${params.toString()}`)
    },
    [navigate, activeLedgerId, categoryScope],
  )

  return (
    <>
      <TransactionDetailDialog
        tx={tx}
        tags={tagsDict}
        onClose={() => setTx(null)}
        onEdit={handleEditTx}
        onCopy={(target) => {
          setTx(null)
          const copyTx = { ...target, id: '', happened_at: new Date().toISOString() }
          handleEditTx(copyTx as typeof target)
        }}
        onRefund={(target) => {
          // 关详情 → 开退款:退款弹窗自身持有 original,不依赖详情 state。
          setTx(null)
          setRefundOriginal(target)
        }}
        onCancelRefund={(target) => setCancelRefundTarget(target)}
        originalTx={originalTx}
        onOpenOriginal={handleOpenOriginal}
        mergedOriginals={mergedOriginals}
        flowLinks={flowLinks}
        onOpenFlowLink={handleOpenFlowLink}
      />
      <AccountDetailDialog
        account={account}
        scope={accountScope}
        onScopeChange={handleAccountScopeChange}
        transactions={accountTxs}
        total={accountTotal}
        offset={accountOffset}
        loading={accountLoading}
        tags={tagsDict}
        currency={activeCurrency}
        onPreviewAttachment={previewAttachment}
        resolveAttachmentPreviewUrl={ref => resolvePreview(ref.cloudFileId || '')}
        onClose={() => setAccount(null)}
        onLoadMore={(name, off) => {
          const isCreditCard = (account?.account_type || '') === 'credit_card'
          void loadAccountTxs(name, accountScope, off, isCreditCard ? CREDIT_CARD_DETAIL_LIMIT : DETAIL_PAGE_SIZE)
        }}
        onSelect={(row) => {
          setAccount(null)
          dispatchOpenDetailTx(row)
        }}
      />
      <CategoryDetailDialog
        onPreviewAttachment={previewAttachment}
        category={category}
        scope={categoryScope}
        onScopeChange={handleCategoryScopeChange}
        currency={activeCurrency}
        statsTransactions={categoryStatsTxs}
        statsLoading={categoryStatsLoading}
        statsTruncated={categoryStatsTruncated}
        transactions={categoryTxs}
        total={categoryTotal}
        offset={categoryOffset}
        loading={categoryLoading}
        tags={tagsDict}
        iconPreviewUrlByFileId={iconPreviewByFileId}
        ledgerMonthStartDay={currentLedger?.month_start_day ?? 1}
        onClose={() => setCategory(null)}
        onLoadMore={(syncId, off) => void loadCategoryTxs(syncId, categoryScope, off)}
        onEdit={handleEditCategory}
        onJumpToTransactions={handleJumpToTransactions}
      />
      <TagDetailDialog
        tag={tag}
        scope={tagScope}
        onScopeChange={handleTagScopeChange}
        transactions={tagTxs}
        total={tagTotal}
        offset={tagOffset}
        loading={tagLoading}
        tags={tagsDict}
        tagStatsById={{}}
        onPreviewAttachment={previewAttachment}
        resolveAttachmentPreviewUrl={ref => resolvePreview(ref.cloudFileId || '')}
        onClose={() => setTag(null)}
        onLoadMore={(syncId, off) => void loadTagTxs(syncId, tagScope, off)}
      />
      <RefundDialog
        original={refundOriginal}
        token={token}
        ledgerId={refundOriginal?.ledger_id ?? activeLedgerId ?? ''}
        canManage
        open={Boolean(refundOriginal)}
        onOpenChange={(v) => !v && setRefundOriginal(null)}
        onSubmitted={() => {
          setRefundOriginal(null)
          dispatchTxRefresh()
        }}
      />
      {/* 取消退款二次确认 —— 删除退款流水后原单已退金额自动回落、重新可退。 */}
      <Dialog
        open={Boolean(cancelRefundTarget)}
        onOpenChange={(v) => !v && !cancelRefundSaving && setCancelRefundTarget(null)}
      >
        <DialogContent className="max-w-md gap-0 p-0">
          <DialogHeader className="border-b border-border/60 px-6 py-4">
            <DialogTitle>{t('refund.cancel.title')}</DialogTitle>
          </DialogHeader>
          <div className="px-6 py-5 text-sm text-muted-foreground">
            {t('refund.cancel.description', {
              amount: (cancelRefundTarget?.amount ?? 0).toLocaleString('zh-CN', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              }),
            })}
          </div>
          <DialogFooter className="border-t border-border/60 bg-muted/20 px-6 py-3">
            <Button
              variant="outline"
              size="sm"
              disabled={cancelRefundSaving}
              onClick={() => setCancelRefundTarget(null)}
            >
              {t('dialog.cancel')}
            </Button>
            <Button
              size="sm"
              variant="destructive"
              disabled={cancelRefundSaving}
              onClick={() => void handleConfirmCancelRefund()}
            >
              {t('refund.cancel.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
