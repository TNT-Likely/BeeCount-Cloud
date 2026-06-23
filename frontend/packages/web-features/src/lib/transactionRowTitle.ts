export type NoteDisplayMode = 'category' | 'note'

export type TransactionRowTitle = {
  /** 常驻主文本 */
  primary: string
  /** 非空时在主文本后渲染「(备注)」小灰字;null = 不渲染 */
  parenNote: string | null
}

/**
 * 组装交易行第一行。
 * - mode='note' 且有真实分类(categoryName 非空)且有备注 → 备注为主、无括号(备注优先,纯替换)。
 * - 否则(分类优先,默认;或转账等无分类行)→ categoryText 为主,有备注则挂括号。
 */
export function composeTransactionRowTitle(params: {
  mode: NoteDisplayMode
  categoryName: string | null | undefined
  categoryText: string
  note: string | null | undefined
}): TransactionRowTitle {
  const { mode, categoryName, categoryText, note } = params
  const noteText = note ?? ''
  if (mode === 'note' && categoryName && noteText) {
    return { primary: noteText, parenNote: null }
  }
  return { primary: categoryText, parenNote: noteText || null }
}
