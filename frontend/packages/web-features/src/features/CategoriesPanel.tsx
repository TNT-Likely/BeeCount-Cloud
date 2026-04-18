import { useMemo, useState, type ReactNode } from 'react'

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
  useT
} from '@beecount/ui'

import type { ReadCategory } from '@beecount/api-client'

import type { CategoryForm } from '../forms'

type CategoryKind = 'expense' | 'income' | 'transfer'

type CardBodyProps = {
  rows: ReadCategory[]
  onEdit: (row: ReadCategory) => void
  onDelete?: (row: ReadCategory) => void
  canManage: boolean
  showCreatorColumn: boolean
  renderIcon: (
    icon: string | null | undefined,
    iconType: string | null | undefined,
    iconCloudFileId?: string | null
  ) => ReactNode
}

/**
 * 分类卡片视图：kind tab（支出 / 收入 / 转账）+ 父分类分组，子分类以小卡嵌
 * 在父卡片下方。与 mobile 的 category_manage_page 结构对齐。
 */
function CategoriesCardBody({
  rows,
  onEdit,
  onDelete,
  canManage,
  showCreatorColumn,
  renderIcon
}: CardBodyProps) {
  const t = useT()
  const [activeKind, setActiveKind] = useState<CategoryKind>('expense')
  const grouped = useMemo(() => {
    const parentsByKind: Record<CategoryKind, ReadCategory[]> = {
      expense: [],
      income: [],
      transfer: []
    }
    const childrenByParent: Record<string, ReadCategory[]> = {}
    for (const row of rows) {
      const kind = (row.kind as CategoryKind) || 'expense'
      const parent = (row.parent_name || '').trim()
      if (parent) {
        childrenByParent[`${kind}::${parent.toLowerCase()}`] =
          childrenByParent[`${kind}::${parent.toLowerCase()}`] || []
        childrenByParent[`${kind}::${parent.toLowerCase()}`].push(row)
      } else {
        parentsByKind[kind].push(row)
      }
    }
    for (const kind of Object.keys(parentsByKind) as CategoryKind[]) {
      parentsByKind[kind].sort(
        (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name)
      )
    }
    for (const key of Object.keys(childrenByParent)) {
      childrenByParent[key].sort(
        (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name)
      )
    }
    return { parentsByKind, childrenByParent }
  }, [rows])
  const kindCounts = useMemo(
    () => ({
      expense: rows.filter((r) => r.kind === 'expense').length,
      income: rows.filter((r) => r.kind === 'income').length,
      transfer: rows.filter((r) => r.kind === 'transfer').length
    }),
    [rows]
  )

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
               strokeLinejoin="round">
            <path d="M3 6l3-3h12l3 3" />
            <path d="M3 6v14a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V6" />
            <path d="M8 11h8" />
          </svg>
        }
        title={t('categories.empty.title')}
        description={t('categories.empty.desc')}
      />
    )
  }

  const parents = grouped.parentsByKind[activeKind]
  const kinds: CategoryKind[] = ['expense', 'income', 'transfer']

  return (
    <div className="space-y-4">
      {/* tabs — 选中态用主题色背景 + 主题色左边框强化存在感，dark mode 下
          原来的 bg-card 跟 bg-muted 差异太小（基本都是深灰），看不出来。 */}
      <div className="flex gap-1 rounded-xl border border-border/50 bg-muted/30 p-1">
        {kinds.map((k) => {
          const active = k === activeKind
          const label = t(`enum.txType.${k}`)
          const count = kindCounts[k]
          return (
            <button
              key={k}
              type="button"
              aria-selected={active}
              className={`relative flex-1 rounded-lg px-3 py-2 text-sm font-medium transition-all ${
                active
                  ? 'bg-primary/15 text-primary ring-1 ring-primary/40 shadow-[0_6px_20px_-12px_hsl(var(--primary)/0.55)]'
                  : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground'
              }`}
              onClick={() => setActiveKind(k)}
            >
              <span className="inline-flex items-center gap-1.5">
                <span>{label}</span>
                <span
                  className={`rounded-full px-1.5 py-0.5 text-[10px] leading-none ${
                    active ? 'bg-primary/25 text-primary' : 'bg-muted text-muted-foreground/80'
                  }`}
                >
                  {count}
                </span>
              </span>
            </button>
          )
        })}
      </div>

      {parents.length === 0 ? (
        <div className="py-8 text-center text-xs text-muted-foreground">
          {t('categories.empty.byType')}
        </div>
      ) : (
        <div className="space-y-3">
          {parents.map((parent) => {
            const children =
              grouped.childrenByParent[`${activeKind}::${parent.name.toLowerCase()}`] || []
            return (
              <div key={parent.id} className="rounded-xl border border-border/60 bg-card/60 p-3">
                {/* parent row */}
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2.5">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                      {renderIcon(parent.icon, parent.icon_type, parent.icon_cloud_file_id)}
                    </div>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">{parent.name}</div>
                      {showCreatorColumn ? (
                        <div className="truncate text-[11px] text-muted-foreground">
                          {parent.created_by_email || parent.created_by_user_id || '-'}
                        </div>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      className="text-xs text-muted-foreground hover:text-primary"
                      disabled={!canManage}
                      type="button"
                      onClick={() => onEdit(parent)}
                    >
                      {t('common.edit')}
                    </button>
                    {onDelete ? (
                      <button
                        className="text-xs text-muted-foreground hover:text-destructive"
                        disabled={!canManage}
                        type="button"
                        onClick={() => onDelete(parent)}
                      >
                        {t('common.delete')}
                      </button>
                    ) : null}
                  </div>
                </div>
                {/* children grid */}
                {children.length > 0 ? (
                  <div className="mt-3 grid gap-2 pl-12 sm:grid-cols-2 lg:grid-cols-3">
                    {children.map((child) => (
                      <div
                        key={child.id}
                        className="group flex items-center justify-between gap-2 rounded-lg border border-border/40 bg-background/60 px-2.5 py-1.5 text-xs"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-muted/50">
                            {renderIcon(child.icon, child.icon_type, child.icon_cloud_file_id)}
                          </div>
                          <span className="truncate">{child.name}</span>
                        </div>
                        <div className="flex items-center gap-2 opacity-0 transition-opacity group-hover:opacity-100">
                          <button
                            className="text-[10px] text-muted-foreground hover:text-primary"
                            disabled={!canManage}
                            type="button"
                            onClick={() => onEdit(child)}
                          >
                            {t('common.edit')}
                          </button>
                          {onDelete ? (
                            <button
                              className="text-[10px] text-muted-foreground hover:text-destructive"
                              disabled={!canManage}
                              type="button"
                              onClick={() => onDelete(child)}
                            >
                              {t('common.delete')}
                            </button>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

type CategoriesPanelProps = {
  form: CategoryForm
  rows: ReadCategory[]
  iconPreviewUrlByFileId?: Record<string, string>
  canManage: boolean
  showCreatorColumn?: boolean
  onFormChange: (next: CategoryForm) => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onEdit: (row: ReadCategory) => void
  onDelete?: (row: ReadCategory) => void
  /** Upload a custom icon file to the cloud and return the refs to store in the form. */
  onUploadIcon?: (file: File) => Promise<{ fileId: string; sha256: string } | null>
}

export function CategoriesPanel({
  form,
  rows,
  iconPreviewUrlByFileId = {},
  canManage,
  showCreatorColumn = false,
  onFormChange,
  onSave,
  onReset,
  onEdit,
  onDelete,
  onUploadIcon
}: CategoriesPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const parentOptions = rows
    .map((row) => row.name.trim())
    .filter((name) => name.length > 0 && name !== form.name.trim())
    .sort((a, b) => a.localeCompare(b))

  // 只输出一个"小图标视觉"，不再带名字文本/badge —— 放在 h-9 w-9 的方块里
  // 要小而稳定，不能出现原来那样图标名 + Material/Custom 标签叠加导致的错乱
  // 排版。图标数据三种来源：云端文件（有 cloudFileId + 本地 map 到 URL）>
  // 文件本身是 URL/路径（绝对路径时直接显示）> material 名字（取首字母）。
  const renderIcon = (
    icon: string | null | undefined,
    iconType: string | null | undefined,
    iconCloudFileId?: string | null
  ) => {
    const normalized = (icon || '').trim()
    const kind = (iconType || 'material').trim() || 'material'
    const cloudFileId = typeof iconCloudFileId === 'string' ? iconCloudFileId.trim() : ''
    const cloudPreview = cloudFileId ? iconPreviewUrlByFileId[cloudFileId] : undefined
    if (kind === 'custom' && cloudPreview) {
      return (
        <img
          alt=""
          className="h-full w-full rounded-[inherit] object-cover"
          src={cloudPreview}
        />
      )
    }
    if (kind === 'custom' && /^(https?:\/\/|data:image\/|\/)/.test(normalized)) {
      return (
        <img
          alt=""
          className="h-full w-full rounded-[inherit] object-cover"
          src={normalized}
        />
      )
    }
    const letter = (normalized[0] || '?').toUpperCase()
    return (
      <span className="text-[13px] font-medium text-primary">{letter}</span>
    )
  }

  return (
    <>
      <CategoriesCardBody
        rows={rows}
        onEdit={(row) => {
          onEdit(row)
          setOpen(true)
        }}
        onDelete={onDelete}
        canManage={canManage}
        showCreatorColumn={showCreatorColumn}
        renderIcon={renderIcon}
      />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{form.editingId ? t('categories.button.update') : t('categories.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('categories.table.name')}</Label>
              <Input
                placeholder={t('categories.placeholder.name')}
                value={form.name}
                onChange={(e) => onFormChange({ ...form, name: e.target.value })}
              />
            </div>
            {/* 编辑模式下只允许改名；新建模式下保留全部字段。
                其他字段在 mobile 端是跟着分类模板固化的，web 上改风险大，先屏蔽。 */}
            {form.editingId ? null : (
            <>
            <div className="space-y-1">
              <Label>{t('categories.table.kind')}</Label>
              <Select
                value={form.kind}
                onValueChange={(value) => onFormChange({ ...form, kind: value as CategoryForm['kind'] })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="expense">{t('enum.txType.expense')}</SelectItem>
                  <SelectItem value="income">{t('enum.txType.income')}</SelectItem>
                  <SelectItem value="transfer">{t('enum.txType.transfer')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <Label>{t('categories.table.level')}</Label>
                <Input
                  placeholder={t('categories.placeholder.level')}
                  value={form.level}
                  onChange={(e) => onFormChange({ ...form, level: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('categories.table.sort')}</Label>
                <Input
                  placeholder={t('categories.placeholder.sort')}
                  value={form.sort_order}
                  onChange={(e) => onFormChange({ ...form, sort_order: e.target.value })}
                />
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <Label>{t('categories.placeholder.icon')}</Label>
                <Input
                  placeholder={t('categories.placeholder.icon')}
                  value={form.icon}
                  onChange={(e) => onFormChange({ ...form, icon: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('categories.placeholder.iconType')}</Label>
                <Select
                  value={form.icon_type || 'material'}
                  onValueChange={(value) => onFormChange({ ...form, icon_type: value })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('categories.placeholder.iconType')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="material">material</SelectItem>
                    <SelectItem value="custom">custom</SelectItem>
                    <SelectItem value="community">community</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('categories.placeholder.parent')}</Label>
                <Select
                  value={form.parent_name || '__none__'}
                  onValueChange={(value) => onFormChange({ ...form, parent_name: value === '__none__' ? '' : value })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('categories.placeholder.parent')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">{t('common.none')}</SelectItem>
                    {parentOptions.map((name) => (
                      <SelectItem key={name} value={name}>
                        {name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            </>
            )}
            {!form.editingId && onUploadIcon && form.icon_type === 'custom' ? (
              <div className="space-y-1">
                <Label>{t('categories.placeholder.customIcon')}</Label>
                <div className="flex items-center gap-2">
                  <input
                    type="file"
                    accept="image/*"
                    className="text-sm"
                    onChange={async (e) => {
                      const file = e.target.files?.[0]
                      // Reset input so the same file can be re-picked after reset.
                      e.currentTarget.value = ''
                      if (!file) return
                      const res = await onUploadIcon(file)
                      if (res) {
                        onFormChange({
                          ...form,
                          icon_cloud_file_id: res.fileId,
                          icon_cloud_sha256: res.sha256
                        })
                      }
                    }}
                  />
                  {form.icon_cloud_file_id ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onFormChange({ ...form, icon_cloud_file_id: '', icon_cloud_sha256: '' })}
                    >
                      {t('common.remove')}
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : null}
            <div className="space-y-1">
              <Label>{t('categories.preview')}</Label>
              <div className="rounded-md border border-border/70 bg-muted/40 px-3 py-2">
                {renderIcon(form.icon, form.icon_type, form.icon_cloud_file_id)}
              </div>
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
              {form.editingId ? t('categories.button.update') : t('categories.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
