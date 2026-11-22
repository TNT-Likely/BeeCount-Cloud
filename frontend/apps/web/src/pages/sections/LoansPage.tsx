import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ChevronDown, HandCoins, Plus, Receipt } from 'lucide-react'

import {
  type Contact,
  type Loan,
  type LoanDirection,
  type LoanInterestType,
  type LoanRepayment,
  type LoanStatus,
  type ReadAccount,
  createLoan,
  createMergedRepayment,
  createRepayment,
  deleteLoan,
  deleteRepayment,
  fetchContacts,
  fetchLoan,
  fetchLoans,
  fetchReadAccounts,
  fetchRepayments,
  updateLoan,
} from '@beecount/api-client'
import {
  useT,
  useToast,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
  Button,
  Input,
  Textarea,
  Label,
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@beecount/ui'
import {
  CategoryIcon,
  flowIconKeyForLoan,
  formatBalanceCompact,
  formatIsoDateTimeLocal,
  resolveFlowIcon,
  resolveFlowIconColor,
  toLocalDateTimeInput as toLocalInput,
  fromLocalDateTimeInput as fromLocalInput,
  useFlowIconColorPrefs,
  useFlowIconPrefs,
  SectionPageHeader,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { openTxDetailBySyncId, onOpenDetailLoan } from '../../lib/txDialogEvents'
import { useLedgers } from '../../context/LedgersContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import {
  LoanAttachmentEditorField,
  LoanAttachmentThumbs,
  type LoanAttachmentEditorHandle,
} from '../../components/LoanAttachments'

const DIRECTIONS: LoanDirection[] = ['lend', 'borrow']
const INTEREST_TYPES: LoanInterestType[] = ['none', 'fixed', 'annual']
const STATUSES: LoanStatus[] = ['unpaid', 'partial', 'overdue', 'settled']

function statusBadgeClass(status: LoanStatus): string {
  switch (status) {
    case 'settled':
      return 'bg-emerald-500/15 text-emerald-600'
    case 'overdue':
      return 'bg-red-500/15 text-red-600'
    case 'partial':
      return 'bg-amber-500/15 text-amber-600'
    default:
      return 'bg-muted text-muted-foreground'
  }
}

/**
 * 借款/还款共用的账户下拉数据。借款原账户即使已被隐藏也要 pin 进选项,
 * 否则编辑回填时 Select 的 value 找不到匹配项,会显示成空。
 */
function useLoanAccountOptions(open: boolean, loan: Loan | null) {
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !token || !activeLedgerId) return
    let cancelled = false
    setLoading(true)
    fetchReadAccounts(token, activeLedgerId)
      .then((rows) => { if (!cancelled) setAccounts(rows.filter((a) => !a.hidden)) })
      .catch(() => { if (!cancelled) setAccounts([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [open, token, activeLedgerId])

  const options = useMemo(() => {
    const rows = accounts.map((a) => ({ id: a.id, name: a.name }))
    if (loan?.account_id && !rows.some((r) => r.id === loan.account_id)) {
      rows.unshift({ id: loan.account_id, name: loan.account_name || '' })
    }
    return rows
  }, [accounts, loan])

  return { options, loading }
}

/**
 * 借款余额统一视图,由「本金 + 已结算还款额」派生四态(容差 0.005 与后端一致):
 *  - overpaid:还款 > 本金 → 超出金额要显示(+overpaid,琥珀色);
 *  - partial :部分还款 → 显示剩余(方向色);
 *  - cleared :恰好还清 → 原金额删除线;
 *  - unpaid  :无还款 → 原金额。
 *
 * 注意:这里不再直接吃 loan.total_repaid。结清维度按「人」,还款额由
 * groupLoans 把整人还款池分配到各笔后(allocated)再传进来,避免
 * 「这一笔超还、同一人另一笔还欠着」这种以单条记录为单位的错误口径。
 */
type LoanBalanceView = {
  state: 'overpaid' | 'partial' | 'cleared' | 'unpaid'
  remaining: number
  overpaid: number
}
function balanceView(amount: number, repaid: number): LoanBalanceView {
  const diff = amount - repaid
  if (diff < -0.005) return { state: 'overpaid', remaining: 0, overpaid: -diff }
  if (repaid <= 0) return { state: 'unpaid', remaining: amount, overpaid: 0 }
  if (diff <= 0.005) return { state: 'cleared', remaining: 0, overpaid: 0 }
  return { state: 'partial', remaining: diff, overpaid: 0 }
}

/** 按「人 + 方向」聚合后的结算单元。结清状态以整组为单位,不再逐笔判定。 */
type LoanGroup = {
  key: string
  direction: LoanDirection
  contactName: string | null
  loans: Loan[]
  /** 整人合计本金 */
  principal: number
  /** 整人合计已还(还款池) */
  repaid: number
  /** 整人净额 = principal - repaid(>0 待还,<0 超还) */
  diff: number
  /** 整人结清状态 */
  state: LoanBalanceView['state']
  /** 整人待还 */
  pending: number
  /** 整人超还 */
  overpaid: number
  /** 还款池按「先借先还」分配到各笔的已结算额(loanId → amount) */
  allocated: Record<string, number>
  /** 按分配后口径仍未结清的笔数 */
  unsettled: number
}

/**
 * 同一个人的借款合并结算:分组 key = 方向 + 联系人,因此「借出」与「借入」
 * 一定分属两组,不会互相抵消。没有联系人的散账各自独立(solo key),
 * 避免把互不相关的记录误聚合成一个人。
 *
 * 关键:必须传**全量**借款(不能先按状态筛选),否则还款池会被截断、结算错误。
 * 整人还款池对整人本金结算;超额部分自动冲抵同人其他借款,不再出现
 * 「A 笔超还 + B 笔未还」。池内按 happened_at 升序「先借先还」分配到各笔,
 * 只是为了让展开的逐笔行各有合理剩余,不改变整人结清结论。
 */
function groupLoans(rows: Loan[]): LoanGroup[] {
  const map = new Map<string, LoanGroup>()
  for (const l of rows) {
    const contactKey = l.contact_id || l.contact_name || `solo:${l.id}`
    const key = `${l.direction}|${contactKey}`
    let g = map.get(key)
    if (!g) {
      g = {
        key,
        direction: l.direction,
        contactName: l.contact_name || null,
        loans: [],
        principal: 0,
        repaid: 0,
        diff: 0,
        state: 'unpaid',
        pending: 0,
        overpaid: 0,
        allocated: {},
        unsettled: 0,
      }
      map.set(key, g)
    }
    g.loans.push(l)
    g.principal += l.amount
    g.repaid += l.total_repaid
  }

  for (const g of map.values()) {
    const view = balanceView(g.principal, g.repaid)
    g.state = view.state
    g.pending = view.remaining
    g.overpaid = view.overpaid
    g.diff = g.principal - g.repaid

    // 先发生的借款先被偿还;每笔最多分到自己的本金,整人超还留在组层面。
    let pool = g.repaid
    const ordered = [...g.loans].sort(
      (a, b) => new Date(a.happened_at).getTime() - new Date(b.happened_at).getTime()
    )
    for (const l of ordered) {
      const used = Math.min(Math.max(pool, 0), l.amount)
      g.allocated[l.id] = used
      pool -= used
    }
    g.unsettled = g.loans.filter(
      (l) => balanceView(l.amount, g.allocated[l.id] ?? 0).state !== 'cleared'
    ).length
  }
  // 保持 rows 的顺序(后端按 happened_at desc),分组按首笔出现顺序排列。
  return Array.from(map.values())
}

/** 组状态 → 状态筛选/徽章使用的 LoanStatus 口径。超还视为已结清。 */
function groupStatus(g: LoanGroup): LoanStatus {
  if (g.state === 'cleared' || g.state === 'overpaid') return 'settled'
  if (g.loans.some((l) => l.status === 'overdue')) return 'overdue'
  return g.state === 'partial' ? 'partial' : 'unpaid'
}

type FlowEntry =
  | { kind: 'loan'; at: string; loan: Loan }
  | { kind: 'repayment'; at: string; loan: Loan; repayment: LoanRepayment }

/**
 * 点开某人后展示「所有流水」:该人同方向的每笔借款本金 + 这些借款下的每笔还款,
 * 按时间倒序合并成一条时间轴。逐条流水保持原始金额、不加删除线;
 * 还款是挂在借款记录上的,所以并行拉取组内每笔借款的还款列表。
 * group 对象在每次列表刷新时都会重建,因此还款/追加/删除后会自动重新拉取。
 *
 * 单笔组与多笔组共用这一个时间轴(0054):单笔时组内只有一笔借款,时间轴同样
 * 渲染「本金 + 每笔还款」,不再另写一套内联列表,保证两种情况的展示完全一致。
 */
function GroupFlowTimeline({
  group,
  currency,
  onOpenLoan,
  onBeforeOpenTx,
  onDeleteRepayment,
  refreshKey = 0,
}: {
  group: LoanGroup
  currency: string
  onOpenLoan: (l: Loan) => void
  /** 跳原交易流水前先关闭来源借款详情弹窗,避免两个弹窗叠加。 */
  onBeforeOpenTx?: () => void
  /** 删除一笔还款(用还款所属借款 id;合并还款由后端按整组删除)。 */
  onDeleteRepayment?: (repayment: LoanRepayment, loanId: string) => void
  /** 删除还款等操作后手动触发重新拉取(列表本身不变时 group 引用不变)。 */
  refreshKey?: number
}) {
  const t = useT()
  const { token } = useAuth()
  const flowIconPrefs = useFlowIconPrefs()
  const flowIconColorPrefs = useFlowIconColorPrefs()
  const [repayments, setRepayments] = useState<LoanRepayment[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!token || group.loans.length === 0) {
      setRepayments([])
      return
    }
    let cancelled = false
    setLoading(true)
    void Promise.all(
      group.loans.map((l) => fetchRepayments(token, l.id).catch(() => [] as LoanRepayment[]))
    )
      .then((rows) => { if (!cancelled) setRepayments(rows.flat()) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [token, group, refreshKey])

  const loanById = useMemo(() => new Map(group.loans.map((l) => [l.id, l])), [group])
  const timeline: FlowEntry[] = useMemo(() => {
    const entries: FlowEntry[] = group.loans.map((l) => ({ kind: 'loan', at: l.happened_at, loan: l }))
    // 合并还款(0039):一次实际收付款可能同时结清多笔借款,时间轴上只应出现
    // **一条**流水(真实银行流水就是一笔)。按 group_id 去重,取持合并本金流水的
    // 代表行;代表行缺失时退化为组内第一行。金额用合并后的本金合计。
    const groupRows = new Map<string, LoanRepayment[]>()
    for (const r of repayments) {
      if (!r.group_id) continue
      const arr = groupRows.get(r.group_id) ?? []
      arr.push(r)
      groupRows.set(r.group_id, arr)
    }
    const consumedGroups = new Set<string>()
    for (const r of repayments) {
      const loan = loanById.get(r.loan_id)
      if (!loan) continue
      if (r.group_id) {
        if (consumedGroups.has(r.group_id)) continue
        const siblings = groupRows.get(r.group_id) ?? [r]
        const owner = siblings.find((x) => x.tx_sync_id) ?? siblings[0]
        consumedGroups.add(r.group_id)
        const ownerLoan = loanById.get(owner.loan_id) ?? loan
        entries.push({ kind: 'repayment', at: owner.repaid_at, loan: ownerLoan, repayment: owner })
        continue
      }
      entries.push({ kind: 'repayment', at: r.repaid_at, loan, repayment: r })
    }
    entries.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0))
    return entries
  }, [group, repayments, loanById])

  return (
    <div className="min-w-0 space-y-2 border-t border-border p-2">
      <div className="flex items-center justify-between px-1 text-xs">
        <span className="text-sm font-medium text-foreground">{t('loans.group.allFlows')}</span>
        <span className="text-muted-foreground">
          {timeline.length} {t('loans.group.flowCount')}
        </span>
      </div>
      {loading && timeline.length === 0 ? (
        <div className="py-4 text-center text-sm text-muted-foreground">{t('notice.loading')}</div>
      ) : (
        <div className="max-h-[40vh] divide-y divide-border overflow-y-auto overflow-x-hidden rounded-lg border border-border bg-background">
          {timeline.map((entry) => {
            const isLoan = entry.kind === 'loan'
            const loan = entry.loan
            const rp = isLoan ? null : entry.repayment
            const interestAmount = !isLoan && rp ? (rp.interest_amount ?? 0) : 0
            // 合并还款(0039):合并组的还款展示**合并后**的总额(后端 group_amount 已含利息);
            // 单笔还款:金额含利息(0041 利息并入同一笔流水,不再单独成流水)。
            const amount = isLoan
              ? loan.amount
              : (rp && rp.group_id && rp.group_amount != null
                  ? rp.group_amount
                  : (rp ? (rp.amount ?? 0) + interestAmount : 0))
            const mergedCount = !isLoan && rp ? (rp.group_size || 0) : 0
            const at = isLoan ? loan.happened_at : entry.repayment.repaid_at
            const note = isLoan ? loan.note : entry.repayment.note
            const account = isLoan ? loan.account_name : entry.repayment.account_name
            const label = isLoan
              ? (loan.direction === 'lend' ? t('loans.direction.lend') : t('loans.direction.borrow'))
              : (loan.direction === 'lend' ? t('loans.timeline.repayForLend') : t('loans.timeline.repayForBorrow'))
            const key = isLoan ? `loan-${loan.id}` : `repay-${entry.repayment.id}`
            const fileIds = isLoan ? (loan.voucher_file_ids || []) : (entry.repayment.attachment_file_ids || [])
            // 流水图标走全局自定义:本金=借出/借入,还款=收回借款/偿还借款。
            const fk = flowIconKeyForLoan(loan.direction, isLoan ? 'principal' : 'repayment')
            const fIcon = resolveFlowIcon(fk, flowIconPrefs)
            const fColor = resolveFlowIconColor(fk, flowIconColorPrefs)
            // 0046:该时间轴行关联的交易流水信息已在主行(标题/金额/时间/账户/备注)
            // 完整展示,不再用 LinkedTxList 重复渲染同一笔流水,避免「一条信息显示两遍」。
            // 与单笔明细分支一致:点主行跳原交易流水;合并还款由代表行持有合并本金流水。
            const syncId = isLoan ? loan.tx_sync_id : rp?.tx_sync_id
            return (
              <div key={key} className="min-w-0 px-3 py-2 transition-colors hover:bg-muted/40">
                <div className="flex w-full min-w-0 items-start justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      if (syncId) openTxDetailBySyncId(token, syncId, loan.ledger_id, onBeforeOpenTx)
                      else onOpenLoan(loan)
                    }}
                    className="flex min-w-0 flex-1 items-start gap-2 text-left"
                    title={syncId ? (t('deposits.detail.viewFlow') as string) : (t('loans.group.viewDetail') as string)}
                  >
                    <span
                      className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
                      style={{ backgroundColor: `${fColor}26`, color: fColor }}
                    >
                      <CategoryIcon icon={fIcon} iconType="material" size={16} color={fColor} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="break-words text-sm font-medium">
                        <span className={loan.direction === 'lend' ? 'text-emerald-600' : 'text-red-600'}>{label}</span>
                        {' · '}{formatBalanceCompact(amount, currency)}
                        {!isLoan && interestAmount > 0 ? (
                          <span className="ml-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-normal text-amber-600">
                            {t('loans.repayment.includesInterest')} {formatBalanceCompact(interestAmount, currency)}
                          </span>
                        ) : null}
                        {mergedCount > 1 ? (
                          <span className="ml-1 rounded bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-normal text-sky-600">
                            {t('loans.repayment.mergedCount', { count: String(mergedCount) })}
                          </span>
                        ) : null}
                      </div>
                      <div className="break-words text-xs text-muted-foreground">
                        {formatIsoDateTimeLocal(at)}
                        {account ? ` · ${account}` : ''}
                        {note ? ` · ${note}` : ''}
                        {/* 多笔借款时标明这笔还款挂在哪笔本金上(合并还款除外:
                            它同时结清多笔,已在「合并 N 笔」徽标里体现)。 */}
                        {!isLoan && group.loans.length > 1 && mergedCount <= 1
                          ? ` · ${t('loans.group.relatedLoan')} ${formatBalanceCompact(loan.amount, currency)} (${formatIsoDateTimeLocal(loan.happened_at)})`
                          : ''}
                      </div>
                    </div>
                  </button>
                  {/* 还款行可删除(单笔组原先只有内联列表有此入口,统一后两处一致)。 */}
                  {!isLoan && rp && onDeleteRepayment ? (
                    <button
                      type="button"
                      onClick={() => onDeleteRepayment(rp, rp.loan_id)}
                      className="mt-0.5 shrink-0 rounded px-1 text-xs text-destructive hover:underline"
                      title={t('confirm.delete') as string}
                    >
                      {t('confirm.delete')}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => onOpenLoan(loan)}
                    className="mt-0.5 shrink-0 rounded px-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
                    title={t('loans.group.viewDetail') as string}
                  >
                    {t('loans.group.viewDetail')}
                  </button>
                </div>
                <LoanAttachmentThumbs
                  fileIds={fileIds}
                  label={isLoan ? t('loans.field.vouchers') : t('loans.attachment.title')}
                />
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/**
 * 「人 + 方向」维度的结算卡片 —— 单笔组与多笔组**完全同一套渲染**。
 *
 * 之前单笔组走的是另一个 LoanRow 组件(方向+金额+时间的流水条样式),
 * 多笔组走组卡片(方向+姓名+笔数 + 合计本金/已还/待还汇总),两套样式放在一起
 * 视觉割裂。现在统一为组卡片口径:
 *  - 卡头:方向 + 姓名(+ 笔数 / 未结清笔数徽标,仅多笔时显示,单笔时是噪音);
 *  - 卡身:合计本金(结清划线) / 已还合计 / 待还合计 / 超还 / 已结清;
 *  - 右侧:整组结算状态徽章。
 * 单笔组下「合计」就是这一笔本身,口径不变。
 */
function LoanGroupCard({
  group,
  currency,
  expanded,
  onToggle,
  onOpen,
}: {
  group: LoanGroup
  currency: string
  expanded: boolean
  onToggle: (key: string) => void
  onOpen: (l: Loan) => void
}) {
  const t = useT()
  const status = groupStatus(group)
  // 单笔组只有一笔,「N 笔」「N 笔未结清」徽标没有信息量,不显示;
  // 卡片结构与多笔组保持完全一致。
  const multi = group.loans.length > 1
  // 点击卡片始终弹出详情(LoanDetailDialog)，不再展开成时间轴。
  // 传入该组第一笔借款作为默认打开的记录。
  const handleClick = () => {
    onOpen(group.loans[0])
  }
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      <button
        type="button"
        onClick={handleClick}
        className="flex w-full items-center justify-between gap-3 p-4 text-left transition-colors hover:bg-muted/40"
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`text-sm font-semibold ${group.direction === 'lend' ? 'text-emerald-600' : 'text-red-600'}`}>
              {group.direction === 'lend' ? t('loans.direction.lend') : t('loans.direction.borrow')}
            </span>
            <span className="font-semibold">{group.contactName || t('loans.field.noContact')}</span>
            {multi ? (
              <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                {group.loans.length} {t('loans.group.records')}
              </span>
            ) : null}
            {multi && group.unsettled > 0 ? (
              <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-600">
                {group.unsettled} {t('loans.group.unsettled')}
              </span>
            ) : null}
          </div>
          {/* 删除线只在「人」维度:合计本金按整人还款池结算后划掉 */}
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
            <span>
              {t('loans.group.principal')}{' '}
              <span className={group.state === 'unpaid' ? '' : 'line-through decoration-muted-foreground/70'}>
                {formatBalanceCompact(group.principal, currency)}
              </span>
            </span>
            <span>{t('loans.group.repaid')} {formatBalanceCompact(group.repaid, currency)}</span>
            {group.pending > 0 ? (
              <span className={group.direction === 'lend' ? 'text-emerald-600' : 'text-red-600'}>
                {t('loans.group.pending')} {formatBalanceCompact(group.pending, currency)}
              </span>
            ) : null}
            {group.overpaid > 0 ? (
              <span className="text-amber-600">
                {t('loans.detail.overpaid')} +{formatBalanceCompact(group.overpaid, currency)}
              </span>
            ) : null}
            {group.pending === 0 && group.overpaid === 0 ? <span>{t('loans.status.settled')}</span> : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${statusBadgeClass(status)}`}>
            {status === 'settled' ? t('loans.status.settled') : t(`loans.status.${status}`)}
          </span>
          {/* 移除展开/收起图标，改为始终进入详情 */}
        </div>
      </button>
      {/* 删除卡片层面的 GroupFlowTimeline，时间轴功能保留在详情页的「全部流水」中 */}
      {/* {expanded ? (
        <GroupFlowTimeline group={group} currency={currency} onOpenLoan={onOpen} />
      ) : null} */}
    </div>
  )
}

function LoanFormDialog({
  open,
  editing,
  contacts,
  currency,
  presetContactId,
  presetLoan,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  editing: Loan | null
  contacts: Contact[]
  currency: string
  presetContactId?: string | null
  /** 「另记一笔借款」:沿用原借款的方向/联系人/账户,只让用户填新金额。 */
  presetLoan?: Loan | null
  onOpenChange: (v: boolean) => void
  onSaved: () => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const [direction, setDirection] = useState<LoanDirection>('lend')
  const [contactId, setContactId] = useState<string>('none')
  const [amount, setAmount] = useState('')
  const [happenedAt, setHappenedAt] = useState('')
  const [dueAt, setDueAt] = useState('')
  const [interestType, setInterestType] = useState<LoanInterestType>('none')
  const [interestRate, setInterestRate] = useState('')
  const [interestAmount, setInterestAmount] = useState('')
  const [note, setNote] = useState('')
  const [accountId, setAccountId] = useState('none')
  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [accountsLoading, setAccountsLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const attachmentRef = useRef<LoanAttachmentEditorHandle>(null)

  useEffect(() => {
    if (!open) return
    if (editing) {
      setDirection(editing.direction)
      setContactId(editing.contact_id || 'none')
      setAmount(String(editing.amount))
      setHappenedAt(toLocalInput(editing.happened_at))
      setDueAt(toLocalInput(editing.due_at))
      setInterestType(editing.interest_type)
      setInterestRate(editing.interest_rate != null ? String(editing.interest_rate) : '')
      setInterestAmount(editing.interest_amount != null ? String(editing.interest_amount) : '')
      setNote(editing.note || '')
      setAccountId(editing.account_id || 'none')
    } else if (presetLoan) {
      // 追加借款时选「另记一笔借款」:预填同联系人/方向/账户,金额与时间由用户填。
      setDirection(presetLoan.direction)
      setContactId(presetLoan.contact_id || 'none')
      setAccountId(presetLoan.account_id || 'none')
      setAmount('')
      setHappenedAt(toLocalInput(new Date().toISOString()))
      setDueAt('')
      setInterestType('none')
      setInterestRate('')
      setInterestAmount('')
      setNote('')
    } else {
      setDirection('lend')
      setContactId(presetContactId && presetContactId !== 'null' ? presetContactId : 'none')
      setAmount('')
      setHappenedAt(toLocalInput(new Date().toISOString()))
      setDueAt('')
      setInterestType('none')
      setInterestRate('')
      setInterestAmount('')
      setNote('')
      setAccountId('none')
    }
  }, [open, editing, presetContactId, presetLoan])

  // 拉取当前账本的真实账户,供"扣款/到款账户"下拉选择。
  useEffect(() => {
    if (!open || !token || !activeLedgerId) return
    let cancelled = false
    setAccountsLoading(true)
    fetchReadAccounts(token, activeLedgerId)
      .then((rows) => { if (!cancelled) setAccounts(rows.filter((a) => !a.hidden)) })
      .catch(() => { if (!cancelled) setAccounts([]) })
      .finally(() => { if (!cancelled) setAccountsLoading(false) })
    return () => { cancelled = true }
  }, [open, token, activeLedgerId])

  const save = async () => {
    if (!token || !activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return
    }
    const amt = parseFloat(amount)
    if (!Number.isFinite(amt) || amt <= 0) {
      toast.error(t('loans.field.amount') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    const happened = fromLocalInput(happenedAt)
    if (!happened) {
      toast.error(t('loans.field.happenedAt') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    const selectedAccountId = accountId === 'none' ? null : accountId
    if (!selectedAccountId) {
      toast.error(t('loans.notice.accountRequired'), t('notice.error'))
      return
    }
    const selectedAccount = accounts.find((a) => a.id === selectedAccountId)
    setSaving(true)
    try {
      // 附件到保存时才上传;prepare() 返回 null 表示有文件上传失败,不提交借款。
      const voucherIds = await attachmentRef.current?.prepare()
      if (voucherIds == null) {
        toast.error(t('loans.attachment.uploadFailed'), t('notice.error'))
        return
      }
      const body = {
        ledger_id: activeLedgerId,
        contact_id: contactId === 'none' ? null : contactId,
        direction,
        amount: amt,
        currency_code: currency,
        happened_at: happened,
        due_at: fromLocalInput(dueAt),
        interest_type: interestType,
        interest_rate: interestType === 'annual' && interestRate ? parseFloat(interestRate) : null,
        interest_amount: interestType === 'fixed' && interestAmount ? parseFloat(interestAmount) : null,
        note: note || null,
        voucher_file_ids: voucherIds,
        account_id: selectedAccountId,
        account_name: selectedAccount?.name ?? null,
      }
      if (editing) await updateLoan(token, editing.id, body)
      else await createLoan(token, body)
      toast.success(editing ? t('loans.notice.updated') : t('loans.notice.created'), t('notice.success'))
      onSaved()
      onOpenChange(false)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{editing ? t('loans.button.update') : presetLoan ? t('loans.append.createTitle') : t('loans.button.create')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('loans.field.direction')}</Label>
              <Select value={direction} onValueChange={(v) => setDirection(v as LoanDirection)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {DIRECTIONS.map((d) => (
                    <SelectItem key={d} value={d}>{t(`loans.direction.${d}`)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('loans.field.contact')}</Label>
              <Select value={contactId} onValueChange={setContactId}>
                <SelectTrigger><SelectValue placeholder={t('loans.field.contact')} /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">{t('loans.field.noContact')}</SelectItem>
                  {contacts
                    .filter((c) => !c.is_archived && (c.is_active || c.id === contactId))
                    .map((c) => (
                      <SelectItem key={c.id} value={c.id}>
                        {c.name}{!c.is_active ? ` · ${t('contacts.state.disabled')}` : ''}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1">
            <Label>
              {direction === 'lend' ? t('loans.field.debitAccount') : t('loans.field.creditAccount')}
            </Label>
            <Select value={accountId} onValueChange={setAccountId}>
              <SelectTrigger>
                <SelectValue placeholder={accountsLoading ? t('notice.loading') : t('loans.field.noAccount')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">{t('loans.field.noAccount')}</SelectItem>
                {accounts.map((a) => (
                  <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>{t('loans.field.amount')}</Label>
            <Input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('loans.field.happenedAt')}</Label>
              <Input type="datetime-local" step="1" value={happenedAt} onChange={(e) => setHappenedAt(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>{t('loans.field.dueAt')}</Label>
              <Input type="datetime-local" step="1" value={dueAt} onChange={(e) => setDueAt(e.target.value)} />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1">
              <Label>{t('loans.field.interestType')}</Label>
              <Select value={interestType} onValueChange={(v) => setInterestType(v as LoanInterestType)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {INTEREST_TYPES.map((it) => (
                    <SelectItem key={it} value={it}>{t(`loans.interest.${it}`)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {interestType === 'annual' ? (
              <div className="space-y-1">
                <Label>{t('loans.field.interestRate')}</Label>
                <Input type="number" step="0.01" value={interestRate} onChange={(e) => setInterestRate(e.target.value)} />
              </div>
            ) : null}
            {interestType === 'fixed' ? (
              <div className="space-y-1">
                <Label>{t('loans.field.interestAmount')}</Label>
                <Input type="number" step="0.01" value={interestAmount} onChange={(e) => setInterestAmount(e.target.value)} />
              </div>
            ) : null}
          </div>
          <div className="space-y-1">
            <Label>{t('loans.field.note')}</Label>
            <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
          </div>
          <LoanAttachmentEditorField
            key={`loan-form:${open}:${editing?.id || presetLoan?.id || 'new'}`}
            ref={attachmentRef}
            fileIds={editing?.voucher_file_ids || []}
            disabled={saving}
            title={t('loans.field.vouchers')}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('confirm.cancel')}</Button>
          <Button onClick={() => void save()} disabled={saving}>{t('confirm.save')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function RepayDialog({
  open,
  loan,
  group,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  loan: Loan | null
  /** 本笔所属「人 + 方向」结算组:合并还款时用来算可一并结清的借款笔数。 */
  group: LoanGroup | null
  onOpenChange: (v: boolean) => void
  onSaved: () => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const [amount, setAmount] = useState('')
  const [repaidAt, setRepaidAt] = useState('')
  const [note, setNote] = useState('')
  const [accountId, setAccountId] = useState('none')
  // 合并还款(0039):一次实际收付款可同时结清同联系人同方向下的多笔借款,
  // 交易流水里只生成一条本金流水。
  const [mergeAll, setMergeAll] = useState(false)
  const [saving, setSaving] = useState(false)
  const attachmentRef = useRef<LoanAttachmentEditorHandle>(null)
  const { options: accountOptions, loading: accountsLoading } = useLoanAccountOptions(open, loan)

  // 只填还款总额:超出剩余本金的部分由后端自动拆成利息。这里做同样口径的预览。
  const totalAmount = parseFloat(amount)
  // 合并模式下,剩余本金 = 该联系人同方向全部未结清本金(整组)。
  const groupOutstanding = group
    ? Math.max(0, group.principal - group.repaid)
    : 0
  const remainingPrincipal = mergeAll
    ? groupOutstanding
    : Math.max(0, (loan?.amount ?? 0) - (loan?.total_repaid ?? 0))
  const previewPrincipal = Number.isFinite(totalAmount) && totalAmount > 0
    ? Math.min(totalAmount, remainingPrincipal) : 0
  const previewInterest = Number.isFinite(totalAmount) && totalAmount > 0
    ? Math.max(0, Math.round((totalAmount - remainingPrincipal) * 100) / 100) : 0
  // 合并模式下一并结清的借款笔数(未结清的那些)。
  const mergeCount = group
    ? group.loans.filter((l) => (group.allocated[l.id] ?? 0) < l.amount - 0.005).length
    : 0

  useEffect(() => {
    if (open) {
      setAmount('')
      setRepaidAt(toLocalInput(new Date().toISOString()))
      setNote('')
      setMergeAll(false)
      // 默认沿用借款账户,但用户可自由改选(收回的钱进别的账户很常见)。
      setAccountId(loan?.account_id || 'none')
    }
  }, [open, loan])

  const save = async () => {
    if (!token || !loan) return
    const amt = parseFloat(amount)
    if (!Number.isFinite(amt) || amt <= 0) {
      toast.error(t('loans.repayment.amount') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    const at = fromLocalInput(repaidAt)
    if (!at) {
      toast.error(t('loans.repayment.at') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    const selected = accountOptions.find((a) => a.id === accountId)
    if (!selected) {
      toast.error(t('loans.notice.accountRequired'), t('notice.error'))
      return
    }
    setSaving(true)
    try {
      const attachmentIds = await attachmentRef.current?.prepare()
      if (attachmentIds == null) {
        toast.error(t('loans.attachment.uploadFailed'), t('notice.error'))
        return
      }
      // 只传还款总额;后端按剩余本金自动拆分本金/利息,利息自动生成一条流水。
      if (mergeAll && loan.contact_id) {
        // 合并还款(0039):一次实际收付款结清同联系人同方向的多笔借款,
        // 交易流水里只生成一条本金流水。
        await createMergedRepayment(token, {
          ledger_id: loan.ledger_id,
          contact_id: loan.contact_id,
          direction: loan.direction,
          amount: amt,
          repaid_at: at,
          note: note || null,
          account_id: selected.id,
          account_name: selected.name,
          attachment_file_ids: attachmentIds,
        })
      } else {
        await createRepayment(token, loan.id, {
          amount: amt,
          repaid_at: at,
          note: note || null,
          account_id: selected.id,
          account_name: selected.name,
          attachment_file_ids: attachmentIds,
        })
      }
      toast.success(t('loans.notice.repaid'), t('notice.success'))
      onSaved()
      onOpenChange(false)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[calc(100vh-4rem)] overflow-y-auto space-y-4 p-4">
        <DialogHeader>
          <DialogTitle>{t('loans.detail.recordRepayment')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('loans.repayment.account')}</Label>
            <Select value={accountId} onValueChange={setAccountId} disabled={accountsLoading}>
              <SelectTrigger>
                <SelectValue placeholder={accountsLoading ? t('notice.loading') : t('loans.field.noAccount')} />
              </SelectTrigger>
              <SelectContent>
                {accountOptions.map((a) => (
                  <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{t('loans.repayment.accountHint')}</p>
          </div>
          <div className="space-y-1">
            <Label>{t('loans.repayment.amount')}</Label>
            <Input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
            <p className="text-xs text-muted-foreground">{t('loans.repayment.amountHint')}</p>
            {previewInterest > 0 ? (
              <div className="rounded-md bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
                {t('loans.repayment.splitHint')}{' '}
                <span className="font-medium text-foreground">{formatBalanceCompact(previewPrincipal, loan?.currency_code || 'CNY')}</span>
                {' + '}
                {t('loans.repayment.splitInterest')}{' '}
                <span className="font-medium text-foreground">{formatBalanceCompact(previewInterest, loan?.currency_code || 'CNY')}</span>
                <p className="mt-1">{t('loans.repayment.flowHint')}</p>
              </div>
            ) : null}
          </div>
          {/* 合并还款(0039):一次实际收付款同时结清同联系人同方向的多笔借款,
              交易流水里只生成一条本金流水(与真实银行流水一致)。 */}
          {loan?.contact_id && mergeCount > 1 ? (
            <label className="flex cursor-pointer items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4"
                checked={mergeAll}
                onChange={(e) => setMergeAll(e.target.checked)}
              />
              <span className="text-xs text-muted-foreground">
                {t('loans.repayment.mergeAll')}
                <span className="ml-1 font-medium text-foreground">
                  {t('loans.repayment.mergeAllCount', { count: String(mergeCount) })}
                </span>
              </span>
            </label>
          ) : null}
          <div className="space-y-1">
            <Label>{t('loans.repayment.at')}</Label>
            <Input type="datetime-local" step="1" value={repaidAt} onChange={(e) => setRepaidAt(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>{t('loans.repayment.note')}</Label>
            <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className="max-h-[3rem] resize-none" />
          </div>
          <LoanAttachmentEditorField
            key={`repay:${open}:${loan?.id || 'new'}`}
            ref={attachmentRef}
            fileIds={[]}
            disabled={saving}
            title={t('loans.attachment.repayment')}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('confirm.cancel')}</Button>
          <Button onClick={() => void save()} disabled={saving}>{t('confirm.save')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 追加借款:在「本笔」借款上追加本金(不新建记录),后端会同步更新本金流水
 * 并按新本金重算 status —— 已结清的借款因此能重新产生待还余额。
 * 追加时间用 datetime-local + step="1"(秒级),与新建借款/记录还款保持一致;
 * 后端 LoanUpdate.happened_at 会同时更新借款发生时间与本金流水时间,所以这里
 * 把所选时间明确传下去,并在弹窗里把「原时间 → 新时间」的变化展示出来。
 * 若用户想另起一笔业务,提供「另记一笔借款」入口(预填联系人/方向/账户)。
 */
function AppendLoanDialog({
  open,
  loan,
  currency,
  onOpenChange,
  onSaved,
  onCreateInstead,
}: {
  open: boolean
  loan: Loan | null
  currency: string
  onOpenChange: (v: boolean) => void
  onSaved: () => void
  onCreateInstead: (l: Loan) => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [accountId, setAccountId] = useState('none')
  const [appendAt, setAppendAt] = useState('')
  const [saving, setSaving] = useState(false)
  const attachmentRef = useRef<LoanAttachmentEditorHandle>(null)
  const { options: accountOptions, loading: accountsLoading } = useLoanAccountOptions(open, loan)

  useEffect(() => {
    if (open) {
      setAmount('')
      setNote('')
      setAccountId(loan?.account_id || 'none')
      // 打开时取当前时间(含秒),用户可手动改成任意历史时间,常见「现在追加」无需操作。
      setAppendAt(toLocalInput(new Date().toISOString()))
    }
  }, [open, loan])

  const appendAmount = parseFloat(amount)
  const valid = Number.isFinite(appendAmount) && appendAmount > 0
  const newTotal = loan ? loan.amount + (valid ? appendAmount : 0) : 0
  const appendAtIso = fromLocalInput(appendAt)

  const save = async () => {
    if (!token || !loan) return
    if (!valid) {
      toast.error(t('loans.append.amount') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    if (!appendAtIso) {
      toast.error(t('loans.append.at') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    const selected = accountOptions.find((a) => a.id === accountId)
    if (!selected) {
      toast.error(t('loans.notice.accountRequired'), t('notice.error'))
      return
    }
    const mergedNote = [loan.note?.trim(), note.trim()].filter(Boolean).join(' · ') || null
    setSaving(true)
    try {
      // 追加不新建借款记录:新上传的凭证合并进本笔借款凭证(编辑器已带出原有凭证)。
      const voucherIds = await attachmentRef.current?.prepare()
      if (voucherIds == null) {
        toast.error(t('loans.attachment.uploadFailed'), t('notice.error'))
        return
      }
      await updateLoan(token, loan.id, {
        amount: newTotal,
        happened_at: appendAtIso,
        account_id: selected.id,
        account_name: selected.name,
        note: mergedNote,
        voucher_file_ids: voucherIds,
      })
      toast.success(t('loans.notice.appended'), t('notice.success'))
      onSaved()
      onOpenChange(false)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('loans.append.title')}</DialogTitle>
          <DialogDescription>{t('loans.append.hint')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('loans.append.amount')}</Label>
            <Input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>{t('loans.append.at')}</Label>
            <div className="flex items-center gap-2">
              <Input
                type="datetime-local"
                step="1"
                value={appendAt}
                onChange={(e) => setAppendAt(e.target.value)}
                className="flex-1"
              />
              <Button size="sm" variant="outline" onClick={() => setAppendAt(toLocalInput(new Date().toISOString()))}>
                {t('loans.append.now')}
              </Button>
            </div>
            <div className="text-xs text-muted-foreground">{t('loans.append.atHint')}</div>
          </div>
          <div className="space-y-1">
            <Label>{t('loans.append.account')}</Label>
            <Select value={accountId} onValueChange={setAccountId} disabled={accountsLoading}>
              <SelectTrigger>
                <SelectValue placeholder={accountsLoading ? t('notice.loading') : t('loans.field.noAccount')} />
              </SelectTrigger>
              <SelectContent>
                {accountOptions.map((a) => (
                  <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>{t('loans.append.note')}</Label>
            <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
          </div>
          <LoanAttachmentEditorField
            key={`append:${open}:${loan?.id || 'new'}`}
            ref={attachmentRef}
            fileIds={loan?.voucher_file_ids || []}
            disabled={saving}
            title={t('loans.field.vouchers')}
          />
          {loan ? (
            <div className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm">
              <div>
                {t('loans.append.newTotal')} {formatBalanceCompact(loan.amount, currency)}
                {' → '}
                <span className="font-semibold text-primary">{formatBalanceCompact(newTotal, currency)}</span>
              </div>
              {/* 追加时间会更新借款发生时间/本金流水时间,变化时明确提示避免误以为原日期被保留。
                  比较本地墙钟字符串而不是 Date 绝对值:后端返回的是无时区的 naive 时间。 */}
              {appendAtIso && appendAt.slice(0, 19) !== toLocalInput(loan.happened_at).slice(0, 19) ? (
                <div className="mt-1 text-xs text-muted-foreground">
                  {t('loans.field.happenedAt')} {formatIsoDateTimeLocal(loan.happened_at)}
                  {' → '}{formatIsoDateTimeLocal(appendAtIso)}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        <DialogFooter>
          {loan ? (
            <Button variant="ghost" onClick={() => { onOpenChange(false); onCreateInstead(loan) }}>
              {t('loans.append.createInstead')}
            </Button>
          ) : null}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('confirm.cancel')}</Button>
          <Button onClick={() => void save()} disabled={saving}>{t('loans.append.confirm')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function LoanDetailDialog({
  open,
  loan,
  group,
  currency,
  onOpenChange,
  onEdit,
  onDeleted,
  onChanged,
  onCreateInstead,
  onViewPersonFlows,
  onOpenLoan,
}: {
  open: boolean
  loan: Loan | null
  /** 本笔所属「人 + 方向」结算组:按人结算的口径来源。 */
  group: LoanGroup | null
  currency: string
  onOpenChange: (v: boolean) => void
  onEdit: (l: Loan) => void
  onDeleted: () => void
  /** 还款/追加/删除还款后刷新详情里的借款金额与状态(loan prop 是快照)。 */
  onChanged: () => void
  /** 追加借款对话框里的「另记一笔借款」。 */
  onCreateInstead: (l: Loan) => void
  /** 回到列表并展开此人,查看 TA 同方向的所有借还流水。 */
  onViewPersonFlows: () => void
  /** 在详情中切换查看同组的另一笔借款 */
  onOpenLoan?: (l: Loan) => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const flowIconPrefs = useFlowIconPrefs()
  const flowIconColorPrefs = useFlowIconColorPrefs()
  const [repayments, setRepayments] = useState<LoanRepayment[]>([])
  const [loading, setLoading] = useState(false)
  const [repayOpen, setRepayOpen] = useState(false)
  const [appendOpen, setAppendOpen] = useState(false)
  const [pendingDeleteRepay, setPendingDeleteRepay] = useState<{ repayment: LoanRepayment; loanId: string } | null>(null)
  // 时间轴(单笔/多笔共用)的刷新键:删除还款后列表本身不变,group 引用也不变,
  // 需要手动让 GroupFlowTimeline 重新拉取还款列表。
  const [flowRefreshKey, setFlowRefreshKey] = useState(0)

  const loadRepayments = useCallback(async () => {
    if (!token || !loan) return
    setLoading(true)
    try {
      const list = await fetchRepayments(token, loan.id)
      setRepayments(list)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setLoading(false)
    }
  }, [token, loan, toast, t])

  useEffect(() => {
    if (open && loan) void loadRepayments()
  }, [open, loan, loadRepayments])

  const onDeleteLoan = async () => {
    if (!token || !loan) return
    try {
      await deleteLoan(token, loan.id)
      toast.success(t('loans.notice.deleted'), t('notice.success'))
      onDeleted()
      onOpenChange(false)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    }
  }

  const onDeleteRepay = async () => {
    if (!token || !pendingDeleteRepay) return
    try {
      await deleteRepayment(token, pendingDeleteRepay.loanId, pendingDeleteRepay.repayment.id)
      toast.success(t('loans.notice.repaid'), t('notice.success'))
      await loadRepayments()
      setFlowRefreshKey((k) => k + 1)
      onChanged()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setPendingDeleteRepay(null)
    }
  }

  // 按人结算:本笔进度用「整人还款池分配到本笔」的金额,而非本笔自己登记的还款。
  const allocated = group && loan ? (group.allocated[loan.id] ?? 0) : (loan?.total_repaid ?? 0)
  const view = loan ? balanceView(loan.amount, allocated) : null
  const remaining = view?.remaining ?? 0
  // 超还属于整个人:单笔组时下放到本笔展示;多笔组超还显示在组结算块里。
  const isOverpaid = Boolean(group && group.state === 'overpaid' && group.loans.length === 1)
  const overpaid = isOverpaid ? (group?.overpaid ?? 0) : (view?.overpaid ?? 0)
  const detailStatus: LoanStatus | null = group ? groupStatus(group) : (loan?.status ?? null)
  const progress = loan && loan.amount > 0 ? Math.min(100, Math.max(0, (allocated / loan.amount) * 100)) : 0
  const progressOver = isOverpaid && loan && loan.amount > 0 ? Math.min(100, ((group?.overpaid ?? 0) / loan.amount) * 100) : 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex shrink-0 flex-row items-start justify-between gap-3 border-b border-border/60 bg-gradient-to-br from-muted/40 to-transparent px-6 py-4">
          <DialogTitle className="flex items-center gap-3 pr-8">
            {loan ? (
              <>
                <span
                  className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-xl"
                  style={{
                    backgroundColor: `${resolveFlowIconColor(
                      flowIconKeyForLoan(loan.direction, 'principal'),
                      flowIconColorPrefs,
                    )}1A`,
                  }}
                >
                  <CategoryIcon
                    icon={resolveFlowIcon(flowIconKeyForLoan(loan.direction, 'principal'), flowIconPrefs)}
                    iconType="material"
                    size={24}
                    color={resolveFlowIconColor(
                      flowIconKeyForLoan(loan.direction, 'principal'),
                      flowIconColorPrefs,
                    )}
                  />
                </span>
                <div className="flex min-w-0 flex-col">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span className={loan.direction === 'lend' ? 'text-emerald-600' : 'text-red-600'}>
                      {loan.direction === 'lend' ? t('loans.direction.lend') : t('loans.direction.borrow')}
                    </span>
                    {/* 详情是「单条记录」维度:金额原样展示,不加删除线(删除线只在人维度)。 */}
                    <span className="text-lg font-semibold">{formatBalanceCompact(loan.amount, currency)}</span>
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] font-normal">
                    {view && view.state !== 'unpaid' ? (
                      <span className="text-muted-foreground">
                        {isOverpaid
                          ? `${t('loans.detail.overpaid')} +${formatBalanceCompact(overpaid, currency)}`
                          : view.state === 'cleared'
                            ? t('loans.status.settled')
                            : `${t('loans.detail.remaining')} ${formatBalanceCompact(remaining, currency)}`}
                      </span>
                    ) : null}
                    {isOverpaid ? (
                      <span className="rounded bg-amber-500/15 px-2 py-0.5 text-amber-600">{t('loans.detail.overpaid')}</span>
                    ) : detailStatus === 'settled' ? (
                      <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-emerald-600">{t('loans.settled.stamp')}</span>
                    ) : detailStatus === 'overdue' ? (
                      <span className="rounded bg-red-500/15 px-2 py-0.5 text-red-600">{t('loans.overdue.stamp')}</span>
                    ) : null}
                  </span>
                </div>
              </>
            ) : null}
          </DialogTitle>
        </DialogHeader>
        {loan ? (
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-6 py-4">
            <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
              <Info label={t('loans.field.contact')} value={loan.contact_name || t('loans.field.noContact')} />
              <Info
                label={loan.direction === 'lend' ? t('loans.field.debitAccount') : t('loans.field.creditAccount')}
                value={loan.account_name || t('loans.field.noAccount')}
              />
              <Info label={t('loans.field.happenedAt')} value={formatIsoDateTimeLocal(loan.happened_at)} />
              <Info label={t('loans.field.dueAt')} value={loan.due_at ? formatIsoDateTimeLocal(loan.due_at) : '-'} />
              <Info label={t('loans.interest.none')} value={t(`loans.interest.${loan.interest_type}`)} />
              {loan.interest_type === 'annual' && loan.interest_rate != null ? (
                <Info label={t('loans.field.interestRate')} value={`${loan.interest_rate}%`} />
              ) : null}
              {loan.interest_type === 'fixed' && loan.interest_amount != null ? (
                <Info label={t('loans.field.interestAmount')} value={formatBalanceCompact(loan.interest_amount, currency)} />
              ) : null}
            </div>
            {loan.note ? <div className="break-words whitespace-pre-wrap rounded-lg border border-border p-3 text-sm text-muted-foreground">{loan.note}</div> : null}
            <LoanAttachmentThumbs fileIds={loan.voucher_file_ids || []} label={t('loans.field.vouchers')} />

            <div>
              <div className="mb-1 flex items-center justify-between text-sm">
                <span className="font-medium">{t('loans.detail.progress')}</span>
                <span className="text-muted-foreground">
                  {formatBalanceCompact(allocated, currency)} / {formatBalanceCompact(loan.amount, currency)} · {isOverpaid ? t('loans.detail.overpaid') : t('loans.detail.remaining')} {formatBalanceCompact(isOverpaid ? overpaid : remaining, currency)}
                </span>
              </div>
              <div className="relative h-2 w-full overflow-hidden rounded-full bg-muted">
                <div className={`h-full ${progressOver > 0 ? 'bg-emerald-500' : 'bg-primary'}`} style={{ width: `${progress}%` }} />
                {progressOver > 0 ? (
                  <div
                    className="absolute inset-y-0 right-0 bg-amber-500/80"
                    style={{ width: `${Math.max(8, progressOver)}%`, maxWidth: '40%' }}
                    title={t('loans.detail.overpaid') as string}
                  />
                ) : null}
              </div>
            </div>

            {/* 按人结算说明:同一人同方向的还款合并成一个池,结清以整人为单位,
                本笔进度里的「已还」是该池分配到本笔的金额。
                单笔组与多笔组展示同一块(0054),只有提示文案按笔数不同:
                多笔才谈得上「合并/冲抵其他借款」。 */}
            {group ? (
              <div className="min-w-0 rounded-lg border border-dashed border-border bg-muted/30 px-3 py-2">
                <div className="break-words text-sm font-medium">
                  {t('loans.detail.personSettlement')} · {group.contactName || t('loans.field.noContact')}
                </div>
                <div className="mt-0.5 break-words text-xs text-muted-foreground">
                  {group.loans.length > 1 ? t('loans.detail.personHint') : t('loans.detail.personHintSingle')}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
                  <span>{t('loans.group.principal')} {formatBalanceCompact(group.principal, currency)}</span>
                  <span>{t('loans.group.repaid')} {formatBalanceCompact(group.repaid, currency)}</span>
                  {group.pending > 0 ? (
                    <span className={group.direction === 'lend' ? 'text-emerald-600' : 'text-red-600'}>
                      {t('loans.group.pending')} {formatBalanceCompact(group.pending, currency)}
                    </span>
                  ) : null}
                  {group.overpaid > 0 ? (
                    <span className="text-amber-600">
                      {t('loans.detail.overpaid')} +{formatBalanceCompact(group.overpaid, currency)}
                    </span>
                  ) : null}
                </div>
                {/* 「查看此人全部流水」是回到列表的快捷入口,单笔组下方已是全部流水,
                    再放一个同名按钮会让人以为点了没反应 → 只在多笔组显示。 */}
                {group.loans.length > 1 ? (
                  <Button size="sm" variant="ghost" className="mt-1 h-7 px-2 text-xs" onClick={onViewPersonFlows}>
                    {t('loans.detail.viewPersonFlows')}
                  </Button>
                ) : null}
              </div>
            ) : null}

            {/* 全部流水:借款本金 + 每一笔还款,按原始金额展示,不加删除线。
                单笔组与多笔组**共用同一个时间轴**(GroupFlowTimeline),展示完全一致;
                只有拿不到结算组时(例如从关联记录直接跳转、列表尚未加载完成)才退回
                按本笔展开的内联列表。 */}
            <div className="min-w-0">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">
                  {t('loans.detail.allFlows')}
                  {group ? null : (
                    <span className="ml-1 text-xs font-normal text-muted-foreground">
                      {1 + repayments.length} {t('loans.group.flowCount')}
                    </span>
                  )}
                </span>
                {/* 已结清也保留业务入口:可继续记录还款(含超还),也可追加借款重新产生欠款。 */}
                <div className="flex shrink-0 gap-2">
                  <Button size="sm" variant="outline" onClick={() => setAppendOpen(true)}>
                    <Plus className="mr-1 h-4 w-4" />{t('loans.detail.appendLoan')}
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setRepayOpen(true)}>
                    <Receipt className="mr-1 h-4 w-4" />{t('loans.detail.addRepayment')}
                  </Button>
                </div>
              </div>
              {group ? (
                <GroupFlowTimeline
                  group={group}
                  currency={currency}
                  onOpenLoan={(l) => onOpenLoan?.(l)}
                  onBeforeOpenTx={() => onOpenChange(false)}
                  onDeleteRepayment={(repayment, loanId) => setPendingDeleteRepay({ repayment, loanId })}
                  refreshKey={flowRefreshKey}
                />
              ) : loading ? (
                <div className="py-4 text-center text-sm text-muted-foreground">{t('notice.loading')}</div>
              ) : (
                <div className="divide-y divide-border overflow-hidden rounded-lg border border-border">
                  <div
                    className={`flex items-center justify-between px-3 py-2 ${
                      loan.tx_sync_id ? 'cursor-pointer transition-colors hover:bg-primary/5' : ''
                    }`}
                    onClick={() => openTxDetailBySyncId(token, loan.tx_sync_id, loan.ledger_id, () => onOpenChange(false))}
                    title={loan.tx_sync_id ? (t('deposits.detail.viewFlow') as string) : undefined}
                  >
                    <div className="flex min-w-0 flex-1 items-center gap-2">
                      <span
                        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
                        style={{
                          backgroundColor: `${resolveFlowIconColor(
                            flowIconKeyForLoan(loan.direction, 'principal'),
                            flowIconColorPrefs,
                          )}26`,
                          color: resolveFlowIconColor(
                            flowIconKeyForLoan(loan.direction, 'principal'),
                            flowIconColorPrefs,
                          ),
                        }}
                      >
                        <CategoryIcon
                          icon={resolveFlowIcon(
                            flowIconKeyForLoan(loan.direction, 'principal'),
                            flowIconPrefs,
                          )}
                          iconType="material"
                          size={16}
                          color={resolveFlowIconColor(
                            flowIconKeyForLoan(loan.direction, 'principal'),
                            flowIconColorPrefs,
                          )}
                        />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="break-words text-sm font-medium">
                          <span className={loan.direction === 'lend' ? 'text-emerald-600' : 'text-red-600'}>
                            {t('loans.detail.principalFlow')}
                          </span>
                          {' · '}{formatBalanceCompact(loan.amount, currency)}
                        </div>
                        <div className="break-words text-xs text-muted-foreground">
                          {formatIsoDateTimeLocal(loan.happened_at)}
                          {loan.account_name ? ` · ${loan.account_name}` : ''}
                          {loan.note ? ` · ${loan.note}` : ''}
                        </div>
                      </div>
                    </div>
                  </div>
                  {/* 0046:借出关联的交易流水已由上方「借出 · 金额」展示,避免重复。 */}
                  {repayments.length === 0 ? (
                    <div className="px-3 py-2 text-sm text-muted-foreground">{t('loans.repayment.empty')}</div>
                  ) : repayments
                      // 合并还款(0039):同一「一次实际收付款」组只展示一次。
                      // 代表行 = 组内持合并本金流水的那行;若当前列表拿不到它
                      // (代表行属于别的借款),退化为组内第一行,金额仍显示组合计。
                      .filter((r, _i, arr) => {
                        if (!r.group_id) return true
                        const sameGroup = arr.filter((x) => x.group_id === r.group_id)
                        const owner = sameGroup.find((x) => x.tx_sync_id)
                        const rep = owner || sameGroup[0]
                        return rep.id === r.id
                      })
                      .map((r) => {
                    const rk = flowIconKeyForLoan(loan.direction, 'repayment')
                    const rIcon = resolveFlowIcon(rk, flowIconPrefs)
                    const rColor = resolveFlowIconColor(rk, flowIconColorPrefs)
                    // 合并组显示合并后的总额(后端 group_amount 已含利息);
                    // 普通单笔显示自身金额 + 利息(0041 利息并入同一笔流水)。
                    const rInterest = r.interest_amount || 0
                    const shownAmount =
                      r.group_id && r.group_amount != null ? r.group_amount : r.amount + rInterest
                    const mergedCount = r.group_size || 0
                    return (
                    <div key={r.id} className="px-3 py-2">
                      <div className="flex items-start justify-between gap-2">
                        <div
                          className={`flex min-w-0 flex-1 items-center gap-2 rounded-md ${
                            r.tx_sync_id ? 'cursor-pointer transition-colors hover:bg-primary/5' : ''
                          }`}
                          onClick={() => openTxDetailBySyncId(token, r.tx_sync_id, loan.ledger_id, () => onOpenChange(false))}
                          title={r.tx_sync_id ? (t('deposits.detail.viewFlow') as string) : undefined}
                        >
                          <span
                            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
                            style={{ backgroundColor: `${rColor}26`, color: rColor }}
                          >
                            <CategoryIcon icon={rIcon} iconType="material" size={16} color={rColor} />
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="break-words text-sm font-medium">
                              <span className={loan.direction === 'lend' ? 'text-emerald-600' : 'text-red-600'}>
                                {loan.direction === 'lend' ? t('loans.timeline.repayForLend') : t('loans.timeline.repayForBorrow')}
                              </span>
                              {' · '}{formatBalanceCompact(shownAmount, currency)}
                              {r.interest_amount && r.interest_amount > 0 ? (
                                <span className="ml-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-normal text-amber-600">
                                  {t('loans.repayment.includesInterest')} {formatBalanceCompact(r.interest_amount, currency)}
                                </span>
                              ) : null}
                              {mergedCount > 1 ? (
                                <span className="ml-1 rounded bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-normal text-sky-600">
                                  {t('loans.repayment.mergedCount', { count: String(mergedCount) })}
                                </span>
                              ) : null}
                            </div>
                            <div className="break-words text-xs text-muted-foreground">
                              {formatIsoDateTimeLocal(r.repaid_at)}
                              {r.account_name ? ` · ${r.account_name}` : ''}
                              {r.note ? ` · ${r.note}` : ''}
                            </div>
                          </div>
                        </div>
                        <button type="button" className="mt-0.5 shrink-0 text-xs text-destructive hover:underline" onClick={() => setPendingDeleteRepay({ repayment: r, loanId: loan.id })}>{t('confirm.delete')}</button>
                      </div>
                      {/* 0046:还款关联的交易流水信息已在上方主行(标题/金额/时间/账户/备注)
                          完整展示,且整行可点击跳到原流水单。此处不再用 LinkedTxList
                          重复渲染同一笔流水,避免「一条信息显示两遍」及两套金额格式。 */}
                      <LoanAttachmentThumbs fileIds={r.attachment_file_ids || []} label={t('loans.attachment.repayment')} />
                    </div>
                    )
                  })}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => onEdit(loan)}>{t('confirm.edit')}</Button>
              <Button variant="destructive" onClick={() => void onDeleteLoan()}>{t('confirm.delete')}</Button>
            </div>
          </div>
        ) : null}
      </DialogContent>
      <RepayDialog
        open={repayOpen}
        loan={loan}
        group={group}
        onOpenChange={setRepayOpen}
        onSaved={() => { void loadRepayments(); setFlowRefreshKey((k) => k + 1); onChanged() }}
      />
      <AppendLoanDialog
        open={appendOpen}
        loan={loan}
        currency={currency}
        onOpenChange={setAppendOpen}
        onSaved={onChanged}
        onCreateInstead={(l) => { setAppendOpen(false); onCreateInstead(l) }}
      />
      <Dialog open={!!pendingDeleteRepay} onOpenChange={(v) => { if (!v) setPendingDeleteRepay(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('confirm.delete')}</DialogTitle>
            <DialogDescription>{t('loans.confirm.deleteRepayment')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPendingDeleteRepay(null)}>{t('confirm.cancel')}</Button>
            <Button variant="destructive" onClick={() => void onDeleteRepay()}>{t('confirm.delete')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Dialog>
  )
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="break-words font-medium">{value}</div>
    </div>
  )
}

export function LoansPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { currency, activeLedgerId } = useLedgers()
  const [searchParams] = useSearchParams()

  const [loans, setLoans] = useState<Loan[]>([])
  const [contacts, setContacts] = useState<Contact[]>([])
  const [directionFilter, setDirectionFilter] = useState<'all' | LoanDirection>('all')
  const [statusFilter, setStatusFilter] = useState<'all' | LoanStatus>('all')
  const [loading, setLoading] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Loan | null>(null)
  const [detail, setDetail] = useState<Loan | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [presetLoan, setPresetLoan] = useState<Loan | null>(null)
  const presetContactId = searchParams.get('contact')
  // 关联记录跳转(0043):?loan_id=xxx → 自动打开该笔借款详情。
  const presetLoanId = searchParams.get('loan_id')

  const load = useCallback(async () => {
    if (!token || !activeLedgerId) {
      setLoans([])
      return
    }
    setLoading(true)
    try {
      const [loanRows, contactRows] = await Promise.all([
        fetchLoans(token, { ledger_id: activeLedgerId }),
        fetchContacts(token),
      ])
      setLoans(loanRows)
      setContacts(contactRows)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setLoading(false)
    }
  }, [token, activeLedgerId, toast, t])

  useEffect(() => { void load() }, [load])
  useSyncRefresh(() => { void load() })

  // 结算必须基于**全量**借款:同一人同方向的还款是一个池,先按状态筛选
  // 会把池截断(例如只留下未结清那笔),整人结算就会算错。
  const allGroups = useMemo(() => groupLoans(loans), [loans])

  // 筛选作用于「人 + 方向」组:结清状态按组判定,不再逐笔判定。
  // 选「已结清」时展示的是已结清的人(整组所有笔),符合按人结算的口径。
  const visibleGroups = useMemo(
    () => allGroups.filter((g) => {
      if (directionFilter !== 'all' && g.direction !== directionFilter) return false
      if (statusFilter !== 'all' && groupStatus(g) !== statusFilter) return false
      return true
    }),
    [allGroups, directionFilter, statusFilter]
  )

  const detailGroup = useMemo(
    () => (detail ? allGroups.find((g) => g.loans.some((l) => l.id === detail.id)) ?? null : null),
    [allGroups, detail]
  )

  const openCreate = () => { setEditing(null); setPresetLoan(null); setFormOpen(true) }
  const openDetail = (l: Loan) => { setDetail(l); setDetailOpen(true) }

  // 关联记录跳转(0043):?loan_id=xxx → 拉起该笔借款详情。
  const openedPresetRef = useRef<string | null>(null)
  useEffect(() => {
    if (!token || !presetLoanId) return
    if (openedPresetRef.current === presetLoanId) return
    openedPresetRef.current = presetLoanId
    void fetchLoan(token, presetLoanId)
      .then((l) => {
        setDetail(l)
        setDetailOpen(true)
      })
      .catch(() => undefined)
  }, [token, presetLoanId])

  // 流水详情「关联记录」跳回借款:同页面时 URL 参数 effect 的 ref guard
  // 会拦截,靠此事件直接打开(跨页面时两条通道都会触发,setDetail 幂等)。
  useEffect(() => {
    if (!token) return
    return onOpenDetailLoan((loanId) => {
      void fetchLoan(token, loanId)
        .then((l) => {
          setDetail(l)
          setDetailOpen(true)
        })
        .catch(() => undefined)
    })
  }, [token])

  // 还款/追加后借款金额、状态、total_repaid 都变了,详情弹窗拿的是打开时的
  // 快照,必须重新拉一次,否则进度条与四态展示会停留在旧值。
  const refreshDetail = useCallback(async () => {
    const id = detail?.id
    if (!token || !id) return
    try {
      setDetail(await fetchLoan(token, id))
    } catch {
      // 取详情失败就让列表刷新兜底,不打断用户操作。
    }
    void load()
  }, [token, load, detail?.id])

  // 顶部总览也按人净额:某笔的超额还款自动冲抵同一人的其他欠款,
  // 不再出现「一边超还、一边还欠钱」的重复计数。
  const receivable = allGroups.filter((g) => g.direction === 'lend').reduce((s, g) => s + g.pending, 0)
  const payable = allGroups.filter((g) => g.direction === 'borrow').reduce((s, g) => s + g.pending, 0)
  const overpaidLend = allGroups.filter((g) => g.direction === 'lend').reduce((s, g) => s + g.overpaid, 0)
  const overpaidBorrow = allGroups.filter((g) => g.direction === 'borrow').reduce((s, g) => s + g.overpaid, 0)

  return (
    <div className="space-y-4">
      <SectionPageHeader
        section="loans"
        title={t('loans.title')}
        subtitle={t('loans.subtitle')}
        actions={
          <Button onClick={openCreate}>
            <Plus className="mr-1 h-4 w-4" />{t('loans.button.create')}
          </Button>
        }
      />

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="text-sm text-muted-foreground">{t('loans.summary.received')}</div>
          <div className="mt-1 text-lg font-semibold text-emerald-600">{formatBalanceCompact(receivable, currency)}</div>
          {overpaidLend > 0 ? (
            <div className="mt-1 text-xs text-amber-600">
              {t('loans.summary.overpaid')} +{formatBalanceCompact(overpaidLend, currency)}
            </div>
          ) : null}
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="text-sm text-muted-foreground">{t('loans.summary.paid')}</div>
          <div className="mt-1 text-lg font-semibold text-red-600">{formatBalanceCompact(payable, currency)}</div>
          {overpaidBorrow > 0 ? (
            <div className="mt-1 text-xs text-amber-600">
              {t('loans.summary.overpaid')} +{formatBalanceCompact(overpaidBorrow, currency)}
            </div>
          ) : null}
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Chip active={directionFilter === 'all'} onClick={() => setDirectionFilter('all')}>{t('loans.filter.all')}</Chip>
        <Chip active={directionFilter === 'lend'} onClick={() => setDirectionFilter('lend')}>{t('loans.filter.lend')}</Chip>
        <Chip active={directionFilter === 'borrow'} onClick={() => setDirectionFilter('borrow')}>{t('loans.filter.borrow')}</Chip>
        <span className="mx-1 w-px self-stretch bg-border" />
        <Chip active={statusFilter === 'all'} onClick={() => setStatusFilter('all')}>{t('loans.filter.all')}</Chip>
        {STATUSES.map((s) => (
          <Chip key={s} active={statusFilter === s} onClick={() => setStatusFilter(s)}>{t(`loans.status.${s}`)}</Chip>
        ))}
      </div>

      {!activeLedgerId ? (
        <div className="rounded-xl border border-dashed border-border py-12 text-center text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</div>
      ) : loading && loans.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground">{t('notice.loading')}</div>
      ) : visibleGroups.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border py-12 text-center">
          <div className="font-medium">{t('loans.empty.title')}</div>
          <div className="mt-1 text-sm text-muted-foreground">{t('loans.empty.desc')}</div>
        </div>
      ) : (
        <div className="space-y-2">
          {visibleGroups.map((g) => (
            <LoanGroupCard
              key={g.key}
              group={g}
              currency={currency}
              expanded={false}
              onToggle={() => {}}
              onOpen={openDetail}
            />
          ))}
        </div>
      )}

      <LoanFormDialog
        open={formOpen}
        editing={editing}
        contacts={contacts}
        currency={currency}
        presetContactId={presetContactId}
        presetLoan={presetLoan}
        onOpenChange={setFormOpen}
        onSaved={() => void load()}
      />
      <LoanDetailDialog
        open={detailOpen}
        loan={detail}
        group={detailGroup}
        currency={currency}
        onOpenChange={setDetailOpen}
        onEdit={(l) => { setDetailOpen(false); setEditing(l); setPresetLoan(null); setFormOpen(true) }}
        onDeleted={() => void load()}
        onChanged={() => void refreshDetail()}
        onCreateInstead={(l) => {
          setDetailOpen(false)
          setEditing(null)
          setPresetLoan(l)
          setFormOpen(true)
        }}
        onViewPersonFlows={() => {
          // 详情页本身已展示「全部流水」，此按钮保留为关闭详情回到列表的快捷操作
          setDetailOpen(false)
        }}
        onOpenLoan={(l) => setDetail(l)}
      />
    </div>
  )
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-sm transition-colors ${
        active ? 'border-primary bg-primary/15 text-primary' : 'border-border text-muted-foreground hover:text-foreground'
      }`}
    >
      {children}
    </button>
  )
}
