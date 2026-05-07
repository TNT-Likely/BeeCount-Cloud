import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Loader2, Tag as TagIcon, Trash2 } from 'lucide-react'

import {
  type ApiError,
  type BatchTxItem,
  type TxDraft,
  type WorkspaceAccount,
  type WorkspaceCategory,
  batchCreateTransactions,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
} from '@beecount/api-client'
import {
  Button,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
  useToast,
} from '@beecount/ui'
import { CategoryPickerDialog } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'

export type TxDraftListProps = {
  drafts: TxDraft[]
  ledgerId: string
  imageId?: string | null
  extraTagName: string
  locale: string
  onSaved: (createdSyncIds: string[]) => void
  onCancel: () => void
}

/**
 * B2 / B3 共享 — 渲染 N 张可编辑 tx 卡片 + 批量保存。
 *
 * 关键改进(v2):
 * - 类目走 CategoryPickerDialog(跟单笔 tx editor 一致)
 * - 账户走 Select(跟单笔 editor 一致)
 * - 保存时按 LLM 给的名字在 ledger 候选里查 id,带上 category_id / account_id 给 server
 *   → projection 表的 category_sync_id 正确关联,跟 mobile 创建的 tx 完全一致
 */
export function TxDraftList({
  drafts: initialDrafts,
  ledgerId,
  imageId,
  extraTagName,
  locale,
  onSaved,
  onCancel,
}: TxDraftListProps) {
  const t = useT()
  const { token } = useAuth()
  const toast = useToast()

  const [categories, setCategories] = useState<WorkspaceCategory[]>([])
  const [accounts, setAccounts] = useState<WorkspaceAccount[]>([])
  const [editable, setEditable] = useState<Editable[]>([])
  const [autoAiTag, setAutoAiTag] = useState(true)
  const [saving, setSaving] = useState(false)

  // 加载 ledger candidates(必须先加载,然后才能初始化 editable 把 LLM name 映射到 id)
  useEffect(() => {
    Promise.all([
      fetchWorkspaceCategories(token, { ledgerId, limit: 500 }),
      fetchWorkspaceAccounts(token, { ledgerId, limit: 200 }),
    ])
      .then(([cats, accts]) => {
        setCategories(cats)
        setAccounts(accts)
      })
      .catch(() => {})
  }, [token, ledgerId])

  // 候选数据 ready 后初始化 editable;drafts 变化(重新解析)也重置
  useEffect(() => {
    setEditable(initialDrafts.map((d) => toEditable(d, categories, accounts)))
  }, [initialDrafts, categories, accounts])

  const selected = useMemo(() => editable.filter((d) => d.selected), [editable])
  const allChecked = editable.length > 0 && editable.every((d) => d.selected)
  const toggleAll = () => {
    setEditable((prev) => prev.map((d) => ({ ...d, selected: !allChecked })))
  }

  // 选中的笔里有没有「非转账 + 类目缺失」的 → 阻止保存。LLM 经常对小额、
  // 模糊凭证识别不出类目;不强制让用户选,会落到一个 category_id=null 的
  // 交易,projection 上分类列空,后面统计/筛选都不准。要求显式选完再保存。
  const missingCategoryCount = useMemo(
    () => selected.filter((d) => d.type !== 'transfer' && !d.categoryId).length,
    [selected],
  )

  const updateOne = (idx: number, patch: Partial<Editable>) => {
    setEditable((prev) => prev.map((d, i) => (i === idx ? { ...d, ...patch } : d)))
  }
  const removeOne = (idx: number) => {
    setEditable((prev) => prev.filter((_, i) => i !== idx))
  }

  const handleSave = async () => {
    if (selected.length === 0) return
    if (missingCategoryCount > 0) {
      toast.error(
        t('cmdk.parseTx.missingCategoryHint', { count: missingCategoryCount }),
        t('cmdk.parseTx.missingCategoryTitle'),
      )
      return
    }
    setSaving(true)
    try {
      const items: BatchTxItem[] = selected.map((d) => ({
        tx_type: d.type,
        amount: Number(d.amountText) || 0,
        happened_at: d.happenedAtIso,
        note: d.note || null,
        // 关键:同时传 name + id(server projection 用 id 关联,name 用作显示)
        category_name: d.categoryName || null,
        category_id: d.categoryId || null,
        category_kind: d.type === 'transfer' ? null : d.type,
        account_name: d.accountName || null,
        account_id: d.accountId || null,
        from_account_name: d.fromAccountName || null,
        from_account_id: d.fromAccountId || null,
        to_account_name: d.toAccountName || null,
        to_account_id: d.toAccountId || null,
        tags: d.tags,
      }))
      const result = await batchCreateTransactions(token, {
        ledgerId,
        transactions: items,
        autoAiTag,
        extraTagName,
        attachImageId: imageId || null,
        locale,
      })
      toast.success(t('cmdk.parseTx.saved', { count: result.created_sync_ids.length }))
      onSaved(result.created_sync_ids)
    } catch (err) {
      const apiErr = err as ApiError
      toast.error(apiErr.message || String(err), t('cmdk.parseTx.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  if (editable.length === 0) {
    return (
      <div className="rounded-md border border-border/40 bg-muted/20 px-4 py-8 text-center">
        <AlertTriangle className="mx-auto mb-2 h-5 w-5 text-amber-500" />
        <p className="text-sm text-muted-foreground">{t('cmdk.parseTx.empty')}</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={onCancel}>
          {t('common.close')}
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        {editable.map((d, idx) => (
          <TxCard
            key={d.localId}
            draft={d}
            categories={categories}
            accounts={accounts}
            onChange={(patch) => updateOne(idx, patch)}
            onRemove={() => removeOne(idx)}
            t={t}
          />
        ))}
      </div>

      <div className="flex items-center justify-between border-t border-border/40 pt-3">
        <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground hover:text-foreground">
          <input
            type="checkbox"
            checked={autoAiTag}
            onChange={(e) => setAutoAiTag(e.target.checked)}
            className="h-3.5 w-3.5 cursor-pointer accent-primary"
          />
          <TagIcon className="h-3 w-3" />
          {t('cmdk.parseTx.autoAiTag')}
        </label>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={toggleAll} disabled={saving}>
            {allChecked ? t('cmdk.parseTx.deselectAll') : t('cmdk.parseTx.selectAll')}
          </Button>
          <Button variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleSave}
            disabled={saving || selected.length === 0 || missingCategoryCount > 0}
            title={
              missingCategoryCount > 0
                ? t('cmdk.parseTx.missingCategoryHint', { count: missingCategoryCount })
                : undefined
            }
          >
            {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
            {t('cmdk.parseTx.saveSelected', { count: selected.length })}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────
// 单笔卡片
// ────────────────────────────────────────────────────────────────────────

type T = ReturnType<typeof useT>

function TxCard({
  draft,
  categories,
  accounts,
  onChange,
  onRemove,
  t,
}: {
  draft: Editable
  categories: WorkspaceCategory[]
  accounts: WorkspaceAccount[]
  onChange: (patch: Partial<Editable>) => void
  onRemove: () => void
  t: T
}) {
  const isTransfer = draft.type === 'transfer'
  const lowConf = draft.confidence === 'low'
  const missingCategory = !isTransfer && !draft.categoryId
  const [catPickerOpen, setCatPickerOpen] = useState(false)

  const accountNames = useMemo(() => accounts.map((a) => a.name).filter(Boolean), [accounts])

  // type 变化时同步 categoryKind 候选
  const pickerKind: 'expense' | 'income' = draft.type === 'income' ? 'income' : 'expense'

  return (
    <div
      className={`rounded-lg border bg-card p-3 shadow-sm transition ${
        draft.selected ? 'border-border' : 'border-border/30 opacity-50'
      } ${
        draft.selected && missingCategory
          ? 'ring-1 ring-destructive/40'
          : lowConf && draft.selected
            ? 'ring-1 ring-amber-500/30'
            : ''
      }`}
    >
      {/* 头部:checkbox + type chip + 金额 + 删除 */}
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={draft.selected}
          onChange={(e) => onChange({ selected: e.target.checked })}
          className="h-4 w-4 cursor-pointer accent-primary"
        />
        <Select
          value={draft.type}
          onValueChange={(v) => {
            const next = v as TxDraft['type']
            // 切换 type 时,如果当前 categoryId 跟新 type 的 kind 不匹配 → 清掉
            const cat = categories.find((c) => c.id === draft.categoryId)
            const shouldClearCat = cat && cat.kind && cat.kind !== next && next !== 'transfer'
            onChange({
              type: next,
              ...(shouldClearCat ? { categoryId: '', categoryName: '' } : {}),
            })
          }}
        >
          <SelectTrigger className="h-8 w-[78px] text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="expense">{t('enum.txType.expense')}</SelectItem>
            <SelectItem value="income">{t('enum.txType.income')}</SelectItem>
            <SelectItem value="transfer">{t('enum.txType.transfer')}</SelectItem>
          </SelectContent>
        </Select>
        <Input
          type="number"
          step="0.01"
          inputMode="decimal"
          value={draft.amountText}
          onChange={(e) => onChange({ amountText: e.target.value })}
          className="h-8 flex-1 text-sm font-medium"
          placeholder="0.00"
        />
        <button
          type="button"
          onClick={onRemove}
          className="rounded p-1 text-muted-foreground transition hover:bg-destructive/10 hover:text-destructive"
          aria-label={t('common.delete')}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* 类目 + 账户(transfer 换 from/to) */}
      <div className="mt-2 grid grid-cols-2 gap-2">
        {!isTransfer ? (
          <>
            <CategoryButton
              label={t('cmdk.parseTx.category')}
              value={draft.categoryName}
              missing={missingCategory}
              onClick={() => setCatPickerOpen(true)}
              t={t}
            />
            <AccountSelect
              label={t('cmdk.parseTx.account')}
              value={draft.accountName}
              accountNames={accountNames}
              onChange={(name) => {
                const matched = accounts.find((a) => a.name === name)
                onChange({ accountName: name, accountId: matched?.id || '' })
              }}
              t={t}
            />
          </>
        ) : (
          <>
            <AccountSelect
              label={t('cmdk.parseTx.fromAccount')}
              value={draft.fromAccountName}
              accountNames={accountNames}
              onChange={(name) => {
                const matched = accounts.find((a) => a.name === name)
                onChange({ fromAccountName: name, fromAccountId: matched?.id || '' })
              }}
              t={t}
            />
            <AccountSelect
              label={t('cmdk.parseTx.toAccount')}
              value={draft.toAccountName}
              accountNames={accountNames}
              onChange={(name) => {
                const matched = accounts.find((a) => a.name === name)
                onChange({ toAccountName: name, toAccountId: matched?.id || '' })
              }}
              t={t}
            />
          </>
        )}
      </div>

      {/* 时间 + 备注 */}
      <div className="mt-2 grid grid-cols-2 gap-2">
        <FieldRow label={t('cmdk.parseTx.time')}>
          <Input
            type="datetime-local"
            value={toLocalInput(draft.happenedAtIso)}
            onChange={(e) => onChange({ happenedAtIso: fromLocalInput(e.target.value) })}
            className="h-8 text-xs"
          />
        </FieldRow>
        <FieldRow label={t('cmdk.parseTx.note')}>
          <Input
            value={draft.note}
            onChange={(e) => onChange({ note: e.target.value })}
            className="h-8 text-xs"
            placeholder={t('cmdk.parseTx.notePlaceholder')}
          />
        </FieldRow>
      </div>

      {missingCategory ? (
        <p className="mt-2 flex items-center gap-1 text-[10px] text-destructive">
          <AlertTriangle className="h-3 w-3" />
          {t('cmdk.parseTx.missingCategoryRow')}
        </p>
      ) : lowConf ? (
        <p className="mt-2 flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400">
          <AlertTriangle className="h-3 w-3" />
          {t('cmdk.parseTx.lowConfidenceHint')}
        </p>
      ) : null}

      {!isTransfer && (
        <CategoryPickerDialog
          open={catPickerOpen}
          onClose={() => setCatPickerOpen(false)}
          kind={pickerKind}
          rows={categories}
          selectedId={draft.categoryId || null}
          title={t('cmdk.parseTx.pickCategory')}
          onSelect={(c) => {
            onChange({ categoryId: c.id ?? '', categoryName: c.name })
            setCatPickerOpen(false)
          }}
          onClear={() => {
            onChange({ categoryId: '', categoryName: '' })
            setCatPickerOpen(false)
          }}
          clearLabel={t('cmdk.parseTx.clearCategory')}
        />
      )}
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────
// 子组件
// ────────────────────────────────────────────────────────────────────────


function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  )
}


function CategoryButton({
  label,
  value,
  missing,
  onClick,
  t: _t,
}: {
  label: string
  value: string
  missing?: boolean
  onClick: () => void
  t: T
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onClick}
        className={`h-8 justify-start text-xs ${
          missing
            ? 'border-destructive/60 text-destructive hover:text-destructive'
            : value
              ? ''
              : 'text-muted-foreground'
        }`}
      >
        {value || _t('cmdk.parseTx.pickCategory')}
      </Button>
    </div>
  )
}


function AccountSelect({
  label,
  value,
  accountNames,
  onChange,
  t,
}: {
  label: string
  value: string
  accountNames: string[]
  onChange: (name: string) => void
  t: T
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <Label className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      <Select value={value || undefined} onValueChange={onChange}>
        <SelectTrigger className="h-8 text-xs">
          <SelectValue placeholder={t('cmdk.parseTx.pickAccount')} />
        </SelectTrigger>
        <SelectContent>
          {accountNames.map((name) => (
            <SelectItem key={name} value={name}>
              {name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────────
// 内部 state 类型 + 辅助
// ────────────────────────────────────────────────────────────────────────


type Editable = {
  localId: string
  selected: boolean
  type: TxDraft['type']
  amountText: string
  happenedAtIso: string
  note: string
  tags: string[]
  confidence: TxDraft['confidence']
  categoryId: string
  categoryName: string
  accountId: string
  accountName: string
  fromAccountId: string
  fromAccountName: string
  toAccountId: string
  toAccountName: string
}

function toEditable(
  d: TxDraft,
  categories: WorkspaceCategory[],
  accounts: WorkspaceAccount[],
): Editable {
  // 「有子分类的父类」不能被作为交易分类(产品规则跟 mobile 一致)。如果 LLM 返
  // 了这种父类名字,我们 lookup 时拒绝命中,categoryId 留空 → 用户在 Picker
  // 里手动选具体子分类。否则保存的 tx 关联到一个不该被选的父类,projection
  // 行为虽然 ok 但跟 mobile 行为不一致。
  const parentNamesWithChildren = new Set<string>()
  for (const c of categories) {
    if (c.parent_name) parentNamesWithChildren.add(c.parent_name)
  }
  const isSelectableCategory = (c: WorkspaceCategory) => {
    if (c.parent_name) return true   // 子分类,可选
    return !parentNamesWithChildren.has(c.name)   // 父分类无子,可选
  }

  const matchCategory = (name: string, kind: 'expense' | 'income' | 'transfer'): WorkspaceCategory | null => {
    if (!name) return null
    const exact = categories.find((c) => c.name === name && c.kind === kind && isSelectableCategory(c))
    if (exact) return exact
    const anyKind = categories.find((c) => c.name === name && isSelectableCategory(c))
    return anyKind || null
  }
  const matchAccount = (name: string): WorkspaceAccount | null => {
    if (!name) return null
    return accounts.find((a) => a.name === name) || null
  }

  const cat = matchCategory(d.category_name, d.type)
  const acct = matchAccount(d.account_name)
  const fromAcct = d.from_account_name ? matchAccount(d.from_account_name) : null
  const toAcct = d.to_account_name ? matchAccount(d.to_account_name) : null

  return {
    localId: Math.random().toString(36).slice(2),
    selected: true,
    type: d.type,
    amountText: String(d.amount),
    happenedAtIso: d.happened_at || new Date().toISOString(),
    note: d.note || '',
    tags: d.tags || [],
    confidence: d.confidence,
    categoryId: cat?.id || '',
    categoryName: cat?.name || d.category_name || '',
    accountId: acct?.id || '',
    accountName: acct?.name || d.account_name || '',
    fromAccountId: fromAcct?.id || '',
    fromAccountName: fromAcct?.name || d.from_account_name || '',
    toAccountId: toAcct?.id || '',
    toAccountName: toAcct?.name || d.to_account_name || '',
  }
}

function toLocalInput(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return ''
  }
}

function fromLocalInput(local: string): string {
  if (!local) return ''
  try {
    return new Date(local).toISOString()
  } catch {
    return local
  }
}
