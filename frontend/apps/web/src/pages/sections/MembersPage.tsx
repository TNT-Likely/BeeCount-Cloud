import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import {
  createMember,
  deleteMember,
  emptyPartyProfile,
  fetchReadMembers,
  fetchWorkspaceAnalytics,
  fetchWorkspaceTransactions,
  normalizePartyProfile,
  updateMember,
  type ReadMember,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import { Button, Dialog, DialogContent, DialogHeader, DialogTitle } from '@beecount/ui'
import {
  ConfirmDialog,
  formatBalanceCompact,
  formatIsoDateTimeLocal,
  MembersPanel,
  memberDefaults,
  PartyAvatar,
  PartyProfileView,
  type MemberForm,
  type MemberStat,
  SectionPageHeader,
} from '@beecount/web-features'

import { useLedgerWrite } from '../../app/useLedgerWrite'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { usePartyProfileIo } from '../../hooks/usePartyProfileIo'
import { onOpenDetailMember, openTxDetailBySyncId } from '../../lib/txDialogEvents'

/** 成员详情弹窗一次拉取的流水上限（统计笔数可能远大于此，列表只显示最近若干条）。 */
const MEMBER_TX_LIMIT = 200

/** 统计卡片：label + 金额。tone 用来区分支出(红)/收入(绿)/净额。 */
function Stat({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'rose' | 'emerald' | 'default'
}) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div
        className={`mt-1 text-base font-semibold ${
          tone === 'rose' ? 'text-rose-600' : tone === 'emerald' ? 'text-emerald-600' : ''
        }`}
      >
        {value}
      </div>
    </div>
  )
}

/**
 * 成员详情弹窗（0055）。
 *
 * 版式与联系人详情弹窗完全对齐：头部固定 / 中间独立滚动 / 底部操作栏固定。
 *
 * 统计维度**刻意与联系人不同**：
 * - 联系人 = 「人」口径（借出总额 / 借入总额 / 应收 / 应付），关心的是欠多少；
 * - 成员   = 「收支归属」口径（支出合计 / 收入合计 / 净额 / 流水笔数），关心的是
 *   这笔收支算在谁头上。多人分摊时，金额取**该成员分摊到的那一份**，
 *   而不是整笔金额（整笔金额在流水列表里会重复算到每个成员名下）。
 *
 * 流水可互相跳转：点流水行跳原交易详情；交易详情的「关联记录」里再跳回本弹窗。
 */
function MemberDetailDialog({
  open,
  member,
  stats,
  currency,
  activeLedgerId,
  onOpenChange,
  onEdit,
  onViewAll,
}: {
  open: boolean
  member: ReadMember | null
  /** analytics.member_ranks 的全量成员口径统计（支出/收入/笔数）。 */
  stats: MemberStat | null
  currency: string
  activeLedgerId: string | null
  onOpenChange: (v: boolean) => void
  onEdit: (m: ReadMember) => void
  onViewAll: (m: ReadMember) => void
}) {
  const t = useT()
  const { token } = useAuth()
  const { resolvePreview } = usePartyProfileIo()
  const [txs, setTxs] = useState<WorkspaceTransaction[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || !token || !activeLedgerId || !member) {
      setTxs([])
      return
    }
    let cancelled = false
    setLoading(true)
    fetchWorkspaceTransactions(token, {
      ledgerId: activeLedgerId,
      memberSyncId: member.id,
      limit: MEMBER_TX_LIMIT,
    })
      .then((data) => {
        if (!cancelled) setTxs(data.items || [])
      })
      .catch(() => {
        if (!cancelled) setTxs([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, token, activeLedgerId, member])

  const profile = member ? normalizePartyProfile(member.profile) : null
  const color = member?.color || '#94a3b8'
  const expense = stats?.expense ?? 0
  const income = stats?.income ?? 0
  const net = income - expense

  /** 该成员在这笔流水里分摊到的金额；没有分摊明细时为 null（用整笔金额）。 */
  const shareOf = (tx: WorkspaceTransaction): number | null => {
    if (!member || !tx.member_splits || tx.member_splits.length === 0) return null
    const hit = tx.member_splits.find((s) => s.member_id === member.id)
    return hit && typeof hit.amount === 'number' ? hit.amount : null
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex shrink-0 flex-row items-start justify-between gap-3 border-b border-border/60 bg-gradient-to-br from-muted/40 to-transparent px-6 py-4">
          <DialogTitle className="flex items-center gap-3 pr-8">
            {member ? (
              <>
                <PartyAvatar
                  fileId={profile?.avatarFileId}
                  name={member.name}
                  color={color}
                  size={44}
                  resolvePreview={resolvePreview}
                />
                <div className="flex min-w-0 flex-col">
                  <span className="truncate text-lg font-semibold">{member.name}</span>
                  <span className="mt-0.5 text-[11px] font-normal text-muted-foreground">
                    {t('members.detail.ownership')}
                  </span>
                </div>
              </>
            ) : null}
          </DialogTitle>
        </DialogHeader>

        {member ? (
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-6 py-4">
            {/* 统计概览：成员口径（支出/收入/净额/笔数），与联系人的借还口径不同。 */}
            <div>
              <div className="mb-2 text-sm font-medium">{t('members.detail.summary')}</div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Stat
                  label={t('members.detail.expenseTotal')}
                  value={formatBalanceCompact(expense, currency)}
                  tone="rose"
                />
                <Stat
                  label={t('members.detail.incomeTotal')}
                  value={formatBalanceCompact(income, currency)}
                  tone="emerald"
                />
                <Stat
                  label={t('members.detail.net')}
                  value={formatBalanceCompact(net, currency)}
                  tone={net < 0 ? 'rose' : net > 0 ? 'emerald' : 'default'}
                />
                <Stat label={t('members.detail.txCount')} value={String(stats?.txCount ?? 0)} />
              </div>
            </div>

            {profile ? (
              <div>
                <div className="mb-2 text-sm font-medium">{t('party.section')}</div>
                <PartyProfileView
                  profile={profile}
                  name={member.name}
                  color={color}
                  resolvePreview={resolvePreview}
                />
              </div>
            ) : null}

            {/* 流水明细：点整行跳原交易详情（与联系人详情同款互相跳转）。 */}
            <div className="min-w-0">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium">{t('members.detail.timeline')}</span>
                <span className="text-xs text-muted-foreground">
                  {/* 列表只取最近 200 条,统计笔数可能远大于此 —— 明确标注避免误解。 */}
                  {txs.length >= MEMBER_TX_LIMIT
                    ? t('members.detail.recentCount').replace('{count}', String(txs.length))
                    : `${txs.length} ${t('loans.group.flowCount')}`}
                </span>
              </div>
              {loading && txs.length === 0 ? (
                <div className="py-6 text-center text-sm text-muted-foreground">
                  {t('notice.loading')}
                </div>
              ) : txs.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border py-6 text-center text-sm text-muted-foreground">
                  {t('members.detail.empty')}
                </div>
              ) : (
                <div className="divide-y divide-border overflow-hidden rounded-lg border border-border">
                  {txs.map((tx) => {
                    const share = shareOf(tx)
                    const amount = share ?? Number(tx.amount || 0)
                    const isIncome = tx.tx_type === 'income'
                    const isExpense = tx.tx_type === 'expense'
                    return (
                      <div
                        key={tx.id}
                        className="flex cursor-pointer items-center gap-3 px-3 py-2.5 transition-colors hover:bg-primary/5"
                        onClick={() =>
                          openTxDetailBySyncId(token, tx.id, activeLedgerId ?? undefined, () =>
                            onOpenChange(false),
                          )
                        }
                        title={t('deposits.detail.viewFlow') as string}
                      >
                        <span
                          className={`block h-8 w-1 shrink-0 rounded-full ${
                            isIncome
                              ? 'bg-emerald-500'
                              : isExpense
                                ? 'bg-rose-500'
                                : 'bg-muted-foreground/40'
                          }`}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium">
                            {tx.category_name || '—'}
                            {share != null ? (
                              <span className="ml-1 rounded bg-sky-500/15 px-1.5 py-0.5 text-[11px] font-normal text-sky-600">
                                {t('members.detail.splitTag')}
                              </span>
                            ) : null}
                          </div>
                          <div className="truncate text-[11px] text-muted-foreground">
                            {formatIsoDateTimeLocal(tx.happened_at)}
                            {tx.account_name ? ` · ${tx.account_name}` : ''}
                            {tx.note ? ` · ${tx.note}` : ''}
                          </div>
                        </div>
                        <div
                          className={`shrink-0 text-sm font-semibold tabular-nums ${
                            isIncome
                              ? 'text-emerald-600'
                              : isExpense
                                ? 'text-rose-600'
                                : 'text-foreground'
                          }`}
                        >
                          {isIncome ? '+' : isExpense ? '-' : ''}
                          {formatBalanceCompact(amount, currency)}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        ) : null}

        {/* 底部操作栏固定可见，不随流水列表滚动。 */}
        {member ? (
          <div className="shrink-0 border-t border-border/60 px-6 py-3">
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => onViewAll(member)}>
                {t('members.detail.viewAllFlows')}
              </Button>
              <Button variant="ghost" onClick={() => onEdit(member)}>
                {t('confirm.edit')}
              </Button>
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

/**
 * 成员管理页（0024）。
 *
 * - 成员是 user-global 实体，跨账本复用；通过 /write/ledgers/{active}/members
 *   走统一写入引擎创建/改名/删除（写端点需要账本作为变更载体）。
 * - 统计来自 /read/workspace/analytics 的 member_ranks：分别按 expense / income
 *   两种 metric 各取一次全部时间范围，合并成支出/收入/笔数三列。
 * - 点卡片打开成员详情弹窗（0055）：统计用成员口径（支出/收入/净额/笔数），
 *   流水可跳原交易详情；交易详情的「关联记录」又能跳回本弹窗。
 */
export function MembersPage() {
  const t = useT()
  const toast = useToast()
  const navigate = useNavigate()
  const { token } = useAuth()
  const { currency, activeLedgerId } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()
  const { upload, resolvePreview } = usePartyProfileIo()
  const [searchParams] = useSearchParams()

  const [rows, setRows] = usePageCache<ReadMember[]>('members:rows', [])
  const rowsRef = useRef<ReadMember[]>(rows)
  useEffect(() => {
    rowsRef.current = rows
  }, [rows])
  const [statsById, setStatsById] = useState<Record<string, MemberStat>>({})
  const [form, setForm] = useState<MemberForm>(memberDefaults())
  const [pendingDelete, setPendingDelete] = useState<{ id: string; name: string } | null>(null)
  // 点开成员 → 弹窗显示该成员的流水 + 收支统计
  const [detailMember, setDetailMember] = useState<ReadMember | null>(null)
  // 详情弹窗底部「编辑」→ 复用面板内的编辑表单（nonce 递增触发一次）。
  const [editRequest, setEditRequest] = useState<{ memberId: string; nonce: number } | null>(null)
  // 关联记录跳转(0055):?member_id=xxx → 自动打开该成员详情。
  const presetMemberId = searchParams.get('member_id')

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t],
  )

  const refresh = useCallback(async () => {
    // 成员读接口挂在某个账本路径下（沿用 tags 的写法），但实体本身是
    // user-global，所以没选账本时不发请求，只清掉缓存展示空态。
    if (!token || !activeLedgerId) {
      setRows([])
      setStatsById({})
      return
    }
    try {
      const [memberRows, expenseAnalytics, incomeAnalytics] = await Promise.all([
        fetchReadMembers(token, activeLedgerId),
        // analytics 是"锦上添花"的统计维度：失败不应该把整个成员列表带崩。
        fetchWorkspaceAnalytics(token, {
          scope: 'all', metric: 'expense', ledgerId: activeLedgerId,
        }).catch(() => null),
        fetchWorkspaceAnalytics(token, {
          scope: 'all', metric: 'income', ledgerId: activeLedgerId,
        }).catch(() => null),
      ])
      setRows(memberRows)

      const next: Record<string, MemberStat> = {}
      for (const row of memberRows) {
        next[row.id] = { expense: 0, income: 0, txCount: 0 }
      }
      const applyRanks = (
        ranks: Array<{ member_id: string; total: number; tx_count: number }> | undefined,
        key: 'expense' | 'income',
      ) => {
        for (const rank of ranks || []) {
          const slot = next[rank.member_id] || { expense: 0, income: 0, txCount: 0 }
          slot[key] = rank.total
          slot.txCount = Math.max(slot.txCount, rank.tx_count || 0)
          next[rank.member_id] = slot
        }
      }
      applyRanks(expenseAnalytics?.member_ranks, 'expense')
      applyRanks(incomeAnalytics?.member_ranks, 'income')
      setStatsById(next)
    } catch (err) {
      notifyError(err)
    }
  }, [token, activeLedgerId, setRows, notifyError])

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
    const wasEditing = Boolean(form.editingId)
    try {
      const payload = {
        name: form.name.trim(),
        color: form.color || null,
        // 资料随成员一起走写入引擎(snapshot item.profile → 投影 profile_json)。
        profile: form.profile,
      }
      await retryOnConflict(activeLedgerId, (base) =>
        form.editingId
          ? updateMember(token, activeLedgerId, form.editingId, base, payload)
          : createMember(token, activeLedgerId, base, payload),
      )
      setForm(memberDefaults())
      await refresh()
      notifySuccess(wasEditing ? t('notice.memberUpdated') : t('notice.memberCreated'))
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const confirmDelete = async () => {
    if (!pendingDelete || !activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteMember(token, activeLedgerId, pendingDelete.id, base),
      )
      await refresh()
      notifySuccess(t('notice.memberDeleted'))
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    } finally {
      setPendingDelete(null)
    }
  }

  const openDetail = useCallback((member: ReadMember) => {
    setDetailMember(member)
  }, [])

  /** 按 id 打开成员详情：列表里没有就直接调接口拉一次（跨页跳转 / 事件触发）。 */
  const openDetailById = useCallback(
    (memberId: string) => {
      const found = rowsRef.current.find((r) => r.id === memberId)
      if (found) {
        setDetailMember(found)
        return
      }
      if (!token || !activeLedgerId) return
      void fetchReadMembers(token, activeLedgerId)
        .then((list) => {
          const hit = list.find((r) => r.id === memberId)
          if (hit) setDetailMember(hit)
        })
        .catch(() => undefined)
    },
    [token, activeLedgerId],
  )

  // 关联记录跳转(0055):?member_id=xxx → 拉起该成员详情。
  const openedPresetRef = useRef<string | null>(null)
  useEffect(() => {
    if (!token || !presetMemberId) return
    if (openedPresetRef.current === presetMemberId) return
    openedPresetRef.current = presetMemberId
    openDetailById(presetMemberId)
  }, [token, presetMemberId, openDetailById])

  // 流水详情「关联记录」跳回成员：同页面时 URL 参数 effect 的 ref guard 会拦截，
  // 靠此事件直接打开。
  useEffect(() => {
    if (!token) return
    return onOpenDetailMember((memberId) => {
      openDetailById(memberId)
    })
  }, [token, openDetailById])

  const detailStats = useMemo(
    () => (detailMember ? statsById[detailMember.id] ?? null : null),
    [detailMember, statsById],
  )

  return (
    <>
      <SectionPageHeader
        section="members"
        title={t('members.title')}
        subtitle={t('members.subtitle')}
      />
      <MembersPanel
        form={form}
        rows={rows}
        canManage={Boolean(activeLedgerId)}
        statsById={statsById}
        upload={upload}
        resolvePreview={resolvePreview}
        onFormChange={setForm}
        onCreate={() => setForm(memberDefaults())}
        onSave={onSave}
        onReset={() => setForm(memberDefaults())}
        onEdit={(row) => {
          setForm({
            editingId: row.id,
            editingOwnerUserId: row.created_by_user_id || '',
            name: row.name,
            color: row.color || '#3F51B5',
            profile: normalizePartyProfile(row.profile) ?? emptyPartyProfile(),
          })
        }}
        onDelete={(row) => {
          // 后端 delete_member 会拒绝仍被交易引用的成员，这里提前给友好提示。
          const linkedCount = statsById[row.id]?.txCount ?? 0
          if (linkedCount > 0) {
            toast.error(
              t('members.error.hasTransactions').replace('{count}', String(linkedCount)),
              t('notice.error'),
            )
            return
          }
          setPendingDelete({ id: row.id, name: row.name })
        }}
        onClickMember={openDetail}
        editRequest={editRequest}
      />
      <MemberDetailDialog
        open={!!detailMember}
        member={detailMember}
        stats={detailStats}
        currency={currency}
        activeLedgerId={activeLedgerId}
        onOpenChange={(v) => {
          if (!v) setDetailMember(null)
        }}
        onEdit={(m) => {
          setDetailMember(null)
          setEditRequest({ memberId: m.id, nonce: Date.now() })
        }}
        onViewAll={(m) => {
          setDetailMember(null)
          navigate(`/app/transactions?member=${encodeURIComponent(m.id)}`)
        }}
      />
      <ConfirmDialog
        open={!!pendingDelete}
        title={t('confirm.deleteMember.title')}
        description={
          pendingDelete ? t('confirm.deleteMember.desc').replace('{name}', pendingDelete.name) : ''
        }
        confirmText={t('confirm.delete')}
        cancelText={t('confirm.cancel')}
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => void confirmDelete()}
      />
    </>
  )
}
