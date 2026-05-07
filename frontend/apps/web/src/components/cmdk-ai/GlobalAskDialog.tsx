import { useEffect, useState } from 'react'

import { listenOpenAsk } from '../../lib/askDialogEvents'
import { AskDialog } from './AskDialog'

/**
 * 全局 AI 文档问答弹窗。
 *
 * 跟 GlobalEditDialogs 同模式 — 挂在 AppShell 顶层,监听 `beecount:open-ask`
 * 全局事件,任意页面 dispatchOpenAsk(query) 都能打开。
 *
 * 设计原因:
 * - AskDialog 跟 ⌘K 解耦,不被 cmdk Command.List 渲染规则吞内容
 * - 不需要先 navigate 到某页才能问 AI
 * - 多个页面内的「问 AI」入口共享同一个 dialog 实例
 */
export function GlobalAskDialog() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')

  useEffect(() => {
    return listenOpenAsk((q) => {
      setQuery(q)
      setOpen(true)
    })
  }, [])

  return <AskDialog open={open} initialQuery={query} onOpenChange={setOpen} />
}
