import { describe, expect, it, vi } from 'vitest'
import {
  prepareTransactionAttachments,
  validateTransactionImage,
  MAX_TRANSACTION_IMAGE_BYTES,
  type AttachmentDraft
} from '../../../packages/web-features/src/lib/transactionAttachments'
import type { AttachmentRef } from '@beecount/api-client'

const ref = (id: string): AttachmentRef => ({
  fileName: `${id}.png`,
  cloudFileId: id,
  cloudSha256: id
})
const file = (name: string) => ({ name, type: 'image/png', size: 12 }) as File

describe('transaction attachment drafts', () => {
  it('rejects non-images, empty images and files above the upload limit', () => {
    expect(validateTransactionImage({ type: 'image/svg+xml', size: 12 })).toBe(
      'type'
    )
    expect(validateTransactionImage({ type: 'image/png', size: 0 })).toBe(
      'size'
    )
    expect(
      validateTransactionImage({
        type: 'image/png',
        size: MAX_TRANSACTION_IMAGE_BYTES + 1
      })
    ).toBe('size')
    expect(
      validateTransactionImage({
        type: 'image/jpeg',
        size: MAX_TRANSACTION_IMAGE_BYTES
      })
    ).toBeNull()
  })

  it('preserves App metadata and original objects, assigning consecutive sort orders', async () => {
    const original = { ...ref('old'), width: 120, height: 80, sortOrder: 8 }
    const upload = vi.fn().mockResolvedValue(ref('new'))
    const out = await prepareTransactionAttachments(
      [
        { id: '1', ref: original },
        { id: '2', file: file('b.png'), width: 50, height: 60 }
      ],
      upload
    )
    expect(out).toEqual([
      { ...original, sortOrder: 0 },
      { ...ref('new'), width: 50, height: 60, sortOrder: 1 }
    ])
    expect(original.sortOrder).toBe(8)
    expect(upload).toHaveBeenCalledTimes(1)
  })

  it('keeps successful uploads when a later upload fails, allowing retry without reupload', async () => {
    const drafts: AttachmentDraft[] = [
      { id: 'a', file: file('a.png') },
      { id: 'b', file: file('b.png') }
    ]
    const progress = vi.fn()
    const upload = vi
      .fn()
      .mockResolvedValueOnce(ref('a'))
      .mockRejectedValueOnce(new Error('503'))
      .mockResolvedValueOnce(ref('b'))
    await expect(
      prepareTransactionAttachments(drafts, upload, progress)
    ).rejects.toThrow('503')
    expect(progress).toHaveBeenLastCalledWith(null)
    const result = await prepareTransactionAttachments(drafts, upload, progress)
    expect(upload).toHaveBeenCalledTimes(3)
    expect(result.map((r) => r.cloudFileId)).toEqual(['a', 'b'])
    await prepareTransactionAttachments(drafts, upload)
    expect(upload).toHaveBeenCalledTimes(3)
  })

  it('deduplicates uploaded content and only saves references left in the draft', async () => {
    const upload = vi.fn().mockResolvedValue(ref('same'))
    const out = await prepareTransactionAttachments(
      [
        { id: '1', ref: ref('same') },
        { id: '2', file: file('same.png') }
      ],
      upload
    )
    expect(out).toHaveLength(1)
    expect(await prepareTransactionAttachments([], upload)).toEqual([])
    const replaced = await prepareTransactionAttachments(
      [{ id: 'r', ref: ref('replacement') }],
      upload
    )
    expect(replaced.map((r) => r.cloudFileId)).toEqual(['replacement'])
  })
})
