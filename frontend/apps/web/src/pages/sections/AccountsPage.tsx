import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  createAccount,
  deleteAccount,
  fetchExchangeRateOverrides,
  fetchExchangeRates,
  fetchWorkspaceAccounts,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  updateAccount,
  type ExchangeRateOverride,
  type ExchangeRatesResponse,
  type ReadAccount,
  type WorkspaceAccount,
  type WorkspaceTag,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import {
  Button,
  Card,
  CardContent,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  useT,
  useToast,
} from '@beecount/ui'
import {
  AccountsPanel,
  Amount,
  AssetsCompositionMini,
  ConfirmDialog,
  CurrencyAssetCard,
  accountDefaults,
  computeCurrencySummary,
  computeTypeGroups,
  effectiveRateToBase,
  mergeGroupsToBase,
  splitByCurrency,
  type AccountForm,
  type CurrencyBucket,
} from '@beecount/web-features'

import { dispatchOpenDetailAccount } from '../../lib/txDialogEvents'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

const ACCOUNT_DETAIL_PAGE_SIZE = 20

// 折算汇总开关(设备级,跨账本/跨用户共用)。默认 true = 显示折算汇总视图;
// 关闭后回到「每币种一张卡」的现状。照 App.tsx token 惯例:useState 初始化器
// 读 localStorage,useEffect 写回。仅在「主币种已设 且 ≥2 币种」时才在 UI 露出。
const CONVERTED_VIEW_KEY = 'beecount:web:accounts:converted'

function readConvertedView(): boolean {
  try {
    // 缺省(从未设置过)按 true;仅显式存了 '0' 才算关闭。
    return localStorage.getItem(CONVERTED_VIEW_KEY) !== '0'
  } catch {
    return true
  }
}

/**
 * 账户 / 资产页 —— 账户列表 + CRUD(无 delete,web 只支持创建/编辑)
 * + 账户详情 dialog(点卡片弹出该账户的交易列表,无限滚动)。
 *
 * tags 独立 fetch 一份,只为 AccountDetailDialog 里 TransactionList 渲染
 * tag chip 用 —— 不跟其它 page 共享,每次进入该页现拉。
 *
 * 已知回归:AccountDetailDialog 的附件预览(resolveAttachmentPreviewUrl /
 * onPreviewAttachment)本轮留空,预览功能待 "附件预览共享 hook" 独立 task。
 */
export function AccountsPage() {
  const t = useT()
  const toast = useToast()
  const { token, profileMe } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const base = profileMe?.primary_currency || ''

  // 主要数据走 PageDataCache —— 切走再切回来立刻显示上次的值,不闪烁。
  // rows 用 WorkspaceAccount(包含 tx_count / balance 等聚合字段),删除前需要
  // 看 tx_count 决定是否提示用户(对齐 mobile account_edit_page._delete)。
  const [rows, setRows] = usePageCache<WorkspaceAccount[]>('accounts:rows', [])
  const [tags, setTags] = usePageCache<WorkspaceTag[]>('accounts:tags', [])
  const [form, setForm] = useState<AccountForm>(accountDefaults())
  // 删除前的待确认账户。null = 无 pending。WorkspaceAccount 带 tx_count 字段,
  // confirm dialog 直接读它,不再发额外请求。
  const [pendingDelete, setPendingDelete] = useState<WorkspaceAccount | null>(null)
  const [deleting, setDeleting] = useState(false)

  // 折算汇总视图开关 + 分币种明细 dialog。开关设备级持久化(见 CONVERTED_VIEW_KEY)。
  const [convertedView, setConvertedView] = useState<boolean>(() => readConvertedView())
  const [detailOpen, setDetailOpen] = useState(false)
  useEffect(() => {
    try {
      localStorage.setItem(CONVERTED_VIEW_KEY, convertedView ? '1' : '0')
    } catch {
      // private mode / 超配额忽略
    }
  }, [convertedView])

  // 多币种折算(只读卡)。主币种存在且账户币种 ≥2 种时,并行拉汇率 + 手动 override,
  // 任一失败置 null 不阻塞账户列表。单币种 / 无主币种则不渲染卡(零变化)。
  // key 带 base 维度:切换主币种后不会复用旧 base 的汇率缓存。
  const [rates, setRates] = usePageCache<ExchangeRatesResponse | null>(
    base ? `accounts:rates:${base}` : 'accounts:rates:',
    null,
  )
  const [rateOverrides, setRateOverrides] = usePageCache<ExchangeRateOverride[]>(
    base ? `accounts:rateOverrides:${base}` : 'accounts:rateOverrides:',
    [],
  )

  // detail 弹窗已迁到 GlobalEntityDialogs(AppShell 顶层),本页只负责
  // dispatch openDetailAccount 事件,弹窗在全局渲染。

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t]
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t]
  )

  const refresh = useCallback(async () => {
    try {
      const [accountRows, tagRows] = await Promise.all([
        fetchWorkspaceAccounts(token, { limit: 500 }),
        fetchWorkspaceTags(token, { limit: 500 }),
      ])
      setRows(accountRows)
      setTags(tagRows)

      // 只有"主币种存在 + 账户涉及 ≥2 种币种"才需要折算卡。其余情况清空缓存,
      // 让卡不渲染。汇率请求任一失败置 null,不影响账户列表正常展示。
      const distinct = new Set<string>()
      for (const a of accountRows) {
        const cur = (a.currency || '').toUpperCase()
        if (cur) distinct.add(cur)
      }
      if (base && distinct.size >= 2) {
        const [r, o] = await Promise.all([
          fetchExchangeRates(token, base).catch(() => null),
          fetchExchangeRateOverrides(token).catch(() => [] as ExchangeRateOverride[]),
        ])
        setRates(r)
        setRateOverrides(o)
      } else {
        setRates(null)
        setRateOverrides([])
      }
    } catch (err) {
      notifyError(err)
    }
  }, [token, base, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  const onSave = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const trimmedName = form.name.trim()
    if (!trimmedName) {
      toast.error(t('accounts.error.nameRequired'), t('notice.error'))
      return false
    }
    // mobile account_edit_page 也禁止重名,跨端一致。编辑自己时跳过。
    const duplicate = rows.find(
      (row) =>
        (row.name || '').trim().toLowerCase() === trimmedName.toLowerCase() &&
        row.id !== form.editingId,
    )
    if (duplicate) {
      toast.error(t('accounts.error.nameDuplicate'), t('notice.error'))
      return false
    }
    const initialBalanceNum = Number(form.initial_balance || 0)
    if (!Number.isFinite(initialBalanceNum)) {
      toast.error(t('accounts.error.balanceInvalid'), t('notice.error'))
      return false
    }
    // 信用卡日期校验:1-31,空字符串视作未填(null)。其他类型不要这两个字段,
    // 走 onFormChange 切换类型时已经清空,这里再 guard 一次。
    const billingDayNum =
      form.account_type === 'credit_card' && form.billing_day.trim()
        ? Math.round(Number(form.billing_day))
        : null
    if (billingDayNum !== null && (!Number.isFinite(billingDayNum) || billingDayNum < 1 || billingDayNum > 31)) {
      toast.error(t('accounts.error.billingDayInvalid'), t('notice.error'))
      return false
    }
    const paymentDueDayNum =
      form.account_type === 'credit_card' && form.payment_due_day.trim()
        ? Math.round(Number(form.payment_due_day))
        : null
    if (paymentDueDayNum !== null && (!Number.isFinite(paymentDueDayNum) || paymentDueDayNum < 1 || paymentDueDayNum > 31)) {
      toast.error(t('accounts.error.paymentDueDayInvalid'), t('notice.error'))
      return false
    }
    const creditLimitRaw = form.credit_limit.trim()
    const creditLimitNum =
      form.account_type === 'credit_card' && creditLimitRaw ? Number(creditLimitRaw) : null
    if (creditLimitNum !== null && (!Number.isFinite(creditLimitNum) || creditLimitNum < 0)) {
      toast.error(t('accounts.error.creditLimitInvalid'), t('notice.error'))
      return false
    }
    try {
      const isCreditCard = form.account_type === 'credit_card'
      const isBankOrCredit = isCreditCard || form.account_type === 'bank_card'
      const payload = {
        name: trimmedName,
        account_type: form.account_type || null,
        currency: form.currency || null,
        initial_balance: initialBalanceNum,
        // 扩展字段:non-credit_card 类型显式传 null 清空 server 上残留的值;
        // bank_card / credit_card 才有 bank_name / card_last_four。
        note: form.note.trim() || null,
        credit_limit: isCreditCard ? creditLimitNum : null,
        billing_day: isCreditCard ? billingDayNum : null,
        payment_due_day: isCreditCard ? paymentDueDayNum : null,
        bank_name: isBankOrCredit ? form.bank_name.trim() || null : null,
        card_last_four: isBankOrCredit ? form.card_last_four.trim() || null : null,
      }
      await retryOnConflict(activeLedgerId, (base) =>
        form.editingId
          ? updateAccount(token, activeLedgerId, form.editingId, base, payload)
          : createAccount(token, activeLedgerId, base, payload)
      )
      setForm(accountDefaults())
      await refresh()
      notifySuccess(form.editingId ? t('notice.accountUpdated') : t('notice.accountCreated'))
      return true
    } catch (err) {
      if (isWriteConflict(err)) {
        await refresh()
        notifyError(err)
        return false
      }
      notifyError(err)
      return false
    }
  }


  // 删除流程:点删除按钮 → 弹 ConfirmDialog,dialog 里根据 tx_count 决定文案。
  // 跟 mobile account_edit_page._delete 对齐:有交易则警示总条数 + 红色按钮。
  const onConfirmDelete = async () => {
    if (!pendingDelete) return
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return
    }
    setDeleting(true)
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteAccount(token, activeLedgerId, pendingDelete.id, base),
      )
      setPendingDelete(null)
      await refresh()
      notifySuccess(t('notice.accountDeleted'))
    } catch (err) {
      if (isWriteConflict(err)) {
        await refresh()
      }
      notifyError(err)
    } finally {
      setDeleting(false)
    }
  }

  // 折算汇总:按币种分组 → 每币种 summary(netWorth/assetTotal/liabilityTotal)× 汇率累加。
  // 复用 assetAggregation 铁律原语确保负债符号契约单点。缺失汇率的币种进 missing 列表、
  // 不计入任何总额 / donut(整币种剔除),**绝不按 1 折算**。
  // 单币种 / 无主币种 → converted=null,折算卡及开关都不出现(现状零变化)。
  //   - buckets:每币种 summary + 分组,既给合并 donut(mergeGroupsToBase),也给详情 dialog 的分币种卡复用。
  //   - mergedGroups:各币种 groups × 汇率折算后按 type 聚合成一份主币种构成,喂 donut(currency=base)。
  const converted = useMemo(() => {
    if (!base) return null
    const byCur = splitByCurrency(rows)
    if (byCur.size < 2) return null

    const buckets: CurrencyBucket[] = []
    let netWorth = 0
    let assetTotal = 0
    let liabilityTotal = 0
    const missing = new Set<string>()
    for (const [cur, curRows] of byCur) {
      const summary = computeCurrencySummary(curRows)
      // 详情 dialog 复用 CurrencyAssetCard,需要全部币种(含缺失汇率的)原样展示。
      buckets.push({ currency: cur, summary, groups: computeTypeGroups(curRows, t) })
      const eff = effectiveRateToBase(cur, base, rates, rateOverrides)
      if (!eff) {
        missing.add(cur)
        continue
      }
      netWorth += summary.netWorth * eff.rate
      assetTotal += summary.assetTotal * eff.rate
      liabilityTotal += summary.liabilityTotal * eff.rate
    }
    // donut 与上面总额同口径:mergeGroupsToBase 内部对缺失汇率币种同样剔除。
    const mergedGroups = mergeGroupsToBase(buckets, base, rates, rateOverrides)
    return {
      netWorth,
      assetTotal,
      liabilityTotal,
      mergedGroups,
      buckets: buckets.sort(
        (a, b) =>
          b.summary.assetTotal +
          Math.abs(b.summary.liabilityTotal) -
          (a.summary.assetTotal + Math.abs(a.summary.liabilityTotal)),
      ),
      missing: [...missing].sort(),
      rateDate: rates?.rate_date,
    }
  }, [base, rows, rates, rateOverrides, t])

  return (
    <>
      {/* 折算汇总:仅「主币种已设 且 ≥2 币种」时出现(converted!=null)。
          - convertedView 开(默认):折算卡(净资产≈ + 资产≈/负债≈ + 合并构成 donut
            + 详情按钮),header 右侧 toggle 可关;AccountsPanel 隐藏分币种卡。
          - convertedView 关:折算卡隐藏,这里只留一行 toggle(可逆,再打开),
            分币种卡由 AccountsPanel 照常渲染。 */}
      {converted ? (
        convertedView ? (
          <Card className="bc-panel mb-4">
            <CardContent className="space-y-3 p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <p className="text-xs text-muted-foreground">
                    {t('accounts.converted.netWorth', { currency: base })}
                  </p>
                  <div className="flex items-baseline gap-1">
                    <span className="font-mono text-sm text-muted-foreground">≈</span>
                    <Amount
                      value={converted.netWorth}
                      currency={base}
                      showCurrency
                      size="2xl"
                      bold
                      tone={converted.netWorth >= 0 ? 'positive' : 'negative'}
                    />
                  </div>
                </div>
                <ConvertedToggle
                  on={convertedView}
                  label={t('accounts.converted.toggle')}
                  onChange={setConvertedView}
                />
              </div>

              {/* 资产 ≈ / 负债 ≈ 两个小项(与净资产同口径:缺失汇率币种已剔除) */}
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-3 py-2">
                  <div className="text-[10px] uppercase tracking-wider text-emerald-600/80 dark:text-emerald-400/80">
                    {t('accounts.assets')}
                  </div>
                  <div className="mt-0.5 flex items-baseline gap-1">
                    <span className="font-mono text-xs text-muted-foreground">≈</span>
                    <Amount
                      value={converted.assetTotal}
                      currency={base}
                      size="lg"
                      bold
                      showCurrency
                      tone="positive"
                    />
                  </div>
                </div>
                <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-3 py-2">
                  <div className="text-[10px] uppercase tracking-wider text-rose-600/80 dark:text-rose-400/80">
                    {t('accounts.liabilities')}
                  </div>
                  <div className="mt-0.5 flex items-baseline gap-1">
                    <span className="font-mono text-xs text-muted-foreground">≈</span>
                    <Amount
                      value={Math.abs(converted.liabilityTotal)}
                      currency={base}
                      size="lg"
                      bold
                      showCurrency
                      tone="negative"
                    />
                  </div>
                </div>
              </div>

              {/* 合并构成 donut —— 各币种分组折算到主币种后按类型聚合(currency=base)。 */}
              {converted.mergedGroups.length > 0 ? (
                <AssetsCompositionMini
                  groups={converted.mergedGroups}
                  totalAbs={converted.assetTotal + Math.abs(converted.liabilityTotal)}
                  currency={base}
                  showCurrency
                  embedded
                  approx
                  title={t('accounts.converted.composition', { currency: base })}
                />
              ) : null}

              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 pt-1">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                  {converted.rateDate ? (
                    <span className="text-[11px] text-muted-foreground">
                      {t('accounts.converted.footnote', { date: converted.rateDate })}
                    </span>
                  ) : null}
                  {converted.missing.length > 0 ? (
                    <span className="text-[11px] text-amber-600 dark:text-amber-500">
                      {t('accounts.converted.missing', {
                        currencies: converted.missing.join(', '),
                      })}
                    </span>
                  ) : null}
                </div>
                <Button variant="outline" size="sm" onClick={() => setDetailOpen(true)}>
                  {t('accounts.converted.detail')}
                </Button>
              </div>
            </CardContent>
          </Card>
        ) : (
          // 折算关:折算卡隐藏,仅留可逆 toggle 在分币种卡上方。
          <div className="mb-3 flex items-center justify-end">
            <ConvertedToggle
              on={convertedView}
              label={t('accounts.converted.toggle')}
              onChange={setConvertedView}
            />
          </div>
        )
      ) : null}
      <AccountsPanel
        form={form}
        rows={rows}
        canManage
        hideCurrencyCards={Boolean(converted) && convertedView}
        onFormChange={setForm}
        onSave={onSave}
        onReset={() => setForm(accountDefaults())}
        onEdit={(row) => {
          setForm({
            editingId: row.id,
            editingOwnerUserId: row.created_by_user_id || '',
            name: row.name,
            account_type: row.account_type || '',
            currency: row.currency || '',
            initial_balance: String(row.initial_balance ?? 0),
            note: row.note ?? '',
            credit_limit: row.credit_limit !== null && row.credit_limit !== undefined
              ? String(row.credit_limit)
              : '',
            billing_day: row.billing_day !== null && row.billing_day !== undefined
              ? String(row.billing_day)
              : '',
            payment_due_day: row.payment_due_day !== null && row.payment_due_day !== undefined
              ? String(row.payment_due_day)
              : '',
            bank_name: row.bank_name ?? '',
            card_last_four: row.card_last_four ?? '',
          })
        }}
        onClickAccount={(row) =>
          dispatchOpenDetailAccount(row as WorkspaceAccount, { defaultScope: 'all' })
        }
        onDelete={(row) => {
          // 严格策略:有关联交易直接拒绝,不弹"是否强制删除"。先要求用户在
          // 详情页/交易页把这些交易改/删/迁走,账户回到 0 笔再来删。比 mobile
          // 现在的"warn + allow orphan"更严格 —— 避免误删导致一堆 ungrouped
          // 交易污染 ledger。
          const ws = rows.find((r) => r.id === row.id) || (row as WorkspaceAccount)
          if ((ws.tx_count ?? 0) > 0) {
            toast.error(
              t('accounts.delete.blockedByTransactions', {
                name: ws.name,
                count: ws.tx_count ?? 0,
              }),
              t('notice.error'),
            )
            return
          }
          setPendingDelete(ws)
        }}
      />
      {/* AccountDetailDialog 已迁到 GlobalEntityDialogs */}
      {/* 删除确认 — 有 tx 时显示 warning 文案 + count(对齐 mobile);无 tx
          就普通确认。dialog confirm 后调 deleteAccount,server 端会 silent
          orphan 关联交易(snapshot_mutator.delete_account 已实现 strip
          accountName)—— 跟 mobile 同款语义。 */}
      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void onConfirmDelete()}
        loading={deleting}
        title={t('dialog.confirm')}
        description={t('accounts.delete.confirmMessage', { name: pendingDelete?.name || '' })}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
      {/* 分币种明细 dialog —— 折算汇总卡的「详情」入口,复用 CurrencyAssetCard
          按现状网格逐币种渲染(含缺失汇率币种,原样不折算)。 */}
      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="max-h-[88vh] w-[92vw] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{t('accounts.converted.detailTitle')}</DialogTitle>
          </DialogHeader>
          {converted ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {converted.buckets.map((entry) => (
                <CurrencyAssetCard key={entry.currency} entry={entry} />
              ))}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  )
}

/**
 * 折算汇总开关 —— 仓内无 Switch 组件,用一颗自绘 pill toggle(checkbox 语义 +
 * label),亮暗通用、不引新依赖。开启态填主题色,关闭态描边灰。
 */
function ConvertedToggle({
  on,
  label,
  onChange,
}: {
  on: boolean
  label: string
  onChange: (next: boolean) => void
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      className={`inline-flex shrink-0 items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
        on
          ? 'border-primary/40 bg-primary/10 text-primary'
          : 'border-border bg-transparent text-muted-foreground hover:bg-muted/40'
      }`}
    >
      <span
        className={`relative h-3.5 w-6 shrink-0 rounded-full transition-colors ${
          on ? 'bg-primary' : 'bg-muted-foreground/40'
        }`}
        aria-hidden
      >
        <span
          className={`absolute top-1/2 h-2.5 w-2.5 -translate-y-1/2 rounded-full bg-white shadow-sm transition-all ${
            on ? 'left-[12px]' : 'left-0.5'
          }`}
        />
      </span>
      <span className="whitespace-nowrap">{label}</span>
    </button>
  )
}
