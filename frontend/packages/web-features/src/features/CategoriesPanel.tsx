import { useEffect, useMemo, useState, type ReactNode } from 'react'

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

import type { ReadCategory, WorkspaceCategory } from '@beecount/api-client'

import { CategoryIcon } from '../components/CategoryIcon'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { getIconGroupsByKind, type CategoryIconItem } from '../lib/categoryIconGroups'
import type { CategoryForm } from '../forms'

type CategoryKind = 'expense' | 'income' | 'transfer'

/** 用户能新建的分类类型。`transfer` 是虚拟分类(转账自动归类),系统种子,
 *  app 端也不允许用户手动创建,这里同步限制。 */
const CREATABLE_KINDS: ReadonlyArray<Extract<CategoryKind, 'expense' | 'income'>> = [
  'expense',
  'income',
]

type CardBodyProps = {
  rows: WorkspaceCategory[]
  onEdit: (row: ReadCategory) => void
  onDelete?: (row: ReadCategory) => void
  canManage: boolean
  showCreatorColumn: boolean
  /** 按 category.id 查询的笔数。card 上展示笔数 badge,跟 mobile 对齐。 */
  txCountById?: Record<string, number>
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
  txCountById = {},
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
    return null  // 空态由外层 panel 渲染(带新建 CTA)
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
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-semibold">{parent.name}</span>
                        {/* 笔数 badge:跟 app 端 category_manage_page 对齐,
                            一眼看出哪个分类用得最多。0 笔的会显示 0,跟 tag 行为一致。 */}
                        <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] leading-none text-muted-foreground tabular-nums">
                          {txCountById[parent.id] ?? 0} {t('tags.count.unit')}
                        </span>
                      </div>
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
                          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[9px] leading-none text-muted-foreground tabular-nums">
                            {txCountById[child.id] ?? 0} {t('tags.count.unit')}
                          </span>
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

/**
 * 分类图标选择器。
 *
 * 跟 app 端 `lib/pages/category/icon_picker_page.dart` 行为一致:
 * - 按当前 `kind`(支出 / 收入)拿到分组(`getIconGroupsByKind`),组里都是
 *   app 已经在用的图标(8 组支出 + 4 组收入,≈70 个),所有 key 都在
 *   categoryIconMap.ts 的 KNOWN_NAMES / FLUTTER_RENAMES 里有定义,Material
 *   Symbols 字体一定能渲出来,不会再出现"图标渲成字面文字"的乱码。
 * - 顶部 group tabs(餐饮/出行/购物...)切换。
 * - 搜索框:有内容时切换为"跨组按 key 模糊匹配"的扁平结果(label 也参与匹配)。
 * - 选中后 onSelect 写回 form.icon 并关闭;stored 值保持跟 app 端 stored 值
 *   一致(例如"part_time" 这种 FLUTTER_RENAMES 的 key,保存的是 part_time
 *   不是 schedule,跨端解释靠 resolveMaterialIconName)。
 */
function IconPickerDialog({
  open,
  kind,
  currentIcon,
  onClose,
  onSelect,
}: {
  open: boolean
  /** 当前编辑的分类类型,决定 picker 显示支出还是收入图标分组 */
  kind: string
  currentIcon: string | null | undefined
  onClose: () => void
  onSelect: (icon: string) => void
}) {
  const t = useT()
  const groups = getIconGroupsByKind(kind)
  const [activeGroupIdx, setActiveGroupIdx] = useState(0)
  const [query, setQuery] = useState('')

  useEffect(() => {
    if (!open) {
      setQuery('')
      setActiveGroupIdx(0)
    }
  }, [open])

  // kind 改了重置选中 tab,避免上次留下的索引超出新 group 长度
  useEffect(() => {
    setActiveGroupIdx(0)
  }, [kind])

  const isSearching = query.trim().length > 0

  // 搜索:跨组按 key 包含 / label 包含 模糊匹配
  const searchResults = useMemo<CategoryIconItem[]>(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    const seen = new Set<string>()
    const out: CategoryIconItem[] = []
    for (const group of groups) {
      for (const item of group.icons) {
        if (seen.has(item.key)) continue
        const matchKey = item.key.toLowerCase().includes(q)
        const matchLabel = item.label.toLowerCase().includes(q)
        if (matchKey || matchLabel) {
          seen.add(item.key)
          out.push(item)
        }
      }
    }
    return out
  }, [groups, query])

  const visibleIcons: CategoryIconItem[] = isSearching
    ? searchResults
    : groups[Math.min(activeGroupIdx, groups.length - 1)]?.icons ?? []

  const renderItem = (item: CategoryIconItem) => {
    const isSelected =
      (currentIcon || '').trim().toLowerCase() === item.key.toLowerCase()
    return (
      <button
        key={item.key}
        type="button"
        title={item.key}
        aria-label={item.label}
        onClick={() => {
          onSelect(item.key)
          onClose()
        }}
        className={`flex h-16 w-full flex-col items-center justify-center gap-1 rounded-lg border transition-all ${
          isSelected
            ? 'border-primary bg-primary/10 text-primary ring-1 ring-primary/50'
            : 'border-border/40 bg-card hover:border-primary/40 hover:bg-accent/40'
        }`}
      >
        <CategoryIcon icon={item.key} iconType="material" size={22} />
        <span className="truncate text-[10px] leading-none text-muted-foreground">
          {item.label}
        </span>
      </button>
    )
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose() }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('categories.iconPicker.title')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <Input
            placeholder={t('categories.iconPicker.search')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />

          {/* group tabs:搜索时隐藏,搜索结果优先 */}
          {!isSearching ? (
            <div className="flex flex-wrap gap-1.5">
              {groups.map((group, idx) => {
                const active = idx === activeGroupIdx
                return (
                  <button
                    key={group.labelKey}
                    type="button"
                    onClick={() => setActiveGroupIdx(idx)}
                    className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                      active
                        ? 'bg-primary/15 text-primary ring-1 ring-primary/40'
                        : 'text-muted-foreground hover:bg-accent/40 hover:text-foreground'
                    }`}
                  >
                    {t(group.labelKey)}
                  </button>
                )
              })}
            </div>
          ) : null}

          <div className="max-h-[55vh] overflow-y-auto">
            {visibleIcons.length === 0 ? (
              <div className="py-8 text-center text-xs text-muted-foreground">
                {t('categories.iconPicker.empty')}
              </div>
            ) : (
              <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
                {visibleIcons.map(renderItem)}
              </div>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

type CategoriesPanelProps = {
  form: CategoryForm
  rows: WorkspaceCategory[]
  iconPreviewUrlByFileId?: Record<string, string>
  canManage: boolean
  showCreatorColumn?: boolean
  /** category.id → 笔数。从 WorkspaceCategoryOut.tx_count 派生,跨账本聚合后
   *  的总笔数。CardBody 上展示 + 编辑 level=2 时父级候选过滤都会用。 */
  txCountById?: Record<string, number>
  onFormChange: (next: CategoryForm) => void
  /** 触发"新建"流程:外层负责把 form 重置成 categoryDefaults() 并打开 dialog。 */
  onCreate?: () => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onEdit: (row: ReadCategory) => void
  onDelete?: (row: ReadCategory) => void
  /** Upload a custom icon file to the cloud and return the refs to store in the form. */
  onUploadIcon?: (file: File) => Promise<{ fileId: string; sha256: string } | null>
  /** 受控 dialog 开关 — 让外层(如详情弹窗 → 编辑链)能命令式打开本 panel
   *  的编辑 dialog。不传时 panel 内部用 state 自己管;传了就 controlled。 */
  dialogOpen?: boolean
  onDialogOpenChange?: (next: boolean) => void
  /** 不渲染列表/EmptyState,只挂 Dialog + picker — 用于全局编辑容器复用。 */
  dialogOnlyMode?: boolean
}

/**
 * 分类管理面板。
 *
 * 这一版对齐 app 能力:
 * - 顶部"新建分类" CTA(EmptyState 同 CTA)
 * - kind 限 expense / income(transfer 是虚拟分类用户不能新建,跟 app
 *   _categoryRepo.createCategory 的契约一致)
 * - 父子层级:选了"父分类"自动 level=2,清空则 level=1。隐藏 level/sort 文本输
 *   入,避免用户填错。父分类候选按当前 kind 过滤、只列已存在的 level=1。
 * - 同 kind 同名前端查重(workspace 维度,server 兜底)
 * - 图标用 Material Symbols 网格选择器替代裸文本输入,~290 个图标 + 搜索;
 *   custom 模式仍走文件上传走 cloud attachment
 * - 编辑模式打开**所有字段**(原版只允许改名)
 */
export function CategoriesPanel({
  form,
  rows,
  iconPreviewUrlByFileId = {},
  canManage,
  showCreatorColumn = false,
  txCountById = {},
  onFormChange,
  onCreate,
  onSave,
  onReset,
  onEdit,
  onDelete,
  onUploadIcon,
  dialogOpen,
  onDialogOpenChange,
  dialogOnlyMode
}: CategoriesPanelProps) {
  const t = useT()
  const [internalOpen, setInternalOpen] = useState(false)
  const open = dialogOpen ?? internalOpen
  const setOpen = (next: boolean) => {
    if (onDialogOpenChange) onDialogOpenChange(next)
    else setInternalOpen(next)
  }
  const [iconPickerOpen, setIconPickerOpen] = useState(false)
  const [parentPickerOpen, setParentPickerOpen] = useState(false)
  const [duplicateError, setDuplicateError] = useState<string | null>(null)

  // 父分类候选:跟当前编辑/新建的 kind 一致 + 必须是 level=1(顶级) + 排除自
  // 己(避免自己挂自己当父的死循环)。app 端 createSubCategory 只允许 level=2
  // 挂 level=1 父,这里同 contract。
  //
  // **额外规则**(对齐 app):候选父级必须**自身笔数为 0**。一个 level=1 分类
  // 一旦有了直接交易就不能再加子分类——交易跟子分类的归属会模糊(原本计入
  // 父分类的钱,加完子分类应该挂哪个?app 用这个规则强制"先空再分"的卫生),
  // 跨设备同样适用。
  const parentCandidateRows = useMemo(() => {
    const editingId = form.editingId
    return rows.filter((row) => {
      if (row.kind !== form.kind) return false
      if (Number(row.level) !== 1) return false
      if (editingId && row.id === editingId) return false
      if ((txCountById[row.id] ?? 0) > 0) return false
      return (row.name || '').trim().length > 0
    })
  }, [rows, form.kind, form.editingId, txCountById])

  // 当前选中的父级 row(用 form.parent_name 反查同 kind 的 level=1) — 用于
  // CategoryPickerDialog 的 selectedId 高亮 + 触发按钮显示图标。
  const selectedParentRow = useMemo(() => {
    const name = (form.parent_name || '').trim().toLowerCase()
    if (!name) return null
    return (
      rows.find(
        (row) =>
          row.kind === form.kind &&
          Number(row.level) === 1 &&
          (row.name || '').trim().toLowerCase() === name,
      ) ?? null
    )
  }, [rows, form.kind, form.parent_name])

  // 同 kind 同名查重(workspace 维度。fetchWorkspaceCategories 已经按
  // current_user.id 过滤,所以 rows 自然是用户作用域的)。编辑模式排除自己以
  // 允许"改图标不改名"。case-insensitive,跟 server snapshot_mutator 对齐。
  const existingNamesLower = useMemo(() => {
    const set = new Set<string>()
    for (const row of rows) {
      if (form.editingId && row.id === form.editingId) continue
      if (row.kind !== form.kind) continue
      const name = (row.name || '').trim().toLowerCase()
      if (name) set.add(name)
    }
    return set
  }, [rows, form.kind, form.editingId])

  const renderIcon = (
    icon: string | null | undefined,
    iconType: string | null | undefined,
    iconCloudFileId?: string | null
  ) => (
    <CategoryIcon
      icon={icon}
      iconType={iconType}
      iconCloudFileId={iconCloudFileId}
      iconPreviewUrlByFileId={iconPreviewUrlByFileId}
      size={20}
      className="text-primary"
    />
  )

  const startCreate = () => {
    if (!canManage) return
    setDuplicateError(null)
    onCreate?.()
    setOpen(true)
  }

  const startEdit = (row: ReadCategory) => {
    setDuplicateError(null)
    onEdit(row)
    setOpen(true)
  }

  const handleSave = async () => {
    const trimmed = form.name.trim()
    if (!trimmed) {
      setDuplicateError(t('categories.error.nameRequired'))
      return
    }
    if (existingNamesLower.has(trimmed.toLowerCase())) {
      setDuplicateError(t('categories.error.nameDuplicate'))
      return
    }
    // 自定义图片必须有图。点了 remove 但还没重新上传就保存是非法状态:
    // server 落库会得到 icon_type='custom' 但 icon_cloud_file_id 空 → web/app
    // 渲不出来。这里前端拦下,要么用户重传图,要么改回 material。
    if (form.icon_type === 'custom') {
      const hasCloudFile = (form.icon_cloud_file_id || '').trim().length > 0
      const hasUrl = /^(https?:\/\/|data:image\/|\/)/.test((form.icon || '').trim())
      if (!hasCloudFile && !hasUrl) {
        setDuplicateError(t('categories.error.customIconRequired'))
        return
      }
    }
    setDuplicateError(null)
    const success = await onSave()
    if (success) {
      setOpen(false)
    }
  }

  const isEmpty = rows.length === 0

  return (
    <>
      {/* dialogOnlyMode: 全局编辑容器复用 Dialog + picker,不渲染列表 */}
      {!dialogOnlyMode && (
        <>
          {/* 顶部操作条:右上角"新建分类"。即使 rows 为空也保留 */}
          {onCreate && canManage ? (
            <div className="mb-4 flex justify-end">
              <Button onClick={startCreate}>{t('categories.button.create')}</Button>
            </div>
          ) : null}

          {isEmpty ? (
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
              action={
                onCreate && canManage ? (
                  <Button onClick={startCreate}>{t('categories.button.create')}</Button>
                ) : undefined
              }
            />
          ) : (
            <CategoriesCardBody
              rows={rows}
              onEdit={startEdit}
              onDelete={onDelete}
              canManage={canManage}
              showCreatorColumn={showCreatorColumn}
              txCountById={txCountById}
              renderIcon={renderIcon}
            />
          )}
        </>
      )}

      <Dialog open={open} onOpenChange={(next) => {
        setOpen(next)
        if (!next) setDuplicateError(null)
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{form.editingId ? t('categories.button.update') : t('categories.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            {/* 名称 */}
            <div className="space-y-1">
              <Label>{t('categories.table.name')}</Label>
              <Input
                placeholder={t('categories.placeholder.name')}
                value={form.name}
                onChange={(e) => {
                  if (duplicateError) setDuplicateError(null)
                  onFormChange({ ...form, name: e.target.value })
                }}
              />
              {duplicateError ? (
                <p className="text-xs text-destructive">{duplicateError}</p>
              ) : null}
            </div>

            {/* 类型(收/支)。transfer 不在选项里 —— 那是系统种子虚拟分类,用
                户不能手动创建,跟 app 行为对齐。
                **编辑模式下 kind 不可改**:跨 kind 变更会让所有引用此分类的交易
                归类错乱,且子分类按 (parent_name, kind) 匹配父级会错位,跟 app
                category_edit_page 的限制对齐。 */}
            <div className="space-y-1">
              <Label>{t('categories.table.kind')}</Label>
              {form.editingId ? (
                <div className="flex items-center justify-between rounded-md border border-border/40 bg-muted/30 px-3 py-2 text-sm">
                  <span>{t(`enum.txType.${form.kind}`)}</span>
                  <span className="text-xs text-muted-foreground">
                    {t('categories.kind.locked')}
                  </span>
                </div>
              ) : (
                <Select
                  value={CREATABLE_KINDS.includes(form.kind as 'expense' | 'income')
                    ? form.kind
                    : 'expense'}
                  onValueChange={(value) => {
                    // 改 kind 后,parent_name 可能跟新 kind 不匹配,清掉避免幻象。
                    onFormChange({
                      ...form,
                      kind: value as CategoryForm['kind'],
                      parent_name: '',
                      level: '1',
                    })
                  }}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CREATABLE_KINDS.map((k) => (
                      <SelectItem key={k} value={k}>
                        {t(`enum.txType.${k}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>

            {/* 父分类。
                **新建**:可选,选了 → level=2(子分类),不选 → level=1(顶级)。
                **编辑 level=1**:锁住"无父分类",不允许把顶级降级成子级——会让
                现有子分类归属错位 + 跟 app 行为对齐("一级分类无法升级为二级")。
                **编辑 level=2**:可以换到另一个同 kind 的父分类,但**不能**清空回
                level=1(同样的归属错位风险)。 */}
            <div className="space-y-1">
              <Label>{t('categories.placeholder.parent')}</Label>
              {form.editingId && form.level === '1' ? (
                <div className="flex items-center justify-between rounded-md border border-border/40 bg-muted/30 px-3 py-2 text-sm">
                  <span className="text-muted-foreground">
                    {t('common.none')}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {t('categories.parent.lockedTopLevel')}
                  </span>
                </div>
              ) : (
                // 跟 SelectTrigger 同样的尺寸 / 背景色,保证 form 里几个 select
                // 视觉对齐(h-10 + bg-muted + border-input + px-3 py-2)。图标
                // 用 h-6 w-6 圆形小徽章,塞得进 40px 高度。
                <button
                  type="button"
                  onClick={() => setParentPickerOpen(true)}
                  className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {selectedParentRow ? (
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15">
                      {renderIcon(
                        selectedParentRow.icon,
                        selectedParentRow.icon_type,
                        selectedParentRow.icon_cloud_file_id,
                      )}
                    </span>
                  ) : null}
                  <span className="flex-1 truncate">
                    {form.parent_name?.trim() || t('common.none')}
                  </span>
                  <span className="text-xs text-muted-foreground opacity-60">▾</span>
                </button>
              )}
            </div>

            {/* 图标:material 走网格选择器,custom 走文件上传 */}
            <div className="space-y-1">
              <Label>{t('categories.placeholder.iconType')}</Label>
              <Select
                value={form.icon_type || 'material'}
                onValueChange={(value) => onFormChange({ ...form, icon_type: value })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="material">{t('categories.iconType.material')}</SelectItem>
                  <SelectItem value="custom">{t('categories.iconType.custom')}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {(form.icon_type || 'material') === 'material' ? (
              <div className="space-y-1">
                <Label>{t('categories.placeholder.icon')}</Label>
                <button
                  type="button"
                  onClick={() => setIconPickerOpen(true)}
                  className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                >
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15">
                    {renderIcon(form.icon || 'category', 'material')}
                  </span>
                  <span className="flex-1 truncate font-mono text-xs text-muted-foreground">
                    {form.icon || t('categories.iconPicker.choose')}
                  </span>
                  <span className="text-xs text-muted-foreground opacity-60">▾</span>
                </button>
              </div>
            ) : null}

            {form.icon_type === 'custom' && onUploadIcon ? (
              <div className="space-y-1">
                <Label>{t('categories.placeholder.customIcon')}</Label>
                <div className="flex items-center gap-2">
                  <input
                    type="file"
                    accept="image/*"
                    className="text-sm"
                    onChange={async (e) => {
                      const file = e.target.files?.[0]
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
                  {form.icon_cloud_file_id || form.custom_icon_path ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        // 同时清掉 cloud refs(server 端 GC 孤儿 attachment)和
                        // custom_icon_path(app 端 _applyCategoryChange 收到 path 空 →
                        // 删本地 custom_icons 文件)。点 remove 是"彻底丢掉这张图"
                        // 的语义,不只是清云端引用。
                        onFormChange({
                          ...form,
                          icon_cloud_file_id: '',
                          icon_cloud_sha256: '',
                          custom_icon_path: '',
                        })
                      }
                    >
                      {t('common.remove')}
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : null}

            {/* 预览 */}
            <div className="space-y-1">
              <Label>{t('categories.preview')}</Label>
              <div className="flex items-center gap-2 rounded-md border border-border/70 bg-muted/40 px-3 py-2">
                <div className="flex h-9 w-9 items-center justify-center rounded-md bg-primary/10">
                  {renderIcon(form.icon || 'category', form.icon_type, form.icon_cloud_file_id)}
                </div>
                <span className="text-sm font-medium">
                  {form.name.trim() || t('categories.placeholder.name')}
                </span>
                {form.parent_name ? (
                  <span className="text-xs text-muted-foreground">
                    ({form.parent_name})
                  </span>
                ) : null}
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
              {form.editingId ? t('categories.button.update') : t('categories.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <IconPickerDialog
        open={iconPickerOpen}
        kind={form.kind}
        currentIcon={form.icon}
        onClose={() => setIconPickerOpen(false)}
        onSelect={(icon) => onFormChange({ ...form, icon, icon_type: 'material' })}
      />

      {/* 父级分类 picker —— 跟 transaction 表单选交易分类共用同一个 dialog 组件,
          交互(顶级网格 + 子级展开)对齐 mobile category_selector_dialog。
          编辑 level=2 时不显示"无父分类"按钮(降级会让现有子分类归属错位,
          跟 app 一致禁掉)。 */}
      <CategoryPickerDialog
        open={parentPickerOpen}
        onClose={() => setParentPickerOpen(false)}
        kind={form.kind === 'income' ? 'income' : 'expense'}
        rows={parentCandidateRows}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={selectedParentRow?.id}
        title={t('categories.placeholder.parent')}
        emptyText={t('categories.parent.noCandidates')}
        onSelect={(cat) => {
          onFormChange({
            ...form,
            parent_name: cat.name.trim(),
            level: '2',
          })
        }}
        onClear={
          form.editingId && form.level === '2'
            ? undefined
            : () => onFormChange({ ...form, parent_name: '', level: '1' })
        }
        clearLabel={t('common.none')}
      />
    </>
  )
}
