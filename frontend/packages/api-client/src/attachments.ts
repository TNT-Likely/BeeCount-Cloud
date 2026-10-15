import { extractApiError } from './errors'
import { API_BASE } from './http'
import type { AttachmentBatchExistsResponse, AttachmentUploadOut, AttachmentRef } from './types'
import { authedGet } from './http'

export type AttachmentTransaction = {
  id: string
  ledger_id: string
  note: string | null
  category_name: string | null
  category_id: string | null
  amount: number
  tx_type: string
  currency_code: string | null
  native_amount: number | null
  happened_at: string
}

export type GalleryAttachment = {
  key: string
  attachment: AttachmentRef
  happened_at: string
  mime_type: string | null
  status: 'ready' | 'pending' | 'missing'
  transactions: AttachmentTransaction[]
}

export type GalleryAttachmentPage = {
  items: GalleryAttachment[]
  total: number
  transaction_total: number
  limit: number
  offset: number
}

export function fetchGalleryAttachments(token: string, options: {
  ledgerId: string; q?: string; categorySyncId?: string; dateFrom?: string; dateTo?: string
  limit?: number; offset?: number
}): Promise<GalleryAttachmentPage> {
  const query = new URLSearchParams({ ledger_id: options.ledgerId })
  for (const [key, value] of Object.entries({ q: options.q, category_sync_id: options.categorySyncId,
    date_from: options.dateFrom, date_to: options.dateTo, limit: options.limit, offset: options.offset })) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  return authedGet(`/read/workspace/attachments?${query}`, token)
}

export async function downloadAttachmentThumbnail(token: string, fileId: string, signal: AbortSignal): Promise<Blob> {
  const response = await fetch(`${API_BASE}/attachments/${encodeURIComponent(fileId)}/thumbnail`, {
    headers: { Authorization: `Bearer ${token}` }, signal,
  })
  if (!response.ok) throw await extractApiError(response)
  return response.blob()
}

export async function batchAttachmentExists(
  token: string,
  payload: { ledger_id: string; sha256_list: string[] }
): Promise<AttachmentBatchExistsResponse> {
  const res = await fetch(`${API_BASE}/attachments/batch-exists`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(payload)
  })
  if (!res.ok) {
    throw await extractApiError(res)
  }
  return res.json()
}

export async function uploadAttachment(
  token: string,
  payload: { ledger_id: string; file: File; mime_type?: string | null }
): Promise<AttachmentUploadOut> {
  const body = new FormData()
  body.append('ledger_id', payload.ledger_id)
  body.append('file', payload.file)
  if (payload.mime_type && payload.mime_type.trim()) {
    body.append('mime_type', payload.mime_type.trim())
  }

  const res = await fetch(`${API_BASE}/attachments/upload`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`
    },
    body
  })
  if (!res.ok) {
    throw await extractApiError(res)
  }
  return res.json()
}

export async function downloadAttachment(
  token: string,
  fileId: string,
  signal?: AbortSignal,
): Promise<{ blob: Blob; fileName: string | null; mimeType: string | null }> {
  const res = await fetch(`${API_BASE}/attachments/${encodeURIComponent(fileId)}`, {
    signal,
    headers: {
      Authorization: `Bearer ${token}`
    }
  })
  if (!res.ok) {
    throw await extractApiError(res)
  }
  const contentDisposition = res.headers.get('content-disposition')
  let fileName: string | null = null
  if (contentDisposition) {
    const match = /filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?/i.exec(contentDisposition)
    if (match) {
      const rawName = match[1] || match[2] || ''
      try {
        fileName = decodeURIComponent(rawName)
      } catch {
        fileName = rawName
      }
    }
  }
  return {
    blob: await res.blob(),
    fileName,
    mimeType: res.headers.get('content-type')
  }
}
