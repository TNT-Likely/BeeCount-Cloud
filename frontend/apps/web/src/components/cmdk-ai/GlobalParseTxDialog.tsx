import { useEffect, useState } from 'react'

import { listenOpenParseTx } from '../../lib/parseTxEvents'
import { ParseTxDialog } from './ParseTxDialog'

type State =
  | { open: false }
  | { open: true; mode: 'image'; image: Blob }
  | { open: true; mode: 'text'; text: string }

/**
 * AppShell 顶层挂载,监听全局 parse-tx 事件 → 开 ParseTxDialog。
 *
 * 跟 GlobalAskDialog 同模式 — 跟 ⌘K 解耦,任意页面都能 dispatch 触发。
 */
export function GlobalParseTxDialog() {
  const [state, setState] = useState<State>({ open: false })

  useEffect(() => {
    return listenOpenParseTx((detail) => {
      if (detail.mode === 'image') {
        setState({ open: true, mode: 'image', image: detail.image })
      } else {
        setState({ open: true, mode: 'text', text: detail.text })
      }
    })
  }, [])

  if (!state.open) {
    return (
      <ParseTxDialog
        open={false}
        onOpenChange={() => {}}
        initialMode="image"
      />
    )
  }

  return (
    <ParseTxDialog
      open={state.open}
      onOpenChange={(v) => {
        if (!v) setState({ open: false })
      }}
      initialMode={state.mode}
      initialImage={state.mode === 'image' ? state.image : null}
      initialText={state.mode === 'text' ? state.text : ''}
    />
  )
}
