import { useMemo, useState } from 'react'

import {
  AmountInput,
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
  useT,
} from '@beecount/ui'

import type {
  ReadAccount,
  ReadCardRewardRule,
  ReadRecurringRule,
  ReadTransaction,
  RecurringOccurrenceUpdatePayload,
  RecurringUpdateFromPayload,
  SecuritySearchItem,
  WorkspaceCategory,
} from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { AccountPickerDialog } from '../components/AccountPickerDialog'
import { CategoryIcon } from '../components/CategoryIcon'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { DateTimePicker } from '../components/DateTimePicker'
import { RecurringDeleteChoiceDialog } from '../components/RecurringDeleteChoiceDialog'
import { SecuritySymbolField } from '../components/SecuritySymbolField'
import type { RecurringRuleForm } from '../forms'
import { computeTxTotalAmount, recurringRuleDefaults } from '../forms'
import { formatAmountTrimmed } from '../format'
import {
  defaultMarketForCurrency,
  marketCurrency,
  rateToPercentText,
  resolveInvestmentSettings,
  stockDcaWholeShares,
} from '../lib/investment'

type RecurringRulesPanelProps = {
  rules: readonly ReadRecurringRule[]
  categories: readonly WorkspaceCategory[]
  accounts: readonly ReadAccount[]
  iconPreviewUrlByFileId?: Record<string, string>
  currency: string
  form: RecurringRuleForm
  onFormChange: (next: RecurringRuleForm) => void
  /** 信用卡紅利回饋(2026-08 使用者回饋):目前選中帳戶名下、可勾選套用到
   *  這條規則的回饋規則清單(呼叫方依 form.account_id 抓取)。 */
  rewardRules?: readonly ReadCardRewardRule[]
  onSubmit: () => Promise<boolean> | boolean
  /** 2026-08-16 補:第二參數 = 是否連同刪除未來已產生（尚未發生）的交易。 */
  onDelete: (rule: ReadRecurringRule, deleteFutureOccurrences: boolean) => Promise<void> | void
  onToggleEnabled: (rule: ReadRecurringRule, enabled: boolean) => Promise<void> | void
  /** §2.12.2:该规则已生成的 occurrence 列表,懒加载(展开才 fetch),
   *  key=rule.id。只看当前已加载交易范围,受限于 fetchReadTransactions 的
   *  limit(跟旧版退款候选清单同一种已知限制)。 */
  occurrencesByRuleId: Readonly<Record<string, ReadTransaction[] | undefined>>
  onLoadOccurrences: (rule: ReadRecurringRule) => void
  onUpdateOccurrence: (
    rule: ReadRecurringRule,
    txId: string,
    payload: RecurringOccurrenceUpdatePayload,
  ) => Promise<void> | void
  onDeleteOccurrence: (rule: ReadRecurringRule, txId: string) => Promise<void> | void
  onUpdateFrom: (
    rule: ReadRecurringRule,
    txId: string,
    payload: RecurringUpdateFromPayload,
  ) => Promise<void> | void
  onTerminateFuture: (rule: ReadRecurringRule) => Promise<void> | void
  /** 账本 owner 才能写(server _OWNER_ONLY_ROLES),非 owner 时按钮禁用。 */
  canManage: boolean
  /** 股票定期定額的代號搜尋(2026-09-29):呼叫方包 `searchSecurities`。 */
  onSearchSecurities?: (query: string, market: string | null) => Promise<SecuritySearchItem[]>
}

/**
 * 週期性收支规则管理面板(MOZE_FEATURE_GAP_SD.md §2.2 / Phase 1.5 修正版
 * §2.12.2)。
 *
 * 建规则时 server 依视窗策略当下就批次生成 occurrence 交易(有 end_at 全部
 * 生成;没有则生成默认视窗,之后由排程续产生),不再是"到期才逐笔生成"。
 * 这里展开"已生成 occurrence"可以看到具体交易并做差异化编辑:单独编辑/
 * 删除某一期、连同未来调整、终止未来週期。
 */
export function RecurringRulesPanel({
  rules,
  categories,
  accounts,
  iconPreviewUrlByFileId,
  currency,
  form,
  onFormChange,
  rewardRules = [],
  onSubmit,
  onDelete,
  onToggleEnabled,
  occurrencesByRuleId,
  onLoadOccurrences,
  onUpdateOccurrence,
  onDeleteOccurrence,
  onUpdateFrom,
  onTerminateFuture,
  canManage,
  onSearchSecurities,
}: RecurringRulesPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  // Phase 19(帳戶選擇器全站統一):帳戶群組(account_group)本身是純管理
  // 容器不能被選中,但仍要傳完整 accounts(含 account_group)給
  // AccountPickerDialog,否則掛靠群組底下的子帳戶會因為找不到巢狀父列而
  // 從清單裡整個消失(§2.9 群組模型;後端 _assert_account_not_group 仍是
  // 最終防線)。
  const [accountPickerOpen, setAccountPickerOpen] = useState(false)
  const [fromAccountPickerOpen, setFromAccountPickerOpen] = useState(false)
  const [toAccountPickerOpen, setToAccountPickerOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadRecurringRule | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [expandedRuleId, setExpandedRuleId] = useState<string | null>(null)
  // 股票定期定額(2026-09-28):分類篩選 tab,只是 UI 層篩選,不影響資料。
  const [kindFilter, setKindFilter] = useState<'all' | 'general' | 'stock_dca'>('all')

  const isTransfer = form.tx_type === 'transfer'
  const isStockDca = form.kind === 'stock_dca'
  const categoryKind = form.tx_type === 'income' ? 'income' : 'expense'
  // 股票定期定額:投資理財帳戶只能是 account_type === 'investment'(同單筆
  // 買進的既有限制),交割帳戶沿用一般的帳戶清單(允許任何非群組帳戶)。
  const investmentAccounts = useMemo(
    () => accounts.filter((a) => a.account_type === 'investment'),
    [accounts],
  )
  const filteredRules = useMemo(
    () =>
      kindFilter === 'all'
        ? rules
        : rules.filter((r) => (kindFilter === 'stock_dca' ? r.kind === 'stock_dca' : r.kind !== 'stock_dca')),
    [rules, kindFilter],
  )

  // 股票定期定額預設市場(2026-09-29):以前 form.market 預設是空字串,下拉
  // 選單只是「畫面上」顯示 TW(`value={form.market || 'TW'}`),使用者沒去
  // 動市場的話 canSubmit 永遠是 false,「新增規則」按鈕按不下去。
  const defaultStockMarket = defaultMarketForCurrency(currency)
  // 投資理財帳戶(to)與交割帳戶(from)的幣別限制:v1 不支援跨幣別定期定額
  // (server `_assert_stock_dca_settlement_currency` 也會擋),交割帳戶清單
  // 只列跟證券同幣別的帳戶;account_group 保留,子帳戶才不會因為找不到巢狀
  // 父列而消失(見 AccountPickerDialog 說明)。
  const stockCurrency = (marketCurrency(form.market || defaultStockMarket) || '').toUpperCase()
  const settlementAccounts = useMemo(
    () =>
      accounts.filter(
        (a) =>
          a.id !== form.to_account_id &&
          (a.account_type === 'account_group' || (a.currency || '').toUpperCase() === stockCurrency),
      ),
    [accounts, form.to_account_id, stockCurrency],
  )
  const defaultStockFee = resolveInvestmentSettings(
    accounts.find((a) => a.id === form.to_account_id)?.investment_settings ?? null,
    form.market || defaultStockMarket,
  )

  const handleOpenCreate = () => {
    const stock = kindFilter === 'stock_dca'
    onFormChange({
      ...recurringRuleDefaults(),
      kind: stock ? 'stock_dca' : 'general',
      tx_type: stock ? 'transfer' : 'expense',
      market: stock ? defaultStockMarket : '',
    })
    setDialogOpen(true)
  }

  const handleOpenEdit = (rule: ReadRecurringRule) => {
    const account = rule.account_id ? accounts.find((a) => a.id === rule.account_id) : null
    const fromAccount = rule.from_account_id
      ? accounts.find((a) => a.id === rule.from_account_id)
      : null
    const toAccount = rule.to_account_id ? accounts.find((a) => a.id === rule.to_account_id) : null
    onFormChange({
      editingId: rule.id,
      tx_type: (rule.tx_type as RecurringRuleForm['tx_type']) || 'expense',
      // 手續費/折扣(2026-08 使用者回饋):金額欄位回填 base_amount(原始
      // 金額),沒用過這個功能時 fallback 回 amount。
      amount: String(rule.base_amount ?? rule.amount),
      note: rule.note || '',
      category_id: rule.category_id || '',
      category_name: rule.category_name || '',
      account_id: rule.account_id || '',
      account_name: account?.name || '',
      from_account_id: rule.from_account_id || '',
      from_account_name: fromAccount?.name || '',
      to_account_id: rule.to_account_id || '',
      to_account_name: toAccount?.name || '',
      frequency: rule.frequency,
      interval: String(rule.interval),
      // 2026-09-29:transfer/stock_dca 的 next_run_at 在第一期之後就不再變,
      // 真正的下一期是 server 算的 upcoming_run_at;改這個欄位 server 會從新
      // 時間重新起算(見 write/recurring_rules.py::update_recurring_rule_ep)。
      next_run_at: isoToLocalInput(rule.upcoming_run_at || rule.next_run_at),
      end_at: rule.end_at ? isoToLocalInput(rule.end_at) : '',
      enabled: rule.enabled,
      advanced_mode: 'none',
      weekly_days: [],
      monthly_day: '1',
      // 手續費/折扣/信用卡回饋(2026-08 使用者回饋):金額欄位回填
      // base_amount(原始金額),沒用過這個功能時 fallback 回 amount,
      // 對既有規則的顯示完全沒有影響(同 TransactionsPage.tsx 既有慣例)。
      reward_rule_ids: rule.reward_rule_ids || [],
      fee_enabled: rule.fee_amount != null || rule.discount_amount != null,
      fee_amount: rule.fee_amount != null ? String(rule.fee_amount) : '',
      fee_label: rule.fee_label || '',
      discount_amount: rule.discount_amount != null ? String(rule.discount_amount) : '',
      discount_label: rule.discount_label || '',
      kind: rule.kind === 'stock_dca' ? 'stock_dca' : 'general',
      market: rule.market || '',
      symbol: rule.symbol || '',
      security_name: rule.security_name || '',
      stock_fee_override: rule.stock_fee_rate != null || rule.stock_fee_min != null,
      stock_fee_rate: rule.stock_fee_rate != null ? rateToPercentText(rule.stock_fee_rate) : '',
      stock_fee_min: rule.stock_fee_min != null ? String(rule.stock_fee_min) : '',
    })
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const ok = await onSubmit()
      if (ok) setDialogOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirmDelete = async (deleteFutureOccurrences: boolean) => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await onDelete(pendingDelete, deleteFutureOccurrences)
      setPendingDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  const toggleExpand = (rule: ReadRecurringRule) => {
    if (expandedRuleId === rule.id) {
      setExpandedRuleId(null)
      return
    }
    setExpandedRuleId(rule.id)
    if (!occurrencesByRuleId[rule.id]) onLoadOccurrences(rule)
  }

  const categoryPickerRows = useMemo(
    () => categories.filter((c) => c.kind === categoryKind),
    [categories, categoryKind],
  )

  const canSubmit =
    Boolean(form.amount.trim()) &&
    Boolean(form.next_run_at.trim()) &&
    (isStockDca
      ? Boolean(form.from_account_id) && Boolean(form.to_account_id) &&
        Boolean(form.market || defaultStockMarket) && Boolean(form.symbol.trim())
      : isTransfer
        ? Boolean(form.from_account_id) && Boolean(form.to_account_id)
        : true)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('recurringRules.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('recurringRules.button.create')}
        </Button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {(['all', 'general', 'stock_dca'] as const).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setKindFilter(key)}
            className={`rounded-full border px-2.5 py-1 text-xs ${
              kindFilter === key
                ? 'border-primary bg-primary/15 text-primary'
                : 'border-input text-muted-foreground hover:bg-accent/40'
            }`}
          >
            {t(`recurringRules.kindFilter.${key}`)}
          </button>
        ))}
      </div>

      {filteredRules.length === 0 ? (
        <EmptyState
          icon={
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 12a9 9 0 1 1-3-6.7" />
              <path d="M21 3v6h-6" />
            </svg>
          }
          title={t('recurringRules.empty')}
          description={t('recurringRules.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {filteredRules.map((rule) => {
            const cat = rule.category_id
              ? categories.find((c) => c.id === rule.category_id)
              : null
            return (
              <RecurringRuleCard
                key={rule.id}
                rule={rule}
                category={cat || null}
                currency={currency}
                iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                canManage={canManage}
                expanded={expandedRuleId === rule.id}
                occurrences={occurrencesByRuleId[rule.id]}
                onToggleExpand={() => toggleExpand(rule)}
                onEdit={() => handleOpenEdit(rule)}
                onDelete={() => setPendingDelete(rule)}
                onToggleEnabled={(next) => void onToggleEnabled(rule, next)}
                onUpdateOccurrence={(txId, payload) => onUpdateOccurrence(rule, txId, payload)}
                onDeleteOccurrence={(txId) => onDeleteOccurrence(rule, txId)}
                onUpdateFrom={(txId, payload) => onUpdateFrom(rule, txId, payload)}
                onTerminateFuture={() => onTerminateFuture(rule)}
              />
            )
          })}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('recurringRules.button.update') : t('recurringRules.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
            <div className="space-y-1">
              <Label>{t('recurringRules.field.kind')}</Label>
              <div className="flex gap-2">
                {(['general', 'stock_dca'] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    disabled={!!form.editingId}
                    onClick={() =>
                      onFormChange({
                        ...form,
                        kind: k,
                        tx_type: k === 'stock_dca' ? 'transfer' : 'expense',
                        market: k === 'stock_dca' ? form.market || defaultStockMarket : '',
                        symbol: k === 'stock_dca' ? form.symbol : '',
                        security_name: k === 'stock_dca' ? form.security_name : '',
                        category_id: '',
                        category_name: '',
                        account_id: '',
                        account_name: '',
                        from_account_id: '',
                        from_account_name: '',
                        to_account_id: '',
                        to_account_name: '',
                        reward_rule_ids: [],
                        fee_enabled: false,
                      })
                    }
                    className={`flex-1 rounded-md border px-2.5 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60 ${
                      form.kind === k
                        ? 'border-primary bg-primary/15 text-primary'
                        : 'border-input text-muted-foreground hover:bg-accent/40'
                    }`}
                  >
                    {t(`recurringRules.kind.${k}`)}
                  </button>
                ))}
              </div>
            </div>

            {!isStockDca ? (
              <div className="space-y-1">
                <Label>{t('transactions.table.type')}</Label>
                <Select
                  value={form.tx_type}
                  disabled={!!form.editingId}
                  onValueChange={(value) =>
                    onFormChange({
                      ...form,
                      tx_type: value as RecurringRuleForm['tx_type'],
                      category_id: '',
                      category_name: '',
                      // 手續費/折扣/信用卡回饋(2026-08 使用者回饋):transfer
                      // 沒有明確方向語意,不支援這兩個功能(server 端也會拒絕)。
                      fee_enabled: value === 'transfer' ? false : form.fee_enabled,
                      fee_amount: value === 'transfer' ? '' : form.fee_amount,
                      fee_label: value === 'transfer' ? '' : form.fee_label,
                      discount_amount: value === 'transfer' ? '' : form.discount_amount,
                      discount_label: value === 'transfer' ? '' : form.discount_label,
                      reward_rule_ids: value === 'expense' ? form.reward_rule_ids : [],
                    })
                  }
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
            ) : null}

            {isStockDca ? (
              <>
                <SecuritySymbolField
                  market={form.market || defaultStockMarket}
                  symbol={form.symbol}
                  disabled={!!form.editingId}
                  onSearch={onSearchSecurities}
                  onMarketChange={(market) =>
                    onFormChange({
                      ...form,
                      market,
                      // 換市場 = 換幣別,原本選的交割帳戶可能不再同幣別。
                      from_account_id: '',
                      from_account_name: '',
                    })
                  }
                  onSymbolChange={(symbol) => onFormChange({ ...form, symbol: symbol.toUpperCase() })}
                  onPick={(item) =>
                    onFormChange({
                      ...form,
                      market: item.market,
                      symbol: item.symbol,
                      security_name: item.name || form.security_name,
                      ...(item.market !== (form.market || defaultStockMarket)
                        ? { from_account_id: '', from_account_name: '' }
                        : {}),
                    })
                  }
                />
                <div className="space-y-1">
                  <Label>{t('investments.field.name')}</Label>
                  <Input
                    disabled={!!form.editingId}
                    value={form.security_name}
                    onChange={(e) => onFormChange({ ...form, security_name: e.target.value })}
                  />
                </div>
              </>
            ) : null}

            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <Label>{isStockDca ? t('recurringRules.field.stockAmount') : t('budgets.field.amount')}</Label>
                {/* 手續費/折扣(2026-08 使用者回饋,同交易表單金額旁的
                    「+」):只在 expense/income 顯示,轉帳沒有明確方向語意
                    (server 端也會拒絕)。 */}
                {!isTransfer && !form.fee_enabled ? (
                  <button
                    type="button"
                    onClick={() => onFormChange({ ...form, fee_enabled: true })}
                    className="flex h-5 w-5 items-center justify-center rounded-full border border-input text-xs text-muted-foreground hover:bg-accent/40"
                    title={t('transactions.field.feeDiscountToggle')}
                  >
                    +
                  </button>
                ) : null}
              </div>
              <AmountInput
                placeholder={t('transactions.placeholder.amount')}
                value={form.amount}
                onChange={(value) => onFormChange({ ...form, amount: value })}
              />
              {!isTransfer && form.fee_enabled ? (
                <div className="space-y-2 rounded-md border border-input/60 bg-muted/30 p-2">
                  <div className="flex items-center gap-2">
                    <Input
                      className="h-8 w-24 text-xs"
                      placeholder={t('transactions.field.fee')}
                      value={form.fee_label}
                      onChange={(e) => onFormChange({ ...form, fee_label: e.target.value })}
                    />
                    <AmountInput
                      className="h-8"
                      placeholder="0"
                      value={form.fee_amount}
                      onChange={(value) => onFormChange({ ...form, fee_amount: value })}
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <Input
                      className="h-8 w-24 text-xs"
                      placeholder={t('transactions.field.discount')}
                      value={form.discount_label}
                      onChange={(e) => onFormChange({ ...form, discount_label: e.target.value })}
                    />
                    <AmountInput
                      className="h-8"
                      placeholder="0"
                      value={form.discount_amount}
                      onChange={(value) => onFormChange({ ...form, discount_amount: value })}
                    />
                  </div>
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <div className="flex items-center gap-2">
                      <span>{t('transactions.field.total')}</span>
                      <button
                        type="button"
                        onClick={() =>
                          onFormChange({
                            ...form,
                            fee_enabled: false,
                            fee_amount: '',
                            fee_label: '',
                            discount_amount: '',
                            discount_label: '',
                          })
                        }
                        className="text-muted-foreground/70 hover:text-foreground"
                      >
                        {t('transactions.field.feeDiscountRemove')}
                      </button>
                    </div>
                    <span className="font-medium text-foreground">
                      {computeTxTotalAmount(
                        form.tx_type,
                        Number(form.amount) || 0,
                        Number(form.fee_amount) || 0,
                        Number(form.discount_amount) || 0,
                      )}
                    </span>
                  </div>
                </div>
              ) : null}
            </div>

            {isStockDca ? (
              <>
                <div className="space-y-1">
                  <Label>{t('investments.field.settlementAccount')}</Label>
                  <button
                    type="button"
                    onClick={() => setFromAccountPickerOpen(true)}
                    className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                  >
                    <span className={`flex-1 truncate ${form.from_account_name ? '' : 'text-muted-foreground'}`}>
                      {form.from_account_name || t('transactions.placeholder.accountName')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
                <div className="space-y-1">
                  <Label>{t('investments.field.account')}</Label>
                  <button
                    type="button"
                    onClick={() => setToAccountPickerOpen(true)}
                    className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                  >
                    <span className={`flex-1 truncate ${form.to_account_name ? '' : 'text-muted-foreground'}`}>
                      {form.to_account_name || t('transactions.placeholder.accountName')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
                <div className="space-y-2 rounded-md border border-input/60 bg-muted/30 p-2">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-medium">{t('recurringRules.field.customFee')}</p>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={form.stock_fee_override}
                      onClick={() =>
                        // 打開時預填帳戶目前生效的費率(含市場預設),不是 0——
                        // 以前直接開關不打字存檔會存成 0% / 最低 0 元。
                        onFormChange({
                          ...form,
                          stock_fee_override: !form.stock_fee_override,
                          ...(!form.stock_fee_override && !form.stock_fee_rate && !form.stock_fee_min
                            ? {
                                stock_fee_rate: rateToPercentText(defaultStockFee.feeRate),
                                stock_fee_min: String(defaultStockFee.feeMin),
                              }
                            : {}),
                        })
                      }
                      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                        form.stock_fee_override ? 'bg-primary' : 'bg-muted-foreground/30'
                      }`}
                    >
                      <span
                        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                          form.stock_fee_override ? 'translate-x-[18px]' : 'translate-x-0.5'
                        }`}
                      />
                    </button>
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {form.stock_fee_override
                      ? t('recurringRules.field.customFeeDiscountHint', {
                          discount: rateToPercentText(defaultStockFee.feeDiscount),
                        })
                      : t('recurringRules.field.customFeeDefaultHint', {
                          rate: rateToPercentText(defaultStockFee.feeRate),
                          discount: rateToPercentText(defaultStockFee.feeDiscount),
                          min: String(defaultStockFee.feeMin),
                        })}
                  </p>
                  {form.stock_fee_override ? (
                    <div className="grid grid-cols-2 gap-2">
                      <div className="space-y-1">
                        <Label className="text-xs">{t('recurringRules.field.stockFeeRate')}</Label>
                        <Input
                          type="number"
                          step="0.0001"
                          value={form.stock_fee_rate}
                          onChange={(e) => onFormChange({ ...form, stock_fee_rate: e.target.value })}
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">{t('recurringRules.field.stockFeeMin')}</Label>
                        <Input
                          type="number"
                          step="1"
                          value={form.stock_fee_min}
                          onChange={(e) => onFormChange({ ...form, stock_fee_min: e.target.value })}
                        />
                      </div>
                    </div>
                  ) : null}
                </div>
                <p className="text-[11px] text-muted-foreground">
                  {t(
                    stockDcaWholeShares(form.market || defaultStockMarket)
                      ? 'investments.dca.wholeShareHint'
                      : 'investments.dca.fractionalHint',
                  )}
                </p>
              </>
            ) : isTransfer ? (
              <>
                <div className="space-y-1">
                  <Label>{t('transactions.placeholder.fromAccountName')}</Label>
                  <button
                    type="button"
                    onClick={() => setFromAccountPickerOpen(true)}
                    className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                  >
                    <span className={`flex-1 truncate ${form.from_account_name ? '' : 'text-muted-foreground'}`}>
                      {form.from_account_name || t('transactions.placeholder.accountName')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
                <div className="space-y-1">
                  <Label>{t('transactions.placeholder.toAccountName')}</Label>
                  <button
                    type="button"
                    onClick={() => setToAccountPickerOpen(true)}
                    className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                  >
                    <span className={`flex-1 truncate ${form.to_account_name ? '' : 'text-muted-foreground'}`}>
                      {form.to_account_name || t('transactions.placeholder.accountName')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="space-y-1">
                  <Label>{t('transactions.table.category')}</Label>
                  <button
                    type="button"
                    onClick={() => setCategoryPickerOpen(true)}
                    className="flex h-10 w-full items-center justify-between gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                  >
                    <span className={`truncate ${form.category_name ? '' : 'text-muted-foreground'}`}>
                      {form.category_name || t('transactions.placeholder.categoryName')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
                <div className="space-y-1">
                  <Label>{t('transactions.table.account')}</Label>
                  <button
                    type="button"
                    onClick={() => setAccountPickerOpen(true)}
                    className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                  >
                    <span className={`flex-1 truncate ${form.account_name ? '' : 'text-muted-foreground'}`}>
                      {form.account_name || t('transactions.placeholder.accountName')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
              </>
            )}

            {/* 信用卡紅利回饋(2026-08 使用者回饋):同交易表單語意,只在
                expense + 選中帳戶名下有回饋規則時顯示,呼叫方已依
                form.account_id 篩過清單。 */}
            {!isTransfer && form.tx_type === 'expense' && rewardRules.length > 0 ? (
              <div className="space-y-1">
                <Label>{t('transactions.field.rewardRules')}</Label>
                <div className="flex flex-wrap gap-1.5">
                  {rewardRules.map((rule) => {
                    const checked = form.reward_rule_ids.includes(rule.id)
                    return (
                      <button
                        key={rule.id}
                        type="button"
                        onClick={() =>
                          onFormChange({
                            ...form,
                            reward_rule_ids: checked
                              ? form.reward_rule_ids.filter((id) => id !== rule.id)
                              : [...form.reward_rule_ids, rule.id],
                          })
                        }
                        className={`rounded-full border px-2.5 py-1 text-xs ${
                          checked
                            ? 'border-primary bg-primary/15 text-primary'
                            : 'border-input text-muted-foreground hover:bg-accent/40'
                        }`}
                      >
                        {rule.label}
                        {!rule.enabled ? (
                          <span className="ml-1 text-[10px] text-muted-foreground">
                            {t('cardRewards.disabled')}
                          </span>
                        ) : null}
                      </button>
                    )
                  })}
                </div>
              </div>
            ) : null}

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('recurringRules.field.frequency')}</Label>
                <Select
                  value={form.frequency}
                  onValueChange={(value) =>
                    onFormChange({ ...form, frequency: value as RecurringRuleForm['frequency'] })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="daily">{t('recurringRules.frequency.daily')}</SelectItem>
                    <SelectItem value="weekly">{t('recurringRules.frequency.weekly')}</SelectItem>
                    <SelectItem value="monthly">{t('recurringRules.frequency.monthly')}</SelectItem>
                    <SelectItem value="yearly">{t('recurringRules.frequency.yearly')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('recurringRules.field.interval')}</Label>
                <Input
                  type="number"
                  inputMode="numeric"
                  min="1"
                  max="365"
                  value={form.interval}
                  onChange={(e) => onFormChange({ ...form, interval: e.target.value })}
                />
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('recurringRules.field.nextRunAt')}</Label>
              <DateTimePicker
                value={form.next_run_at}
                onChange={(next) => onFormChange({ ...form, next_run_at: next })}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('recurringRules.field.endAt')}</Label>
              <DateTimePicker
                value={form.end_at}
                onChange={(next) => onFormChange({ ...form, end_at: next })}
                clearable
              />
            </div>

            {/* Phase 1.5(§2.12.2)进阶规则,只在新建时有意义(既有规则的
                occurrence 已经生成完,编辑规则本身不会回头重新套用)。 */}
            {!form.editingId ? (
              <>
                <div className="space-y-1">
                  <Label>{t('recurringRules.field.advancedMode')}</Label>
                  <Select
                    value={form.advanced_mode}
                    onValueChange={(value) =>
                      onFormChange({ ...form, advanced_mode: value as RecurringRuleForm['advanced_mode'] })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">{t('recurringRules.advancedMode.none')}</SelectItem>
                      <SelectItem value="weekly_days">{t('recurringRules.advancedMode.weeklyDays')}</SelectItem>
                      <SelectItem value="monthly_day">{t('recurringRules.advancedMode.monthlyDay')}</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {form.advanced_mode === 'weekly_days' ? (
                  <div className="space-y-1">
                    <Label>{t('recurringRules.field.weeklyDays')}</Label>
                    <div className="flex flex-wrap gap-1.5">
                      {(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const).map((key, index) => {
                        const selected = form.weekly_days.includes(index)
                        return (
                          <button
                            key={key}
                            type="button"
                            onClick={() =>
                              onFormChange({
                                ...form,
                                weekly_days: selected
                                  ? form.weekly_days.filter((d) => d !== index)
                                  : [...form.weekly_days, index].sort(),
                              })
                            }
                            className={`flex h-8 w-8 items-center justify-center rounded-full border text-sm transition-colors ${
                              selected
                                ? 'border-primary bg-primary text-primary-foreground'
                                : 'border-border/60 bg-background text-muted-foreground hover:bg-accent/40'
                            }`}
                          >
                            {t(`recurringRules.weekday.${key}`)}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                ) : null}
                {form.advanced_mode === 'monthly_day' ? (
                  <div className="space-y-1">
                    <Label>{t('recurringRules.field.monthlyDay')}</Label>
                    <Input
                      type="number"
                      min={1}
                      max={31}
                      value={form.monthly_day}
                      onChange={(e) => onFormChange({ ...form, monthly_day: e.target.value })}
                    />
                  </div>
                ) : null}
              </>
            ) : null}

            <div className="space-y-1">
              <Label>{t('transactions.table.note')}</Label>
              <Input
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-sm font-medium">{t('recurringRules.field.enabled')}</p>
              <button
                type="button"
                role="switch"
                aria-checked={form.enabled}
                onClick={() => onFormChange({ ...form, enabled: !form.enabled })}
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                  form.enabled ? 'bg-primary' : 'bg-muted-foreground/30'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    form.enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={submitting || !canManage || !canSubmit} onClick={() => void handleSubmit()}>
              {form.editingId ? t('recurringRules.button.update') : t('recurringRules.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CategoryPickerDialog
        open={categoryPickerOpen}
        onClose={() => setCategoryPickerOpen(false)}
        kind={categoryKind}
        rows={categoryPickerRows}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={form.category_id || undefined}
        title={t('transactions.placeholder.categoryName')}
        onSelect={(cat) =>
          onFormChange({ ...form, category_id: cat.id, category_name: cat.name })
        }
      />

      <AccountPickerDialog
        open={accountPickerOpen}
        onClose={() => setAccountPickerOpen(false)}
        accounts={accounts}
        value={form.account_name}
        allowNone
        noneLabel={t('transactions.placeholder.noAccount')}
        title={t('transactions.table.account')}
        onSelect={(row) =>
          onFormChange({ ...form, account_id: row.id, account_name: row.id ? row.name.trim() : '' })
        }
      />
      <AccountPickerDialog
        open={fromAccountPickerOpen}
        onClose={() => setFromAccountPickerOpen(false)}
        accounts={isStockDca ? settlementAccounts : accounts}
        value={form.from_account_name}
        title={isStockDca ? t('investments.field.settlementAccount') : t('transactions.placeholder.fromAccountName')}
        onSelect={(row) =>
          onFormChange({ ...form, from_account_id: row.id, from_account_name: row.name.trim() })
        }
      />
      <AccountPickerDialog
        open={toAccountPickerOpen}
        onClose={() => setToAccountPickerOpen(false)}
        accounts={isStockDca ? investmentAccounts : accounts}
        value={form.to_account_name}
        title={isStockDca ? t('investments.field.account') : t('transactions.placeholder.toAccountName')}
        onSelect={(row) => {
          // 股票定期定額:交割帳戶還沒選時,帶入這個投資理財帳戶設定的預設交割戶
          // (同幣別才帶)。
          const next = { ...form, to_account_id: row.id, to_account_name: row.name.trim() }
          const investment = accounts.find((a) => a.id === row.id)
          // 還沒輸入代號時,市場跟著投資理財帳戶的預設市場/幣別走。
          if (isStockDca && !form.symbol.trim() && investment) {
            next.market = investment.investment_settings?.market || defaultMarketForCurrency(investment.currency)
          }
          const nextStockCurrency = (marketCurrency(next.market || defaultStockMarket) || '').toUpperCase()
          if (isStockDca && !form.from_account_id) {
            const defaultId = investment?.investment_settings?.settlementAccountId
            const settlement = defaultId ? accounts.find((a) => a.id === defaultId) : undefined
            if (settlement && (settlement.currency || '').toUpperCase() === nextStockCurrency) {
              next.from_account_id = settlement.id
              next.from_account_name = settlement.name.trim()
            }
          }
          if (isStockDca && form.from_account_id === row.id) {
            next.from_account_id = ''
            next.from_account_name = ''
          }
          onFormChange(next)
        }}
      />

      <RecurringDeleteChoiceDialog
        open={pendingDelete !== null}
        loading={deleting}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onChooseKeepFuture={() => void handleConfirmDelete(false)}
        onChooseDeleteFuture={() => void handleConfirmDelete(true)}
      />
    </div>
  )
}

function RecurringRuleCard({
  rule,
  category,
  currency,
  iconPreviewUrlByFileId,
  canManage,
  expanded,
  occurrences,
  onToggleExpand,
  onEdit,
  onDelete,
  onToggleEnabled,
  onUpdateOccurrence,
  onDeleteOccurrence,
  onUpdateFrom,
  onTerminateFuture,
}: {
  rule: ReadRecurringRule
  category: WorkspaceCategory | null
  currency: string
  iconPreviewUrlByFileId?: Record<string, string>
  canManage: boolean
  expanded: boolean
  occurrences: ReadTransaction[] | undefined
  onToggleExpand: () => void
  onEdit: () => void
  onDelete: () => void
  onToggleEnabled: (next: boolean) => void
  onUpdateOccurrence: (txId: string, payload: RecurringOccurrenceUpdatePayload) => void
  onDeleteOccurrence: (txId: string) => void
  onUpdateFrom: (txId: string, payload: RecurringUpdateFromPayload) => void
  onTerminateFuture: () => void
}) {
  const t = useT()
  const isStockDca = rule.kind === 'stock_dca'
  const title = isStockDca
    ? [rule.symbol, rule.security_name].filter(Boolean).join(' ') || rule.symbol || ''
    : rule.tx_type === 'transfer'
      ? t('enum.txType.transfer')
      : category?.name || rule.category_name || t('budgets.label.unknownCategory')

  const [editingTx, setEditingTx] = useState<ReadTransaction | null>(null)
  const [updateFromTx, setUpdateFromTx] = useState<ReadTransaction | null>(null)
  const [pendingTerminate, setPendingTerminate] = useState(false)
  const [busy, setBusy] = useState(false)

  const sortedOccurrences = useMemo(
    () =>
      occurrences
        ? [...occurrences].sort((a, b) => a.happened_at.localeCompare(b.happened_at))
        : undefined,
    [occurrences],
  )

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
        >
          {rule.tx_type === 'transfer' ? (
            <span className="material-symbols-outlined text-2xl">
              {isStockDca ? 'trending_up' : 'sync_alt'}
            </span>
          ) : (
            <CategoryIcon
              icon={category?.icon}
              iconType={category?.icon_type || 'material'}
              iconCloudFileId={category?.icon_cloud_file_id || null}
              iconPreviewUrlByFileId={iconPreviewUrlByFileId}
              size={24}
            />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold">{title}</span>
            {isStockDca ? (
              <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                {t('recurringRules.kindFilter.stock_dca')}
              </span>
            ) : null}
            {!rule.enabled ? (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {t('recurringRules.disabled')}
              </span>
            ) : null}
          </div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {t(`recurringRules.frequency.${rule.frequency}`)}
            {rule.interval > 1 ? ` ×${rule.interval}` : ''} ·{' '}
            {t('recurringRules.label.nextRun')} {formatLocalDate(rule.upcoming_run_at || rule.next_run_at)}
          </div>
          {rule.note ? (
            <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{rule.note}</div>
          ) : null}
        </div>
        <div className="shrink-0 text-right">
          <Amount value={rule.amount} currency={currency} size="md" bold tone="default" />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          role="switch"
          aria-checked={rule.enabled}
          disabled={!canManage}
          onClick={() => onToggleEnabled(!rule.enabled)}
          className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
            rule.enabled ? 'bg-primary' : 'bg-muted-foreground/30'
          }`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
              rule.enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
            }`}
          />
        </button>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="ghost" onClick={onToggleExpand}>
            {expanded ? t('recurringRules.button.hideOccurrences') : t('recurringRules.button.showOccurrences')}
          </Button>
          <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => setPendingTerminate(true)}>
            {t('recurringRules.button.terminateFuture')}
          </Button>
          <Button size="sm" variant="ghost" disabled={!canManage} onClick={onEdit}>
            {t('common.edit')}
          </Button>
          <Button size="sm" variant="ghost" disabled={!canManage} onClick={onDelete}>
            {t('common.delete')}
          </Button>
        </div>
      </div>

      {expanded ? (
        <div className="mt-3 space-y-1 rounded-lg border border-border/50 bg-muted/10 p-2">
          {!sortedOccurrences ? (
            <p className="px-2 py-3 text-center text-xs text-muted-foreground">{t('common.loading')}</p>
          ) : sortedOccurrences.length === 0 ? (
            <p className="px-2 py-3 text-center text-xs text-muted-foreground">
              {t('recurringRules.occurrences.empty')}
            </p>
          ) : (
            <div className="max-h-64 overflow-y-auto">
              <table className="w-full text-xs">
                <tbody>
                  {sortedOccurrences.map((tx) => (
                    <tr key={tx.id} className="border-b border-border/30 last:border-0">
                      <td className="whitespace-nowrap py-1.5 pr-2 text-muted-foreground">
                        {formatDate(tx.happened_at)}
                      </td>
                      <td className="whitespace-nowrap py-1.5 pr-2 text-right font-medium tabular-nums">
                        {formatAmountTrimmed(tx.amount)}
                      </td>
                      <td className="whitespace-nowrap py-1.5 pr-2">
                        {tx.recurring_occurrence_overridden ? (
                          <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                            {t('installmentPlans.periods.overridden')}
                          </span>
                        ) : null}
                      </td>
                      <td className="whitespace-nowrap py-1.5 text-right">
                        {isStockDca ? (
                          <span className="text-[11px] text-muted-foreground">
                            {t('recurringRules.occurrences.stockHint')}
                          </span>
                        ) : (
                          <>
                            <button
                              type="button"
                              disabled={!canManage}
                              className="mr-2 text-[11px] text-primary underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:text-muted-foreground disabled:no-underline"
                              onClick={() => setUpdateFromTx(tx)}
                            >
                              {t('recurringRules.occurrences.updateFrom')}
                            </button>
                            <button
                              type="button"
                              disabled={!canManage}
                              className="mr-2 text-[11px] text-primary underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:text-muted-foreground disabled:no-underline"
                              onClick={() => setEditingTx(tx)}
                            >
                              {t('common.edit')}
                            </button>
                            <button
                              type="button"
                              disabled={!canManage}
                              className="text-[11px] text-destructive underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:text-muted-foreground disabled:no-underline"
                              onClick={() => onDeleteOccurrence(tx.id)}
                            >
                              {t('common.delete')}
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : null}

      {editingTx ? (
        <OccurrenceEditDialog
          tx={editingTx}
          busy={busy}
          title={t('recurringRules.occurrences.editTitle')}
          onClose={() => setEditingTx(null)}
          onConfirm={async (payload) => {
            setBusy(true)
            try {
              await onUpdateOccurrence(editingTx.id, payload)
              setEditingTx(null)
            } finally {
              setBusy(false)
            }
          }}
        />
      ) : null}

      {updateFromTx ? (
        <OccurrenceEditDialog
          tx={updateFromTx}
          busy={busy}
          title={t('recurringRules.occurrences.updateFromTitle')}
          onClose={() => setUpdateFromTx(null)}
          onConfirm={async (payload) => {
            setBusy(true)
            try {
              await onUpdateFrom(updateFromTx.id, payload)
              setUpdateFromTx(null)
            } finally {
              setBusy(false)
            }
          }}
        />
      ) : null}

      <ConfirmDialog
        open={pendingTerminate}
        onCancel={() => setPendingTerminate(false)}
        onConfirm={async () => {
          setBusy(true)
          try {
            await onTerminateFuture()
            setPendingTerminate(false)
          } finally {
            setBusy(false)
          }
        }}
        loading={busy}
        title={t('recurringRules.terminateFuture.title')}
        description={t('recurringRules.terminateFuture.confirm')}
        confirmText={t('recurringRules.button.terminateFuture')}
        confirmVariant="destructive"
      />
    </div>
  )
}

function OccurrenceEditDialog({
  tx,
  busy,
  title,
  onClose,
  onConfirm,
}: {
  tx: ReadTransaction
  busy: boolean
  title: string
  onClose: () => void
  onConfirm: (payload: RecurringOccurrenceUpdatePayload) => void
}) {
  const t = useT()
  const [amount, setAmount] = useState(String(tx.amount))
  const [note, setNote] = useState(tx.note || '')

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('transactions.table.amount')}</Label>
            <Input type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.note')}</Label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button
            disabled={busy}
            onClick={() =>
              onConfirm({
                amount: Number(amount) > 0 ? Number(amount) : undefined,
                note: note || null,
              })
            }
          >
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function isoToLocalInput(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatLocalDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString()
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
