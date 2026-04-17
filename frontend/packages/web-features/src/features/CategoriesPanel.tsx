import { useState } from 'react'

import {
  Badge,
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useT
} from '@beecount/ui'

import type { ReadCategory } from '@beecount/api-client'

import type { CategoryForm } from '../forms'
import { ListTableShell } from '../components/ListTableShell'

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
  const textActionClass =
    'text-sm text-foreground underline-offset-4 hover:text-primary hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const textDangerActionClass =
    'text-sm text-destructive underline-offset-4 hover:text-destructive/90 hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const parentOptions = rows
    .map((row) => row.name.trim())
    .filter((name) => name.length > 0 && name !== form.name.trim())
    .sort((a, b) => a.localeCompare(b))
  const colCount = 5 + (showCreatorColumn ? 1 : 0)

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
        <div className="flex items-center gap-2">
          <img alt="custom icon" className="h-7 w-7 rounded-md border border-border object-cover" src={cloudPreview} />
          <Badge variant="secondary" className="h-5 rounded px-1.5 text-[10px]">
            custom
          </Badge>
        </div>
      )
    }
    if (!normalized) {
      if (kind === 'custom') {
        return (
          <Badge variant="secondary" className="h-6 rounded px-2 text-[10px]">
            {t('categories.icon.unsynced')}
          </Badge>
        )
      }
      return <span className="text-xs text-muted-foreground">{t('common.none')}</span>
    }
    if (kind === 'custom' && /^(https?:\/\/|data:image\/|\/)/.test(normalized)) {
      return (
        <div className="flex items-center gap-2">
          <img
            alt={normalized}
            className="h-7 w-7 rounded-md border border-border object-cover"
            src={normalized}
          />
          <Badge variant="secondary" className="h-5 rounded px-1.5 text-[10px]">
            {kind}
          </Badge>
        </div>
      )
    }
    if (kind === 'custom') {
      return (
        <Badge variant="secondary" className="h-6 rounded px-2 text-[10px]">
          {t('categories.icon.unsynced')}
        </Badge>
      )
    }
    const display = normalized.length > 24 ? `${normalized.slice(0, 24)}...` : normalized
    return (
      <div className="flex items-center gap-2">
        <span className="inline-flex h-7 min-w-7 items-center justify-center rounded-md border border-border bg-muted px-2 text-xs font-medium">
          {normalized[0]?.toUpperCase() || '?'}
        </span>
        <div className="min-w-0">
          <p className="truncate text-xs font-medium">{display}</p>
          <Badge variant="secondary" className="h-5 rounded px-1.5 text-[10px]">
            {kind}
          </Badge>
        </div>
      </div>
    )
  }

  return (
    <>
      {/* 跟账户一样先屏蔽 web 端新建分类：两端模型未对齐，新建容易产生
          snapshot / UserCategory 双份残留。只保留编辑/删除入口。 */}
      <ListTableShell title={t('categories.title')}>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="bc-table-head">
                  {t('categories.table.name')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('categories.table.kind')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('categories.table.level')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('categories.table.sort')}
                </TableHead>
                <TableHead className="bc-table-head">
                  {t('categories.table.icon')}
                </TableHead>
                {showCreatorColumn ? (
                  <TableHead className="bc-table-head">
                    {t('transactions.table.user')}
                  </TableHead>
                ) : null}
                <TableHead className="bc-table-head sticky right-0 z-20 min-w-[132px] bg-card">
                  {t('categories.table.ops')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={colCount} className="p-0">
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
                      title="还没有分类"
                      description={'从手机端同步后会自动出现，或点击"新建分类"手动添加。'}
                    />
                  </TableCell>
                </TableRow>
              ) : null}
              {rows.map((row) => (
                <TableRow
                  key={row.id}
                  className="odd:bg-muted/20 [&>td:last-child]:sticky [&>td:last-child]:right-0 [&>td:last-child]:z-10 [&>td:last-child]:min-w-[132px] [&>td:last-child]:bg-background odd:[&>td:last-child]:bg-muted/20"
                >
                  <TableCell>{row.name}</TableCell>
                  <TableCell>{t(`enum.txType.${row.kind}`)}</TableCell>
                  <TableCell>{row.level ?? '-'}</TableCell>
                  <TableCell>{row.sort_order ?? '-'}</TableCell>
                  <TableCell>{renderIcon(row.icon, row.icon_type, row.icon_cloud_file_id)}</TableCell>
                  {showCreatorColumn ? <TableCell>{row.created_by_email || row.created_by_user_id || '-'}</TableCell> : null}
                  <TableCell>
                    <div className="flex items-center gap-3 whitespace-nowrap">
                      <button
                        className={textActionClass}
                        disabled={!canManage}
                        type="button"
                        onClick={() => {
                          onEdit(row)
                          setOpen(true)
                        }}
                      >
                        {t('common.edit')}
                      </button>
                      {onDelete ? (
                        <button
                          className={textDangerActionClass}
                          disabled={!canManage}
                          type="button"
                          onClick={() => onDelete(row)}
                        >
                          {t('common.delete')}
                        </button>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </ListTableShell>

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
                <Label>{t('categories.placeholder.customIcon') || '自定义图标'}</Label>
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
                      {t('common.remove') || '移除'}
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
