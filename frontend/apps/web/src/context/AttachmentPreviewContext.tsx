import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { downloadAttachment, fetchWorkspaceTransactions, type AttachmentRef, type GalleryAttachment } from '@beecount/api-client'
import { Button, Dialog, DialogContent, DialogHeader, DialogTitle, useLocale, useT } from '@beecount/ui'
import { ChevronLeft, ChevronRight, Download, FileWarning, RotateCw, ZoomIn, ZoomOut } from 'lucide-react'
import { useAuth } from './AuthContext'
import { useLedgers } from './LedgersContext'
import { useSyncEvent } from './SyncSocketContext'
import { dispatchOpenDetailTx } from '../lib/txDialogEvents'

type OpenPreview = (refs: AttachmentRef[], startIndex?: number, items?: GalleryAttachment[]) => Promise<void>
const Context = createContext<OpenPreview | null>(null)

export function AttachmentPreviewProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth()
  const { activeLedgerId, currency } = useLedgers()
  const { locale } = useLocale()
  const t = useT()
  const [selection, setSelection] = useState<{ refs: AttachmentRef[]; index: number; items?: GalleryAttachment[] } | null>(null)
  const [image, setImage] = useState<{ url: string; blob: Blob; width: number; height: number } | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [zoom, setZoom] = useState(1)
  const [rotation, setRotation] = useState(0)
  const [retry, setRetry] = useState(0)
  const [loading, setLoading] = useState(false)
  const [openingTx, setOpeningTx] = useState(false)
  const canvas = useRef<HTMLDivElement>(null)
  const [viewport, setViewport] = useState({ width: 500, height: 430 })
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null)
  const current = selection?.refs[selection.index]
  const item = selection?.items?.[selection.index]
  const name = current?.originalName || current?.fileName || t('attachment.default')
  const open = useCallback<OpenPreview>(async (refs, startIndex = 0, items) => {
    if (!refs.length) return
    setSelection({ refs, index: Math.max(0, Math.min(startIndex, refs.length - 1)), items })
  }, [])

  useEffect(() => { setSelection(null) }, [activeLedgerId, token])
  useSyncEvent('member_change', () => setSelection(null))
  useEffect(() => {
    if (!selection || !canvas.current) return
    const node = canvas.current
    const observer = new ResizeObserver(() => setViewport({ width: node.clientWidth, height: node.clientHeight }))
    observer.observe(node)
    return () => observer.disconnect()
  }, [Boolean(selection)])

  useEffect(() => {
    setImage(null); setError(''); setNotice(''); setZoom(1); setRotation(0)
    if (!current) return
    if (!current.cloudFileId) { setError('pending'); setLoading(false); return }
    if (item?.status === 'missing') { setError('missing'); setLoading(false); return }
    const controller = new AbortController()
    let url = ''
    setLoading(true)
    void downloadAttachment(token, current.cloudFileId, controller.signal).then(result => {
      if (controller.signal.aborted) return
      url = URL.createObjectURL(result.blob)
      const probe = new Image()
      probe.onload = () => {
        if (controller.signal.aborted) return
        setImage({ url, blob: result.blob, width: probe.naturalWidth, height: probe.naturalHeight }); setLoading(false)
      }
      probe.onerror = () => { if (!controller.signal.aborted) { setError('unsupported'); setLoading(false) } }
      probe.src = url
    }).catch(err => {
      if (!controller.signal.aborted) { setError(err?.status === 404 ? 'missing' : 'failed'); setLoading(false) }
    })
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url) }
  }, [current, item?.status, token, retry])

  const move = (delta: number) => setSelection(s => !s ? null : ({ ...s, index: Math.max(0, Math.min(s.index + delta, s.refs.length - 1)) }))
  const download = async () => {
    if (!current?.cloudFileId) return
    try {
      const blob = image?.blob || (await downloadAttachment(token, current.cloudFileId)).blob
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a'); link.href = url; link.download = name; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch { setNotice(t('attachments.downloadFailed')) }
  }
  const viewTransaction = async (id: string, ledgerId: string) => {
    setOpeningTx(true); setNotice('')
    try {
      const page = await fetchWorkspaceTransactions(token, { ledgerId, txSyncId: id, limit: 1 })
      if (!page.items[0]) throw new Error('Missing transaction')
      dispatchOpenDetailTx(page.items[0])
    } catch { setNotice(t('attachments.transactionFailed')) }
    finally { setOpeningTx(false) }
  }
  const turned = rotation % 180 !== 0
  const naturalWidth = image ? (turned ? image.height : image.width) : 1
  const naturalHeight = image ? (turned ? image.width : image.height) : 1
  const scale = Math.min((viewport.width - 32) / naturalWidth, (viewport.height - 32) / naturalHeight, 1) * zoom
  return <Context.Provider value={open}>
    {children}
    <Dialog open={Boolean(selection)} onOpenChange={value => { if (!value) setSelection(null) }}>
      <DialogContent className="max-h-[92dvh] max-w-6xl overflow-y-auto" onKeyDown={event => {
        if (event.key === 'ArrowLeft') { event.preventDefault(); move(-1) }
        if (event.key === 'ArrowRight') { event.preventDefault(); move(1) }
      }}>
        <DialogHeader><DialogTitle className="break-all pr-8 text-base">{name}</DialogTitle></DialogHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" aria-label={t('transactions.attachment.prev')} disabled={!selection || selection.index === 0} onClick={() => move(-1)}><ChevronLeft className="h-4 w-4" /></Button>
          <span className="text-xs tabular-nums text-muted-foreground">{(selection?.index ?? 0) + 1} / {selection?.refs.length ?? 0}</span>
          <Button size="sm" variant="outline" aria-label={t('transactions.attachment.next')} disabled={!selection || selection.index === selection.refs.length - 1} onClick={() => move(1)}><ChevronRight className="h-4 w-4" /></Button>
        </div>
        <div className={`grid min-w-0 gap-5 ${item ? 'lg:grid-cols-[minmax(0,1fr)_280px]' : ''}`}>
          <div className="min-w-0">
            <div ref={canvas} data-testid="attachment-preview-canvas" className="h-[52dvh] min-h-[260px] overflow-auto rounded-xl border border-border/60 bg-muted/40 touch-pan-x touch-pan-y" onPointerDown={event => {
              if (event.pointerType === 'touch' || !image || zoom <= 1) return
              const node = event.currentTarget
              drag.current = { x: event.clientX, y: event.clientY, left: node.scrollLeft, top: node.scrollTop }
              node.setPointerCapture(event.pointerId)
            }} onPointerMove={event => {
              if (!drag.current) return
              event.currentTarget.scrollLeft = drag.current.left - event.clientX + drag.current.x
              event.currentTarget.scrollTop = drag.current.top - event.clientY + drag.current.y
            }} onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}>
              {image ? <div className="grid place-items-center p-4" style={{ minWidth: '100%', minHeight: '100%', width: naturalWidth * scale + 32, height: naturalHeight * scale + 32 }}>
                <div className="relative shrink-0" style={{ width: naturalWidth * scale, height: naturalHeight * scale }}><img src={image.url} alt={name} draggable={false} className="absolute left-1/2 top-1/2 max-w-none select-none" style={{ width: image.width * scale, height: image.height * scale, transform: `translate(-50%, -50%) rotate(${rotation}deg)` }} /></div>
              </div> : <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center text-sm text-muted-foreground" role="status">
                {loading ? <span>{t('attachments.loading')}</span> : <><FileWarning className="h-7 w-7" /><span>{error ? t(`attachments.${error}`) : ''}</span>{error === 'failed' ? <Button variant="outline" onClick={() => setRetry(v => v + 1)}>{t('attachments.retry')}</Button> : null}</>}
              </div>}
            </div>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              <Button size="sm" variant="outline" disabled={!image || zoom <= .5} aria-label={t('attachments.zoomOut')} onClick={() => setZoom(v => Math.max(.5, v - .25))}><ZoomOut className="h-4 w-4" /></Button>
              <Button size="sm" variant="outline" disabled={!image} onClick={() => { setZoom(1); setRotation(0) }}>{Math.round(zoom * 100)}%</Button>
              <Button size="sm" variant="outline" disabled={!image || zoom >= 3} aria-label={t('attachments.zoomIn')} onClick={() => setZoom(v => Math.min(3, v + .25))}><ZoomIn className="h-4 w-4" /></Button>
              <Button size="sm" variant="outline" disabled={!image} onClick={() => setRotation(v => (v + 90) % 360)}><RotateCw className="mr-1 h-4 w-4" />{t('attachments.rotate')}</Button>
              <Button size="sm" variant="outline" disabled={!current?.cloudFileId || error === 'missing'} onClick={() => void download()}><Download className="mr-1 h-4 w-4" />{t('attachments.download')}</Button>
            </div>
          </div>
          {item ? <aside className="min-w-0 space-y-3"><h3 className="text-sm font-medium">{t('attachments.related', { count: item.transactions.length })}</h3>{item.transactions.map(tx => <div key={tx.id} className="rounded-xl border border-border/60 bg-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm"><span>{tx.category_name || t('common.none')}</span><strong className={`font-medium tabular-nums ${tx.tx_type === 'expense' ? 'text-expense' : tx.tx_type === 'income' ? 'text-income' : ''}`}>{tx.tx_type === 'expense' ? '−' : tx.tx_type === 'income' ? '+' : ''}{new Intl.NumberFormat(locale, { style: 'currency', currency: tx.currency_code || currency }).format(tx.amount)}</strong></div>
            <p className="mt-2 break-words text-xs text-muted-foreground">{tx.note || '—'}</p><p className="mt-2 text-xs text-muted-foreground">{new Date(tx.happened_at).toLocaleString(locale)}</p>
            <Button size="sm" variant="outline" className="mt-3" disabled={openingTx} onClick={() => void viewTransaction(tx.id, tx.ledger_id)}>{t('attachments.viewTransaction')}</Button>
          </div>)}</aside> : null}
        </div>
        {notice ? <p role="alert" className="text-sm text-destructive">{notice}</p> : null}
      </DialogContent>
    </Dialog>
  </Context.Provider>
}

export function useAttachmentPreview(): OpenPreview {
  const value = useContext(Context)
  if (!value) throw new Error('useAttachmentPreview requires AttachmentPreviewProvider')
  return value
}
