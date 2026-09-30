import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import type {
  AccountBillingPeriodOption,
  AccountBillingSummary,
  AccountInterestFreeSuggestion,
  InstallmentInterestPeriod,
  InstallmentRepaymentMethod,
  ReadAccount,
  WorkspaceAccount,
  WorkspaceCategory,
  WorkspaceTag,
  WorkspaceTransaction
} from '@beecount/api-client'
import {
  cardPayment,
  createCategory,
  createInstallmentPlan,
  createTransaction,
  fetchAccountBillingPeriods,
  fetchAccountBillingSummary,
  fetchAccountInterestFreeSuggestion,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories
} from '@beecount/api-client'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
  useToast
} from '@beecount/ui'
import {
  AccountPickerDialog,
  DatePicker,
  interestRateToPercentDisplay,
  percentDisplayToInterestRate,
  TransactionList
} from '@beecount/web-features'
import {
  Banknote,
  Calendar as CalendarIcon,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CreditCard
} from 'lucide-react'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { localizeError } from '../../i18n/errors'
import { jwtUserId } from '../../state/jwt'
import type { DetailScope } from '../../lib/txDialogEvents'
import { dispatchOpenDetailTx, dispatchOpenEditAccount } from '../../lib/txDialogEvents'
import { AccountStatementSection, BalanceAdjustmentButton } from './AccountReconciliationSection'
import { CardRewardRulesSection } from './CardRewardRulesSection'
import { DetailScopeToggle } from './DetailScopeToggle'
import { InvestmentAccountPanel } from './InvestmentAccountPanel'

type AccountWithStats = ReadAccount & {
  tx_count?: number | null
  income_total?: number | null
  expense_total?: number | null
  balance?: number | null
}

interface Props {
  account: AccountWithStats | null
  scope: DetailScope
  onScopeChange: (next: DetailScope) => void
  transactions: WorkspaceTransaction[]
  total: number
  offset: number
  loading: boolean
  tags: WorkspaceTag[]
  onClose: () => void
  onLoadMore: (accountId: string, offset: number) => void
  onPreviewAttachment?: (ctx: unknown) => void
  resolveAttachmentPreviewUrl?: (att: unknown) => string | null
  /** 信用卡帳戶瀏覽帳單週期時,下面交易列表跟著过滤到该週期日期区间;非
   *  信用卡帳戶 / 週期未定時传 null(见 CreditCardBillingSection 用法)。 */
  onPeriodRangeChange?: (range: { start: string; end: string } | null) => void
  /** 信用卡紅利回饋(§2.9.5,2026-08-04 補強):從交易詳情彈窗「使用回饋」
   *  chip 跳過來時帶上這條規則的 id,轉傳給 `CardRewardRulesSection` 自動
   *  展開 + 打開這條規則的交易明細彈窗。其它入口(帳戶列表點卡片)開這個
   *  彈窗時不帶,不觸發任何自動展開。 */
  highlightRewardRuleId?: string
  /** 2026-08-04 第二輪使用者反饋:已掛靠群組的子卡詳情裡顯示「所屬主帳戶」
   *  連結,點擊後由呼叫端(`GlobalEntityDialogs`)查出主帳戶的完整物件並
   *  切換這個彈窗去顯示它——查帳戶列表是異步的,這裡只丟出 accountId。 */
  onJumpToParentAccount?: (accountId: string) => void
}

/**
 * 信用卡合併帳單資料抓取(2026-08-04 使用者反饋從 `CreditCardBillingSection`
 * 提升到 `AccountDetailDialog` 這一層):彈窗頂部的統計區塊(見下面
 * `AccountStatsHeader`)跟頭部新增的週期選擇器都需要這份資料,原本只有
 * `CreditCardBillingSection` 自己知道,現在改成共用同一份 state,`
 * CreditCardBillingSection` 收 `billing` prop 而不是自己重新 fetch 一次。
 * `isBillingRoot`:account_group(主帳戶),或沒有掛靠任何群組的獨立信用卡
 * 才會去查(見 `credit_card_billing.is_billing_root` 的後端對應邏輯)。
 *
 * 2026-08-04 第二輪使用者反饋:已經掛靠群組的子卡打開詳情時原本完全看不到
 * 帳單資訊(`isBillingRoot` 恆為 false)——改成子卡也能瀏覽,只是查詢/繳款/
 * 建分期時一律改用它掛靠的 `parent_account_id`(`billingAccountId`)當
 * `account_id` 打後端既有的 billing-summary/interest-free-suggestion/
 * card-payment/installment-plan 端點——這些端點本來就要求「必須是
 * account_group 或無父的獨立信用卡」(`credit_card_billing.is_billing_
 * root`),子卡自己過不了這個檢查,但它的父群組永遠過得了,所以不需要改
 * 後端,單純讓前端「問對帳戶」即可。回傳的 `summary.account_id`/
 * `account_name` 因此會是父群組自己的,`isBillingChild` 讓 UI 知道現在
 * 顯示的是「借用父群組資料」,用來顯示「所屬主帳戶」連結 + 隱藏只對群組
 * 自己有意義的自動扣繳狀態(那個欄位設在群組身上,子卡自己讀不到正確值)。
 */
function useAccountBilling(
  account: AccountWithStats | null,
  token: string | null,
  activeLedgerId: string | null,
) {
  const accountId = account?.id
  const isBillingRoot =
    !!account &&
    ((account.account_type || '') === 'account_group' ||
      ((account.account_type || '') === 'credit_card' && !account.parent_account_id))
  const isBillingChild =
    !!account && (account.account_type || '') === 'credit_card' && !!account.parent_account_id
  const canViewBilling = isBillingRoot || isBillingChild
  const billingAccountId = isBillingChild ? account!.parent_account_id! : accountId

  const [summary, setSummary] = useState<AccountBillingSummary | null>(null)
  const [suggestion, setSuggestion] = useState<AccountInterestFreeSuggestion | null>(null)
  const [loading, setLoading] = useState(false)
  const [available, setAvailable] = useState(false)
  // 「選擇區間」清單(2026-09-06,對齊 mobile app 同名功能):跟 summary/
  // suggestion 不同,不需要每次 cycleOffset 改變就重新抓——清單本身只跟
  // 「今天」+「最早一筆交易在哪」有關,不受目前瀏覽到哪一期影響,所以改成
  // 點開彈窗時才拉(loadPeriodOptions),不放進上面那個 useEffect。
  const [periodOptions, setPeriodOptions] = useState<AccountBillingPeriodOption[]>([])
  const [periodOptionsLoading, setPeriodOptionsLoading] = useState(false)
  // 帳單週期瀏覽(§2.9 補強,2026-08-02):0 = 最近一次已結束的週期,負數 =
  // 更早的歷史週期,+1 = 目前還在累積中的那期。切換帳戶時歸零,不沿用上一
  // 張卡瀏覽到的期數。
  const [cycleOffset, setCycleOffset] = useState(0)
  // 2026-08-03 使用者反饋:上期(cycleOffset=0)如果已經繳清,預設畫面不該
  // 停在一筆「已經結案」的舊帳單,應該自動跳去這個月還在累積中的那期(+1)。
  const autoAdvancedRef = useRef(false)

  useEffect(() => {
    autoAdvancedRef.current = false
    setCycleOffset(0)
  }, [accountId])

  useEffect(() => {
    if (!canViewBilling || !token || !activeLedgerId || !billingAccountId) {
      setAvailable(false)
      setSummary(null)
      setSuggestion(null)
      return
    }
    let cancelled = false
    setLoading(true)
    Promise.all([
      fetchAccountBillingSummary(token, activeLedgerId, billingAccountId, cycleOffset),
      fetchAccountInterestFreeSuggestion(token, activeLedgerId, billingAccountId),
    ])
      .then(([s, sug]) => {
        if (cancelled) return
        setSummary(s)
        setSuggestion(sug)
        setAvailable(true)
        if (
          !autoAdvancedRef.current &&
          cycleOffset === 0 &&
          s.period_remaining_due <= 0 &&
          s.period_has_newer
        ) {
          autoAdvancedRef.current = true
          setCycleOffset(1)
        }
      })
      .catch(() => {
        if (cancelled) return
        setAvailable(false)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [canViewBilling, token, activeLedgerId, billingAccountId, cycleOffset])

  // 繳款 / 建分期成功後手動重新拉一次最新的帳單摘要,不用整個 effect 重跑。
  const reload = useCallback(() => {
    if (!canViewBilling || !token || !activeLedgerId || !billingAccountId) return
    fetchAccountBillingSummary(token, activeLedgerId, billingAccountId, cycleOffset)
      .then(setSummary)
      .catch(() => {})
  }, [canViewBilling, token, activeLedgerId, billingAccountId, cycleOffset])

  // 點開「選擇區間」清單彈窗時才拉一次(不快取跨帳戶——切換帳戶後
  // billingAccountId 變了,呼叫端每次開彈窗都會重新拉,天然拿到最新資料)。
  const loadPeriodOptions = useCallback(() => {
    if (!canViewBilling || !token || !activeLedgerId || !billingAccountId) return
    setPeriodOptionsLoading(true)
    fetchAccountBillingPeriods(token, activeLedgerId, billingAccountId)
      .then((res) => setPeriodOptions(res.periods))
      .catch(() => setPeriodOptions([]))
      .finally(() => setPeriodOptionsLoading(false))
  }, [canViewBilling, token, activeLedgerId, billingAccountId])

  // 2026-08-07 使用者反饋(§2.9.6 Phase 7):子卡自己的詳情頁不該顯示整組
  // 合併金額(其它子卡/主帳戶的消費會混進來看到)。查詢仍然只能用主帳戶的
  // billingAccountId(後端 is_billing_root 限制,子卡自己過不了這個檢查),
  // 但回傳的 `summary.members` 裡本來就帶著每個成員自己的 period_new_spend/
  // remaining_due(見 `read/ledgers.py::get_account_billing_summary`)——
  // 從裡面挑出這張卡自己的那筆當「自身資料」,不用整組數字。
  const ownMember = isBillingChild && summary ? summary.members.find((m) => m.account_id === accountId) : undefined

  return {
    isBillingRoot,
    isBillingChild,
    canViewBilling,
    billingAccountId,
    summary,
    ownMember,
    suggestion,
    loading,
    available,
    cycleOffset,
    setCycleOffset,
    reload,
    periodOptions,
    periodOptionsLoading,
    loadPeriodOptions,
  }
}

/** 点账户卡片弹出的详情:顶部账户名 + 统计信息 + 交易列表(无限滚动加载)。 */
export function AccountDetailDialog({
  account,
  scope,
  onScopeChange,
  transactions,
  total,
  offset,
  loading,
  tags,
  onClose,
  onLoadMore,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
  onPeriodRangeChange,
  highlightRewardRuleId,
  onJumpToParentAccount,
}: Props) {
  const t = useT()
  const { profileMe, token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const noteDisplayMode = profileMe?.appearance?.note_display_mode ?? 'category'
  // 2026-08-04 使用者反饋:帳單週期抓取邏輯從 `CreditCardBillingSection`
  // 提升到這一層(useAccountBilling),因為彈窗頂部的統計區塊(下面
  // AccountStatsHeader)跟頭部新增的週期選擇器(見 DialogHeader)都需要
  // 同一份資料,以前只有 CreditCardBillingSection 自己知道。
  const billing = useAccountBilling(account, token, activeLedgerId)
  // 投資理財(股票)帳戶:餘額是持股成本的帳面數,不能調整餘額;統計與明細
  // 改用 `InvestmentAccountPanel`(持股/市值/損益/買賣操作)。
  const isInvestment = (account?.account_type || '') === 'investment'
  // 「選擇區間」清單彈窗(2026-09-06,對齊 mobile app 同名功能 + 比照
  // `ProjectDetailDialog` 同款 UI):切換帳戶時關閉,不沿用上一張卡的展開
  // 狀態。
  const [periodPickerOpen, setPeriodPickerOpen] = useState(false)
  useEffect(() => {
    setPeriodPickerOpen(false)
  }, [account?.id])

  useEffect(() => {
    if (!billing.canViewBilling || !billing.summary) {
      onPeriodRangeChange?.(null)
      return
    }
    onPeriodRangeChange?.({ start: billing.summary.period_cycle_start, end: billing.summary.period_cycle_end })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [billing.canViewBilling, billing.summary])

  // 卸载(切换到别的账户 / 关闭详情弹窗)时清掉过滤,不让下一个账户的交易
  // 列表继承上一张卡選中的期数窗口。
  useEffect(() => {
    return () => onPeriodRangeChange?.(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // §2.9.5.4 使用者反饋:紅利回饋卡片沒有跟著上面的帳單週期選擇器走。
  // `cycleOffset`(0=最近一次已結束的週期)換算成 card-rewards 的
  // `period_offset` 語意(0=目前還在累積、尚未結束的那期)傳給
  // `CardRewardRulesSection`——兩者對「0」的定義差一期,見
  // `read/ledgers.py::get_account_card_rewards` docstring。非
  // billing-root(掛靠群組的子卡)或帳單資料還不可用(沒設定帳單日等)時,
  // 對齊「目前這期」,不跟著一個不存在的週期選擇器亂跑。
  const rewardCycleOffset = billing.canViewBilling && billing.available ? billing.cycleOffset : 1
  const rewardPeriodOffset = rewardCycleOffset - 1

  return (
    <Dialog open={Boolean(account)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex flex-row items-center justify-between gap-3 border-b border-border/60 px-6 py-4">
          <DialogTitle className="min-w-0 flex-1 truncate">{account?.name || ''}</DialogTitle>
          {/* 帳單週期日期選擇器(2026-08-04 使用者反饋:移到標題跟 全部帳本/
              當前帳本 切換之間,尺寸比照那顆切換按鈕)。只在 billing-root
              帳戶且帳單資料抓取成功時顯示。 */}
          {billing.canViewBilling && billing.available && billing.summary ? (
            <div className="relative inline-flex shrink-0 items-center gap-0.5 rounded-md border border-border/60 bg-muted/30 p-0.5 text-xs">
              <button
                type="button"
                disabled={!billing.summary.period_has_older}
                aria-label={t('cardBilling.period.prev')}
                onClick={() => billing.setCycleOffset((v) => v - 1)}
                className="rounded p-1 text-muted-foreground hover:bg-background disabled:cursor-not-allowed disabled:opacity-30"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => {
                  const next = !periodPickerOpen
                  setPeriodPickerOpen(next)
                  if (next) billing.loadPeriodOptions()
                }}
                className="whitespace-nowrap px-0.5 font-mono font-medium tabular-nums text-foreground hover:underline"
              >
                {formatDateSlash(billing.summary.period_cycle_start)} – {formatDateSlash(billing.summary.period_cycle_end)}
              </button>
              <button
                type="button"
                disabled={!billing.summary.period_has_newer}
                aria-label={t('cardBilling.period.next')}
                onClick={() => billing.setCycleOffset((v) => v + 1)}
                className="rounded p-1 text-muted-foreground hover:bg-background disabled:cursor-not-allowed disabled:opacity-30"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
              {/* 「選擇區間」清單(2026-09-06):比照 ProjectDetailDialog 同款
                  下拉,差別是選項來自 `billing.periodOptions`(後端依實際
                  交易資料算出的範圍),不是前端寫死一個固定長度的清單。 */}
              {periodPickerOpen ? (
                <div className="absolute right-0 top-full z-20 mt-1 max-h-64 w-40 overflow-y-auto rounded-md border border-border/60 bg-popover p-1 shadow-md">
                  {billing.periodOptionsLoading ? (
                    <div className="px-2 py-1.5 text-xs text-muted-foreground">{t('cardBilling.loading')}</div>
                  ) : (
                    billing.periodOptions.map((opt) => (
                      <button
                        key={opt.offset}
                        type="button"
                        onClick={() => {
                          billing.setCycleOffset(opt.offset)
                          setPeriodPickerOpen(false)
                        }}
                        className={[
                          'block w-full rounded px-2 py-1 text-left text-xs tabular-nums',
                          opt.offset === billing.cycleOffset
                            ? 'bg-primary/10 font-medium text-primary'
                            : 'hover:bg-accent/40',
                        ].join(' ')}
                      >
                        {formatDateSlash(opt.cycle_start)} – {formatDateSlash(opt.cycle_end)}
                      </button>
                    ))
                  )}
                </div>
              ) : null}
            </div>
          ) : null}
          <DetailScopeToggle value={scope} onChange={onScopeChange} className="shrink-0" />
        </DialogHeader>
        {account ? (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* 统计:account_group(主帳戶)/沒有掛靠群組的獨立信用卡改顯示
                Moze 風格的帳單欄位(新增花費/上期欠款/應繳金額/已繳金額/
                帳單分期/剩餘帳款,見 useAccountBilling + 下面
                AccountStatsHeader;對帳確認筆數改顯示在下面
                `AccountStatementSection` 自己的標題列,不在這裡重複),
                跟著上面新增的週期選擇器走;其它帳戶
                類型維持原本的餘額/收入/支出統計。 */}
            {/* 所屬主帳戶(2026-08-04 第二輪使用者反饋)+ 餘額調整(§2.10
                Phase 5)合併成同一列(2026-08-07 第三輪使用者反饋:調整餘額
                原本自己另開一列,要求收進這裡,不要多一層)。左邊「所屬主
                帳戶」只有子卡才顯示,點擊切換這個彈窗去看主帳戶(§2.9 群組
                模型),名稱取自 billing.summary.account_name——查詢時已經
                改用主帳戶的 parent_account_id 當 account_id(見
                useAccountBilling),後端回傳的 account_name 因此天然就是
                主帳戶自己的名字。右邊調整餘額按鈕:account_group 是純管理
                容器,自己沒有餘額可調整(對齐服务端 balance-adjustment 拒绝
                account_group 目标的校验),不渲染——目前只有子卡會同時
                出現兩者,子卡必然不是 account_group,故不會有「只有連結、
                沒有按鈕」時擠在最左邊 vs 最右邊的視覺落差問題。 */}
            {(billing.isBillingChild && billing.summary && onJumpToParentAccount) ||
            (account.account_type !== 'account_group' && !isInvestment) ? (
              <div
                className={`flex items-center gap-2 border-b border-border/60 bg-muted/10 px-6 py-2 ${
                  billing.isBillingChild && billing.summary && onJumpToParentAccount ? 'justify-between' : 'justify-end'
                }`}
              >
                {billing.isBillingChild && billing.summary && onJumpToParentAccount ? (
                  <button
                    type="button"
                    className="flex items-center gap-1.5 text-left text-xs text-muted-foreground hover:text-foreground hover:underline"
                    onClick={() => onJumpToParentAccount(billing.billingAccountId!)}
                  >
                    <CreditCard className="h-3.5 w-3.5 shrink-0" />
                    <span>{t('cardBilling.parentAccountLink', { name: billing.summary.account_name })}</span>
                  </button>
                ) : null}
                {account.account_type !== 'account_group' && !isInvestment ? (
                  <BalanceAdjustmentButton account={account} />
                ) : null}
              </div>
            ) : null}

            {isInvestment ? null : <AccountStatsHeader account={account} t={t} billing={billing} />}

            {/* 信用卡 / 银行卡专属信息:bank_name / 卡号末 4 / 信用额度 /
                账单日 / 还款日 + 倒计时。普通账户类型不渲染。 */}
            <AccountCardInfo account={account} t={t} billing={billing} />

            {/* 信用卡合併帳單 + 免息期建议 + 繳款(§2.9 Phase 4,2026-08-02
                改版為群組模型,同日第二輪放寬到單卡,2026-08-04 第二輪放寬到
                已掛靠群組的子卡——子卡瀏覽/繳款/建分期一律借用主帳戶的
                billingAccountId,見 useAccountBilling)。只在
                account_type=account_group(主帳戶)/沒有掛靠任何群組的獨立
                信用卡/已掛靠群組的子卡,且当前有选中账本时渲染(帐单摘要是
                ledger-scoped 读端点)。 */}
            <CreditCardBillingSection
              account={account}
              t={t}
              onClose={onClose}
              billing={billing}
              onJumpToParentAccount={onJumpToParentAccount}
            />

            {/* 信用卡紅利回饋(§2.9.5 Phase 4.5):規則管理 + 當期回饋預覽。
                綁定的是這張真實的 credit_card 帳戶(獨立卡或掛靠群組的子卡
                都可以),不是 account_group 本身。periodOffset 跟著上面的
                帳單週期選擇器走(§2.9.5.4)。 */}
            <CardRewardRulesSection
              accountId={account.id}
              accountType={account.account_type}
              periodOffset={rewardPeriodOffset}
              highlightRuleId={highlightRewardRuleId}
            />

            {/* 對帳模式(§2.10 Phase 5,2026-08-09 改版為 Moze 式逐筆核對清單):
                原文入口本來就限定在「信用卡交易明細頁」,範圍跟
                `CreditCardBillingSection`/`CardRewardRulesSection` 一致
                ——account_group 或沒掛靠群組的獨立信用卡才有意義,`billing.
                billingAccountId`/`billing.cycleOffset` 直接沿用上面標題列
                的帳單週期選擇器狀態,不要自己另外维护一份(§2.9.5.4 的既有
                教训)。跟 `CreditCardBillingSection` 同款用 `billing.
                available` 把關(還沒設定 billing_day/payment_due_day 時
                後端一律 400,乾脆不掛載,不讓使用者看到一片「載入失敗」)。 */}
            {billing.canViewBilling && billing.available && billing.billingAccountId ? (
              <AccountStatementSection
                billingAccountId={billing.billingAccountId}
                cycleOffset={billing.cycleOffset}
                onFinished={billing.reload}
              />
            ) : null}

            <div className="min-h-0 flex-1 overflow-y-auto">
              {isInvestment ? (
                <>
                  <InvestmentAccountPanel accountId={account.id} />
                  <div className="px-6 pb-1 pt-3 text-sm font-medium">{t('investments.detail.cashFlow')}</div>
                  <p className="px-6 pb-2 text-xs text-muted-foreground">{t('investments.detail.cashFlowHint')}</p>
                </>
              ) : null}
              <TransactionList
                items={transactions}
                tags={tags}
                variant="compact"
                loading={loading}
                hasMore={transactions.length < total}
                onSelect={(row) => dispatchOpenDetailTx(row as WorkspaceTransaction)}
                onLoadMore={() => {
                  if (!loading) onLoadMore(account.id, offset)
                }}
                onPreviewAttachment={onPreviewAttachment as never}
                resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl as never}
                emptyTitle={t('transactions.empty.forAccount.title')}
                showLedger={scope === 'all'}
                noteDisplayMode={noteDisplayMode}
              />
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function StatTile({
  label,
  value,
  tone,
  emphasis,
}: {
  label: string
  value: string
  tone?: 'income' | 'expense'
  emphasis?: boolean
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div
        className={`mt-0.5 font-mono tabular-nums ${emphasis ? 'text-base font-bold' : 'text-sm font-medium'} ${
          tone === 'income' ? 'text-income' : tone === 'expense' ? 'text-expense' : 'text-foreground'
        }`}
      >
        {value}
      </div>
    </div>
  )
}

function AccountStatsHeader({
  account,
  t,
  billing,
}: {
  account: AccountWithStats
  t: (key: string, params?: Record<string, string | number>) => string
  billing: ReturnType<typeof useAccountBilling>
}) {
  const hasServerStats = typeof account.balance === 'number'
  const balance = hasServerStats ? account.balance! : account.initial_balance ?? 0
  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })

  // 已掛靠群組的子卡(2026-08-07 使用者反饋 §2.9.6 Phase 7):自己的詳情頁
  // 只顯示自己的本期新增花費/終身應繳餘額,不再借用整組合併數字(那些數字
  // 混了主帳戶跟其它兄弟卡的金額,使用者反饋「不該顯示主帳戶/其它子卡
  // 金額」)。想看合併帳單/其它子卡明細,走既有的「所屬主帳戶」連結。
  if (billing.isBillingChild && billing.available && billing.ownMember) {
    const m = billing.ownMember
    return (
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-border/60 bg-muted/20 px-6 py-4">
        <StatTile label={t('cardBilling.period.newSpend')} value={fmt(m.period_new_spend)} />
        <StatTile
          label={t('cardBilling.selfRemainingDue')}
          value={fmt(m.remaining_due)}
          tone="expense"
          emphasis
        />
      </div>
    )
  }

  // 主帳戶(account_group)/沒有掛靠群組的獨立信用卡(2026-08-04 使用者反饋):
  // 頂部改顯示 Moze 風格的帳單欄位,取代原本的餘額/收入/支出或「當前欠款」
  // ——這些欄位對信用卡群組沒有意義(群組自己不記交易,子卡的欠款才是重點,
  // 而且使用者需要的是「這期帳單」的資訊,不是終身餘額)。只在資料抓取成功
  // 時渲染;沒設定帳單日/還款日(billing.available 恆為 false)或還在載入時
  // 落回下面的舊版顯示,不讓畫面整塊空白。
  if (billing.canViewBilling && billing.available && billing.summary) {
    const s = billing.summary
    const installmentText =
      s.period_installment_active_count === 0
        ? t('cardBilling.period.installmentPlan.none')
        : s.period_installment_active_count === 1
          ? t('cardBilling.period.installmentPlan.progress', {
              paid: s.period_installment_paid_periods ?? 0,
              total: s.period_installment_periods ?? 0,
            })
          : t('cardBilling.period.installmentPlan.multiple', { count: s.period_installment_active_count })
    return (
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-border/60 bg-muted/20 px-6 py-4 sm:grid-cols-4">
        <StatTile label={t('cardBilling.period.newSpend')} value={fmt(s.period_new_spend)} />
        <StatTile label={t('cardBilling.period.carryoverDue')} value={fmt(s.period_carryover_due)} />
        <StatTile label={t('cardBilling.period.totalDue')} value={fmt(s.period_total_due)} emphasis />
        <StatTile label={t('cardBilling.period.paidInCycle')} value={fmt(s.period_paid_in_cycle)} tone="income" />
        <StatTile label={t('cardBilling.period.installmentPlan')} value={installmentText} />
        <StatTile
          label={t('cardBilling.period.remainingDue')}
          value={fmt(s.period_remaining_due)}
          tone="expense"
          emphasis
        />
      </div>
    )
  }

  if (billing.canViewBilling && billing.loading) {
    return (
      <div className="border-b border-border/60 bg-muted/20 px-6 py-4 text-center text-xs text-muted-foreground">
        {t('cardBilling.loading')}
      </div>
    )
  }

  // 信用卡按负债展示:只显当前欠款(= -balance,余额为负=欠款),不显示
  // 累计收入/支出(信用卡入账是还款/退款,非收入);额度/可用/账单在下方
  // AccountCardInfo。对齐 mobile account_detail_page。这裡也是掛靠群組的
  // 子卡(billing.isBillingRoot 恆為 false)跟「主帳戶但還沒設定帳單日」
  // 兩種情況共用的落回顯示。
  if ((account.account_type || '') === 'credit_card') {
    const owed = Math.max(0, -balance)
    return (
      <div className="border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('accounts.bankcard.currentOwed')}
        </div>
        <div className="mt-0.5 font-mono text-lg font-bold tabular-nums text-expense">
          {fmt(owed)}
        </div>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-3 gap-3 border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.currentBalance')}
        </div>
        <div
          className={`mt-0.5 font-mono text-base font-bold tabular-nums ${
            balance >= 0 ? 'text-foreground' : 'text-expense'
          }`}
        >
          {fmt(balance)}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.accumIncome')}
        </div>
        <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-income">
          {fmt(account.income_total ?? 0)}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.accumExpense')}
        </div>
        <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-expense">
          {fmt(account.expense_total ?? 0)}
        </div>
      </div>
    </div>
  )
}

/**
 * 信用卡 / 银行卡专属信息卡片。
 *
 * 展示规则:
 *   - bank_card: 银行 + 卡号末 4 位(2 项中只要任一有值就显示)
 *   - credit_card: 上面 2 项 + 信用额度 + 账单日 + 还款日 + 倒计时
 *   - 其它账户类型(cash / alipay / etc):整块不渲染
 *
 * 倒计时算法:今天日号 ≤ 目标日号 → 目标日号 - 今天日号;否则 → 跨月,
 * (本月剩余天数) + 目标日号。"今天"取本地时区,因为还款日是法律日历日,
 * 不需要 UTC。
 */
function AccountCardInfo({
  account,
  t,
  billing,
}: {
  account: AccountWithStats
  t: (key: string, params?: Record<string, string | number>) => string
  billing: ReturnType<typeof useAccountBilling>
}) {
  const accountType = account.account_type || ''
  const isCreditCard = accountType === 'credit_card'
  // 主帳戶(§2.9 Phase 4,2026-08-02 改版,同日第二輪放寬到單卡):信用
  // 額度/帳單日/還款日設在 account_group 自己身上,或沒有掛靠任何群組的
  // 獨立信用卡自己身上;已經掛靠群組的子帳戶自己沒有這些欄位。2026-08-04
  // 第二輪使用者反饋:子卡改成顯示「跟主帳戶一樣的資訊」——直接借用
  // billing.summary(查詢時已經改用主帳戶的 id,見 useAccountBilling)裡
  // 回傳的 credit_limit/billing_day/payment_due_day,不是靠子卡自己的
  // (恆為 null 的)欄位。
  const isAccountGroup = accountType === 'account_group'
  const isStandaloneCreditCard = isCreditCard && !account.parent_account_id
  const showsBillingFields = isAccountGroup || isStandaloneCreditCard || billing.isBillingChild
  const isBankOrCredit = isCreditCard || isAccountGroup || accountType === 'bank_card'
  if (!isBankOrCredit) return null

  const bankName = account.bank_name?.trim() || ''
  const cardLastFour = account.card_last_four?.trim() || ''
  const creditLimit = showsBillingFields
    ? (billing.isBillingChild ? billing.summary?.credit_limit ?? null : account.credit_limit)
    : null
  const billingDay = showsBillingFields
    ? (billing.isBillingChild ? billing.summary?.billing_day ?? null : account.billing_day)
    : null
  const paymentDueDay = showsBillingFields
    ? (billing.isBillingChild ? billing.summary?.payment_due_day ?? null : account.payment_due_day)
    : null

  // 信用卡已用额度 = -balance(余额为负表示欠款),剩余额度 = limit - used。
  // 这是粗略估算 — 没考虑账单周期,只看终身累计。但作为"大致还能刷多少"
  // 的信号已经够用,后续要精确版本应该按 billing_day 分账期算。
  //
  // 为什么用 balance 而不是 expense_total - income_total:后者会漏掉"储蓄卡
  // 转账到信用卡还款"这种 transfer-in 操作 —— 转账既不是 expense 也不是
  // income,只在 backend 的 balance 计算里被加回去(workspace.py 的
  // transfer_to bucket)。balance 已经统一包含初始余额 + 全部 income /
  // expense / transfer-in / transfer-out,跟 mobile 端
  // `getCreditCardUsedAmount` (balance < 0 ? -balance : 0) 完全一致。
  // 修复 issue #26:储蓄卡转账到信用卡额度没恢复。
  // 注意:account_group 自己的 balance 只反映"溢繳結轉"那类极少数直接打
  // 在群組身上的交易,不是真正的合併應繳金額——那个数字由下方
  // CreditCardBillingSection 的 available_credit(§2.9 Phase 4)才是准的。
  // 这里只static展示 credit_limit 本身,不再用 balance 推算"已用/剩余",
  // 避免跟下面权威数字打架。

  // 没有任何要展示的就直接不渲染(用户没填这些字段时)
  if (
    !bankName &&
    !cardLastFour &&
    creditLimit === null &&
    !billingDay &&
    !paymentDueDay
  ) {
    return null
  }

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })

  return (
    <div className="border-b border-border/60 bg-muted/10 px-6 py-3">
      {/* 第一行:银行 + 卡号末 4 位 + 类型 icon */}
      {(bankName || cardLastFour) ? (
        <div className="flex items-center gap-2 text-sm">
          {isCreditCard || isAccountGroup ? (
            <CreditCard className="h-4 w-4 text-muted-foreground" />
          ) : (
            <Banknote className="h-4 w-4 text-muted-foreground" />
          )}
          <span className="font-medium">{bankName || t('detail.account.bankUnknown')}</span>
          {cardLastFour ? (
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
              •••• {cardLastFour}
            </span>
          ) : null}
        </div>
      ) : null}

      {/* 主帳戶群組 / 獨立信用卡:额度 + 账单日 + 还款日 + 倒计时 */}
      {showsBillingFields ? (
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
          {creditLimit !== null && creditLimit !== undefined ? (
            <CardInfoItem label={t('detail.account.creditLimit')} value={fmt(creditLimit)} />
          ) : null}
          {billingDay ? (
            <CardInfoItem
              label={t('detail.account.billingDay')}
              value={t('detail.account.dayOfMonth', { day: billingDay })}
              hint={t('detail.account.daysUntil', { days: daysUntilDay(billingDay) })}
            />
          ) : null}
          {paymentDueDay ? (
            <CardInfoItem
              label={t('detail.account.paymentDueDay')}
              value={t('detail.account.dayOfMonth', { day: paymentDueDay })}
              hint={t('detail.account.daysUntil', { days: daysUntilDay(paymentDueDay) })}
              urgent={daysUntilDay(paymentDueDay) <= 3}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function CardInfoItem({
  label,
  value,
  hint,
  urgent,
}: {
  label: string
  value: string
  hint?: string
  urgent?: boolean
}) {
  return (
    <div>
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        <CalendarIcon className="h-3 w-3" />
        <span>{label}</span>
      </div>
      <div className="mt-0.5 text-sm font-medium tabular-nums">{value}</div>
      {hint ? (
        <div
          className={`text-[10px] tabular-nums ${
            urgent ? 'text-expense font-semibold' : 'text-muted-foreground'
          }`}
        >
          {hint}
        </div>
      ) : null}
    </div>
  )
}

/**
 * 今天到目标日号还有多少天。
 *   - 今天 ≤ 目标 → 本月内,直接差值
 *   - 今天 > 目标 → 跨月,本月剩余 + 目标日号
 *
 * 目标日号超过当月最大天数(比如 31 号但 2 月)按当月最后一天兜底,跟
 * mobile 端 AccountDetailPage 算法对齐。
 */
function daysUntilDay(targetDay: number): number {
  if (!Number.isFinite(targetDay) || targetDay < 1 || targetDay > 31) return 0
  const now = new Date()
  const today = now.getDate()
  if (today <= targetDay) {
    // 同月内 — 验证当月有这一天(2 月没有 31 号)
    const lastDayThisMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()
    const effective = Math.min(targetDay, lastDayThisMonth)
    return effective - today
  }
  // 跨月 — 算本月剩余 + 下月目标日(下月可能也没那一天)
  const lastDayThisMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()
  const lastDayNextMonth = new Date(now.getFullYear(), now.getMonth() + 2, 0).getDate()
  const effective = Math.min(targetDay, lastDayNextMonth)
  return (lastDayThisMonth - today) + effective
}

/** `2026-08-05T00:00:00Z` -> `2026/08/05`,對齊 Moze 參考截圖的日期顯示格式。 */
function formatDateSlash(iso: string): string {
  return iso.slice(0, 10).replace(/-/g, '/')
}

/**
 * 信用卡合併帳單(§2.9 Phase 4 MOZE_FEATURE_GAP_SD.md)。
 *
 * 只在 account_type=credit_card 且当前有选中账本(activeLedgerId,帐单摘要
 * 是 ledger-scoped 读端点)时渲染。帳戶没設定 billing_day/payment_due_day
 * 时后端回 400,静默不渲染(不是 error 状态,只是这张卡还没配置帐单周期)。
 * 「繳款」按钮开一个小弹窗,建一笔 transfer 交易(cardPayment 语意化端点),
 * 成功后重新拉一次帳單摘要反映最新应缴金额。
 */
function CreditCardBillingSection({
  account,
  t,
  onClose,
  billing,
  onJumpToParentAccount,
}: {
  account: AccountWithStats
  t: (key: string, params?: Record<string, string | number>) => string
  onClose: () => void
  /** 帳單摘要資料(§2.9 Phase 4)現在由 `AccountDetailDialog` 統一透過
   *  `useAccountBilling` 抓取並往下傳,這個元件不再自己 fetch 一次——彈窗
   *  頂部的統計區塊 + 標題列的週期選擇器都需要同一份資料(2026-08-04 使用
   *  者反饋:週期選擇器要移到標題列)。 */
  billing: ReturnType<typeof useAccountBilling>
  onJumpToParentAccount?: (accountId: string) => void
}) {
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict } = useLedgerWrite()
  const toast = useToast()
  const navigate = useNavigate()

  // 2026-08-03 使用者反饋:手機頁面這個區塊太長,展開時交易列表被推到看
  // 不見的地方——加個折疊開關,預設收合只留一行「目前應繳」,點了才展開
  // 看週期明細/子卡明細/免息期建議/自動扣繳等細項。切換帳戶時歸零(不沿用
  // 上一張卡的展開狀態)。
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    setExpanded(false)
  }, [account.id])

  const [payOpen, setPayOpen] = useState(false)
  const [payAmount, setPayAmount] = useState('')
  const [payFromAccountId, setPayFromAccountId] = useState('')
  const [payAccounts, setPayAccounts] = useState<ReadAccount[]>([])
  const [payAccountPickerOpen, setPayAccountPickerOpen] = useState(false)
  const [paying, setPaying] = useState(false)

  const [installOpen, setInstallOpen] = useState(false)

  // 繳費來源帳戶記憶功能(2026-08 使用者反饋 #5):per-user + per-卡(billing
  // Account,子卡時是它掛靠的主帳戶)各記一個,不同卡常用不同的繳費來源。
  const userId = jwtUserId(token || '')
  const payAccountStorageKey = billing.billingAccountId
    ? `beecount:web:cardPaymentAccount:v1:${userId || 'anon'}:${billing.billingAccountId}`
    : ''

  if (!billing.canViewBilling || !activeLedgerId) return null
  if (billing.loading) {
    return (
      <div className="border-b border-border/60 px-6 py-3 text-xs text-muted-foreground">
        {t('cardBilling.loading')}
      </div>
    )
  }
  const { summary, suggestion } = billing
  if (!billing.available || !summary) return null

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })

  const openPayDialog = () => {
    // 2026-09-06 使用者反饋:子卡自己詳情頁點繳費,預設金額該用這張卡自己的
    // 應繳(`ownMember.remaining_due`),不是整組合併帳單的應繳——不然繳款
    // 送出去(見下面 submitPayment)會誤導使用者以為在還自己這張卡,金額卻
    // 是整組的。
    const defaultDue = billing.isBillingChild && billing.ownMember
      ? billing.ownMember.remaining_due
      : summary.remaining_due
    setPayAmount(defaultDue > 0 ? String(defaultDue) : '')
    let remembered = ''
    if (payAccountStorageKey) {
      try {
        remembered = window.localStorage.getItem(payAccountStorageKey) || ''
      } catch {
        remembered = ''
      }
    }
    setPayFromAccountId(remembered)
    setPayOpen(true)
    if (token && activeLedgerId) {
      fetchWorkspaceAccounts(token, { limit: 500 })
        .then((accts) => {
          // 付款來源不能是實際被繳款的那個帳戶(billingAccountId,子卡時是
          // 它掛靠的主帳戶而不是子卡自己)、也不能是正在瀏覽的這張子卡自己。
          // 幣別必須跟這張卡一致(2026-09-06 使用者反饋:這個繳費對話框的
          // 金額欄位沒有匯率換算,不像一般轉帳有 `to_amount`——比照 App 端
          // `CreditCardGroupPaymentPage` 明確排除跨幣別,篩掉幣別不同的帳戶,
          // 避免使用者選了不同幣別的來源卻沒發現金額沒換算)。
          // 注意:不能把 `account_group` 整批濾掉——`AccountPickerDialog`
          // 需要清單裡留著群組列才能正確把掛靠它的子帳戶巢狀渲染在底下
          // (見 `AccountPickerDialog.tsx::buildAccountChildrenMap`,群組列
          // 一旦被抽掉,底下子帳戶會因為 `childIds` 誤判成孤兒而整個消失,
          // 不會回退跟其它同類型帳戶並列)——選不到群組本身這件事已經由
          // `AccountPickerDialog` 自己的 `handleSelect` 擋掉,對齊
          // `TransactionsPanel.tsx::pickerAccountRows` 的既有用法。
          const filtered = accts.filter(
            (a) =>
              a.id !== account.id &&
              a.id !== billing.billingAccountId &&
              a.currency === account.currency,
          )
          setPayAccounts(filtered)
          // 上次記住的帳戶如果已經不在候選清單裡(刪除/隱藏),放棄這個殘留值。
          if (remembered && !filtered.some((a) => a.id === remembered)) {
            setPayFromAccountId('')
          }
        })
        .catch(() => setPayAccounts([]))
    }
  }

  const submitPayment = async () => {
    if (!token || !activeLedgerId || !billing.billingAccountId) return
    const amount = Number(payAmount)
    if (!Number.isFinite(amount) || amount <= 0 || !payFromAccountId) return
    setPaying(true)
    try {
      const fromAccountName = payAccounts.find((a) => a.id === payFromAccountId)?.name || ''
      if (billing.isBillingChild) {
        // 2026-09-06 使用者反饋:子卡自己詳情頁點繳費,伺服器不該把這筆錢
        // 拿去跑群組分攤(`cardPayment` 會依各子卡應繳比例攤給群組底下
        // *所有*子卡,不是只還這張卡)——比照 App 端 `_onAddPaymentRecord`
        // 在 `children.isEmpty`(打開的是子卡自己,不是有掛靠子帳戶的主
        // 帳戶)時的行為,改成一筆單純轉帳直接記在這張卡自己身上。
        await retryOnConflict(activeLedgerId, (base) =>
          createTransaction(token, activeLedgerId, base, {
            tx_type: 'transfer',
            amount,
            happened_at: new Date().toISOString(),
            from_account_id: payFromAccountId,
            from_account_name: fromAccountName,
            to_account_id: account.id,
            to_account_name: account.name,
          }),
        )
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          cardPayment(token, activeLedgerId, billing.billingAccountId!, base, {
            amount,
            from_account_id: payFromAccountId,
          }),
        )
      }
      if (payAccountStorageKey) {
        try {
          window.localStorage.setItem(payAccountStorageKey, payFromAccountId)
        } catch {
          // localStorage 在 private mode / 超配額時可能拋異常,記憶功能失效不影響繳費本身。
        }
      }
      toast.success(t('cardPayment.notice.success'), t('notice.success'))
      setPayOpen(false)
      billing.reload()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setPaying(false)
    }
  }

  return (
    <div className="space-y-3 border-b border-border/60 bg-muted/10 px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {t('cardBilling.title')}
          {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
        <button
          type="button"
          className="rounded-md bg-primary px-3 py-1 text-xs font-medium text-primary-foreground hover:opacity-90"
          onClick={openPayDialog}
        >
          {t('cardBilling.payButton')}
        </button>
      </div>
      {!expanded ? (
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">
            {billing.isBillingChild ? t('cardBilling.selfRemainingDue') : t('cardBilling.remainingDue')}
          </span>
          <span className="font-mono font-semibold tabular-nums text-expense">
            {fmt(billing.isBillingChild && billing.ownMember ? billing.ownMember.remaining_due : summary.remaining_due)}
          </span>
        </div>
      ) : null}
      {expanded ? (
        <>
      {/* 已掛靠群組的子卡(§2.9.6 Phase 7):展開後看到的下面這組數字是
          「整組合併」的帳單金額(共用同一條額度),不是這張卡自己的——加個
          提示避免使用者誤以為是自己單卡的數字,跟上面彈窗頂部
          AccountStatsHeader 的「自身資料」互相呼應。 */}
      {billing.isBillingChild ? (
        <div className="text-[11px] text-muted-foreground">{t('cardBilling.groupMergedHint')}</div>
      ) : null}
      <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('cardBilling.remainingDue')}
          </div>
          <div className="font-mono font-semibold tabular-nums text-expense">
            {fmt(summary.remaining_due)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('cardBilling.dueDate')}
          </div>
          <div className="font-mono">{summary.due_date.slice(0, 10)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('cardBilling.statementAmount')}
          </div>
          <div className="font-mono tabular-nums">{fmt(summary.statement_amount)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('cardBilling.openCycleSpend')}
          </div>
          <div className="font-mono tabular-nums">{fmt(summary.open_cycle_spend)}</div>
        </div>
        {summary.available_credit !== null && summary.available_credit !== undefined ? (
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {t('cardBilling.availableCredit')}
            </div>
            <div className="font-mono font-semibold tabular-nums text-income">
              {fmt(summary.available_credit)}
            </div>
          </div>
        ) : null}
      </div>
      {/* 帳單週期明細(2026-08-04 使用者反饋):新增花費/上期欠款/應繳金額/
          已繳金額/帳單分期/剩餘帳款已經移到彈窗最頂部的統計區塊(見
          AccountStatsHeader),跟著標題列的週期選擇器走,這裡不再重複顯示
          同一組數字。2026-08-07 使用者反饋(§2.9.6 Phase 7):子卡自己的
          詳情頁不該看到「兄弟卡」的金額明細——只在瀏覽的就是主帳戶/獨立卡
          本身(!isBillingChild)時才顯示這份子卡清單,子卡自己的詳情頁想看
          完整明細請走上面的「所屬主帳戶」連結。 */}
      {!billing.isBillingChild && summary.members.length > 1 ? (
        <div className="space-y-1 text-xs text-muted-foreground">
          <div className="font-medium">{t('cardBilling.members')}</div>
          {summary.members.map((m) => (
            <div key={m.account_id} className="flex items-center justify-between">
              <span>{m.account_name}</span>
              <span className="font-mono tabular-nums">{fmt(m.cycle_spend)}</span>
            </div>
          ))}
        </div>
      ) : null}
      {suggestion ? (
        <div className="rounded-md bg-background/60 px-3 py-2 text-xs text-muted-foreground">
          {t('cardBilling.interestFreeHint', {
            date: suggestion.next_cycle_start.slice(0, 10),
            due: suggestion.next_cycle_due_date.slice(0, 10),
            days: suggestion.max_interest_free_days,
          })}
        </div>
      ) : null}

      {/* 自動扣繳(§2.9,2026-08-04 改版)状态:開關 + 來源帳戶現在直接設在
          這張卡/群組自己的編輯表單裡(帳戶列表頁「編輯」),不再是另一個
          頁面的週期性收支規則,這裡只顯示目前狀態 + 指引入口。分期入口
          (§2.3)維持既有的另一個通用頁面。2026-08-04 第二輪使用者反饋:
          子卡自己的 `auto_pay_enabled` 欄位恆為空(這個開關實際設在它掛靠
          的主帳戶身上)——顯示子卡自己的欄位會誤導成「一律關閉」,子卡改成
          指向「前往主帳戶查看」而不是顯示可能錯誤的狀態。 */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {billing.isBillingChild ? (
          onJumpToParentAccount ? (
            <button
              type="button"
              className="rounded-md border border-border px-2.5 py-1 font-medium text-muted-foreground hover:bg-muted/50"
              onClick={() => onJumpToParentAccount(billing.billingAccountId!)}
            >
              {t('cardBilling.autoPayOnParentHint')}
            </button>
          ) : null
        ) : (
          <>
            <span
              className={`rounded-md px-2.5 py-1 font-medium ${
                account.auto_pay_enabled
                  ? 'bg-primary/15 text-primary'
                  : 'bg-muted/50 text-muted-foreground'
              }`}
            >
              {account.auto_pay_enabled ? t('cardBilling.autoPayOn') : t('cardBilling.autoPayOff')}
            </span>
            <button
              type="button"
              className="rounded-md border border-border px-2.5 py-1 font-medium text-muted-foreground hover:bg-muted/50"
              onClick={() => {
                // 详情弹窗是全局挂载的(可能从任意页面打开),先关自己 + 跳到
                // 账户列表页,再派发 openEditAccount 让该页把这个账户装进编辑
                // 表单 —— 单纯 navigate 在已经身处 /app/accounts 时是个
                // no-op,弹窗也不会自己关掉,点了跟没点一样(2026-08-02 用户
                // 实测反馈)。
                onClose()
                navigate('/app/accounts')
                dispatchOpenEditAccount(account as WorkspaceAccount)
              }}
            >
              {t('cardBilling.manageAutoPay')}
            </button>
          </>
        )}
        <button
          type="button"
          disabled={summary.remaining_due <= 0}
          title={summary.remaining_due <= 0 ? t('cardBilling.installment.noBalanceHint') : undefined}
          className="rounded-md border border-border px-2.5 py-1 font-medium text-muted-foreground hover:bg-muted/50 disabled:cursor-not-allowed disabled:opacity-40"
          onClick={() => setInstallOpen(true)}
        >
          {t('cardBilling.setupInstallment')}
        </button>
      </div>
        </>
      ) : null}

      <Dialog open={payOpen} onOpenChange={setPayOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('cardPayment.dialog.title')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('cardPayment.dialog.amount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                value={payAmount}
                onChange={(e) => setPayAmount(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('cardPayment.dialog.fromAccount')}</Label>
              <button
                type="button"
                onClick={() => setPayAccountPickerOpen(true)}
                className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
              >
                <span className={`flex-1 truncate ${payFromAccountId ? '' : 'text-muted-foreground'}`}>
                  {payFromAccountId
                    ? payAccounts.find((a) => a.id === payFromAccountId)?.name || payFromAccountId
                    : t('cardPayment.dialog.fromAccountPlaceholder')}
                </span>
                <span className="text-xs text-muted-foreground opacity-60">▾</span>
              </button>
            </div>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              className="rounded-md border border-border px-3 py-1.5 text-sm"
              onClick={() => setPayOpen(false)}
            >
              {t('dialog.cancel')}
            </button>
            <button
              type="button"
              disabled={paying || !payFromAccountId || !payAmount}
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
              onClick={() => void submitPayment()}
            >
              {t('cardPayment.dialog.submit')}
            </button>
          </div>
        </DialogContent>
      </Dialog>
      <AccountPickerDialog
        open={payAccountPickerOpen}
        onClose={() => setPayAccountPickerOpen(false)}
        title={t('cardPayment.dialog.fromAccount')}
        accounts={payAccounts}
        value={payAccounts.find((a) => a.id === payFromAccountId)?.name || ''}
        onSelect={(row) => setPayFromAccountId(row.id)}
      />

      {installOpen && billing.billingAccountId ? (
        <InstallmentQuickCreateDialog
          onClose={() => setInstallOpen(false)}
          token={token || ''}
          ledgerId={activeLedgerId}
          accountId={billing.billingAccountId}
          accountLabel={summary.account_name || account.name}
          defaultAmount={summary.period_total_due > 0 ? summary.period_total_due : summary.remaining_due}
          defaultDueDate={summary.due_date}
          retryOnConflict={retryOnConflict}
          onCreated={() => billing.reload()}
        />
      ) : null}
    </div>
  )
}

/**
 * 帳單分期快速建立(§2.3,2026-08-02 補強):不再只靠導去 `/app/
 * installment-plans` 頁面 —— 直接在帳單卡片這裡開一個小視窗設定,建完之後
 * 一樣是普通的分期付款計畫,還是可以在 `/app/installment-plans` 總頁面
 * 管理(調利率/提前還本/提前結清等差異化操作留在那邊,這裡只做「新建」)。
 * 攤還方式對應信用卡常見的三種算法:等額本金 / 等額本息 / 固定利率。
 */
function InstallmentQuickCreateDialog({
  onClose,
  token,
  ledgerId,
  accountId,
  accountLabel,
  defaultAmount,
  defaultDueDate,
  retryOnConflict,
  onCreated,
}: {
  onClose: () => void
  token: string
  ledgerId: string | null
  /** 建分期的目標帳戶(§2.3,2026-08-02 第三輪):永遠是使用者正在瀏覽帳單
   *  的那個帳戶(可能是 account_group 主帳戶,也可能是沒有掛靠任何群組的
   *  獨立信用卡)——不再讓使用者從子卡清單裡手動挑一張,用戶反饋「有主帳戶
   *  就該以主帳戶為單位分期,不是選擇信用卡」,server 端會自動把沖銷/各期
   *  交易分攤/掛到正確的真實子帳戶上。 */
  accountId: string
  accountLabel: string
  defaultAmount: number
  defaultDueDate: string
  retryOnConflict: <T,>(ledgerId: string, submit: (baseChangeId: number) => Promise<T>) => Promise<T>
  onCreated: () => void
}) {
  const t = useT()
  const toast = useToast()
  const [amount, setAmount] = useState(defaultAmount > 0 ? String(defaultAmount) : '')
  const [periods, setPeriods] = useState('3')
  const [firstPeriodAt, setFirstPeriodAt] = useState(defaultDueDate.slice(0, 10))
  const [repaymentMethod, setRepaymentMethod] = useState<InstallmentRepaymentMethod>('equal_principal')
  const [interestRate, setInterestRate] = useState('0')
  const [interestPeriod, setInterestPeriod] = useState<InstallmentInterestPeriod>('monthly')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // 分類(2026-08-02 補強):不选就跟以前一样落空(交易列表显示"-")——用户
  // 反馈这样完全看不出那笔分期扣款是什么,所以加个选择器,并且比照 §2.5
  // 借還款「+ 建立新欠款」的既有 sentinel 模式,允许直接现场新建一个分類,
  // 不用跳去分類頁面來回切換。
  const NEW_CATEGORY_VALUE = '__new__'
  const [categories, setCategories] = useState<WorkspaceCategory[]>([])
  const [categoryId, setCategoryId] = useState('')
  const [newCategoryName, setNewCategoryName] = useState('')

  useEffect(() => {
    if (!ledgerId) return
    let cancelled = false
    fetchWorkspaceCategories(token, { ledgerId, limit: 500 })
      .then((rows) => {
        if (!cancelled) setCategories(rows.filter((c) => c.kind === 'expense'))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [token, ledgerId])

  const canSubmit =
    Boolean(accountId) &&
    Number(amount) > 0 &&
    Number(periods) >= 1 &&
    Boolean(firstPeriodAt) &&
    Boolean(ledgerId) &&
    // 需求 #14(Phase 12):分期付款分類必填,避免每期生成的交易漏分類。
    Boolean(categoryId) &&
    (categoryId !== NEW_CATEGORY_VALUE || Boolean(newCategoryName.trim()))

  const handleSubmit = async () => {
    if (!ledgerId || !canSubmit) return
    setSubmitting(true)
    try {
      // 新建分類跟建分期計畫是兩次獨立的 retryOnConflict 呼叫(對齊 §2.5
      // 借還款「+ 建立新欠款」的既有慣例——分類建立失敗只提示,不影響
      // 使用者接下來仍可以選別的既有分類重試,不用整份表單重填)。
      let finalCategoryId = categoryId
      if (categoryId === NEW_CATEGORY_VALUE) {
        const trimmed = newCategoryName.trim()
        const created = await retryOnConflict(ledgerId, (base) =>
          createCategory(token, ledgerId, base, { name: trimmed, kind: 'expense' }),
        )
        finalCategoryId = created.entity_id || ''
      }
      await retryOnConflict(ledgerId, (base) =>
        createInstallmentPlan(token, ledgerId, base, {
          total_amount: Number(amount),
          periods: Math.round(Number(periods)),
          first_period_at: new Date(`${firstPeriodAt}T00:00:00`).toISOString(),
          account_id: accountId,
          category_id: finalCategoryId || null,
          note: note || null,
          repayment_method: repaymentMethod,
          interest_period: interestPeriod,
          interest_rate: Number(interestRate) || 0,
          // 从帳單卡片這裡建的分期,永遠是「把已經欠下的帳單轉成分期」——
          // 一定要沖銷,不然原本那筆消費金額會跟分期各期新生成的 expense
          // 重複計入應繳金額(§2.9,2026-08-02 補強)。
          offset_existing_balance: true,
        }),
      )
      toast.success(t('cardBilling.installment.notice.success'), t('notice.success'))
      onCreated()
      onClose()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t('cardBilling.installment.dialogTitle')}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
          <p className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
            {t('cardBilling.installment.offsetNotice')}
          </p>
          <div className="space-y-1">
            <Label>{t('installmentPlans.field.account')}</Label>
            <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm">
              {accountLabel}
            </div>
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.category')}</Label>
            <Select
              value={categoryId}
              onValueChange={(value) => {
                setCategoryId(value)
                if (value !== NEW_CATEGORY_VALUE) setNewCategoryName('')
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder={t('transactions.placeholder.categoryName')} />
              </SelectTrigger>
              <SelectContent>
                {categories.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name}
                  </SelectItem>
                ))}
                <SelectItem value={NEW_CATEGORY_VALUE}>
                  {t('cardBilling.installment.newCategory')}
                </SelectItem>
              </SelectContent>
            </Select>
            {categoryId === NEW_CATEGORY_VALUE ? (
              <Input
                autoFocus
                value={newCategoryName}
                onChange={(e) => setNewCategoryName(e.target.value)}
                placeholder={t('cardBilling.installment.newCategoryPlaceholder')}
              />
            ) : null}
          </div>
          <div className="space-y-1">
            <Label>{t('installmentPlans.field.totalAmount')}</Label>
            <Input
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>{t('installmentPlans.field.periods')}</Label>
            <Input
              type="number"
              inputMode="numeric"
              min="1"
              max="600"
              value={periods}
              onChange={(e) => setPeriods(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>{t('installmentPlans.field.firstPeriodAt')}</Label>
            <DatePicker value={firstPeriodAt} onChange={(next) => setFirstPeriodAt(next)} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label>{t('installmentPlans.field.repaymentMethod')}</Label>
              <Select
                value={repaymentMethod}
                onValueChange={(value) => setRepaymentMethod(value as InstallmentRepaymentMethod)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="equal_principal">
                    {t('installmentPlans.repaymentMethod.equalPrincipal')}
                  </SelectItem>
                  <SelectItem value="equal_installment">
                    {t('installmentPlans.repaymentMethod.equalInstallment')}
                  </SelectItem>
                  <SelectItem value="fixed_interest">
                    {t('installmentPlans.repaymentMethod.fixedInterest')}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('installmentPlans.field.interestRate')}</Label>
              {/* 需求 #17(Phase 12):輸入整數百分比,`interestRate` state 仍存小數分數。 */}
              <Input
                type="number"
                min="0"
                step="0.1"
                value={interestRateToPercentDisplay(interestRate)}
                onChange={(e) => setInterestRate(percentDisplayToInterestRate(e.target.value))}
              />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label>{t('installmentPlans.field.interestPeriod')}</Label>
              <Select
                value={interestPeriod}
                onValueChange={(value) => setInterestPeriod(value as InstallmentInterestPeriod)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="monthly">{t('installmentPlans.interestPeriod.monthly')}</SelectItem>
                  <SelectItem value="daily">{t('installmentPlans.interestPeriod.daily')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.note')}</Label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-border px-3 py-1.5 text-sm"
            onClick={onClose}
          >
            {t('dialog.cancel')}
          </button>
          <button
            type="button"
            disabled={submitting || !canSubmit}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
            onClick={() => void handleSubmit()}
          >
            {t('installmentPlans.button.create')}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
