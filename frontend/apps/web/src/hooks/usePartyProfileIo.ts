import { useCallback } from 'react'

import { uploadTransactionImage } from '@beecount/web-features'

import { useAuth } from '../context/AuthContext'
import { useAttachmentCache } from '../context/AttachmentCacheContext'
import { useLedgers } from '../context/LedgersContext'

/**
 * 联系人 / 成员头像共用的 IO。头像复用交易附件体系(attachment_files)，
 * 上传需要当前账本作为载体；预览走 AttachmentCache 的 blob URL 缓存。
 */
export function usePartyProfileIo() {
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { resolvePreview } = useAttachmentCache()

  const upload = useCallback(
    async (file: File): Promise<string> => {
      if (!token || !activeLedgerId) throw new Error('no active ledger')
      const ref = await uploadTransactionImage(token, activeLedgerId, file)
      if (!ref.cloudFileId) throw new Error('upload returned no file id')
      return ref.cloudFileId
    },
    [token, activeLedgerId],
  )

  return { upload, resolvePreview }
}
