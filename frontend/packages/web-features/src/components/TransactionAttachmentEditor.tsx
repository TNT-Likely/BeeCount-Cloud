import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { ImagePlus, ImageOff, RefreshCw, Trash2, Loader2 } from 'lucide-react'
import { Button, Dialog, DialogContent, DialogTitle, useT } from '@beecount/ui'
import type { AttachmentRef } from '@beecount/api-client'
import { prepareTransactionAttachments, TRANSACTION_IMAGE_ACCEPT, validateTransactionImage, type AttachmentDraft } from '../lib/transactionAttachments'

export type TransactionAttachmentEditorHandle = { prepare: () => Promise<AttachmentRef[] | null> }

let nextDraftId = 0
const draftId = () => `attachment-${++nextDraftId}`

type Props = {
  attachments: AttachmentRef[]
  disabled: boolean
  onSelectingChange: (selecting: boolean) => void
  upload: (file: File) => Promise<AttachmentRef>
  resolvePreview: (ref: AttachmentRef) => Promise<string | null>
}

export const TransactionAttachmentEditor = forwardRef<TransactionAttachmentEditorHandle, Props>(function TransactionAttachmentEditor({ attachments, disabled, upload, resolvePreview, onSelectingChange }, handle) {
  const t = useT()
  const [drafts, setDrafts] = useState<AttachmentDraft[]>(() => attachments.map((ref) => ({ id: draftId(), ref })))
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState<string | null>(null)
  const [preview, setPreview] = useState<{ url: string; name: string } | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const replacement = useRef<string | null>(null)
  const ownedUrls = useRef(new Set<string>())
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      onSelectingChange(false)
      for (const url of ownedUrls.current) URL.revokeObjectURL(url)
    }
  }, [])

  useImperativeHandle(handle, () => ({
    prepare: async () => {
      setError('')
      try {
        return await prepareTransactionAttachments(drafts, upload, setUploading)
      } catch {
        setError(t('transactions.attachment.uploadFailed'))
        return null
      }
    },
  }), [drafts, upload, t])

  useEffect(() => {
    let cancelled = false
    for (const draft of drafts) {
      if (!draft.ref || draft.previewUrl !== undefined) continue
      void resolvePreview(draft.ref).then((url) => {
        if (cancelled) return
        setDrafts((prev) => prev.map((item) => item.id === draft.id ? { ...item, previewUrl: url || '' } : item))
      }).catch(() => {
        if (!cancelled) setDrafts((prev) => prev.map((item) => item.id === draft.id ? { ...item, previewUrl: '' } : item))
      })
    }
    return () => { cancelled = true }
  }, [drafts, resolvePreview])

  const choose = (id: string | null) => {
    replacement.current = id
    if (input.current) { input.current.multiple = id === null; input.current.click() }
  }
  const select = async (files: File[]) => {
    onSelectingChange(true)
    try {
    const replaceId = replacement.current
    setError('')
    const additions: AttachmentDraft[] = []
    const available = replaceId ? 1 : Math.max(0, 9 - drafts.length)
    if (files.length > available) setError(t('transactions.attachment.limit'))
    for (const file of files.slice(0, available)) {
      const invalid = validateTransactionImage(file)
      if (invalid) { setError(t(`transactions.attachment.invalid${invalid === 'type' ? 'Type' : 'Size'}`)); continue }
      const url = URL.createObjectURL(file)
      ownedUrls.current.add(url)
      const image = new Image()
      const loaded = await new Promise<boolean>((resolve) => {
        image.onload = () => resolve(true)
        image.onerror = () => resolve(false)
        image.src = url
      })
      if (!mounted.current) return
      if (!loaded) { URL.revokeObjectURL(url); ownedUrls.current.delete(url); setError(t('transactions.attachment.invalidImage')); continue }
      additions.push({ id: draftId(), file, previewUrl: url, width: image.naturalWidth, height: image.naturalHeight })
    }
    if (!mounted.current || !additions.length) return
    setDrafts((prev) => replaceId ? prev.flatMap((item) => item.id === replaceId ? [additions[0]] : [item]) : [...prev, ...additions])
    } finally {
      if (mounted.current) onSelectingChange(false)
    }
  }

  return <section className="space-y-3 md:col-span-2" aria-label={t('detail.transaction.attachments')}>
    <div className="flex items-center justify-between gap-3">
      <p className="text-sm font-medium">{t('detail.transaction.attachments')} <span className="ml-1 text-xs font-normal text-muted-foreground">{drafts.length || ''}</span></p>
      <Button type="button" variant="ghost" size="sm" disabled={disabled || drafts.length >= 9} onClick={() => choose(null)}><ImagePlus className="mr-1.5 h-4 w-4" />{t('transactions.attachment.addImages')}</Button>
    </div>
    <input ref={input} type="file" accept={TRANSACTION_IMAGE_ACCEPT} className="hidden" onChange={(event) => {
      const files = Array.from(event.currentTarget.files || [])
      event.currentTarget.value = ''
      void select(files)
    }} />
    {drafts.length ? <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {drafts.map((draft) => {
        const name = draft.file?.name || draft.ref?.originalName || draft.ref?.fileName || t('attachment.default')
        return <div key={draft.id} className="overflow-hidden rounded-lg border border-border/60 bg-muted/20">
          <button type="button" className="relative flex aspect-[4/3] w-full items-center justify-center bg-muted/40" aria-label={`${t('transactions.attachment.preview')} ${name}`} disabled={!draft.previewUrl} onClick={() => draft.previewUrl && setPreview({ url: draft.previewUrl, name })}>
            {draft.previewUrl ? <img src={draft.previewUrl} alt={name} className="h-full w-full object-cover" /> : <ImageOff className="h-6 w-6 text-muted-foreground/50" />}
            {uploading === draft.id ? <span className="absolute inset-0 flex items-center justify-center bg-background/70"><Loader2 className="h-5 w-5 animate-spin" /><span className="sr-only">{t('transactions.attachment.uploading')}</span></span> : null}
          </button>
          <div className="space-y-1.5 p-2">
            <p className="truncate text-xs text-muted-foreground" title={name}>{name}</p>
            <div className="flex items-center justify-between gap-1">
              <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={disabled} onClick={() => choose(draft.id)} aria-label={`${t('transactions.attachment.replace')} ${name}`}><RefreshCw className="mr-1 h-3 w-3" />{t('transactions.attachment.replace')}</Button>
              <Button type="button" variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-destructive" disabled={disabled} onClick={() => setDrafts((prev) => prev.filter((item) => item.id !== draft.id))} aria-label={`${t('transactions.attachment.remove')} ${name}`}><Trash2 className="h-3.5 w-3.5" /></Button>
            </div>
          </div>
        </div>
      })}
    </div> : <button type="button" disabled={disabled} onClick={() => choose(null)} className="flex w-full flex-col items-center gap-2 rounded-lg border border-dashed border-border bg-muted/10 px-4 py-6 text-sm text-muted-foreground transition hover:border-primary/50 hover:bg-muted/30"><ImagePlus className="h-6 w-6" />{t('transactions.attachment.addImages')}</button>}
    <p className="text-xs text-muted-foreground">{t('transactions.attachment.saveHint')}</p>
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    <Dialog open={Boolean(preview)} onOpenChange={(open) => !open && setPreview(null)}>
      <DialogContent className="max-w-3xl"><DialogTitle className="break-all pr-5 text-sm">{preview?.name}</DialogTitle>{preview ? <img src={preview.url} alt={preview.name} className="max-h-[70dvh] w-full object-contain" /> : null}</DialogContent>
    </Dialog>
  </section>
})
