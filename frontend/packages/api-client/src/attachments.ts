import { extractApiError } from './errors'
import { API_BASE } from './http'
import type { AttachmentBatchExistsResponse, AttachmentUploadOut } from './types'

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
  fileId: string
): Promise<{ blob: Blob; fileName: string | null; mimeType: string | null }> {
  const res = await fetch(`${API_BASE}/attachments/${encodeURIComponent(fileId)}`, {
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
