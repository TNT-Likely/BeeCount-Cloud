import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Plus, Search, UserPlus, FileText, HandCoins } from 'lucide-react'

import {
  type Contact,
  type ContactLoanRank,
  type ContactState,
  type ContactSummary,
  type ContactTag,
  type Loan,
  type LoanDirection,
  type LoanRepayment,
  type ReadAccount,
  type SuggestContact,
  archiveContact,
  createContact,
  createLoan,
  deleteContact,
  disableContact,
  emptyPartyProfile,
  enableContact,
  fetchContactLoanRanks,
  fetchContactSuggestions,
  fetchContactSummary,
  fetchContacts,
  fetchLoans,
  fetchReadAccounts,
  fetchRepayments,
  normalizePartyProfile,
  unarchiveContact,
  updateContact,
  type PartyProfile,
} from '@beecount/api-client'
import { useT, useToast, Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription, Button, Input, Textarea, Label, Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@beecount/ui'
import {
  formatBalanceCompact,
  formatIsoDateTimeLocal,
  PartyAvatar,
  PartyProfileEditor,
  PartyProfileView,
  TAG_COLOR_PALETTE,
  toLocalDateTimeInput as toLocalInput,
  fromLocalDateTimeInput as fromLocalInput,
  CategoryIcon,
  flowIconKeyForLoan,
  resolveFlowIcon,
  resolveFlowIconColor,
  useFlowIconPrefs,
  useFlowIconColorPrefs,
  SectionPageHeader,
} from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { openTxDetailBySyncId, onOpenDetailContact } from '../../lib/txDialogEvents'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { usePartyProfileIo } from '../../hooks/usePartyProfileIo'
import {
  LoanAttachmentEditorField,
  LoanAttachmentThumbs,
  type LoanAttachmentEditorHandle,
} from '../../components/LoanAttachments'

const TAGS: ContactTag[] = ['friend', 'family', 'colleague', 'merchant']

/** 联系人状态切换动作,与后端 4 个专用端点一一对应。 */
type ContactStateAction = 'archive' | 'unarchive' | 'disable' | 'enable'

function tagLabel(t: (k: string) => string, tag: ContactTag | null): string {
  if (!tag) return ''
  return t(`contacts.tag.${tag}`)
}

/** 联系人没有成员那样的自定义色,用 id 稳定散列到调色板,卡片光晕与排行圆点同色。 */
function contactColor(c: Contact): string {
  let hash = 0
  for (const ch of c.id || c.name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return TAG_COLOR_PALETTE[hash % TAG_COLOR_PALETTE.length] || '#94a3b8'
}

/** 没有借款记录的联系人按 0 展示,与成员页 statsById 缺省值同语义。 */
const EMPTY_RANK: ContactLoanRank = {
  contact_id: '',
  name: '',
  contact_tag: null,
  loan_count: 0,
  lend_total: 0,
  borrow_total: 0,
  receivable: 0,
  payable: 0,
}

/**
 * 联系人借款排行(0031):对齐成员收支排行的双条形版式。
 *
 * 数据来自 /contacts/loan-ranks(一次返回全部有借款的联系人,默认跨全部账本),
 * 借出/借入各自按最大值归一 —— 两者量级经常差很多,共用刻度会把小的压成一条线。
 * 排行取「往来总额」top 8,点击某一行直接打开该联系人详情。
 */
function ContactLoanRanks({
  contacts,
  ranksById,
  currency,
  onPick,
}: {
  contacts: Contact[]
  ranksById: Record<string, ContactLoanRank> | null
  currency: string
  onPick: (c: Contact) => void
}) {
  const t = useT()
  const byId = useMemo(() => new Map(contacts.map((c) => [c.id, c])), [contacts])
  const ranks = useMemo(() => {
    if (!ranksById) return []
    const items = contacts
      .map((c) => ({
        ...(ranksById[c.id] || EMPTY_RANK),
        // 名称/色值以联系人表为准,避免排行快照里的旧名字覆盖刚改过的名字。
        id: c.id,
        name: c.name,
        color: contactColor(c),
      }))
      .filter((item) => item.lend_total > 0 || item.borrow_total > 0)
      .sort(
        (a, b) =>
          b.lend_total + b.borrow_total - (a.lend_total + a.borrow_total) ||
          b.lend_total - a.lend_total ||
          a.name.localeCompare(b.name),
      )
      .slice(0, 8)
    const maxLend = Math.max(1, ...items.map((item) => item.lend_total))
    const maxBorrow = Math.max(1, ...items.map((item) => item.borrow_total))
    return items.map((item) => ({
      ...item,
      lendPct: (item.lend_total / maxLend) * 100,
      borrowPct: (item.borrow_total / maxBorrow) * 100,
    }))
  }, [contacts, ranksById])

  // 排行接口失败时整个面板隐藏,不拖垮联系人列表(与成员页 analytics 容错一致)。
  if (!ranksById) return null
  return (
    <div className="rounded-2xl border border-border/50 bg-card/70 p-4 backdrop-blur-sm">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          <HandCoins className="h-4 w-4 text-teal-600 dark:text-teal-400" />
          {t('contacts.ranks.title')}
        </h3>
        <span className="text-[11px] text-muted-foreground">
          {t('loans.direction.lend')} / {t('loans.direction.borrow')} · {t('contacts.ranks.scope')} · {t('members.stats.scaleHint')}
        </span>
      </div>
      {ranks.length === 0 ? (
        <p className="py-4 text-center text-xs text-muted-foreground">{t('contacts.ranks.empty')}</p>
      ) : (
        <div className="space-y-2.5">
          {ranks.map((item) => (
            <button
              key={item.id}
              type="button"
              className="grid w-full grid-cols-[minmax(72px,110px)_1fr_auto] items-center gap-3 rounded-md text-left text-xs transition-colors hover:bg-muted/50"
              onClick={() => {
                const c = byId.get(item.id)
                if (c) onPick(c)
              }}
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: item.color }} />
                <span className="truncate">{item.name}</span>
              </span>
              <span className="flex flex-col gap-1">
                <span className="h-1.5 overflow-hidden rounded-full bg-muted">
                  <span className="block h-full rounded-full bg-emerald-500" style={{ width: `${item.lendPct}%` }} />
                </span>
                <span className="h-1.5 overflow-hidden rounded-full bg-muted">
                  <span className="block h-full rounded-full bg-rose-500" style={{ width: `${item.borrowPct}%` }} />
                </span>
              </span>
              <span className="text-right font-mono tabular-nums text-muted-foreground">
                {formatBalanceCompact(item.lend_total, currency)} / {formatBalanceCompact(item.borrow_total, currency)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * 联系人卡片:与成员页同版式(头像 + hover 编辑/删除 + 大数字 + 双色统计 +
 * 点击提示 + 资料计数)。金额口径来自联系人借款排行接口,与详情概览一致。
 */
function ContactCard({
  contact,
  rank,
  currency,
  onOpen,
  onEdit,
  onDelete,
  onStateChange,
  resolvePreview,
}: {
  contact: Contact
  rank: ContactLoanRank | undefined
  currency: string
  onOpen: (c: Contact) => void
  onEdit: (c: Contact) => void
  onDelete: (c: Contact) => void
  onStateChange: (c: Contact, action: ContactStateAction) => void
  resolvePreview: (fileId: string) => Promise<string | null>
}) {
  const t = useT()
  const color = contactColor(contact)
  const profile = normalizePartyProfile(contact.profile)
  const stat = rank || EMPTY_RANK
  return (
    <div
      role="button"
      tabIndex={0}
      className={`group relative cursor-pointer overflow-hidden rounded-2xl border border-border/50 bg-card/80 p-5 backdrop-blur-sm transition-all hover:-translate-y-0.5 hover:border-border hover:shadow-lg ${
        contact.is_archived || !contact.is_active ? 'opacity-60' : ''
      }`}
      onClick={() => onOpen(contact)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onOpen(contact)
        }
      }}
    >
      <div
        className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full blur-3xl"
        style={{ background: color, opacity: 0.18 }}
        aria-hidden
      />
      <div className="relative space-y-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2.5">
            <PartyAvatar
              fileId={profile?.avatarFileId}
              name={contact.name}
              color={color}
              size={36}
              resolvePreview={resolvePreview}
            />
            <span className="truncate text-base font-semibold">{contact.name}</span>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {contact.contact_tag ? (
              <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[11px] text-primary">
                {tagLabel(t, contact.contact_tag)}
              </span>
            ) : null}
            {contact.is_archived ? (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                {t('contacts.state.archived')}
              </span>
            ) : null}
            {!contact.is_active ? (
              <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] text-amber-600 dark:text-amber-400">
                {t('contacts.state.disabled')}
              </span>
            ) : null}
            {/* 触屏没有 hover:小屏常显编辑/删除,桌面端沿用成员页的 hover 显隐。 */}
            <div className="flex items-center gap-1.5 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
              <button
                type="button"
                className="rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-primary/15 hover:text-primary"
                onClick={(e) => { e.stopPropagation(); onEdit(contact) }}
              >
                {t('common.edit')}
              </button>
              <button
                type="button"
                className="rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                onClick={(e) => { e.stopPropagation(); onDelete(contact) }}
              >
                {t('common.delete')}
              </button>
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-baseline gap-1.5">
            <span className="font-mono text-2xl font-bold tabular-nums">{stat.loan_count}</span>
            <span className="text-[11px] text-muted-foreground">{t('contacts.stats.unit')}</span>
          </div>
          <div className="flex items-center justify-between gap-3 rounded-lg border border-border/40 bg-background/40 px-3 py-2 text-xs">
            <div className="flex items-center gap-1.5 text-emerald-600" title={t('contacts.detail.lendTotal') as string}>
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
              <span className="font-mono font-semibold">{formatBalanceCompact(stat.lend_total, currency)}</span>
            </div>
            <div className="flex items-center gap-1.5 text-red-600" title={t('contacts.detail.borrowTotal') as string}>
              <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
              <span className="font-mono font-semibold">{formatBalanceCompact(stat.borrow_total, currency)}</span>
            </div>
          </div>
          <div className="flex items-center justify-between gap-2 rounded-lg border border-border/40 bg-background/40 px-3 py-2 text-[11px]">
            <span className="text-muted-foreground">
              {t('contacts.detail.receivable')} <span className="font-mono font-semibold text-emerald-600">{formatBalanceCompact(stat.receivable, currency)}</span>
            </span>
            <span className="text-muted-foreground">
              {t('contacts.detail.payable')} <span className="font-mono font-semibold text-red-600">{formatBalanceCompact(stat.payable, currency)}</span>
            </span>
          </div>
        </div>

        <p className="truncate text-[11px] text-muted-foreground">{t('contacts.stats.filterHint')}</p>
        {contact.note ? <p className="line-clamp-1 text-[11px] text-muted-foreground">{contact.note}</p> : null}
        {profile && (profile.contactMethods.length > 0 || profile.bankCards.length > 0) ? (
          <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
            {profile.contactMethods.length > 0 ? <span>{t('party.methods')} · {profile.contactMethods.length}</span> : null}
            {profile.bankCards.length > 0 ? <span>{t('party.cards')} · {profile.bankCards.length}</span> : null}
          </div>
        ) : (contact.phone || contact.wechat || contact.alipay) ? (
          <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
            {contact.phone ? <span>📱 {contact.phone}</span> : null}
            {contact.wechat ? <span>💬 {contact.wechat}</span> : null}
            {contact.alipay ? <span>💰 {contact.alipay}</span> : null}
          </div>
        ) : null}
        {/* 归档 / 停用(可逆):与编辑/删除分区,避免顶部按钮过挤。 */}
        <div className="flex flex-wrap items-center gap-1.5 border-t border-border/40 pt-2">
          <button
            type="button"
            className="rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-primary/15 hover:text-primary"
            onClick={(e) => { e.stopPropagation(); onStateChange(contact, contact.is_archived ? 'unarchive' : 'archive') }}
          >
            {t(contact.is_archived ? 'contacts.action.unarchive' : 'contacts.action.archive')}
          </button>
          <button
            type="button"
            className="rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-amber-500/15 hover:text-amber-600"
            onClick={(e) => { e.stopPropagation(); onStateChange(contact, contact.is_active ? 'disable' : 'enable') }}
          >
            {t(contact.is_active ? 'contacts.action.disable' : 'contacts.action.enable')}
          </button>
        </div>
      </div>
    </div>
  )
}

function ContactFormDialog({
  open,
  editing,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  editing: Contact | null
  onOpenChange: (v: boolean) => void
  onSaved: () => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [tag, setTag] = useState<ContactTag | 'none'>('none')
  const [profile, setProfile] = useState<PartyProfile>(emptyPartyProfile())
  const [saving, setSaving] = useState(false)
  const { upload, resolvePreview } = usePartyProfileIo()

  useEffect(() => {
    if (!open) return
    setName(editing?.name || '')
    setNote(editing?.note || '')
    setTag(editing?.contact_tag || 'none')
    // 统一资料结构；旧单值字段由后端读视图合成进 profile。
    setProfile(normalizePartyProfile(editing?.profile) ?? emptyPartyProfile())
  }, [open, editing])

  const save = async () => {
    if (!token) return
    const trimmed = name.trim()
    if (!trimmed) {
      toast.error(t('contacts.field.name') + t('notice.required'), t('notice.error'))
      return
    }
    setSaving(true)
    try {
      const body = {
        name: trimmed,
        note: note || null,
        contact_tag: tag === 'none' ? null : tag,
        // 头像 / 多联系方式 / 多银行卡一次提交；后端同步旧单值列以兼容老客户端。
        profile,
      }
      if (editing) await updateContact(token, editing.id, body)
      else await createContact(token, body)
      toast.success(editing ? t('contacts.notice.updated') : t('contacts.notice.created'), t('notice.success'))
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
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{editing ? t('contacts.button.update') : t('contacts.button.create')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('contacts.field.name')}</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('contacts.placeholder.name')} />
          </div>
          <div className="space-y-1">
            <Label>{t('contacts.field.tag')}</Label>
            <Select value={tag} onValueChange={(v) => setTag(v as ContactTag | 'none')}>
              <SelectTrigger>
                <SelectValue placeholder={t('contacts.field.tag')} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">{t('loans.field.noContact')}</SelectItem>
                {TAGS.map((tg) => (
                  <SelectItem key={tg} value={tg}>{t(`contacts.tag.${tg}`)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>{t('contacts.field.note')}</Label>
            <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
          </div>
          <div className="rounded-lg border border-border p-3">
            <div className="mb-3 text-sm font-medium">{t('party.section')}</div>
            <PartyProfileEditor
              key={`contact-profile:${open}:${editing?.id || 'new'}`}
              value={profile}
              onChange={setProfile}
              upload={upload}
              resolvePreview={resolvePreview}
              disabled={saving}
              name={name}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('confirm.cancel')}</Button>
          <Button onClick={() => void save()} disabled={saving}>{t('confirm.save')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ImportDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  onSaved: () => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const [suggestions, setSuggestions] = useState<SuggestContact[]>([])
  const [loading, setLoading] = useState(false)
  const [added, setAdded] = useState<Record<string, boolean>>({})

  useEffect(() => {
    if (!open || !token) return
    setLoading(true)
    fetchContactSuggestions(token)
      .then((list) => setSuggestions(list))
      .catch((err) => toast.error(localizeError(err, t), t('notice.error')))
      .finally(() => setLoading(false))
  }, [open, token, toast, t])

  const add = async (name: string) => {
    if (!token) return
    try {
      await createContact(token, { name })
      setAdded((prev) => ({ ...prev, [name]: true }))
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    }
  }

  const close = () => {
    setSuggestions([])
    setAdded({})
    onOpenChange(false)
    onSaved()
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) close() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('contacts.fromTransactions')}</DialogTitle>
          <DialogDescription>{t('contacts.fromTransactions.desc')}</DialogDescription>
        </DialogHeader>
        {loading ? (
          <div className="py-6 text-center text-sm text-muted-foreground">{t('notice.loading')}</div>
        ) : suggestions.length === 0 ? (
          <div className="py-6 text-center text-sm text-muted-foreground">{t('contacts.suggest.empty')}</div>
        ) : (
          <div className="max-h-80 space-y-2 overflow-y-auto">
            <div className="text-sm font-medium">{t('contacts.suggest.title')}</div>
            {suggestions.map((s) => (
              <div key={s.name} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                <span className="text-sm">{s.name}</span>
                <Button size="sm" variant="outline" disabled={added[s.name]} onClick={() => void add(s.name)}>
                  {added[s.name] ? t('notice.success') : t('contacts.addSuggested')}
                </Button>
              </div>
            ))}
          </div>
        )}
        <DialogFooter>
          <Button onClick={close}>{t('confirm.done')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StatementDialog({
  open,
  contact,
  loans,
  currency,
  onOpenChange,
}: {
  open: boolean
  contact: Contact | null
  loans: Loan[]
  currency: string
  onOpenChange: (v: boolean) => void
}) {
  const t = useT()
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('statement.title')}</DialogTitle>
          <DialogDescription>
            {contact ? `${t('statement.contact')}: ${contact.name}` : ''} · {t('statement.period')}: {formatIsoDateTimeLocal(new Date().toISOString())}
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto" id="statement-print-area">
          {contact ? (
            <StatementBody contact={contact} loans={loans} currency={currency} />
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('statement.close')}</Button>
          <Button onClick={() => window.print()}>{t('statement.export')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StatementBody({ contact, loans, currency }: { contact: Contact; loans: Loan[]; currency: string }) {
  const t = useT()
  const lendTotal = loans.filter((l) => l.direction === 'lend').reduce((s, l) => s + l.amount, 0)
  const borrowTotal = loans.filter((l) => l.direction === 'borrow').reduce((s, l) => s + l.amount, 0)
  const receivable = loans.filter((l) => l.direction === 'lend' && l.status !== 'settled').reduce((s, l) => s + (l.amount - l.total_repaid), 0)
  const payable = loans.filter((l) => l.direction === 'borrow' && l.status !== 'settled').reduce((s, l) => s + (l.amount - l.total_repaid), 0)
  return (
    <div className="space-y-4 text-sm">
      {contact.note ? <div className="text-muted-foreground">{contact.note}</div> : null}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label={t('statement.lendTotal')} value={formatBalanceCompact(lendTotal, currency)} />
        <Stat label={t('statement.borrowTotal')} value={formatBalanceCompact(borrowTotal, currency)} />
        <Stat label={t('statement.receivable')} value={formatBalanceCompact(receivable, currency)} />
        <Stat label={t('statement.payable')} value={formatBalanceCompact(payable, currency)} />
      </div>
      <div>
        <div className="mb-2 font-medium">{t('statement.transactions')}</div>
        {loans.length === 0 ? (
          <div className="text-muted-foreground">{t('statement.empty')}</div>
        ) : (
          <div className="divide-y divide-border rounded-lg border border-border">
            {loans.map((l) => (
              <div key={l.id} className="flex items-center justify-between px-3 py-2">
                <div>
                  <div className="font-medium">
                    {l.direction === 'lend' ? t('loans.direction.lend') : t('loans.direction.borrow')} · {formatBalanceCompact(l.amount, currency)}
                  </div>
                  <div className="text-xs text-muted-foreground">{formatIsoDateTimeLocal(l.happened_at)}</div>
                </div>
                <span className={`text-xs font-semibold ${l.status === 'settled' ? 'text-emerald-600' : l.status === 'overdue' ? 'text-red-600' : 'text-muted-foreground'}`}>
                  {t(`loans.status.${l.status}`)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-base font-semibold">{value}</div>
    </div>
  )
}

/**
 * 联系人详情里的「追加借入 / 追加借出」快速弹窗。
 *
 * 方向与联系人由按钮固定,弹窗内只填金额/账户/时间/备注/凭证,保存后
 * createLoan 生成对应方向、排除统计的支出/收入流水。两个方向各渲染一个
 * 独立实例,表单状态与附件草稿互不共享。顶部按「人」维度展示该方向当前
 * 余额与追加后预计值,与详情页概览同口径。
 */
function ContactQuickLoanDialog({
  open,
  contact,
  direction,
  currency,
  currentBalance,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  contact: Contact | null
  direction: LoanDirection
  currency: string
  /** 该联系人该方向当前余额(应收/应付),用于追加前后对比。 */
  currentBalance: number
  onOpenChange: (v: boolean) => void
  onSaved: () => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const [amount, setAmount] = useState('')
  const [happenedAt, setHappenedAt] = useState('')
  const [dueAt, setDueAt] = useState('')
  const [note, setNote] = useState('')
  const [accountId, setAccountId] = useState('none')
  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [accountsLoading, setAccountsLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const attachmentRef = useRef<LoanAttachmentEditorHandle>(null)

  const isLend = direction === 'lend'
  const title = isLend ? t('contacts.detail.appendLend') : t('contacts.detail.appendBorrow')
  const balanceLabel = isLend ? t('contacts.detail.receivable') : t('contacts.detail.payable')
  const amt = parseFloat(amount)
  const validAmount = Number.isFinite(amt) && amt > 0
  const projected = currentBalance + (validAmount ? amt : 0)

  useEffect(() => {
    if (!open) return
    setAmount('')
    setHappenedAt(toLocalInput(new Date().toISOString()))
    setDueAt('')
    setNote('')
    setAccountId('none')
  }, [open, contact?.id, direction])

  // 借款要生成真实流水,必须选定扣款/到款账户;与借款页一致过滤隐藏账户。
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
    if (!token || !activeLedgerId || !contact) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return
    }
    if (!validAmount) {
      toast.error(t('loans.field.amount') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    const happened = fromLocalInput(happenedAt)
    if (!happened) {
      toast.error(t('loans.field.happenedAt') + ' ' + t('notice.required'), t('notice.error'))
      return
    }
    const selected = accounts.find((a) => a.id === accountId)
    if (!selected) {
      toast.error(t('loans.notice.accountRequired'), t('notice.error'))
      return
    }
    setSaving(true)
    try {
      // 凭证在保存时才上传;prepare() 返回 null 表示上传失败,阻止提交。
      const voucherIds = await attachmentRef.current?.prepare()
      if (voucherIds == null) {
        toast.error(t('loans.attachment.uploadFailed'), t('notice.error'))
        return
      }
      await createLoan(token, {
        ledger_id: activeLedgerId,
        contact_id: contact.id,
        direction,
        amount: amt,
        currency_code: currency,
        happened_at: happened,
        due_at: fromLocalInput(dueAt),
        interest_type: 'none',
        note: note || null,
        voucher_file_ids: voucherIds,
        account_id: selected.id,
        account_name: selected.name,
      })
      toast.success(t('contacts.quickLoan.created'), t('notice.success'))
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
          <DialogTitle>{title}{contact ? ` · ${contact.name}` : ''}</DialogTitle>
          <DialogDescription>{t('contacts.quickLoan.hint')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {/* 按人维度展示追加前后余额,口径与详情页顶部「应收/应付余额」一致 */}
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm">
            <span className="text-muted-foreground">
              {balanceLabel} {formatBalanceCompact(currentBalance, currency)}
            </span>
            <span>
              {t('contacts.quickLoan.after')}{' '}
              <span className={`font-semibold ${isLend ? 'text-emerald-600' : 'text-red-600'}`}>
                {formatBalanceCompact(projected, currency)}
              </span>
            </span>
          </div>

          <div className="space-y-1">
            <Label>{isLend ? t('loans.field.debitAccount') : t('loans.field.creditAccount')}</Label>
            <Select value={accountId} onValueChange={setAccountId} disabled={accountsLoading}>
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
            <Input type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.00" />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label>{t('loans.field.happenedAt')}</Label>
              <div className="flex gap-1">
                <Input type="datetime-local" step="1" value={happenedAt} onChange={(e) => setHappenedAt(e.target.value)} />
                <Button size="sm" variant="outline" onClick={() => setHappenedAt(toLocalInput(new Date().toISOString()))}>
                  {t('loans.append.now')}
                </Button>
              </div>
            </div>
            <div className="space-y-1">
              <Label>{t('loans.field.dueAt')}</Label>
              <Input type="datetime-local" step="1" value={dueAt} onChange={(e) => setDueAt(e.target.value)} />
            </div>
          </div>

          <div className="space-y-1">
            <Label>{t('loans.field.note')}</Label>
            <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
          </div>

          <LoanAttachmentEditorField
            key={`contact-quick:${direction}:${open}`}
            ref={attachmentRef}
            fileIds={[]}
            disabled={saving}
            title={t('loans.field.vouchers')}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('confirm.cancel')}</Button>
          <Button onClick={() => void save()} disabled={saving || !contact}>{t('loans.append.confirm')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ContactDetailDialog({
  open,
  contact,
  currency,
  activeLedgerId,
  onOpenChange,
  onEdit,
  onStatement,
  onStateChange,
  onChanged,
}: {
  open: boolean
  contact: Contact | null
  currency: string
  activeLedgerId: string | null
  onOpenChange: (v: boolean) => void
  onEdit: (c: Contact) => void
  onStatement: () => void
  /** 归档/恢复/停用/启用;主键由调用方处理刷新与提示。 */
  onStateChange: (c: Contact, action: ContactStateAction) => void
  /** 追加借入/借出成功后同时刷新联系人列表与详情内部数据。 */
  onChanged?: () => void
}) {
  const t = useT()
  const { token } = useAuth()
  const { resolvePreview } = usePartyProfileIo()
  /** 特殊流水图标 / 颜色偏好(与交易行、借款页共用同一份 profile 配置)。 */
  const flowIconPrefs = useFlowIconPrefs()
  const flowIconColors = useFlowIconColorPrefs()
  const [summary, setSummary] = useState<ContactSummary | null>(null)
  const [loans, setLoans] = useState<Loan[]>([])
  const [repayments, setRepayments] = useState<LoanRepayment[]>([])
  const [loading, setLoading] = useState(false)
  /** 追加成功后自增,强制重新拉取概览/时间轴(保持详情弹窗不关闭)。 */
  const [refreshKey, setRefreshKey] = useState(0)
  // 两个方向各一个独立弹窗实例,状态互不共享。
  const [appendBorrowOpen, setAppendBorrowOpen] = useState(false)
  const [appendLendOpen, setAppendLendOpen] = useState(false)

  useEffect(() => {
    if (!open || !token || !contact) return
    setLoading(true)
    const loanPromise = activeLedgerId
      ? fetchLoans(token, { ledger_id: activeLedgerId, contact_id: contact.id })
      : Promise.resolve([])
    Promise.all([fetchContactSummary(token, contact.id), loanPromise])
      .then(([s, ls]) => {
        setSummary(s)
        setLoans(ls)
        // 时间轴要展示「所有借还记录」:并行拉取每笔借款的还款记录。
        // 单笔失败不拖垮整个时间轴(catch → 空数组)。
        if (!token || ls.length === 0) {
          setRepayments([])
          return
        }
        void Promise.all(
          ls.map((l) => fetchRepayments(token, l.id).catch(() => [] as LoanRepayment[]))
        ).then((groups) => {
          setRepayments(groups.flat())
        })
      })
      .catch(() => { setSummary(null); setLoans([]); setRepayments([]) })
      .finally(() => setLoading(false))
  }, [open, token, contact, activeLedgerId, refreshKey])

  // 详情关闭时收起两个快速弹窗,避免下次打开别的联系人时残留。
  useEffect(() => {
    if (open) return
    setAppendBorrowOpen(false)
    setAppendLendOpen(false)
  }, [open])

  // 往来时间轴:借款本金(借出/借入)+ 还款(收回借款/偿还借款)合并,按时间倒序。
  type TimelineEntry =
    | { kind: 'loan'; at: string; loan: Loan }
    | { kind: 'repayment'; at: string; loan: Loan; repayment: LoanRepayment }
  const loanById = useMemo(() => new Map(loans.map((l) => [l.id, l])), [loans])
  const timeline: TimelineEntry[] = useMemo(() => {
    const entries: TimelineEntry[] = loans.map((l) => ({ kind: 'loan', at: l.happened_at, loan: l }))
    for (const r of repayments) {
      const loan = loanById.get(r.loan_id)
      if (loan) entries.push({ kind: 'repayment', at: r.repaid_at, loan, repayment: r })
    }
    entries.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0))
    return entries
  }, [loans, repayments, loanById])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 border-b border-border/60 px-6 py-4">
          <DialogTitle className="truncate">{contact?.name}</DialogTitle>
          {contact?.note ? <DialogDescription className="line-clamp-2">{contact.note}</DialogDescription> : null}
        </DialogHeader>
        {loading && !summary ? (
          <div className="px-6 py-10 text-center text-sm text-muted-foreground">{t('notice.loading')}</div>
        ) : summary ? (
          <>
          {/* 中间区独立滚动：统计 + 资料 + 时间轴可变长，头部与底部操作保持固定可见。 */}
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-4">
            <div>
              <div className="mb-2 text-sm font-medium">{t('contacts.detail.summary')}</div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Stat label={t('contacts.detail.lendTotal')} value={formatBalanceCompact(summary.lend_total, currency)} />
                <Stat label={t('contacts.detail.borrowTotal')} value={formatBalanceCompact(summary.borrow_total, currency)} />
                <Stat label={t('contacts.detail.receivable')} value={formatBalanceCompact(summary.receivable, currency)} />
                <Stat label={t('contacts.detail.payable')} value={formatBalanceCompact(summary.payable, currency)} />
              </div>
            </div>
            {contact && normalizePartyProfile(contact.profile) ? (
              <div>
                <div className="mb-2 text-sm font-medium">{t('party.section')}</div>
                <PartyProfileView
                  profile={normalizePartyProfile(contact.profile)}
                  name={contact.name}
                  resolvePreview={resolvePreview}
                />
              </div>
            ) : null}
            <div>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium">{t('contacts.detail.timeline')}</span>
                <span className="text-xs text-muted-foreground">{t('contacts.detail.loanCount')}: {summary.loan_count}</span>
              </div>
              {timeline.length === 0 ? (
                <div className="text-sm text-muted-foreground">{t('statement.empty')}</div>
              ) : (
                <div className="divide-y divide-border rounded-lg border border-border">
                  {timeline.map((entry) =>
                    entry.kind === 'loan' ? (
                      <div
                        key={`loan-${entry.loan.id}`}
                        className={`px-3 py-2 ${entry.loan.tx_sync_id ? 'cursor-pointer transition-colors hover:bg-primary/5' : ''}`}
                        onClick={() => openTxDetailBySyncId(token, entry.loan.tx_sync_id, activeLedgerId ?? undefined, () => onOpenChange(false))}
                        title={entry.loan.tx_sync_id ? (t('deposits.detail.viewFlow') as string) : undefined}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex min-w-0 items-center gap-2">
                            {/* 借款本金:按借款方向取图标(借出/借入),颜色可由设置自定义 */}
                            {(() => {
                              const color = resolveFlowIconColor(flowIconKeyForLoan(entry.loan.direction, 'principal'), flowIconColors)
                              return (
                                <span
                                  className="inline-flex shrink-0 items-center justify-center rounded-md"
                                  style={{ width: 20, height: 20, backgroundColor: `${color}26`, color }}
                                  title={t('loans.flowIcon') as string}
                                >
                                  <CategoryIcon
                                    icon={resolveFlowIcon(flowIconKeyForLoan(entry.loan.direction, 'principal'), flowIconPrefs)}
                                    iconType="material"
                                    size={14}
                                    color={color}
                                  />
                                </span>
                              )
                            })()}
                            <div className="min-w-0">
                              <div className="font-medium">
                                <span className={entry.loan.direction === 'lend' ? 'text-emerald-600' : 'text-red-600'}>
                                  {entry.loan.direction === 'lend' ? t('loans.direction.lend') : t('loans.direction.borrow')}
                                </span>
                                {' · '}
                                {/* 逐条流水只展示原始金额,不加删除线(删除线只保留在人维度的结算上)。 */}
                                <span>{formatBalanceCompact(entry.loan.amount, currency)}</span>
                                {entry.loan.total_repaid > 0 && entry.loan.status !== 'settled' ? (
                                  <span className="ml-1 text-xs font-normal text-muted-foreground">
                                    · {t('loans.detail.remaining')} {formatBalanceCompact(Math.max(0, entry.loan.amount - entry.loan.total_repaid), currency)}
                                  </span>
                                ) : null}
                              </div>
                              <div className="text-xs text-muted-foreground">
                                {formatIsoDateTimeLocal(entry.loan.happened_at)}
                                {entry.loan.note ? ` · ${entry.loan.note}` : ''}
                              </div>
                            </div>
                          </div>
                          <span className={`shrink-0 text-xs font-semibold ${entry.loan.status === 'settled' ? 'text-emerald-600' : entry.loan.status === 'overdue' ? 'text-red-600' : 'text-muted-foreground'}`}>
                            {t(`loans.status.${entry.loan.status}`)}
                          </span>
                        </div>
                        <LoanAttachmentThumbs fileIds={entry.loan.voucher_file_ids || []} label={t('loans.field.vouchers')} />
                      </div>
                    ) : (
                      <div
                        key={`repay-${entry.repayment.id}`}
                        className={`px-3 py-2 ${entry.repayment.tx_sync_id ? 'cursor-pointer transition-colors hover:bg-primary/5' : ''}`}
                        onClick={() => openTxDetailBySyncId(token, entry.repayment.tx_sync_id, activeLedgerId ?? undefined, () => onOpenChange(false))}
                        title={entry.repayment.tx_sync_id ? (t('deposits.detail.viewFlow') as string) : undefined}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex min-w-0 items-center gap-2">
                            {/* 还款:按借款方向区分「收回借款 / 偿还借款」,颜色可由设置自定义 */}
                            {(() => {
                              const color = resolveFlowIconColor(flowIconKeyForLoan(entry.loan.direction, 'repayment'), flowIconColors)
                              return (
                                <span
                                  className="inline-flex shrink-0 items-center justify-center rounded-md"
                                  style={{ width: 20, height: 20, backgroundColor: `${color}26`, color }}
                                  title={t('loans.repayment.title') as string}
                                >
                                  <CategoryIcon
                                    icon={resolveFlowIcon(flowIconKeyForLoan(entry.loan.direction, 'repayment'), flowIconPrefs)}
                                    iconType="material"
                                    size={14}
                                    color={color}
                                  />
                                </span>
                              )
                            })()}
                            <div className="min-w-0">
                              <div className="font-medium">
                                <span className={entry.loan.direction === 'lend' ? 'text-emerald-600' : 'text-red-600'}>
                                  {entry.loan.direction === 'lend'
                                    ? t('loans.timeline.repayForLend')
                                    : t('loans.timeline.repayForBorrow')}
                                </span>
                                {' · '}{formatBalanceCompact(entry.repayment.amount, currency)}
                              </div>
                              <div className="text-xs text-muted-foreground">
                                {formatIsoDateTimeLocal(entry.repayment.repaid_at)}
                                {entry.repayment.note ? ` · ${entry.repayment.note}` : ''}
                              </div>
                            </div>
                          </div>
                        </div>
                        <LoanAttachmentThumbs fileIds={entry.repayment.attachment_file_ids || []} label={t('loans.attachment.repayment')} />
                      </div>
                    )
                  )}
                </div>
              )}
            </div>
          </div>
          {/* 底部操作栏固定可见,不随时间轴滚动。 */}
          <div className="shrink-0 border-t border-border/60 px-6 py-3">
            <div className="flex flex-wrap gap-2">
              {/* 方向由按钮固定,各自打开独立弹窗;不再跳转到借款页重复选方向。 */}
              <Button variant="outline" onClick={() => setAppendBorrowOpen(true)} disabled={!contact}>
                <HandCoins className="mr-1 h-4 w-4 text-red-600" />{t('contacts.detail.appendBorrow')}
              </Button>
              <Button variant="outline" onClick={() => setAppendLendOpen(true)} disabled={!contact}>
                <HandCoins className="mr-1 h-4 w-4 text-emerald-600" />{t('contacts.detail.appendLend')}
              </Button>
              <Button variant="outline" onClick={() => contact && onStatement()}>
                <FileText className="mr-1 h-4 w-4" />{t('contacts.detail.sendStatement')}
              </Button>
              <Button variant="ghost" onClick={() => contact && onEdit(contact)}>{t('confirm.edit')}</Button>
              {contact ? (
                <>
                  <Button variant="ghost" onClick={() => onStateChange(contact, contact.is_archived ? 'unarchive' : 'archive')}>
                    {t(contact.is_archived ? 'contacts.action.unarchive' : 'contacts.action.archive')}
                  </Button>
                  <Button variant="ghost" onClick={() => onStateChange(contact, contact.is_active ? 'disable' : 'enable')}>
                    {t(contact.is_active ? 'contacts.action.disable' : 'contacts.action.enable')}
                  </Button>
                </>
              ) : null}
            </div>
          </div>
          </>
        ) : null}
      </DialogContent>
      {/* 子 Dialog 放在 DialogContent 外、父 Dialog 内,与借款页追加/还款弹窗同模式。 */}
      <ContactQuickLoanDialog
        open={appendBorrowOpen}
        contact={contact}
        direction="borrow"
        currency={currency}
        currentBalance={summary?.payable ?? 0}
        onOpenChange={setAppendBorrowOpen}
        onSaved={() => { setRefreshKey((k) => k + 1); onChanged?.() }}
      />
      <ContactQuickLoanDialog
        open={appendLendOpen}
        contact={contact}
        direction="lend"
        currency={currency}
        currentBalance={summary?.receivable ?? 0}
        onOpenChange={setAppendLendOpen}
        onSaved={() => { setRefreshKey((k) => k + 1); onChanged?.() }}
      />
    </Dialog>
  )
}

export function ContactsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { currency, activeLedgerId } = useLedgers()
  const { resolvePreview } = usePartyProfileIo()
  const [searchParams, setSearchParams] = useSearchParams()
  const [contacts, setContacts] = useState<Contact[]>([])
  const contactsRef = useRef<Contact[]>(contacts)
  useEffect(() => { contactsRef.current = contacts }, [contacts])
  /** 按 contact.id 索引的借款汇总;null = 排行接口不可用(隐藏排行面板)。 */
  const [ranksById, setRanksById] = useState<Record<string, ContactLoanRank> | null>(null)
  const [search, setSearch] = useState('')
  const [tagFilter, setTagFilter] = useState<ContactTag | 'all'>('all')
  /** 归档/停用筛选,默认只看未归档(含已停用,由卡片标记)。 */
  const [stateFilter, setStateFilter] = useState<ContactState>('active')
  const [loading, setLoading] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Contact | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [detail, setDetail] = useState<Contact | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [statementOpen, setStatementOpen] = useState(false)
  const [statementContact, setStatementContact] = useState<Contact | null>(null)
  const [statementLoans, setStatementLoans] = useState<Loan[]>([])

  const load = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      // 排行是「锦上添花」的统计维度:失败只隐藏排行,不能把联系人列表带崩。
      const [list, ranks] = await Promise.all([
        fetchContacts(token, undefined, stateFilter),
        fetchContactLoanRanks(token).catch(() => null),
      ])
      setContacts(list)
      if (ranks === null) {
        setRanksById(null)
      } else {
        const next: Record<string, ContactLoanRank> = {}
        for (const row of ranks) next[row.contact_id] = row
        setRanksById(next)
      }
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setLoading(false)
    }
  }, [token, toast, t, stateFilter])

  useEffect(() => { void load() }, [load])
  useSyncRefresh(() => { void load() })

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return contacts.filter((c) => {
      if (tagFilter !== 'all' && c.contact_tag !== tagFilter) return false
      if (q && !c.name.toLowerCase().includes(q)) return false
      return true
    })
  }, [contacts, search, tagFilter])

  const openCreate = () => { setEditing(null); setFormOpen(true) }
  const openEdit = (c: Contact) => { setEditing(c); setFormOpen(true) }
  const openDetail = (c: Contact) => { setDetail(c); setDetailOpen(true) }
  /** 按 id 打开联系人详情(0047):交易详情「关联记录」跳回联系人时用。
   *  列表可能还在异步加载,查不到时短暂重试几次。 */
  const openDetailById = useCallback((id: string) => {
    if (!id) return
    const found = contacts.find((c) => c.id === id)
    if (found) {
      setDetail(found)
      setDetailOpen(true)
      return
    }
    // 列表未就绪:轮询几次等待 load() 完成(最多约 1.5s)。
    let tries = 0
    const timer = window.setInterval(() => {
      tries += 1
      const c = contactsRef.current.find((x) => x.id === id)
      if (c) {
        setDetail(c)
        setDetailOpen(true)
        window.clearInterval(timer)
      } else if (tries >= 10) {
        window.clearInterval(timer)
      }
    }, 150)
  }, [contacts])

  // 交易详情「关联记录」跳回联系人:URL 参数(跨页/可分享) + 事件(同页兜底)。
  useEffect(() => {
    const fromUrl = searchParams.get('contact_id')
    if (fromUrl) {
      openDetailById(fromUrl)
      const next = new URLSearchParams(searchParams)
      next.delete('contact_id')
      setSearchParams(next, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  useEffect(() => {
    return onOpenDetailContact((id) => openDetailById(id))
  }, [openDetailById])
  const onStatement = () => {
    if (!detail || !token) return
    setStatementContact(detail)
    const loanPromise = activeLedgerId
      ? fetchLoans(token, { ledger_id: activeLedgerId, contact_id: detail.id })
      : Promise.resolve([])
    loanPromise
      .then((ls) => setStatementLoans(ls))
      .catch(() => setStatementLoans([]))
      .finally(() => setStatementOpen(true))
  }

  /** 归档/恢复/停用/启用:切换后同步本地对象(详情弹窗即时反映)并重拉排行。 */
  const onStateChange = async (c: Contact, action: ContactStateAction) => {
    if (!token) return
    const actions = {
      archive: archiveContact,
      unarchive: unarchiveContact,
      disable: disableContact,
      enable: enableContact,
    }
    try {
      const next = await actions[action](token, c.id)
      toast.success(t(`contacts.notice.${action}`), t('notice.success'))
      setContacts((prev) => prev.map((x) => (x.id === next.id ? next : x)))
      setDetail((prev) => (prev?.id === next.id ? next : prev))
      setEditing((prev) => (prev?.id === next.id ? next : prev))
      await load()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    }
  }

  const onDelete = async (c: Contact) => {
    if (!token) return
    try {
      await deleteContact(token, c.id)
      toast.success(t('contacts.notice.deleted'), t('notice.success'))
      await load()
    } catch (err) {
      const msg = localizeError(err, t)
      // 后端 409 带关联借款数,提取展示友好文案
      const m = msg.match(/(\d+)/)
      if (m) toast.error(t('contacts.error.deleteLinkedLoans').replace('{count}', m[1]), t('notice.error'))
      else toast.error(msg, t('notice.error'))
    }
  }

  return (
    <div className="space-y-4">
      <SectionPageHeader
        section="contacts"
        title={t('contacts.title')}
        subtitle={t('contacts.subtitle')}
        actions={
          <>
            <Button variant="outline" onClick={() => setImportOpen(true)}>
              <UserPlus className="mr-1 h-4 w-4" />{t('contacts.fromTransactions')}
            </Button>
            <Button onClick={openCreate}>
              <Plus className="mr-1 h-4 w-4" />{t('contacts.button.create')}
            </Button>
          </>
        }
      />

      {/* 借款排行:跟成员收支排行同版式,默认跨全部账本;点击行打开该联系人详情。 */}
      <ContactLoanRanks
        contacts={contacts}
        ranksById={ranksById}
        currency={currency}
        onPick={openDetail}
      />

      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="pl-9" value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('contacts.search')} />
      </div>

      <div className="flex flex-wrap gap-2">
        <Chip active={tagFilter === 'all'} onClick={() => setTagFilter('all')}>{t('contacts.tab.all')}</Chip>
        {TAGS.map((tg) => (
          <Chip key={tg} active={tagFilter === tg} onClick={() => setTagFilter(tg)}>{t(`contacts.tab.${tg}`)}</Chip>
        ))}
      </div>

      {/* 状态筛选:归档与停用互相独立,默认只看未归档。 */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">{t('contacts.filter.status')}</span>
        {(['active', 'archived', 'disabled', 'all'] as ContactState[]).map((s) => (
          <Chip key={s} active={stateFilter === s} onClick={() => setStateFilter(s)}>
            {t(`contacts.state.${s}`)}
          </Chip>
        ))}
      </div>

      {loading && contacts.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground">{t('notice.loading')}</div>
      ) : filtered.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border py-12 text-center">
          <div className="font-medium">{t('contacts.empty.title')}</div>
          <div className="mt-1 text-sm text-muted-foreground">{t('contacts.empty.desc')}</div>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filtered.map((c) => (
            <ContactCard
              key={c.id}
              contact={c}
              rank={ranksById?.[c.id]}
              currency={currency}
              onOpen={openDetail}
              onEdit={openEdit}
              onDelete={(contact) => void onDelete(contact)}
              onStateChange={(contact, action) => void onStateChange(contact, action)}
              resolvePreview={resolvePreview}
            />
          ))}
        </div>
      )}

      <ContactFormDialog open={formOpen} editing={editing} onOpenChange={setFormOpen} onSaved={() => void load()} />
      <ImportDialog open={importOpen} onOpenChange={setImportOpen} onSaved={() => void load()} />
      <ContactDetailDialog
        open={detailOpen}
        contact={detail}
        currency={currency}
        activeLedgerId={activeLedgerId}
        onOpenChange={setDetailOpen}
        onEdit={openEdit}
        onStatement={onStatement}
        onStateChange={(contact, action) => void onStateChange(contact, action)}
        onChanged={() => void load()}
      />
      <StatementDialog open={statementOpen} contact={statementContact} loans={statementLoans} currency={currency} onOpenChange={setStatementOpen} />
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
