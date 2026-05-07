import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { Pencil, TrendingDown, TrendingUp, Upload, Users } from 'lucide-react'

import type { ReadLedger } from '@beecount/api-client'
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  useT,
} from '@beecount/ui'
import {
  Amount,
  CurrencySelectorTrigger,
  formatIsoDateTime,
} from '@beecount/web-features'

import { useLedgers } from '../../context/LedgersContext'

interface Props {
  /** 点击账本卡片(整张卡)的回调。当前实现里:打开编辑 dialog,不切换
   *  active ledger 也不跳转 —— 切账本仍走顶部 ledger picker / 其它入口。 */
  onEdit: (ledger: ReadLedger) => void
  onCreate: () => void
}

/**
 * 账本列表 section。
 *
 * 信息密度分三层:
 *   - 头部:首字母色块 avatar(名字哈希稳定色) + 大号账本名 + 徽章
 *   - 统计:tx 数 / 收入 / 支出 三栏
 *   - 底部:净值 + 最近更新时间
 *
 * 顶部右侧 "新建账本" 按钮。点击账本卡片 → 编辑 dialog。
 */
export function LedgersSection({ onEdit, onCreate }: Props) {
  const t = useT()
  const { ledgers, activeLedgerId } = useLedgers()

  return (
    <div className="space-y-4">
      <Card className="bc-panel">
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle>{t('ledgers.title')}</CardTitle>
          <Button size="sm" onClick={onCreate}>
            {t('ledgers.button.create')}
          </Button>
        </CardHeader>
        <CardContent>
          <p className="mb-4 text-xs text-muted-foreground">
            {t('ledgers.subtitle')}
          </p>
          {ledgers.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('ledgers.empty')}</p>
          ) : (
            <LedgerGrid ledgers={ledgers} activeLedgerId={activeLedgerId} onEdit={onEdit} />
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function LedgerGrid({
  ledgers,
  activeLedgerId,
  onEdit,
}: {
  ledgers: ReadLedger[]
  activeLedgerId: string | null
  onEdit: (ledger: ReadLedger) => void
}) {
  const t = useT()
  const navigate = useNavigate()
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {ledgers.map((ledger) => (
        <LedgerCard
          key={ledger.ledger_id}
          ledger={ledger}
          isActive={activeLedgerId === ledger.ledger_id}
          onEdit={() => onEdit(ledger)}
          onImport={() =>
            navigate(`/app/import?ledger=${encodeURIComponent(ledger.ledger_id)}`)
          }
          roleLabel={roleLabelOf(ledger.role, t)}
        />
      ))}
    </div>
  )
}

function roleLabelOf(role: ReadLedger['role'], t: (key: string) => string): string {
  if (role === 'owner') return t('ledgers.role.owner')
  if (role === 'editor') return t('ledgers.role.editor')
  return t('ledgers.role.viewer')
}

const ACCENT_PALETTE = [
  { bg: 'from-amber-400/20 to-amber-500/5', solid: 'bg-amber-500', text: 'text-amber-600 dark:text-amber-400' },
  { bg: 'from-sky-400/20 to-sky-500/5', solid: 'bg-sky-500', text: 'text-sky-600 dark:text-sky-400' },
  { bg: 'from-violet-400/20 to-violet-500/5', solid: 'bg-violet-500', text: 'text-violet-600 dark:text-violet-400' },
  { bg: 'from-emerald-400/20 to-emerald-500/5', solid: 'bg-emerald-500', text: 'text-emerald-600 dark:text-emerald-400' },
  { bg: 'from-rose-400/20 to-rose-500/5', solid: 'bg-rose-500', text: 'text-rose-600 dark:text-rose-400' },
  { bg: 'from-cyan-400/20 to-cyan-500/5', solid: 'bg-cyan-500', text: 'text-cyan-600 dark:text-cyan-400' },
  { bg: 'from-fuchsia-400/20 to-fuchsia-500/5', solid: 'bg-fuchsia-500', text: 'text-fuchsia-600 dark:text-fuchsia-400' },
  { bg: 'from-teal-400/20 to-teal-500/5', solid: 'bg-teal-500', text: 'text-teal-600 dark:text-teal-400' },
  { bg: 'from-indigo-400/20 to-indigo-500/5', solid: 'bg-indigo-500', text: 'text-indigo-600 dark:text-indigo-400' }
]

function accentFor(name: string) {
  let h = 0
  for (let i = 0; i < name.length; i += 1) {
    h = (h * 31 + name.charCodeAt(i)) | 0
  }
  return ACCENT_PALETTE[Math.abs(h) % ACCENT_PALETTE.length]
}

interface LedgerCardProps {
  ledger: ReadLedger
  isActive: boolean
  roleLabel: string
  onEdit: () => void
  onImport: () => void
}

function LedgerCard({ ledger, isActive, roleLabel, onEdit, onImport }: LedgerCardProps) {
  const t = useT()
  const accent = accentFor(ledger.ledger_name || '?')
  const initial = (ledger.ledger_name || '?').trim().slice(0, 1).toUpperCase()

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onEdit}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onEdit()
        }
      }}
      className={`group relative cursor-pointer overflow-hidden rounded-2xl border text-left transition hover:-translate-y-0.5 hover:shadow-lg ${
        isActive
          ? 'border-primary/60 shadow-md ring-1 ring-primary/20'
          : 'border-border/60'
      }`}
    >
      <div className={`absolute inset-x-0 top-0 h-1 ${accent.solid}`} />

      <div
        className={`flex items-start gap-3 bg-gradient-to-br px-4 pb-3 pt-4 ${accent.bg}`}
      >
        <div
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-lg font-bold text-white shadow-sm ${accent.solid}`}
          aria-hidden
        >
          {initial}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">
                {ledger.ledger_name || '—'}
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px]">
                <span className="rounded bg-background/80 px-1.5 py-0.5 font-mono text-muted-foreground">
                  {ledger.currency}
                </span>
                <span className="text-muted-foreground">·</span>
                <span className={`font-medium ${accent.text}`}>{roleLabel}</span>
                {ledger.is_shared ? (
                  <span className="inline-flex items-center gap-0.5 rounded bg-primary/15 px-1.5 py-0.5 text-primary">
                    <Users className="h-2.5 w-2.5" />
                    {ledger.member_count || 1}
                  </span>
                ) : null}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {/* 导入数据入口 — 跳 /app/import?ledger=<id> 把当前账本作为目标
                  预选。stopPropagation 防止冒泡触发外层 onClick(编辑) */}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  onImport()
                }}
                title={t('ledgers.action.import') as string}
                aria-label={t('ledgers.action.import') as string}
                className="rounded bg-background/80 p-1 text-muted-foreground transition hover:bg-primary/15 hover:text-primary"
              >
                <Upload className="h-3 w-3" />
              </button>
              <span className="rounded bg-background/80 px-1.5 py-0.5 text-[10px] text-muted-foreground transition group-hover:bg-primary/15 group-hover:text-primary">
                <Pencil className="mr-0.5 inline h-2.5 w-2.5" />
                {t('common.edit')}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 border-t border-border/40 bg-card px-4 py-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('ledgers.col.tx')}
          </div>
          <div className="mt-0.5 font-mono text-sm font-semibold tabular-nums">
            {ledger.transaction_count.toLocaleString()}
          </div>
        </div>
        <div>
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            <TrendingUp className="h-2.5 w-2.5 text-income" />
            {t('ledgers.col.income')}
          </div>
          <Amount
            value={ledger.income_total}
            currency={ledger.currency}
            size="xs"
            bold
            className="mt-0.5 text-income"
          />
        </div>
        <div>
          <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            <TrendingDown className="h-2.5 w-2.5 text-expense" />
            {t('ledgers.col.expense')}
          </div>
          <Amount
            value={ledger.expense_total}
            currency={ledger.currency}
            size="xs"
            bold
            className="mt-0.5 text-expense"
          />
        </div>
      </div>

      <div className="flex items-end justify-between border-t border-border/40 bg-muted/20 px-4 py-2.5">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('ledgers.col.balance')}
          </div>
          <Amount
            value={ledger.balance}
            currency={ledger.currency}
            size="md"
            bold
            tone={ledger.balance < 0 ? 'negative' : 'default'}
            className="mt-0.5"
          />
        </div>
        <div className="text-right text-[10px] text-muted-foreground">
          <div>{t('ledgers.col.updatedAt')}</div>
          <div className="mt-0.5 font-mono">
            {formatIsoDateTime(ledger.updated_at)}
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * 编辑/新建账本通用 dialog。`mode='create'` 隐藏 ledgerId 字段(server 自动生成),
 * `mode='edit'` 锁定 ledgerId 文案显示。币种走 CurrencySelector。
 */
export type LedgerForm = {
  ledger_name: string
  currency: string
}

interface LedgerEditDialogProps {
  open: boolean
  mode: 'create' | 'edit'
  form: LedgerForm
  onChange: (next: LedgerForm) => void
  onClose: () => void
  onSubmit: () => Promise<boolean> | boolean
  /** 编辑模式额外信息行,例如 ledger id / 创建者 / 角色。 */
  meta?: { label: string; value: string }[]
}

export function LedgerEditDialog({
  open,
  mode,
  form,
  onChange,
  onClose,
  onSubmit,
  meta,
}: LedgerEditDialogProps) {
  const t = useT()
  const [submitting, setSubmitting] = useState(false)
  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const ok = await onSubmit()
      if (ok) onClose()
    } finally {
      setSubmitting(false)
    }
  }
  return (
    <Dialog open={open} onOpenChange={(v) => !v && !submitting && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            {mode === 'create' ? t('ledgers.button.create') : t('ledgers.button.update')}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('ledgers.field.name')}</Label>
            <Input
              placeholder={t('ledgers.placeholder.name')}
              value={form.ledger_name}
              onChange={(e) => onChange({ ...form, ledger_name: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label>{t('ledgers.field.currency')}</Label>
            <CurrencySelectorTrigger
              value={form.currency || 'CNY'}
              onChange={(code) => onChange({ ...form, currency: code })}
            />
          </div>
          {meta && meta.length > 0 ? (
            <div className="space-y-1 rounded-md border border-border/50 bg-muted/30 px-3 py-2 text-xs">
              {meta.map((row) => (
                <div key={row.label} className="flex items-center justify-between gap-2">
                  <span className="text-muted-foreground">{row.label}</span>
                  <span className="truncate font-mono">{row.value}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={submitting} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button disabled={submitting} onClick={() => void handleSubmit()}>
            {mode === 'create' ? t('ledgers.button.create') : t('ledgers.button.update')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
