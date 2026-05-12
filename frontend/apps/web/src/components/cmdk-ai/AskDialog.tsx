import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  BookOpen,
  Copy,
  ExternalLink,
  HelpCircle,
  Loader2,
  RotateCw,
  Send,
  Sparkles,
} from 'lucide-react'

import {
  ApiError,
  type AskEvent,
  type AskSource,
  streamAsk,
} from '@beecount/api-client'
import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  useLocale,
  useT,
  useToast,
} from '@beecount/ui'

import { useAuth } from '../../context/AuthContext'

type Status = 'idle' | 'streaming' | 'done' | 'error' | 'fallback'

type FallbackKind = 'no-chat-provider' | 'embedding-unavailable' | 'docs-index-empty' | 'unknown'

export type AskDialogProps = {
  open: boolean
  /** 打开时的初始问题。Dialog 内可继续追问换 query。 */
  initialQuery: string
  onOpenChange: (open: boolean) => void
}

/**
 * AI 文档 Q&A 独立弹窗。
 *
 * 跟 ⌘K 解耦:任意地方 dispatch openAsk 事件 → AppShell GlobalAskDialog 打开。
 *
 * 内部状态机:
 * - idle      → 刚打开,initialQuery 还没触发
 * - streaming → 正在流式接收 chunks,answer 不断累加
 * - done      → 流完毕,sources 已贴出
 * - error     → server 端 stream 内 emit error event(provider 调用失败)
 * - fallback  → 401/503 等 ApiError(用户没配 / 索引空 / embedding 不可用)
 *
 * 设计:.docs/web-cmdk-ai-doc-search.md
 */
export function AskDialog({ open, initialQuery, onOpenChange }: AskDialogProps) {
  const t = useT()
  const { locale } = useLocale()
  const { token } = useAuth()
  const toast = useToast()

  // 「正在跑的问题」— 跟用户输入的 follow-up 区分
  const [activeQuery, setActiveQuery] = useState(initialQuery)
  // 输入框里跟随用户打字的临时 query
  const [draft, setDraft] = useState(initialQuery)

  const [answer, setAnswer] = useState('')
  const [sources, setSources] = useState<AskSource[]>([])
  const [status, setStatus] = useState<Status>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [fallback, setFallback] = useState<FallbackKind | null>(null)

  const cancelRef = useRef(false)
  const answerScrollRef = useRef<HTMLDivElement>(null)

  // 关闭时清理状态(下次打开是干净的)
  useEffect(() => {
    if (!open) {
      cancelRef.current = true
      setAnswer('')
      setSources([])
      setStatus('idle')
      setErrorMsg(null)
      setFallback(null)
    }
  }, [open])

  // 打开 / activeQuery 变化 → 触发 stream
  useEffect(() => {
    if (!open || !activeQuery) return

    cancelRef.current = false
    setAnswer('')
    setSources([])
    setStatus('streaming')
    setErrorMsg(null)
    setFallback(null)

    void (async () => {
      try {
        for await (const ev of streamAsk(token, { query: activeQuery, locale })) {
          if (cancelRef.current) return
          handleEvent(ev, setAnswer, setSources, setStatus, setErrorMsg)
        }
      } catch (err) {
        if (cancelRef.current) return
        if (err instanceof ApiError) {
          setStatus('fallback')
          if (err.code === 'AI_NO_CHAT_PROVIDER') setFallback('no-chat-provider')
          else if (err.code === 'AI_DOCS_INDEX_EMPTY') setFallback('docs-index-empty')
          else if (err.code === 'AI_EMBEDDING_UNAVAILABLE') setFallback('embedding-unavailable')
          else {
            setFallback('unknown')
            setErrorMsg(err.message)
          }
          return
        }
        setStatus('error')
        setErrorMsg(String(err))
      }
    })()
    return () => {
      cancelRef.current = true
    }
  }, [open, activeQuery, locale, token])

  // 同步 initialQuery → draft / activeQuery(打开时 / 外部换问题)
  useEffect(() => {
    if (open && initialQuery && initialQuery !== activeQuery) {
      setDraft(initialQuery)
      setActiveQuery(initialQuery)
    }
  }, [open, initialQuery]) // eslint-disable-line react-hooks/exhaustive-deps

  // 答案流式追加时自动滚到底
  useEffect(() => {
    if (status === 'streaming' && answerScrollRef.current) {
      answerScrollRef.current.scrollTop = answerScrollRef.current.scrollHeight
    }
  }, [answer, status])

  const submitFollowup = useCallback(() => {
    const q = draft.trim().replace(/^\?+\s*/, '') // 兼容用户带 `?` 前缀
    if (!q || q === activeQuery) return
    setActiveQuery(q)
  }, [draft, activeQuery])

  const handleCopy = useCallback(() => {
    if (!answer) return
    void navigator.clipboard.writeText(answer)
    toast.success(t('cmdk.ai.copied'))
  }, [answer, toast, t])

  const handleRetry = useCallback(() => {
    // 同 query,触发重跑(用 timestamp 强制 effect 重新执行)
    setActiveQuery((prev) => prev) // no-op
    // 实际:先 reset 状态再设回去
    setAnswer('')
    setSources([])
    setStatus('streaming')
    setErrorMsg(null)
    setFallback(null)
    // 用一个 epoch 触发 effect 重跑
    setActiveQuery((prev) => prev + ' ') // 加空格让 effect dep 变,完事 trim
  }, [])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] flex-col gap-0 p-0 sm:max-w-2xl">
        <DialogHeader className="flex-row items-center gap-1.5 border-b border-border/40 p-4">
          <Sparkles className="h-4 w-4 text-primary" />
          <DialogTitle className="text-sm font-medium">
            {t('cmdk.ai.dialog.title')}
          </DialogTitle>
          <span
            className="group relative inline-flex h-4 w-4 cursor-help items-center justify-center text-muted-foreground hover:text-foreground"
            aria-label={t('cmdk.ai.dialog.help')}
          >
            <HelpCircle className="h-3.5 w-3.5" />
            {/* Tailwind group-hover 即时弹出,不靠 native title 的延迟 */}
            <span
              role="tooltip"
              className="pointer-events-none invisible absolute left-1/2 top-full z-50 mt-1.5 w-72 -translate-x-1/2 rounded-md border border-border/60 bg-popover px-3 py-2 text-[11px] leading-relaxed text-popover-foreground opacity-0 shadow-lg transition-opacity duration-150 group-hover:visible group-hover:opacity-100"
            >
              {t('cmdk.ai.dialog.help')}
            </span>
          </span>
        </DialogHeader>

        {/* 输入框(显示当前 query,可改) — 回车提交 follow-up */}
        <div className="flex items-center gap-2 border-b border-border/40 px-4 py-3">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault()
                submitFollowup()
              }
            }}
            placeholder={t('cmdk.ai.dialog.placeholder')}
            className="h-9 flex-1"
            autoFocus
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!draft.trim() || draft.trim() === activeQuery || status === 'streaming'}
            onClick={submitFollowup}
            aria-label={t('cmdk.ai.dialog.submit')}
          >
            <Send className="h-3.5 w-3.5" />
          </Button>
        </div>

        {/* 主体 */}
        <div
          ref={answerScrollRef}
          className="flex-1 overflow-y-auto px-4 py-4"
        >
          {status === 'idle' && !activeQuery && (
            <p className="text-sm text-muted-foreground">{t('cmdk.ai.dialog.idle')}</p>
          )}

          {status === 'streaming' && (
            <div className="mb-3 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span>{t('cmdk.ai.thinking')}</span>
            </div>
          )}

          {status === 'fallback' && (
            <AskFallback kind={fallback ?? 'unknown'} query={activeQuery} locale={locale} t={t} />
          )}

          {status !== 'fallback' && answer && (
            <div className="prose prose-sm max-w-none whitespace-pre-wrap break-words text-[13px] leading-relaxed text-foreground dark:prose-invert">
              {answer}
            </div>
          )}

          {status === 'error' && errorMsg && (
            <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              <div className="mb-1 flex items-center gap-1.5 font-medium">
                <AlertTriangle className="h-3 w-3" />
                {t('cmdk.ai.error')}
              </div>
              <div className="break-words">{errorMsg}</div>
            </div>
          )}

          {/* sources */}
          {sources.length > 0 && status !== 'fallback' && (
            <div className="mt-5 border-t border-border/40 pt-3">
              <div className="mb-2 flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
                <BookOpen className="h-3 w-3" />
                {t('cmdk.ai.sources')}
              </div>
              <div className="flex flex-col gap-1">
                {sources.map((s, i) => (
                  <a
                    key={`${s.url}-${i}`}
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1.5 rounded px-1.5 py-1 text-xs text-muted-foreground transition hover:bg-primary/15 hover:text-primary"
                  >
                    <ExternalLink className="h-3 w-3 shrink-0" />
                    <span className="truncate">
                      {s.doc_title}
                      {s.section ? ` · ${s.section}` : ''}
                    </span>
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* footer */}
        <div className="flex items-center justify-between border-t border-border/40 bg-muted/30 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={!answer || status === 'streaming'}
              onClick={handleCopy}
            >
              <Copy className="mr-1 h-3 w-3" />
              {t('cmdk.ai.copy')}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={status === 'streaming'}
              onClick={handleRetry}
            >
              <RotateCw className="mr-1 h-3 w-3" />
              {t('cmdk.ai.retry')}
            </Button>
          </div>
          <span className="text-[10px] text-muted-foreground">
            {t('cmdk.ai.dialog.poweredBy')}
          </span>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function handleEvent(
  ev: AskEvent,
  setAnswer: (fn: (prev: string) => string) => void,
  setSources: (s: AskSource[]) => void,
  setStatus: (s: Status) => void,
  setErrorMsg: (m: string | null) => void,
) {
  switch (ev.type) {
    case 'chunk':
      setAnswer((prev) => prev + ev.text)
      break
    case 'sources':
      setSources(ev.items)
      break
    case 'done':
      setStatus('done')
      break
    case 'error':
      setStatus('error')
      setErrorMsg(`${ev.error_code}: ${ev.message}`)
      break
  }
}

// ────────────────────────────────────────────────────────────────────────
// Fallback panel(没配 / index empty / embedding 不可用)— 跳官网兜底
// ────────────────────────────────────────────────────────────────────────

type T = ReturnType<typeof useT>

function AskFallback({
  kind,
  query,
  locale,
  t,
}: {
  kind: FallbackKind
  query: string
  locale: string
  t: T
}) {
  const docsBase = locale.startsWith('zh')
    ? 'https://count.beejz.com/docs/'
    : 'https://count.beejz.com/en/docs/'
  const docsSearchUrl = `${docsBase}?q=${encodeURIComponent(query)}`

  let title: string
  let body: string
  if (kind === 'no-chat-provider') {
    title = t('cmdk.ai.fallback.noProvider.title')
    body = t('cmdk.ai.fallback.noProvider.body')
  } else if (kind === 'docs-index-empty') {
    title = t('cmdk.ai.fallback.indexEmpty.title')
    body = t('cmdk.ai.fallback.indexEmpty.body')
  } else if (kind === 'embedding-unavailable') {
    title = t('cmdk.ai.fallback.embeddingUnavailable.title')
    body = t('cmdk.ai.fallback.embeddingUnavailable.body')
  } else {
    title = t('cmdk.ai.fallback.unknown.title')
    body = t('cmdk.ai.fallback.unknown.body')
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
        <AlertTriangle className="h-3.5 w-3.5" />
        <span className="font-medium">{title}</span>
      </div>
      <p className="text-sm text-muted-foreground">{body}</p>
      <a
        href={docsSearchUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-muted/40 px-3 py-1.5 text-xs font-medium hover:bg-muted"
      >
        <ExternalLink className="h-3 w-3" />
        {t('cmdk.ai.fallback.openDocs', { q: query })}
      </a>
    </div>
  )
}
