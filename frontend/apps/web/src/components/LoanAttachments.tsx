import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Paperclip } from 'lucide-react'

import type { AttachmentRef } from '@beecount/api-client'
import { useT } from '@beecount/ui'
import {
  TransactionAttachmentEditor,
  uploadTransactionImage,
  type TransactionAttachmentEditorHandle,
} from '@beecount/web-features'

import { useAuth } from '../context/AuthContext'
import { useAttachmentCache } from '../context/AttachmentCacheContext'
import { useAttachmentPreview } from '../context/AttachmentPreviewContext'
import { useLedgers } from '../context/LedgersContext'

/**
 * 借款/还款凭证在后端只是 `string[]`(attachment file id),而通用附件编辑器
 * 吃的是 AttachmentRef。这里做双向适配:
 *  - 读:file id 直接当 cloudFileId,缩略图/大图预览都靠 downloadAttachment(fileId)
 *  - 写:编辑器 prepare() 上传新文件后取回 cloudFileId,组成新的 id 数组交给保存逻辑
 * 文件名由后端 attachment_files 反查后写进生成流水的 attachments_json。
 */
export function fileIdsToAttachmentRefs(ids: string[] = []): AttachmentRef[] {
  return ids
    .map((id) => id.trim())
    .filter(Boolean)
    .map((id, index) => ({
      fileName: id,
      originalName: id,
      cloudFileId: id,
      sortOrder: index,
    }))
}

export type LoanAttachmentEditorHandle = {
  /** 保存前调用:上传新选文件并返回最终 file id 数组;上传失败返回 null(阻止保存)。 */
  prepare: () => Promise<string[] | null>
}

type EditorProps = {
  /** 该记录已有的附件 file id(编辑借款时回填;追加时为原借款凭证) */
  fileIds: string[]
  disabled?: boolean
  title?: string
}

/**
 * 可编辑附件字段。父弹窗在 DialogContent 里渲染并给一个随「打开/记录 id」
 * 变化的 key,保证每次打开都从最新的 fileIds 重建草稿。
 */
export const LoanAttachmentEditorField = forwardRef<LoanAttachmentEditorHandle, EditorProps>(
  function LoanAttachmentEditorField({ fileIds, disabled, title }, handle) {
    const t = useT()
    const { token } = useAuth()
    const { activeLedgerId } = useLedgers()
    const { resolvePreview } = useAttachmentCache()
    const editor = useRef<TransactionAttachmentEditorHandle>(null)
    const [selecting, setSelecting] = useState(false)

    const refs = useMemo(() => fileIdsToAttachmentRefs(fileIds), [fileIds])
    const upload = useCallback(
      (file: File) => uploadTransactionImage(token || '', activeLedgerId || '', file),
      [token, activeLedgerId],
    )

    useImperativeHandle(handle, () => ({
      prepare: async () => {
        const prepared = await editor.current?.prepare()
        if (prepared === null || prepared === undefined) return null
        return prepared
          .map((ref) => ref.cloudFileId || ref.fileName)
          .filter(Boolean)
      },
    }), [])

    return (
      <div className="space-y-1">
        <TransactionAttachmentEditor
          ref={editor}
          attachments={refs}
          disabled={Boolean(disabled || selecting || !token || !activeLedgerId)}
          onSelectingChange={setSelecting}
          upload={upload}
          resolvePreview={(ref) => resolvePreview(ref.cloudFileId || ref.fileName || '')}
          label={title || t('loans.attachment.title')}
        />
        <p className="text-xs text-muted-foreground">{t('loans.attachment.hint')}</p>
      </div>
    )
  },
)

/**
 * 只读附件缩略图:借款详情、还款流水行、按人流水时间轴共用。
 * 点击走全局大图预览(支持左右切换、下载)。
 */
export function LoanAttachmentThumbs({
  fileIds,
  label,
}: {
  fileIds: string[]
  label?: string
}) {
  const t = useT()
  const { previewMap, ensureLoadedMany } = useAttachmentCache()
  const openPreview = useAttachmentPreview()
  const ids = useMemo(() => fileIds.map((id) => id.trim()).filter(Boolean), [fileIds])
  const refs = useMemo(() => fileIdsToAttachmentRefs(ids), [ids])
  const key = ids.join('|')

  useEffect(() => {
    ensureLoadedMany(ids)
    // key 变化才重新触发,避免数组引用变化导致重复下载。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, ensureLoadedMany])

  if (ids.length === 0) return null
  return (
    <div className="mt-2">
      <div className="mb-1 text-xs text-muted-foreground">
        {label || t('loans.attachment.title')} · {ids.length}
      </div>
      <div className="flex flex-wrap gap-2">
        {ids.map((id, index) => {
          const url = previewMap[id]
          return (
            <button
              key={`${id}-${index}`}
              type="button"
              onClick={() => void openPreview(refs, index)}
              className="flex h-12 w-12 items-center justify-center overflow-hidden rounded-md border border-border bg-muted/30 text-muted-foreground transition-colors hover:border-primary/60"
              title={t('loans.attachment.view') as string}
            >
              {url ? (
                <img src={url} alt="" className="h-full w-full object-cover" />
              ) : (
                <Paperclip className="h-4 w-4" />
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
