import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  type WorkspaceAccount,
  type WorkspaceCategory,
  type WorkspaceTag,
  type WorkspaceTransaction,
} from '@beecount/api-client'

import { useAttachmentCache } from '../context/AttachmentCacheContext'
import { useAuth } from '../context/AuthContext'
import {
  dispatchOpenEditCategory,
  dispatchOpenEditTx,
  onOpenDetailAccount,
  onOpenDetailCategory,
  onOpenDetailTag,
  onOpenDetailTx,
} from '../lib/txDialogEvents'
import { AccountDetailDialog } from './dialogs/AccountDetailDialog'
import { CategoryDetailDialog } from './dialogs/CategoryDetailDialog'
import { TagDetailDialog } from './dialogs/TagDetailDialog'
import { TransactionDetailDialog } from './dialogs/TransactionDetailDialog'

const DETAIL_PAGE_SIZE = 50

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
  const { token } = useAuth()
  const { previewMap: iconPreviewByFileId } = useAttachmentCache()

  // 4 个独立 state — 互不影响,可同时打开(不太可能但理论支持)
  const [tx, setTx] = useState<WorkspaceTransaction | null>(null)

  const [account, setAccount] = useState<WorkspaceAccount | null>(null)
  const [accountTxs, setAccountTxs] = useState<WorkspaceTransaction[]>([])
  const [accountTotal, setAccountTotal] = useState(0)
  const [accountOffset, setAccountOffset] = useState(0)
  const [accountLoading, setAccountLoading] = useState(false)

  const [category, setCategory] = useState<WorkspaceCategory | null>(null)
  const [categoryTxs, setCategoryTxs] = useState<WorkspaceTransaction[]>([])
  const [categoryTotal, setCategoryTotal] = useState(0)
  const [categoryOffset, setCategoryOffset] = useState(0)
  const [categoryLoading, setCategoryLoading] = useState(false)

  const [tag, setTag] = useState<WorkspaceTag | null>(null)
  const [tagTxs, setTagTxs] = useState<WorkspaceTransaction[]>([])
  const [tagTotal, setTagTotal] = useState(0)
  const [tagOffset, setTagOffset] = useState(0)
  const [tagLoading, setTagLoading] = useState(false)

  // 共享 tags 字典 — 4 个详情弹窗里 TransactionList 渲染 tag chip 都要
  const [tagsDict, setTagsDict] = useState<WorkspaceTag[]>([])

  // 监听 tx detail
  useEffect(() => {
    return onOpenDetailTx((next) => setTx(next))
  }, [])

  const loadAccountTxs = useCallback(
    async (accountName: string, offset: number) => {
      setAccountLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          accountName,
          limit: DETAIL_PAGE_SIZE,
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
    [token],
  )

  // 监听 account detail
  useEffect(() => {
    return onOpenDetailAccount((acc) => {
      setAccount(acc)
      setAccountTxs([])
      setAccountTotal(0)
      setAccountOffset(0)
      void loadAccountTxs(acc.name, 0)
      // 同时拉一份 tags 字典(如还没拉)
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 }).then(setTagsDict).catch(() => undefined)
      }
    })
  }, [loadAccountTxs, token, tagsDict.length])

  const loadCategoryTxs = useCallback(
    async (categorySyncId: string, offset: number) => {
      setCategoryLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          categorySyncId,
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
    [token],
  )

  // 监听 category detail
  useEffect(() => {
    return onOpenDetailCategory((cat) => {
      setCategory(cat)
      setCategoryTxs([])
      setCategoryTotal(0)
      setCategoryOffset(0)
      void loadCategoryTxs(cat.id, 0)
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 }).then(setTagsDict).catch(() => undefined)
      }
    })
  }, [loadCategoryTxs, token, tagsDict.length])

  const loadTagTxs = useCallback(
    async (tagSyncId: string, offset: number) => {
      setTagLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          tagSyncId,
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
    [token],
  )

  // 监听 tag detail
  useEffect(() => {
    return onOpenDetailTag((nextTag) => {
      setTag(nextTag)
      setTagTxs([])
      setTagTotal(0)
      setTagOffset(0)
      void loadTagTxs(nextTag.id, 0)
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 }).then(setTagsDict).catch(() => undefined)
      }
    })
  }, [loadTagTxs, token, tagsDict.length])

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

  const handleEditCategory = useCallback(
    (cat: WorkspaceCategory) => {
      setCategory(null)
      dispatchOpenEditCategory(cat)
    },
    [],
  )

  return (
    <>
      <TransactionDetailDialog
        tx={tx}
        onClose={() => setTx(null)}
        onEdit={handleEditTx}
      />
      <AccountDetailDialog
        account={account}
        transactions={accountTxs}
        total={accountTotal}
        offset={accountOffset}
        loading={accountLoading}
        tags={tagsDict}
        onClose={() => setAccount(null)}
        onLoadMore={(name, off) => void loadAccountTxs(name, off)}
      />
      <CategoryDetailDialog
        category={category}
        transactions={categoryTxs}
        total={categoryTotal}
        offset={categoryOffset}
        loading={categoryLoading}
        tags={tagsDict}
        iconPreviewUrlByFileId={iconPreviewByFileId}
        onClose={() => setCategory(null)}
        onLoadMore={(syncId, off) => void loadCategoryTxs(syncId, off)}
        onEdit={handleEditCategory}
      />
      <TagDetailDialog
        tag={tag}
        transactions={tagTxs}
        total={tagTotal}
        offset={tagOffset}
        loading={tagLoading}
        tags={tagsDict}
        tagStatsById={{}}
        onClose={() => setTag(null)}
        onLoadMore={(syncId, off) => void loadTagTxs(syncId, off)}
      />
    </>
  )
}
