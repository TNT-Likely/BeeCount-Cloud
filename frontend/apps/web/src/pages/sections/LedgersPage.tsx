import { useState } from 'react'

import {
  createLedger,
  updateLedgerMeta,
  type ReadLedger,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'

import {
  LedgerEditDialog,
  LedgersSection,
  type LedgerForm,
} from '../../components/sections/LedgersSection'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

const defaultForm: LedgerForm = { ledger_name: '', currency: 'CNY' }

/**
 * 账本列表页 ——
 * - 顶部"新建账本"按钮 → 弹 LedgerEditDialog(mode=create),保存调
 *   createLedger,server 自动生成 ledger_id。
 * - 点账本卡片 → 弹 LedgerEditDialog(mode=edit),保存调 updateLedgerMeta;
 *   不再切 activeLedgerId 也不跳转(切账本走顶部 ledger picker)。
 */
export function LedgersPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { ledgers, refreshLedgers } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const [createOpen, setCreateOpen] = useState(false)
  const [createForm, setCreateForm] = useState<LedgerForm>(defaultForm)
  const [editing, setEditing] = useState<ReadLedger | null>(null)
  const [editForm, setEditForm] = useState<LedgerForm>(defaultForm)

  const notifyError = (err: unknown) => toast.error(localizeError(err, t), t('notice.error'))
  const notifySuccess = (msg: string) => toast.success(msg, t('notice.success'))

  const onOpenCreate = () => {
    setCreateForm(defaultForm)
    setCreateOpen(true)
  }

  const onOpenEdit = (ledger: ReadLedger) => {
    setEditing(ledger)
    setEditForm({
      ledger_name: ledger.ledger_name || '',
      currency: ledger.currency || 'CNY',
    })
  }

  const validateName = (name: string, ignoreId?: string): boolean => {
    const trimmed = name.trim()
    if (!trimmed) {
      toast.error(t('ledgers.error.nameRequired'), t('notice.error'))
      return false
    }
    // 重名检查 —— mobile ledger_edit 也禁止重名,跨端一致。
    const dup = ledgers.find(
      (l) => (l.ledger_name || '').trim().toLowerCase() === trimmed.toLowerCase() && l.ledger_id !== ignoreId,
    )
    if (dup) {
      toast.error(t('ledgers.error.nameDuplicate'), t('notice.error'))
      return false
    }
    return true
  }

  const onCreate = async (): Promise<boolean> => {
    if (!validateName(createForm.ledger_name)) return false
    try {
      await createLedger(token, {
        ledger_name: createForm.ledger_name.trim(),
        currency: createForm.currency || 'CNY',
      })
      notifySuccess(t('ledgers.notice.created'))
      await refreshLedgers()
      return true
    } catch (err) {
      notifyError(err)
      return false
    }
  }

  const onSaveEdit = async (): Promise<boolean> => {
    if (!editing) return false
    if (!validateName(editForm.ledger_name, editing.ledger_id)) return false
    try {
      await retryOnConflict(editing.ledger_id, (base) =>
        updateLedgerMeta(token, editing.ledger_id, base, {
          ledger_name: editForm.ledger_name.trim(),
          currency: editForm.currency || editing.currency,
        }),
      )
      notifySuccess(t('ledgers.notice.updated'))
      await refreshLedgers()
      setEditing(null)
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refreshLedgers()
      notifyError(err)
      return false
    }
  }

  return (
    <>
      <LedgersSection onCreate={onOpenCreate} onEdit={onOpenEdit} />
      <LedgerEditDialog
        open={createOpen}
        mode="create"
        form={createForm}
        onChange={setCreateForm}
        onClose={() => setCreateOpen(false)}
        onSubmit={onCreate}
      />
      <LedgerEditDialog
        open={editing !== null}
        mode="edit"
        form={editForm}
        onChange={setEditForm}
        onClose={() => setEditing(null)}
        onSubmit={onSaveEdit}
        meta={
          editing
            ? [
                { label: t('ledgers.meta.id'), value: editing.ledger_id },
                {
                  label: t('ledgers.meta.role'),
                  value:
                    editing.role === 'owner'
                      ? t('ledgers.role.owner')
                      : editing.role === 'editor'
                        ? t('ledgers.role.editor')
                        : t('ledgers.role.viewer'),
                },
              ]
            : []
        }
      />
    </>
  )
}
