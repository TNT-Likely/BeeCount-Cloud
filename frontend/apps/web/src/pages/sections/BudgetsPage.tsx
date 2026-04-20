import { useCallback, useEffect, useState } from 'react'

import {
  fetchReadBudgets,
  fetchWorkspaceCategories,
  type ReadBudget,
  type WorkspaceCategory,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'

import { BudgetsSection } from '../../components/sections/BudgetsSection'
import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'

/**
 * 预算页 —— budgets 按账本取(预算是账本级概念);categories 是 user-global
 * 跨账本共享,跟 BudgetsSection 内部渲染分类图标匹配。
 *
 * 自定义分类图标走全局 AttachmentCache(与 CategoriesPage / 将来交易
 * 附件预览共享同一份 blob map),避免重复下载 / 内存泄漏。
 */
export function BudgetsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { previewMap: iconPreviewByFileId, ensureLoadedMany } = useAttachmentCache()

  const [budgets, setBudgets] = useState<ReadBudget[]>([])
  const [categories, setCategories] = useState<WorkspaceCategory[]>([])

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t]
  )

  const refresh = useCallback(async () => {
    if (!activeLedgerId) {
      setBudgets([])
      return
    }
    try {
      const [b, c] = await Promise.all([
        fetchReadBudgets(token, activeLedgerId),
        fetchWorkspaceCategories(token, {}),
      ])
      setBudgets(b)
      setCategories(c)
    } catch (err) {
      notifyError(err)
    }
  }, [token, activeLedgerId, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  // categories 拉回来后把所有 icon_cloud_file_id 塞给共享 cache 加载。
  useEffect(() => {
    const ids = categories
      .map((c) => c.icon_cloud_file_id || '')
      .filter((v) => v.trim().length > 0)
    if (ids.length > 0) ensureLoadedMany(ids)
  }, [categories, ensureLoadedMany])

  return (
    <BudgetsSection
      budgets={budgets}
      categories={categories}
      categoryIconPreviewByFileId={iconPreviewByFileId}
    />
  )
}
