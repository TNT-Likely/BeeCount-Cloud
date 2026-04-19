import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCcw } from 'lucide-react'

import type { AdminLogEntry, AdminLogList } from '@beecount/api-client'
import { fetchAdminLogs } from '@beecount/api-client'
import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT
} from '@beecount/ui'

interface Props {
  token: string
  open: boolean
  onOpenChange: (next: boolean) => void
}

const LEVEL_OPTIONS = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR'] as const

// 日志来源预设;value 是 logger 名称前缀(多个前缀逗号分隔传给 server)。
// label 走 i18n,避免硬编码。
const SOURCE_OPTIONS: Array<{ key: string; value: string }> = [
  { key: 'all', value: '' },
  { key: 'access', value: 'beecount.access' },
  { key: 'sync', value: 'src.routers.sync' },
  { key: 'write', value: 'src.routers.write' },
  { key: 'read', value: 'src.routers.read' },
  { key: 'profile', value: 'src.routers.profile' },
  { key: 'attachments', value: 'src.routers.attachments' },
  { key: 'auth', value: 'src.routers.auth' },
  { key: 'admin', value: 'src.routers.admin' },
  { key: 'ws', value: 'src.routers.ws,src.websocket_manager' },
  { key: 'uvicorn', value: 'uvicorn' }
]

// 自动刷新可选间隔(秒)。0 = 关闭。
const REFRESH_INTERVALS = [0, 2, 5, 10, 30] as const

const LEVEL_TONE: Record<string, string> = {
  DEBUG: 'text-muted-foreground',
  INFO: 'text-foreground',
  WARNING: 'text-amber-500',
  ERROR: 'text-rose-500',
  CRITICAL: 'text-rose-600 font-bold'
}

function formatTs(iso: string): string {
  try {
    const d = new Date(iso)
    const HH = String(d.getHours()).padStart(2, '0')
    const MM = String(d.getMinutes()).padStart(2, '0')
    const SS = String(d.getSeconds()).padStart(2, '0')
    return `${HH}:${MM}:${SS}`
  } catch {
    return iso
  }
}

export function LogsDialog({ token, open, onOpenChange }: Props) {
  const t = useT()
  const [level, setLevel] = useState<string>('ALL')
  const [sourceKey, setSourceKey] = useState<string>('all')
  const [q, setQ] = useState('')
  const [autoRefreshSeconds, setAutoRefreshSeconds] = useState<number>(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<AdminLogList | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  // 记录用户是否手动滚离底部 —— 在自动刷新场景下,不在底部时不强制滚,避免
  // 打断阅读。
  const stickToBottomRef = useRef<boolean>(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const src = SOURCE_OPTIONS.find((s) => s.key === sourceKey)?.value || ''
      const res = await fetchAdminLogs(token, {
        level: level === 'ALL' ? undefined : level,
        q: q.trim() || undefined,
        source: src || undefined,
        limit: 500
      })
      setData(res)
      if (stickToBottomRef.current) {
        setTimeout(() => {
          const el = scrollRef.current
          if (el) el.scrollTop = el.scrollHeight
        }, 0)
      }
    } catch (err) {
      setError((err as Error).message || 'failed')
    } finally {
      setLoading(false)
    }
  }, [token, level, sourceKey, q])

  // 首次打开 + 过滤条件变化时立即拉一次
  useEffect(() => {
    if (!open) return
    void load()
  }, [open, load])

  // 自动刷新:仅在 open 且间隔 > 0 时启用轮询
  useEffect(() => {
    if (!open || autoRefreshSeconds <= 0) return
    const timer = window.setInterval(() => {
      void load()
    }, autoRefreshSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [open, autoRefreshSeconds, load])

  // 追踪用户是否接近底部 —— 决定下次刷新要不要自动滚
  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    stickToBottomRef.current = nearBottom
  }

  const items: AdminLogEntry[] = data?.items || []

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] max-w-4xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border/60 px-5 py-3">
          <DialogTitle className="flex items-center justify-between gap-3 pr-8">
            <span>{t('logs.title')}</span>
            <span className="text-[11px] font-normal text-muted-foreground">
              {data
                ? t('logs.meta')
                    .replace('{count}', String(items.length))
                    .replace('{capacity}', String(data.capacity))
                : ''}
            </span>
          </DialogTitle>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-2 border-b border-border/60 bg-muted/30 px-5 py-2.5">
          <Select value={level} onValueChange={setLevel}>
            <SelectTrigger className="h-8 w-28 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LEVEL_OPTIONS.map((lv) => (
                <SelectItem key={lv} value={lv} className="text-xs">
                  {lv === 'ALL' ? t('logs.level.all') : lv}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={sourceKey} onValueChange={setSourceKey}>
            <SelectTrigger className="h-8 w-32 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SOURCE_OPTIONS.map((opt) => (
                <SelectItem key={opt.key} value={opt.key} className="text-xs">
                  {t(`logs.source.${opt.key}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void load()
            }}
            placeholder={t('logs.search.placeholder')}
            className="h-8 min-w-[180px] flex-1 text-xs"
          />
          <Select
            value={String(autoRefreshSeconds)}
            onValueChange={(v) => setAutoRefreshSeconds(Number(v) || 0)}
          >
            <SelectTrigger className="h-8 w-28 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {REFRESH_INTERVALS.map((sec) => (
                <SelectItem key={sec} value={String(sec)} className="text-xs">
                  {sec === 0
                    ? t('logs.autoRefresh.off')
                    : t('logs.autoRefresh.on').replace('{sec}', String(sec))}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void load()}
            disabled={loading}
            className="h-8 gap-1.5 px-3 text-xs"
          >
            <RefreshCcw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            {t('logs.refresh')}
          </Button>
        </div>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="min-h-0 flex-1 overflow-y-auto bg-background/40 font-mono text-[11px] leading-5"
        >
          {error ? (
            <div className="px-5 py-6 text-center text-rose-500">{error}</div>
          ) : items.length === 0 ? (
            <div className="px-5 py-10 text-center text-muted-foreground">
              {loading ? t('logs.loading') : t('logs.empty')}
            </div>
          ) : (
            <ul className="divide-y divide-border/40">
              {items.map((it) => (
                <li
                  key={it.seq}
                  className="flex gap-2 px-5 py-1.5 hover:bg-accent/30"
                >
                  <span className="w-16 shrink-0 text-muted-foreground">{formatTs(it.ts)}</span>
                  <span className={`w-14 shrink-0 font-bold ${LEVEL_TONE[it.level] || 'text-foreground'}`}>
                    {it.level}
                  </span>
                  <span className="w-48 shrink-0 truncate text-muted-foreground" title={it.logger}>
                    {it.logger}
                  </span>
                  <span className="min-w-0 flex-1 break-all text-foreground">{it.message}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-border/60 bg-muted/20 px-5 py-2 text-[11px] text-muted-foreground">
          <span>{t('logs.footer.hint')}</span>
        </div>
      </DialogContent>
    </Dialog>
  )
}
