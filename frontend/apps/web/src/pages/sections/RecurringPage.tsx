import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Plus } from 'lucide-react'
import { ApiError, changeRecurringRule, fetchRecurringRules, fetchSharedResources, type RecurringRuleList,
  type RecurringRuleRow, type RuleConfig, type SharedResourcesBundle } from '@beecount/api-client'
import { Button, Dialog, DialogContent, DialogDescription,
  DialogFooter, DialogHeader, DialogTitle, Input, useT } from '@beecount/ui'
import { RecurringScheduleList } from '../../components/RecurringScheduleList'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'

const selectClass = 'h-10 w-full rounded-md border border-input bg-background px-3 text-sm'
type Tx = RuleConfig['transaction']
type Draft = { row?: RecurringRuleRow; clientId: string; rule: RuleConfig }

function Field({ title, children }: { title: string; children: ReactNode }) {
  return <label className="grid min-w-0 gap-1.5 text-sm font-medium">{title}{children}</label>
}

function newId() {
  // getRandomValues also works on a self-hosted HTTP LAN origin.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('')
  return [hex.slice(0,8), hex.slice(8,12), hex.slice(12,16), hex.slice(16,20), hex.slice(20)].join('-')
}

function formatDue(value: string | null, zone: string) {
  return value ? new Intl.DateTimeFormat(undefined, { timeZone: zone, dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
}

export default function RecurringPage() {
  const t = useT()
  const { activeLedgerId, currency } = useLedgers()
  if (!activeLedgerId) return <p>{t('shell.selectLedgerFirst')}</p>
  return <LedgerRules key={activeLedgerId} ledger={activeLedgerId} currency={currency} />
}

function LedgerRules({ ledger, currency }: { ledger: string; currency: string }) {
  const t = useT()
  const { token } = useAuth()
  const [list, setList] = useState<RecurringRuleList>()
  const [resources, setResources] = useState<SharedResourcesBundle>()
  const [draft, setDraft] = useState<Draft>()
  const [preview, setPreview] = useState<string[]>()
  const [deleting, setDeleting] = useState<RecurringRuleRow>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const loading = useRef(0)
  const showError = (e: unknown) => setError(e instanceof ApiError && e.status === 409
    ? t('recurring.conflict') : e instanceof Error ? e.message : String(e))
  const refresh = useCallback(async () => {
    const version = ++loading.current
    try {
      const response = await fetchRecurringRules(token, ledger)
      if (version === loading.current) setList(response)
    } catch (e) {
      if (version === loading.current) setError(e instanceof Error ? e.message : String(e))
    }
  }, [token, ledger])
  useEffect(() => {
    void refresh()
    const interval = window.setInterval(() => { if (!document.hidden) void refresh() }, 15000)
    window.addEventListener('focus', refresh)
    return () => { loading.current++; clearInterval(interval); window.removeEventListener('focus', refresh) }
  }, [refresh])
  useSyncRefresh(refresh)

  async function open(row?: RecurringRuleRow) {
    setError('')
    setPreview(undefined)
    setBusy(true)
    try {
      setResources(await fetchSharedResources(token, ledger))
      const now = new Date()
      const today = [now.getFullYear(), String(now.getMonth()+1).padStart(2,'0'), String(now.getDate()).padStart(2,'0')].join('-')
      setDraft({ row, clientId: newId(), rule: row ? { ...row.data.rule, enabled: row.status !== 'paused' } : {
        name: '', transaction: { tx_type: 'expense', amount: 0 }, frequency: 'monthly', interval: 1,
        starts_at: today, time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || list!.time_zone, enabled: true,
      } })
    } catch (e) { showError(e) } finally { setBusy(false) }
  }
  function edit(value: Partial<RuleConfig>) {
    if (!draft) return
    setPreview(undefined)
    setDraft({ ...draft, rule: { ...draft.rule, ...value } })
  }
  function editTx(value: Partial<Tx>) {
    if (draft) edit({ transaction: { ...draft.rule.transaction, ...value } })
  }
  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!draft || busy) return
    setBusy(true); setError('')
    try {
      const result = await changeRecurringRule(token, ledger, {
        action: draft.row ? 'update' : 'create', client_id: draft.clientId, record_id: draft.row?.id,
        expected_revision: draft.row?.revision, data: draft.rule, confirm: preview !== undefined,
      })
      if (!preview) setPreview(result.next_occurrences || [])
      else { setDraft(undefined); await refresh() }
    } catch (e) { showError(e); setPreview(undefined); await refresh() } finally { setBusy(false) }
  }
  async function change(row: RecurringRuleRow, action: 'pause' | 'resume' | 'delete') {
    if (busy) return
    setBusy(true); setError('')
    try {
      await changeRecurringRule(token, ledger, { action, record_id: row.id, expected_revision: row.revision, confirm: true })
      setDeleting(undefined)
    } catch (e) { showError(e) } finally { await refresh(); setBusy(false) }
  }

  const config = draft?.rule
  const tx = config?.transaction
  function resource(field: 'category' | 'account' | 'from_account' | 'to_account') {
    if (!tx || !resources) return null
    const id = `${field}_id` as const
    const rows = field === 'category' ? resources.categories.filter(r => r.kind === tx.tx_type) : resources.accounts
    const value = tx[id] || rows.find(r => r.name === tx[field])?.sync_id || ''
    return <Field title={t(`recurring.${field}`)}><select className={selectClass} value={value}
      required={field === 'from_account' || field === 'to_account'}
      onChange={e => editTx({ [id]: e.target.value || null, [field]: null })}>
      <option value="">—</option>
      {value && !rows.some(r => r.sync_id === value) && <option value={value}>{tx[field] || value}</option>}
      {rows.map(r => <option key={r.sync_id} value={r.sync_id}>{r.name}</option>)}
    </select></Field>
  }
  const errorText = error && <p role="alert" className="text-sm text-destructive">{error}</p>
  return <section className="mx-auto max-w-5xl py-6">
    <header className="mb-5 flex items-center justify-between gap-3">
      <div><h1 className="text-3xl font-semibold tracking-tight">{t('nav.recurring')}</h1>
        {list && <p className="mt-1 flex items-center gap-2 text-xs text-muted-foreground" role="status">
          <span className={`h-2 w-2 rounded-full ${list.scheduler_enabled ? 'bg-emerald-600' : 'bg-amber-500'}`} />
          {t(list.scheduler_enabled ? 'recurring.owner' : 'recurring.disabled')}
        </p>}
      </div>
      <Button disabled={!list?.can_create || busy} onClick={() => void open()}><Plus size={16} />{t('recurring.new')}</Button>
    </header>
    {!draft && !deleting && errorText}
    {list ? <RecurringScheduleList ledger={ledger} currency={currency} items={list.items} busy={busy}
      onEdit={row => void open(row)} onToggle={row => void change(row,row.status === 'paused' ? 'resume' : 'pause')}
      onDelete={row => {setError('');setDeleting(row)}} /> : !error && <p role="status">{t('common.loading')}</p>}
    <Dialog open={!!draft} onOpenChange={v => { if (!v && !busy) {setDraft(undefined);setError('')} }}>
      <DialogContent className="max-h-[88dvh] overflow-auto">
        <DialogHeader><DialogTitle>{t(draft?.row ? 'common.edit' : 'recurring.new')}</DialogTitle>
          <DialogDescription>{t('recurring.cloud')}</DialogDescription></DialogHeader>
        {config && tx && <form onSubmit={submit} className="space-y-4"><fieldset disabled={busy} className="grid grid-cols-2 gap-3">
          <div className="col-span-2"><Field title={t('recurring.name')}><Input required maxLength={255} value={config.name} onChange={e => edit({name:e.target.value})}/></Field></div>
          <Field title={t('recurring.type')}><select className={selectClass} value={tx.tx_type} onChange={e => editTx({
            tx_type:e.target.value as Tx['tx_type'],category:null,category_id:null,account:null,account_id:null,
            from_account:null,from_account_id:null,to_account:null,to_account_id:null,
          })}>{['expense','income','transfer'].map(k => <option key={k} value={k}>{t(`recurring.${k}`)}</option>)}</select></Field>
          <Field title={t('recurring.amount')}><Input type="number" required step="any" min="0.00000001" value={tx.amount || ''} onChange={e => {
            const amount=Number(e.target.value)
            editTx({amount, ...(tx.native_amount && tx.amount ? {native_amount:tx.native_amount * amount / tx.amount} : {})})
          }}/></Field>
          <Field title={t('import.mapping.field.currency')}><Input required pattern="[A-Za-z]{3}" maxLength={3} value={tx.currency ?? currency}
            onChange={e => editTx({currency:e.target.value.toUpperCase(),native_amount:null})}/></Field>
          {tx.tx_type === 'transfer' ? <>{resource('from_account')}{resource('to_account')}</> : <>{resource('category')}{resource('account')}</>}
          <Field title={t('recurring.frequency')}><select className={selectClass} value={config.frequency} onChange={e => edit({frequency:e.target.value as RuleConfig['frequency'],day_of_month:null,day_of_week:null,month_of_year:null})}>
            {['daily','weekly','monthly','yearly'].map(k => <option key={k} value={k}>{t(`recurring.${k}`)}</option>)}</select></Field>
          <Field title={t('recurring.interval')}><Input type="number" min={1} max={365} required value={config.interval || ''} onChange={e => edit({interval:Number(e.target.value)})}/></Field>
          <Field title={t('recurring.start')}><Input required type={config.starts_at.length > 10 ? 'text' : 'date'} value={config.starts_at} onChange={e => edit({starts_at:e.target.value})}/></Field>
          <Field title={t('recurring.end')}><Input type={(config.ends_at?.length ?? 0)>10 ? 'text' : 'date'} value={config.ends_at || ''} onChange={e => edit({ends_at:e.target.value || null})}/></Field>
          {config.frequency === 'weekly' && <Field title={t('recurring.weekDay')}><select className={selectClass}
            value={config.day_of_week ?? (new Date(config.starts_at.slice(0,10)+'T12:00:00Z').getUTCDay() || 7)}
            onChange={e => edit({day_of_week:Number(e.target.value)})}>{[1,2,3,4,5,6,7].map(day =>
              <option key={day} value={day}>{new Intl.DateTimeFormat(undefined,{weekday:'long',timeZone:'UTC'}).format(Date.UTC(2024,0,day))}</option>)}</select></Field>}
          {config.frequency === 'yearly' && <Field title={t('recurring.yearMonth')}><Input type="number" min={1} max={12}
            value={config.month_of_year ?? Number(config.starts_at.slice(5,7))} onChange={e => edit({month_of_year:Number(e.target.value) || null})}/></Field>}
          {(config.frequency === 'monthly' || config.frequency === 'yearly') && <Field title={t('recurring.monthDay')}><Input type="number" min={1} max={31} value={config.day_of_month ?? Number(config.starts_at.slice(8,10))} onChange={e => edit({day_of_month:Number(e.target.value) || null})}/></Field>}
          <Field title={t('recurring.zone')}><Input required value={config.time_zone} onChange={e => edit({time_zone:e.target.value})}/></Field>
          <div className="col-span-2"><Field title={t('recurring.note')}><Input value={tx.note || ''} onChange={e => editTx({note:e.target.value})}/></Field></div>
        </fieldset>
        {preview && <div className="rounded-lg bg-muted p-3"><strong>{t('recurring.next')}</strong><ul>{preview.slice(0,3).map(d => <li key={d}>{formatDue(d,config.time_zone)}</li>)}</ul>{!preview.length && t('recurring.completed')}</div>}
        {errorText}<DialogFooter><Button type="button" variant="ghost" disabled={busy} onClick={() => {setDraft(undefined);setError('')}}>{t('common.cancel')}</Button>
          <Button disabled={busy}>{t(preview ? 'common.save' : 'recurring.preview')}</Button></DialogFooter></form>}
      </DialogContent>
    </Dialog>
    <Dialog open={!!deleting} onOpenChange={v => {if (!v && !busy) setDeleting(undefined)}}>
      <DialogContent><DialogTitle>{t('common.delete')} · {deleting?.data.rule.name}</DialogTitle><DialogDescription>{t('recurring.deleteHint')}</DialogDescription>{errorText}
        <DialogFooter><Button variant="ghost" disabled={busy} onClick={() => setDeleting(undefined)}>{t('common.cancel')}</Button>
          <Button variant="destructive" disabled={busy} onClick={() => deleting && void change(deleting,'delete')}>{t('common.delete')}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </section>
}
