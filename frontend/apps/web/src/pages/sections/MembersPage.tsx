import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createMember,
  deleteMember,
  fetchLedgerMembers,
  fetchReadMembers,
  updateMember,
  type LedgerMember,
  type ReadMember,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import {
  MembersPanel,
  memberDefaults,
  type MemberForm,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

/**
 * 账本内自定义成员页(0024)。
 *
 * 跟 RefundsPage 同构:read 投影拉成员列表,写走 web write 端点 + CAS 重试。
 * 建好成员后,支出账单编辑区就能选成员做**人头分摊**。
 */
export function MembersPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const bucket = activeLedgerId || '__none__'
  const [rows, setRows] = usePageCache<ReadMember[]>(`members:${bucket}:rows`, [])
  const [form, setForm] = useState<MemberForm>(memberDefaults())
  // 账本里的登录用户(记账人)—— 成员可以关联其中一个,关联后该用户记的、
  // 没有分摊的交易在统计里并入该成员,不再单列一条「记账人」。
  const [bookkeepers, setBookkeepers] = useState<LedgerMember[]>([])

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t],
  )

  const refresh = useCallback(async () => {
    if (!activeLedgerId) {
      setRows([])
      return
    }
    try {
      const list = await fetchReadMembers(token, activeLedgerId)
      setRows(list)
      // 记账人列表拉取失败不该挡住成员页(只是关联下拉空),单独兜错。
      try {
        setBookkeepers(await fetchLedgerMembers(token, activeLedgerId))
      } catch {
        setBookkeepers([])
      }
    } catch (err) {
      notifyError(err)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  const sortedRows = useMemo(
    () =>
      [...rows].sort(
        (a, b) =>
          (a.sort_order ?? 0) - (b.sort_order ?? 0) ||
          String(a.id).localeCompare(String(b.id)),
      ),
    [rows],
  )

  const onSubmit = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const name = form.name.trim()
    if (!name) return false
    const payload = {
      name,
      note: form.note.trim() || null,
      sort_order: Number(form.sort_order) || 0,
      // 图标 / 关联记账人:空串 = 没设置(后端归一成 NULL)。
      icon: form.icon.trim() || null,
      linked_user_id: form.linked_user_id.trim() || null,
    }
    try {
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateMember(token, activeLedgerId, form.editingId!, base, payload),
        )
        notifySuccess(t('members.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createMember(token, activeLedgerId, base, payload),
        )
        notifySuccess(t('members.notice.created'))
      }
      setForm(memberDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onDelete = async (row: ReadMember): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteMember(token, activeLedgerId, row.id, base),
      )
      notifySuccess(t('members.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('nav.members')}</CardTitle>
      </CardHeader>
      <CardContent>
        {!activeLedgerId ? (
          <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
        ) : (
          <MembersPanel
            members={sortedRows}
            form={form}
            onFormChange={setForm}
            onSubmit={onSubmit}
            onDelete={onDelete}
            canManage={Boolean(activeLedgerId)}
            bookkeepers={bookkeepers}
          />
        )}
      </CardContent>
    </Card>
  )
}
