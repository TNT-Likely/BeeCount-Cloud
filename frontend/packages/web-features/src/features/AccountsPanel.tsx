import { useState } from 'react'

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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useT
} from '@beecount/ui'

import type { ReadAccount } from '@beecount/api-client'

import { formatAmountCny } from '../format'
import type { AccountForm } from '../forms'
import { ListTableShell } from '../components/ListTableShell'

// 与 mobile 端 accounts_page.dart / account_edit_page.dart 对齐的账户类型分组。
// 业务上分"日常账户（可流动）"和"资产/负债（估值）"两大组；液体类是负债。
const TRADABLE_TYPES: { value: string; label: string }[] = [
  { value: 'cash', label: '现金' },
  { value: 'bank_card', label: '银行卡' },
  { value: 'credit_card', label: '信用卡' },
  { value: 'alipay', label: '支付宝' },
  { value: 'wechat', label: '微信' },
  { value: 'other', label: '其他' }
]
const VALUATION_TYPES: { value: string; label: string }[] = [
  { value: 'real_estate', label: '不动产' },
  { value: 'vehicle', label: '车辆' },
  { value: 'investment', label: '投资理财' },
  { value: 'insurance', label: '保险' },
  { value: 'social_fund', label: '公积金/社保' },
  { value: 'loan', label: '贷款' }
]
const TYPE_LABEL_MAP = new Map<string, string>(
  [...TRADABLE_TYPES, ...VALUATION_TYPES].map((t) => [t.value, t.label])
)
const LIABILITY_TYPES = new Set(['credit_card', 'loan'])

function accountTypeLabel(t?: string | null): string {
  if (!t) return '-'
  return TYPE_LABEL_MAP.get(t) || t
}

type AccountsPanelProps = {
  form: AccountForm
  rows: ReadAccount[]
  canManage: boolean
  showCreatorColumn?: boolean
  onFormChange: (next: AccountForm) => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onEdit: (row: ReadAccount) => void
  onDelete?: (row: ReadAccount) => void
}

export function AccountsPanel({
  form,
  rows,
  canManage,
  showCreatorColumn = false,
  onFormChange,
  onSave,
  onReset,
  onEdit,
  onDelete
}: AccountsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const colCount = 4 + (showCreatorColumn ? 1 : 0)
  const textActionClass =
    'text-sm text-foreground underline-offset-4 hover:text-primary hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const textDangerActionClass =
    'text-sm text-destructive underline-offset-4 hover:text-destructive/90 hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'

  return (
    <>
      {/* 新建按钮先屏蔽：web/mobile 两端账户模型还没完全对齐，从 web 新建容易
          跟 mobile 端同名账户产生重复/残留，先只保留 mobile 端建账户。 */}
      <ListTableShell title={t('accounts.title')}>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="bc-table-head">{t('accounts.table.name')}</TableHead>
                <TableHead className="bc-table-head">{t('accounts.table.type')}</TableHead>
                <TableHead className="bc-table-head">
                  {t('accounts.table.currency')}
                </TableHead>
                <TableHead className="bc-table-head">{t('accounts.table.init')}</TableHead>
                {showCreatorColumn ? (
                  <TableHead className="bc-table-head">
                    {t('transactions.table.user')}
                  </TableHead>
                ) : null}
                <TableHead className="bc-table-head sticky right-0 z-20 min-w-[132px] bg-card">
                  {t('accounts.table.ops')}
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
                          <rect x="2" y="5" width="20" height="14" rx="2" />
                          <path d="M2 10h20" />
                          <path d="M6 15h4" />
                        </svg>
                      }
                      title="还没有账户"
                      description={'点击右上角"新建账户"开始管理资产。'}
                    />
                  </TableCell>
                </TableRow>
              ) : null}
              {rows.map((row) => (
                <TableRow
                  key={row.id}
                  className="odd:bg-muted/20 [&>td:last-child]:sticky [&>td:last-child]:right-0 [&>td:last-child]:z-10 [&>td:last-child]:min-w-[132px] [&>td:last-child]:bg-background odd:[&>td:last-child]:bg-muted/20"
                >
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span>{row.name}</span>
                      {LIABILITY_TYPES.has(row.account_type || '') ? (
                        <span className="rounded border border-destructive/40 bg-destructive/10 px-1.5 py-0.5 text-[10px] leading-none text-destructive">
                          负债
                        </span>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell>{accountTypeLabel(row.account_type)}</TableCell>
                  <TableCell>{row.currency || '-'}</TableCell>
                  <TableCell>{row.initial_balance === null ? '-' : formatAmountCny(row.initial_balance)}</TableCell>
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
            <DialogTitle>{form.editingId ? t('accounts.button.update') : t('accounts.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('accounts.table.name')}</Label>
              <Input
                placeholder={t('accounts.placeholder.name')}
                value={form.name}
                onChange={(e) => onFormChange({ ...form, name: e.target.value })}
              />
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <Label>{t('accounts.table.type')}</Label>
                <Select value={form.account_type || 'cash'} onValueChange={(value) => onFormChange({ ...form, account_type: value })}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('accounts.placeholder.type')} />
                  </SelectTrigger>
                  <SelectContent className="max-h-80">
                    <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                      日常账户
                    </div>
                    {TRADABLE_TYPES.map((ty) => (
                      <SelectItem key={ty.value} value={ty.value}>
                        {ty.label}
                      </SelectItem>
                    ))}
                    <div className="mt-1 border-t border-border/50 px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                      资产 / 负债
                    </div>
                    {VALUATION_TYPES.map((ty) => (
                      <SelectItem key={ty.value} value={ty.value}>
                        {ty.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('accounts.table.currency')}</Label>
                <Select value={form.currency || 'CNY'} onValueChange={(value) => onFormChange({ ...form, currency: value })}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('accounts.placeholder.currency')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="CNY">CNY</SelectItem>
                    <SelectItem value="USD">USD</SelectItem>
                    <SelectItem value="HKD">HKD</SelectItem>
                    <SelectItem value="EUR">EUR</SelectItem>
                    <SelectItem value="JPY">JPY</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1">
              <Label>{t('accounts.table.init')}</Label>
              <Input
                placeholder={t('accounts.placeholder.initialBalance')}
                value={form.initial_balance}
                onChange={(e) => onFormChange({ ...form, initial_balance: e.target.value })}
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
              {form.editingId ? t('accounts.button.update') : t('accounts.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
