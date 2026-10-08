import { uploadAttachment, type AttachmentRef } from '@beecount/api-client'

export const TRANSACTION_IMAGE_ACCEPT =
  'image/jpeg,image/png,image/webp,image/gif'
export const MAX_TRANSACTION_IMAGE_BYTES = 64 * 1024 * 1024

export function validateTransactionImage(
  file: Pick<File, 'type' | 'size'>
): 'type' | 'size' | null {
  if (!TRANSACTION_IMAGE_ACCEPT.split(',').includes(file.type)) return 'type'
  if (file.size <= 0 || file.size > MAX_TRANSACTION_IMAGE_BYTES) return 'size'
  return null
}

/** App 按 fileName 对齐附件；用内容身份生成文件名，替换图片不会覆盖旧文件。 */
export async function uploadTransactionImage(
  token: string,
  ledgerId: string,
  file: File
): Promise<AttachmentRef> {
  const uploaded = await uploadAttachment(token, { ledger_id: ledgerId, file })
  return {
    fileName: `${uploaded.file_id}_${uploaded.file_name}`,
    originalName: file.name,
    fileSize: uploaded.size,
    cloudFileId: uploaded.file_id,
    cloudSha256: uploaded.sha256
  }
}

export type AttachmentDraft = {
  id: string
  ref?: AttachmentRef
  file?: File
  previewUrl?: string
  width?: number
  height?: number
}

/** 成功的上传保留在 draft 内，交易保存失败/部分上传失败时可直接重试。 */
export async function prepareTransactionAttachments(
  drafts: AttachmentDraft[],
  upload: (file: File) => Promise<AttachmentRef>,
  onUploading?: (id: string | null) => void
): Promise<AttachmentRef[]> {
  const refs: AttachmentRef[] = []
  try {
    for (const draft of drafts) {
      if (!draft.ref && draft.file) {
        onUploading?.(draft.id)
        draft.ref = {
          ...(await upload(draft.file)),
          width: draft.width,
          height: draft.height
        }
      }
      if (!draft.ref) continue
      // 同一图片重复选择时后端按 sha 去重，客户端也只保留一份引用。
      if (
        draft.ref.cloudFileId &&
        refs.some((r) => r.cloudFileId === draft.ref!.cloudFileId)
      )
        continue
      refs.push({ ...draft.ref, sortOrder: refs.length })
    }
    return refs
  } finally {
    onUploading?.(null)
  }
}
