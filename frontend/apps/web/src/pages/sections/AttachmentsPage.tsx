import { useEffect, useMemo, useRef, useState } from 'react'
import { downloadAttachmentThumbnail, fetchGalleryAttachments, fetchWorkspaceCategories, type GalleryAttachment, type GalleryAttachmentPage, type WorkspaceCategory } from '@beecount/api-client'
import { Button, Input, Pagination, useLocale, useT } from '@beecount/ui'
import { CategoryPickerDialog } from '@beecount/web-features'
import { FileImage, ImageOff, Search, SlidersHorizontal } from 'lucide-react'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { useAttachmentPreview } from '../../context/AttachmentPreviewContext'
import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { YearMonthPicker } from '../../components/YearMonthPicker'
import { attachmentDateRange } from '../../lib/attachmentGallery'

function Thumbnail({ item }: { item: GalleryAttachment }) {
  const { token } = useAuth()
  const t = useT()
  const host = useRef<HTMLDivElement>(null)
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    setUrl(''); setError('')
    if (!host.current || item.status !== 'ready' || !item.attachment.cloudFileId) return
    const controller = new AbortController()
    let objectUrl = ''
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return
      observer.disconnect()
      void downloadAttachmentThumbnail(token, item.attachment.cloudFileId!, controller.signal).then(blob => {
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(blob); setUrl(objectUrl)
      }).catch(err => { if (!controller.signal.aborted) setError(err?.status === 404 ? 'missing' : err?.status === 415 ? 'unsupported' : 'failed') })
    }, { rootMargin: '180px' })
    observer.observe(host.current)
    return () => { observer.disconnect(); controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [item.attachment.cloudFileId, item.status, token])
  const status = item.status === 'ready' ? error : item.status
  return <div ref={host} className="relative flex aspect-[4/3] items-center justify-center overflow-hidden bg-muted/45">
    {url ? <img src={url} alt={item.attachment.originalName || item.attachment.fileName} className="h-full w-full object-contain p-2 transition-transform group-hover:scale-[1.03]" /> : <div className="flex flex-col items-center gap-2 p-4 text-center text-xs text-muted-foreground">{status ? <ImageOff className="h-6 w-6" /> : <FileImage className="h-6 w-6 animate-pulse" />}<span>{status ? t(`attachments.${status === 'pending' ? 'pendingShort' : status === 'missing' ? 'missingShort' : status}`) : t('attachments.loading')}</span></div>}
    {item.transactions.length > 1 ? <span className="absolute right-2 top-2 rounded-md border border-border/50 bg-card px-2 py-1 text-[11px]">{t('attachments.references', { count: item.transactions.length })}</span> : null}
  </div>
}

export function AttachmentsPage() {
  const { token } = useAuth()
  const { activeLedgerId, currentLedger, currency } = useLedgers()
  const { locale } = useLocale()
  const t = useT()
  const preview = useAttachmentPreview()
  const { previewMap, ensureLoadedMany } = useAttachmentCache()
  const [filters, setFilters] = useState({ q: '', categoryId: '', categoryName: '', year: null as number | null, month: null as number | null, page: 1, pageSize: 40 })
  const [search, setSearch] = useState('')
  const [categories, setCategories] = useState<WorkspaceCategory[]>([])
  const [categoryKind, setCategoryKind] = useState<'expense' | 'income'>('expense')
  const [categoryOpen, setCategoryOpen] = useState(false)
  const [result, setResult] = useState<{ key: string; data: GalleryAttachmentPage } | null>(null)
  const [error, setError] = useState(false)
  const [revision, setRevision] = useState(0)
  const queryKey = JSON.stringify([activeLedgerId, filters])
  const data = result?.key === queryKey ? result.data : null
  useSyncRefresh(() => setRevision(v => v + 1))
  useEffect(() => { const timer = setTimeout(() => setFilters(f => ({ ...f, q: search, page: 1 })), 250); return () => clearTimeout(timer) }, [search])
  useEffect(() => { setFilters(f => ({ ...f, categoryId: '', categoryName: '', page: 1 })); setCategories([]) }, [activeLedgerId])
  useEffect(() => {
    if (!activeLedgerId) return
    let alive = true
    void fetchWorkspaceCategories(token, { ledgerId: activeLedgerId, limit: 2000 }).then(rows => { if (alive) setCategories(rows) }).catch(() => { if (alive) setCategories([]) })
    return () => { alive = false }
  }, [activeLedgerId, token, revision])
  useEffect(() => { ensureLoadedMany(categories.map(c => c.icon_cloud_file_id || '').filter(Boolean)) }, [categories, ensureLoadedMany])
  useEffect(() => {
    if (!activeLedgerId) return
    let alive = true
    setError(false)
    void fetchGalleryAttachments(token, { ledgerId: activeLedgerId, q: filters.q, categorySyncId: filters.categoryId, ...attachmentDateRange(filters.year, filters.month), limit: filters.pageSize, offset: (filters.page - 1) * filters.pageSize }).then(page => {
      if (!alive) return
      if (filters.page > 1 && (filters.page - 1) * filters.pageSize >= page.total) { setFilters(f => ({ ...f, page: Math.max(1, Math.ceil(page.total / f.pageSize)) })); return }
      setResult({ key: queryKey, data: page })
    }).catch(() => { if (alive) setError(true) })
    return () => { alive = false }
  }, [queryKey, token, revision])
  const groups = useMemo(() => {
    const months = new Map<string, GalleryAttachment[]>()
    for (const item of data?.items || []) {
      const date = new Date(item.happened_at)
      const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
      months.set(key, [...(months.get(key) || []), item])
    }
    return [...months.entries()]
  }, [data])
  const reset = () => { setSearch(''); setFilters(f => ({ ...f, q: '', categoryId: '', categoryName: '', year: null, month: null, page: 1 })) }
  return <section className="space-y-5" data-testid="attachments-page">
    <div className="flex flex-wrap items-start justify-between gap-3 pt-3"><div><h1 className="text-xl font-semibold">{t('attachments.title')}</h1><p className="mt-1 text-sm text-muted-foreground">{currentLedger?.ledger_name || t('attachments.subtitle')}</p></div></div>
    <div className="flex flex-wrap gap-2"><label className="relative min-w-0 flex-[1_1_240px]"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input type="search" className="pl-9" aria-label={t('attachments.search')} placeholder={t('attachments.search')} value={search} onChange={e => setSearch(e.target.value)} /></label><YearMonthPicker year={filters.year} month={filters.month} onChange={(year, month) => setFilters(f => ({ ...f, year, month, page: 1 }))} /><Button variant="outline" onClick={() => setCategoryOpen(true)}><SlidersHorizontal className="mr-2 h-4 w-4" />{filters.categoryName || t('attachments.allCategories')}</Button>{filters.q || filters.categoryId || filters.year !== null ? <Button variant="ghost" onClick={reset}>{t('attachments.clear')}</Button> : null}</div>
    {!activeLedgerId ? <p className="py-16 text-center text-sm text-muted-foreground">{t('attachments.noLedger')}</p> : error ? <div role="alert" className="py-16 text-center"><p className="text-sm text-destructive">{t('attachments.failed')}</p><Button variant="outline" className="mt-3" onClick={() => setRevision(v => v + 1)}>{t('attachments.retry')}</Button></div> : !data ? <p role="status" className="py-16 text-center text-sm text-muted-foreground">{t('attachments.loading')}</p> : <>
      <p className="text-xs text-muted-foreground" aria-live="polite">{t('attachments.count', { count: data.total, transactions: data.transaction_total })}</p>
      {data.items.length === 0 ? <div className="rounded-xl border border-dashed border-border py-20 text-center"><FileImage className="mx-auto mb-3 h-8 w-8 text-muted-foreground" /><p className="text-sm text-muted-foreground">{t(filters.q || filters.categoryId || filters.year !== null ? 'attachments.noMatches' : 'attachments.empty')}</p></div> : groups.map(([month, items]) => <section key={month} className="space-y-3"><h2 className="text-sm font-medium">{new Intl.DateTimeFormat(locale, { year: 'numeric', month: 'long' }).format(new Date(`${month}-01T12:00:00`))}</h2><div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">{items.map(item => {
        const tx = item.transactions[0]
        const name = item.attachment.originalName || item.attachment.fileName
        return <button key={item.key} type="button" className="group min-w-0 overflow-hidden rounded-xl border border-border/60 bg-card text-left transition-colors hover:border-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`${t('transactions.attachment.preview')} ${name}`} onClick={() => void preview(data.items.map(row => row.attachment), data.items.indexOf(item), data.items)}>
          <Thumbnail item={item} /><div className="space-y-2 p-3"><p className="truncate text-xs font-medium" title={name}>{name}</p><div className="flex flex-wrap items-center justify-between gap-1 text-[11px] text-muted-foreground"><span>{new Date(item.happened_at).toLocaleDateString(locale, { month: '2-digit', day: '2-digit' })} · {tx?.category_name || t('common.none')}</span><span>{item.attachment.fileSize ? `${(item.attachment.fileSize / 1024).toFixed(0)} KB` : ''}</span></div><div className="flex flex-wrap items-center justify-between gap-1 text-xs"><span className="text-muted-foreground">{item.status === 'ready' ? '' : t(`attachments.${item.status}Short`)}</span>{item.transactions.length === 1 && tx ? <span className={`tabular-nums ${tx.tx_type === 'expense' ? 'text-expense' : tx.tx_type === 'income' ? 'text-income' : ''}`}>{tx.tx_type === 'expense' ? '−' : tx.tx_type === 'income' ? '+' : ''}{new Intl.NumberFormat(locale, { style: 'currency', currency: tx.currency_code || currency }).format(tx.amount)}</span> : <span>{t('attachments.references', { count: item.transactions.length })}</span>}</div></div>
        </button>
      })}</div></section>)}
      {data.total > 0 ? <Pagination page={filters.page} pageSize={filters.pageSize} total={data.total} pageSizes={[20, 40, 80]} onPageChange={page => setFilters(f => ({ ...f, page }))} onPageSizeChange={pageSize => setFilters(f => ({ ...f, pageSize, page: 1 }))} /> : null}
    </>}
    <CategoryPickerDialog open={categoryOpen} onClose={() => setCategoryOpen(false)} kind={categoryKind} onKindChange={setCategoryKind} rows={categories} iconPreviewUrlByFileId={previewMap} selectedId={filters.categoryId} title={t('shell.filter.category')} onSelect={cat => setFilters(f => ({ ...f, categoryId: cat.id, categoryName: cat.name, page: 1 }))} onClear={() => setFilters(f => ({ ...f, categoryId: '', categoryName: '', page: 1 }))} clearLabel={t('attachments.allCategories')} />
  </section>
}
