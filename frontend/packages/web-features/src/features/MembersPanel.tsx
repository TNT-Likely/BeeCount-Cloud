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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
  useT,
} from '@beecount/ui'

import type { LedgerMember, ReadMember } from '@beecount/api-client'

import { ConfirmDialog } from '../components/ConfirmDialog'
import { MemberIconBadge, MemberIconPicker } from '../components/MemberIconPicker'
import type { MemberForm } from '../forms'
import { memberDefaults } from '../forms'
import { validateMemberInput } from '../lib/members'

/**
 * 账本内自定义成员面板(0024)。
 *
 * 成员是账本自己维护的「人」名单(爸爸/妈妈/我),不要求登录账号 —— 跟共享
 * 账本的登录用户成员(LedgerMember)是两套东西。建好之后就能在支出账单里
 * 选成员做**人头分摊**。
 *
 * 跟其它 panel 同款契约:自己不发请求,全部通过 props 回调交给 page 层执行。
 */

type MembersPanelProps = {
  members: readonly ReadMember[]
  form: MemberForm
  onFormChange: (next: MemberForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onDelete: (row: ReadMember) => Promise<void> | void
  canManage: boolean
  /** 账本里的登录用户(记账人),用于「关联记账人员」下拉。 */
  bookkeepers?: readonly LedgerMember[]
}

/** 下拉里"不关联"的取值。真实 user_id 是 uuid,不会跟它撞。 */
const NO_BOOKKEEPER = '__none__'

/** 记账人展示名:昵称优先,其次邮箱前缀。 */
function bookkeeperLabel(row: LedgerMember): string {
  return row.display_name || row.email.split('@')[0] || row.user_id
}

export function MembersPanel({
  members,
  form,
  onFormChange,
  onSubmit,
  onDelete,
  canManage,
  bookkeepers = [],
}: MembersPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [iconPickerOpen, setIconPickerOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadMember | null>(null)
  const [deleting, setDeleting] = useState(false)

  const linkedNameByUserId = useMemo(() => {
    const map = new Map<string, string>()
    for (const bk of bookkeepers) map.set(bk.user_id, bookkeeperLabel(bk))
    return map
  }, [bookkeepers])

  const formError = validateMemberInput({ name: form.name })
  const errorText = formError ? t(`members.error.${formError}`) : null

  const handleOpenCreate = () => {
    onFormChange(memberDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (row: ReadMember) => {
    onFormChange({
      editingId: row.id,
      name: row.name || '',
      note: row.note || '',
      sort_order: String(row.sort_order ?? 0),
      icon: row.icon || '',
      linked_user_id: row.linked_user_id || '',
    })
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    if (formError) return
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

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('members.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('members.button.create')}
        </Button>
      </div>

      {members.length === 0 ? (
        <EmptyState
          icon={<span className="material-symbols-outlined text-2xl">group</span>}
          title={t('members.empty')}
          description={t('members.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {members.map((row) => (
            <div
              key={row.id}
              className="rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm"
            >
              <div className="flex items-start gap-3">
                <span
                  aria-hidden
                  className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                >
                  <MemberIconBadge icon={row.icon} size={24} />
                </span>
                <div className="min-w-0 flex-1">
                  <span className="truncate text-sm font-semibold">{row.name}</span>
                  {row.linked_user_id ? (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {t('members.field.linkedUser')}:{' '}
                      {linkedNameByUserId.get(row.linked_user_id) ||
                        t('members.linkedUser.unknown')}
                    </p>
                  ) : null}
                  {row.note ? (
                    <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                      {row.note}
                    </p>
                  ) : null}
                </div>
              </div>
              <div className="mt-3 flex items-center justify-end gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={!canManage}
                  onClick={() => handleOpenEdit(row)}
                >
                  {t('common.edit')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={!canManage}
                  onClick={() => setPendingDelete(row)}
                >
                  {t('common.delete')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 创建/编辑 dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('members.button.update') : t('members.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>{t('members.field.name')}</Label>
              <Input
                autoFocus
                value={form.name}
                placeholder={t('members.field.namePlaceholder')}
                onChange={(e) => onFormChange({ ...form, name: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('members.field.icon')}</Label>
              <div className="flex items-center gap-3">
                <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <MemberIconBadge icon={form.icon} size={24} />
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setIconPickerOpen(true)}
                >
                  {form.icon ? t('members.icon.change') : t('members.icon.set')}
                </Button>
              </div>
            </div>
            <div className="space-y-1">
              <Label>{t('members.field.linkedUser')}</Label>
              <Select
                value={form.linked_user_id || NO_BOOKKEEPER}
                onValueChange={(value) =>
                  onFormChange({
                    ...form,
                    linked_user_id: value === NO_BOOKKEEPER ? '' : value,
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder={t('members.field.linkedUserPlaceholder')} />
                </SelectTrigger>
                <SelectContent className="max-h-80">
                  <SelectItem value={NO_BOOKKEEPER}>
                    {t('members.linkedUser.none')}
                  </SelectItem>
                  {bookkeepers.map((bk) => (
                    <SelectItem key={bk.user_id} value={bk.user_id}>
                      {bookkeeperLabel(bk)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">{t('members.field.linkedUserHint')}</p>
            </div>
            <div className="space-y-1">
              <Label>{t('members.field.note')}</Label>
              <Textarea
                rows={2}
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('members.field.sortOrder')}</Label>
              <Input
                type="number"
                step="1"
                value={form.sort_order}
                onChange={(e) => onFormChange({ ...form, sort_order: e.target.value })}
              />
            </div>
            {errorText ? <p className="text-xs text-destructive">{errorText}</p> : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={submitting || !canManage || formError !== null}
              onClick={() => void handleSubmit()}
            >
              {form.editingId ? t('members.button.update') : t('members.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <MemberIconPicker
        open={iconPickerOpen}
        current={form.icon || null}
        onSelect={(icon) => onFormChange({ ...form, icon: icon || '' })}
        onOpenChange={setIconPickerOpen}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('members.delete.title')}
        description={t('members.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}
