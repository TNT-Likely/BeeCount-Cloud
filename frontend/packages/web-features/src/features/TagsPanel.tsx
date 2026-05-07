import { useMemo, useState, type ChangeEvent } from 'react'

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
  useT
} from '@beecount/ui'

import type { ReadTag } from '@beecount/api-client'

import type { TagForm } from '../forms'
import {
  TAG_COLOR_PALETTE,
  tagTextColorOn
} from '../lib/tagColorPalette'

type TagsPanelProps = {
  form: TagForm
  rows: ReadTag[]
  canManage: boolean
  showCreatorColumn?: boolean
  /** 按 tag.id 查询的统计（交易数/支出/收入），未传则不展开详情。 */
  statsById?: Record<string, { count: number; expense: number; income: number }>
  onFormChange: (next: TagForm) => void
  /** 触发"新建"流程:外层负责把 form 重置成 tagDefaults() 并打开 dialog。 */
  onCreate?: () => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onEdit: (row: ReadTag) => void
  onDelete?: (row: ReadTag) => void
  /** 点击卡片（非编辑/删除按钮）触发：外层用来打开"标签详情+交易"弹窗。 */
  onClickTag?: (tag: ReadTag) => void
}

/**
 * 标签管理面板。
 *
 * 这一版加了:
 * - "新建标签" 按钮(顶部右上角,以及 EmptyState CTA)
 * - 编辑/新建对话框里的 20 色调色板(`TAG_COLOR_PALETTE`,跟 app 一一对齐)
 * - 前端查重:保存前先用现有 `rows`(workspace tags,已经按用户作用域查回)
 *   检查同名,不让用户走完一圈 server 才报错。server 自身仍然兜底 dedup,
 *   双重保险。
 */
export function TagsPanel({
  form,
  rows,
  canManage,
  showCreatorColumn = false,
  statsById,
  onFormChange,
  onCreate,
  onSave,
  onReset,
  onEdit,
  onDelete,
  onClickTag
}: TagsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [duplicateError, setDuplicateError] = useState<string | null>(null)
  const hasStats = Boolean(statsById)
  const fmt = (v: number) =>
    v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  // 同名查重:把当前用户已有标签名字小写化收成 Set,提交时 O(1) 查。编辑模
  // 式下排除自己 (form.editingId 对应的行) 以允许"改色不改名"。
  // rows 是 fetchWorkspaceTags 返回的,已经按 current_user.id 过滤,所以这
  // 里的 dedup 自然是"用户作用域"的,不是单账本作用域。
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
    setDuplicateError(null)
    onCreate?.()
    setOpen(true)
  }

  const startEdit = (row: ReadTag) => {
    setDuplicateError(null)
    onEdit(row)
    setOpen(true)
  }

  const handleSave = async () => {
    const trimmed = form.name.trim()
    if (!trimmed) {
      setDuplicateError(t('tags.error.nameRequired'))
      return
    }
    if (existingNamesLower.has(trimmed.toLowerCase())) {
      setDuplicateError(t('tags.error.nameDuplicate'))
      return
    }
    setDuplicateError(null)
    const success = await onSave()
    if (success) {
      setOpen(false)
    }
  }

  return (
    <>
      {/* 顶部操作条:右上角"新建标签"。即使 rows 为空也保留(EmptyState 那边
          也会再放一个 CTA 按钮,两处都点都能创建)。 */}
      {onCreate && canManage ? (
        <div className="mb-4 flex justify-end">
          <Button onClick={startCreate}>{t('tags.button.create')}</Button>
        </div>
      ) : null}

      {rows.length === 0 ? (
        <EmptyState
          icon={
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
                 strokeLinejoin="round">
              <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
              <circle cx="7" cy="7" r="1.5" />
            </svg>
          }
          title={t('tags.empty.title')}
          description={t('tags.empty.desc')}
          action={
            onCreate && canManage ? (
              <Button onClick={startCreate}>{t('tags.button.create')}</Button>
            ) : undefined
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {rows.map((row) => {
            const stats = statsById?.[row.id]
            const color = row.color || '#94a3b8'
            return (
              <div
                key={row.id}
                className={`group relative overflow-hidden rounded-2xl border border-border/50 bg-card/80 p-5 backdrop-blur-sm transition-all hover:-translate-y-0.5 hover:border-border hover:shadow-lg ${
                  onClickTag ? 'cursor-pointer' : ''
                }`}
                onClick={() => onClickTag?.(row)}
              >
                {/* 磨砂色斑 + tag 颜色的渐变底，整张卡有"主题色"感。dark 模式下
                    opacity 稍微拉一点避免过暗。 */}
                <div
                  className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full blur-3xl"
                  style={{ background: color, opacity: 0.18 }}
                  aria-hidden
                />
                <div
                  className="pointer-events-none absolute inset-x-0 bottom-0 h-20 opacity-40"
                  style={{
                    background: `linear-gradient(to top, ${color}14, transparent)`
                  }}
                  aria-hidden
                />
                <div className="relative space-y-4">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2.5">
                      {/* 左侧"#"徽章用 tag 颜色填充，像社交软件 hashtag 风格 */}
                      <span
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-base font-bold text-white shadow-sm"
                        style={{ background: color }}
                      >
                        #
                      </span>
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
                      {/* 主统计：笔数放最显眼位 */}
                      <div className="flex items-baseline gap-1.5">
                        <span className="font-mono text-2xl font-bold tabular-nums">
                          {stats?.count ?? 0}
                        </span>
                        <span className="text-[11px] text-muted-foreground">{t('tags.count.unit')}</span>
                      </div>
                      {/* 次要统计：支出/收入左右排 */}
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
                  {showCreatorColumn ? (
                    <div className="truncate text-[11px] text-muted-foreground">
                      {row.created_by_email || row.created_by_user_id || '-'}
                    </div>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <Dialog open={open} onOpenChange={(next) => {
        setOpen(next)
        if (!next) setDuplicateError(null)
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{form.editingId ? t('tags.button.update') : t('tags.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4">
            {/* 名称 */}
            <div className="space-y-1">
              <Label>{t('tags.table.name')}</Label>
              <Input
                placeholder={t('tags.placeholder.name')}
                value={form.name}
                onChange={(event: ChangeEvent<HTMLInputElement>) => {
                  // 用户输入时清掉错误提示,避免改完字还红着不放
                  if (duplicateError) setDuplicateError(null)
                  onFormChange({ ...form, name: event.target.value })
                }}
              />
              {duplicateError ? (
                <p className="text-xs text-destructive">{duplicateError}</p>
              ) : null}
            </div>

            {/* 颜色选择器:20 色调色板,grid 布局排成两行 */}
            <div className="space-y-2">
              <Label>{t('tags.table.color')}</Label>
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
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke={checkColor}
                          strokeWidth="3"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          aria-hidden
                        >
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      ) : null}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* 预览:用当前选的 color + name 渲染一个 hashtag 徽章,所见即所得 */}
            <div className="space-y-1">
              <Label>{t('tags.preview')}</Label>
              <div className="flex items-center gap-2 rounded-lg border border-border/40 bg-card/60 px-3 py-2">
                <span
                  className="flex h-7 w-7 items-center justify-center rounded-lg text-sm font-bold text-white shadow-sm"
                  style={{ background: form.color || '#94a3b8' }}
                >
                  #
                </span>
                <span className="text-sm font-medium">
                  {form.name.trim() || t('tags.placeholder.name')}
                </span>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                onReset()
                setDuplicateError(null)
                setOpen(false)
              }}
            >
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={!canManage}
              onClick={() => void handleSave()}
            >
              {form.editingId ? t('tags.button.update') : t('tags.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
