import { useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'

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

import { normalizePartyProfile, type ReadMember } from '@beecount/api-client'

import type { MemberForm } from '../forms'
import { PartyAvatar, PartyProfileEditor } from '../components/PartyProfileEditor'
import { useSingleFlight } from '../lib/singleFlight'
import { TAG_COLOR_PALETTE, tagTextColorOn } from '../lib/tagColorPalette'

/** 成员维度的统计卡片数据（由 analytics.member_ranks 的支出/收入两次口径合并而来）。 */
export type MemberStat = {
  expense: number
  income: number
  txCount: number
}

type MembersPanelProps = {
  form: MemberForm
  rows: ReadMember[]
  canManage: boolean
  /** 按 member.id 查询的支出/收入/笔数；未传则卡片不展开统计区。 */
  statsById?: Record<string, MemberStat>
  /** 头像上传 / 预览由宿主注入，与联系人页共用 usePartyProfileIo。 */
  upload: (file: File) => Promise<string>
  resolvePreview: (fileId: string) => Promise<string | null>
  onFormChange: (next: MemberForm) => void
  /** 触发「新建成员」：外层负责把 form 重置成 memberDefaults() 并打开 dialog。 */
  onCreate?: () => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onEdit: (row: ReadMember) => void
  onDelete?: (row: ReadMember) => void
  /** 点击卡片（非编辑/删除按钮）：外层用来跳「按该成员筛选的交易列表」。 */
  onClickMember?: (row: ReadMember) => void
  /**
   * 外部请求打开某个成员的编辑表单(0055):成员详情弹窗底部的「编辑」按钮
   * 需要复用本面板内的编辑弹窗。用 nonce 递增触发,避免 id 不变时重复触发。
   */
  editRequest?: { memberId: string; nonce: number } | null
}

/**
 * 成员管理面板（0024）。
 *
 * 成员是 user-global 的「消费/收入归属人」，跟共享账本协作者(LedgerMember)无关。
 * 这里复用 TagsPanel 的卡片 + 调色板 + 同名查重交互，额外展示 analytics 的
 * 成员维度排行（支出/收入/笔数），让成员成为可见的统计维度。
 */
export function MembersPanel({
  form,
  rows,
  canManage,
  statsById,
  upload,
  resolvePreview,
  onFormChange,
  onCreate,
  onSave,
  onReset,
  onEdit,
  onDelete,
  onClickMember,
  editRequest,
}: MembersPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [nameError, setNameError] = useState<string | null>(null)
  // 保存请求返回前的连点会重复创建 —— 单飞守卫 + 按钮禁用。
  const { saving, guard } = useSingleFlight()
  const hasStats = Boolean(statsById)
  const fmt = (v: number) =>
    v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  // 成员统计维度：按支出降序，展示 top 8 的支出/收入占比条。
  const ranks = useMemo(() => {
    if (!statsById) return []
    const items = rows
      .map((row) => ({ id: row.id, name: row.name, color: row.color || '#94a3b8', ...(statsById[row.id] || { expense: 0, income: 0, txCount: 0 }) }))
      .filter((item) => item.expense > 0 || item.income > 0)
      .sort((a, b) => b.expense - a.expense || b.income - a.income)
      .slice(0, 8)
    // 支出/收入各自归一化：收入里含工资（量级远大于支出），共用刻度会把
    // 支出条压成一条线，排行就失去可读性。
    const maxExpense = Math.max(1, ...items.map((item) => item.expense))
    const maxIncome = Math.max(1, ...items.map((item) => item.income))
    return items.map((item) => ({
      ...item,
      expensePct: (item.expense / maxExpense) * 100,
      incomePct: (item.income / maxIncome) * 100,
    }))
  }, [rows, statsById])

  const existingNamesLower = useMemo(() => {
    const set = new Set<string>()
    for (const row of rows) {
      if (form.editingId && row.id === form.editingId) continue
      const name = (row.name || '').trim().toLowerCase()
      if (name) set.add(name)
    }
    return set
  }, [rows, form.editingId])

  const startCreate = () => {
    if (!canManage) return
    setNameError(null)
    onCreate?.()
    setOpen(true)
  }

  const startEdit = (row: ReadMember) => {
    setNameError(null)
    onEdit(row)
    setOpen(true)
  }

  // 外部(成员详情弹窗底部「编辑」)请求打开编辑表单:nonce 变化即触发一次。
  const editNonceRef = useRef(0)
  useEffect(() => {
    if (!editRequest?.nonce) return
    if (editNonceRef.current === editRequest.nonce) return
    editNonceRef.current = editRequest.nonce
    const row = rows.find((r) => r.id === editRequest.memberId)
    if (row) startEdit(row)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editRequest?.nonce, editRequest?.memberId, rows])

  const handleSave = async () => {
    const trimmed = form.name.trim()
    if (!trimmed) {
      setNameError(t('members.error.nameRequired'))
      return
    }
    if (existingNamesLower.has(trimmed.toLowerCase())) {
      setNameError(t('members.error.nameDuplicate'))
      return
    }
    setNameError(null)
    const success = await onSave()
    if (success) setOpen(false)
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-end gap-2">
        {onCreate && canManage ? (
          <Button onClick={startCreate}>{t('members.button.create')}</Button>
        ) : null}
      </div>

      {hasStats ? (
        <div className="mb-4 rounded-2xl border border-border/50 bg-card/70 p-4 backdrop-blur-sm">
          <div className="mb-3 flex items-baseline justify-between gap-2">
            <h3 className="text-sm font-semibold">{t('members.stats.title')}</h3>
            <span className="text-[11px] text-muted-foreground">
              {t('members.stats.expense')} / {t('members.stats.income')} · {t('members.stats.scaleHint')}
            </span>
          </div>
          {ranks.length === 0 ? (
            <p className="py-4 text-center text-xs text-muted-foreground">
              {t('members.stats.empty')}
            </p>
          ) : (
            <div className="space-y-2.5">
              {ranks.map((item) => (
                <div key={item.id} className="grid grid-cols-[minmax(72px,110px)_1fr_auto] items-center gap-3 text-xs">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: item.color }} />
                    <span className="truncate">{item.name}</span>
                  </span>
                  <span className="flex flex-col gap-1">
                    <span className="h-1.5 overflow-hidden rounded-full bg-muted">
                      <span className="block h-full rounded-full bg-rose-500"
                            style={{ width: `${item.expensePct}%` }} />
                    </span>
                    <span className="h-1.5 overflow-hidden rounded-full bg-muted">
                      <span className="block h-full rounded-full bg-emerald-500"
                            style={{ width: `${item.incomePct}%` }} />
                    </span>
                  </span>
                  <span className="text-right font-mono tabular-nums text-muted-foreground">
                    ¥{fmt(item.expense)} / ¥{fmt(item.income)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}

      {rows.length === 0 ? (
        <EmptyState
          icon={
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
                 strokeLinejoin="round">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
          }
          title={t('members.empty.title')}
          description={t('members.empty.desc')}
          action={
            onCreate && canManage ? (
              <Button onClick={startCreate}>{t('members.button.create')}</Button>
            ) : undefined
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {rows.map((row) => {
            const stats = statsById?.[row.id]
            const color = row.color || '#94a3b8'
            const profile = normalizePartyProfile(row.profile)
            return (
              <div
                key={row.id}
                className={`group relative overflow-hidden rounded-2xl border border-border/50 bg-card/80 p-5 backdrop-blur-sm transition-all hover:-translate-y-0.5 hover:border-border hover:shadow-lg ${
                  onClickMember ? 'cursor-pointer' : ''
                }`}
                onClick={() => onClickMember?.(row)}
              >
                <div
                  className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full blur-3xl"
                  style={{ background: color, opacity: 0.18 }}
                  aria-hidden
                />
                <div className="relative space-y-4">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2.5">
                      <PartyAvatar
                        fileId={profile?.avatarFileId}
                        name={row.name}
                        color={color}
                        size={36}
                        resolvePreview={resolvePreview}
                      />
                      <span className="truncate text-base font-semibold">{row.name}</span>
                    </div>
                    <div className="flex items-center gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                      <button
                        className="rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-primary/15 hover:text-primary"
                        disabled={!canManage}
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation()
                          startEdit(row)
                        }}
                      >
                        {t('common.edit')}
                      </button>
                      {onDelete ? (
                        <button
                          className="rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                          disabled={!canManage}
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation()
                            onDelete(row)
                          }}
                        >
                          {t('common.delete')}
                        </button>
                      ) : null}
                    </div>
                  </div>

                  {hasStats ? (
                    <div className="space-y-2">
                      <div className="flex items-baseline gap-1.5">
                        <span className="font-mono text-2xl font-bold tabular-nums">
                          {stats?.txCount ?? 0}
                        </span>
                        <span className="text-[11px] text-muted-foreground">
                          {t('members.stats.unit')}
                        </span>
                      </div>
                      <div className="flex items-center justify-between gap-3 rounded-lg border border-border/40 bg-background/40 px-3 py-2 text-xs">
                        <div className="flex items-center gap-1.5 text-expense">
                          <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
                          <span className="font-mono font-semibold">
                            {stats ? fmt(stats.expense) : '0.00'}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5 text-income">
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                          <span className="font-mono font-semibold">
                            {stats ? fmt(stats.income) : '0.00'}
                          </span>
                        </div>
                      </div>
                    </div>
                  ) : null}

                  {onClickMember ? (
                    <p className="truncate text-[11px] text-muted-foreground">
                      {t('members.stats.filterHint')}
                    </p>
                  ) : null}

                  {profile && (profile.contactMethods.length > 0 || profile.bankCards.length > 0) ? (
                    <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                      {profile.contactMethods.length > 0 ? (
                        <span>{t('party.methods')} · {profile.contactMethods.length}</span>
                      ) : null}
                      {profile.bankCards.length > 0 ? (
                        <span>{t('party.cards')} · {profile.bankCards.length}</span>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) setNameError(null)
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('members.button.update') : t('members.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="space-y-1">
              <Label>{t('members.table.name')}</Label>
              <Input
                placeholder={t('members.placeholder.name')}
                value={form.name}
                onChange={(event: ChangeEvent<HTMLInputElement>) => {
                  if (nameError) setNameError(null)
                  onFormChange({ ...form, name: event.target.value })
                }}
              />
              {nameError ? <p className="text-xs text-destructive">{nameError}</p> : null}
            </div>

            <div className="space-y-2">
              <Label>{t('members.table.color')}</Label>
              <div className="flex flex-wrap gap-2">
                {TAG_COLOR_PALETTE.map((hex) => {
                  const isSelected = form.color.toUpperCase() === hex.toUpperCase()
                  const checkColor = tagTextColorOn(hex)
                  return (
                    <button
                      key={hex}
                      type="button"
                      aria-label={hex}
                      title={hex}
                      onClick={() => onFormChange({ ...form, color: hex })}
                      className={`flex h-9 w-9 items-center justify-center rounded-full transition-all ${
                        isSelected
                          ? 'scale-110 ring-2 ring-offset-2 ring-foreground ring-offset-background shadow-md'
                          : 'hover:scale-105'
                      }`}
                      style={{ background: hex }}
                    >
                      {isSelected ? (
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                             stroke={checkColor} strokeWidth="3" strokeLinecap="round"
                             strokeLinejoin="round" aria-hidden>
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      ) : null}
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('members.preview')}</Label>
              <div className="flex items-center gap-2 rounded-lg border border-border/40 bg-card/60 px-3 py-2">
                <PartyAvatar
                  fileId={form.profile.avatarFileId}
                  name={form.name}
                  color={form.color || '#94a3b8'}
                  size={28}
                  resolvePreview={resolvePreview}
                />
                <span className="text-sm font-medium">
                  {form.name.trim() || t('members.placeholder.name')}
                </span>
              </div>
            </div>

            <div className="rounded-lg border border-border p-3">
              <div className="mb-3 text-sm font-medium">{t('party.section')}</div>
              <PartyProfileEditor
                key={`member-profile:${open}:${form.editingId || 'new'}`}
                value={form.profile}
                onChange={(profile) => onFormChange({ ...form, profile })}
                upload={upload}
                resolvePreview={resolvePreview}
                disabled={!canManage}
                name={form.name}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                onReset()
                setNameError(null)
                setOpen(false)
              }}
            >
              {t('dialog.cancel')}
            </Button>
            <Button disabled={!canManage || saving} onClick={() => void guard(handleSave)}>
              {form.editingId ? t('members.button.update') : t('members.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
