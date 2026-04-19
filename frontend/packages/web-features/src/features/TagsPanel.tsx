import { useState, type ChangeEvent } from 'react'

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

type TagsPanelProps = {
  form: TagForm
  rows: ReadTag[]
  canManage: boolean
  showCreatorColumn?: boolean
  /** 按 tag.id 查询的统计（交易数/支出/收入），未传则不展开详情。 */
  statsById?: Record<string, { count: number; expense: number; income: number }>
  onFormChange: (next: TagForm) => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onEdit: (row: ReadTag) => void
  onDelete?: (row: ReadTag) => void
  /** 点击卡片（非编辑/删除按钮）触发：外层用来打开"标签详情+交易"弹窗。 */
  onClickTag?: (tag: ReadTag) => void
}

export function TagsPanel({
  form,
  rows,
  canManage,
  showCreatorColumn = false,
  statsById,
  onFormChange,
  onSave,
  onReset,
  onEdit,
  onDelete,
  onClickTag
}: TagsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const hasStats = Boolean(statsById)
  const fmt = (v: number) =>
    v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  return (
    <>
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
                          onEdit(row)
                          setOpen(true)
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

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{form.editingId ? t('tags.button.update') : t('tags.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('tags.table.name')}</Label>
              <Input
                placeholder={t('tags.placeholder.name')}
                value={form.name}
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  onFormChange({ ...form, name: event.target.value })
                }
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                onReset()
                setOpen(false)
              }}
            >
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={!canManage}
              onClick={async () => {
                const success = await onSave()
                if (success) {
                  setOpen(false)
                }
              }}
            >
              {form.editingId ? t('tags.button.update') : t('tags.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
