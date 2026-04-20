import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowUpRight, CheckCircle2, Sparkles } from 'lucide-react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  useT
} from '@beecount/ui'

/**
 * "更新日志"弹窗 —— 顶部对比当前版本 vs GitHub latest release,下方按发布倒序
 * 列出每个 release 的 tag / 日期 / body。滚动到底部自动加载更多(per_page=10)。
 *
 * 数据源:GitHub REST `GET /repos/{owner}/{repo}/releases?per_page=10&page=N`,
 * 无 token 调用,公开仓库够用(限额 60/h/IP)。body 是 markdown,我们用一个
 * 极简的 renderer(标题 / 列表 / 代码块 / 链接)避免再引一整个 markdown 库。
 *
 * 当前版本走 env var `VITE_APP_VERSION`(CI 构建时从 git tag 注入,见
 * apps/web/src/env.d.ts + vite.config.ts)。没注入时显示 "—"。
 */

const GITHUB_API_BASE = 'https://api.github.com/repos/TNT-Likely/BeeCount-Cloud'
const GITHUB_RELEASES_URL = 'https://github.com/TNT-Likely/BeeCount-Cloud/releases'
const PAGE_SIZE = 10

interface GithubRelease {
  id: number
  tag_name: string
  name: string | null
  published_at: string | null
  html_url: string
  body: string | null
  prerelease: boolean
  draft: boolean
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** 简化版 markdown → HTML(安全 escape + 基础语法),够 release body 用,不引 library。 */
function renderMarkdownLite(md: string): string {
  const escape = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

  const lines = md.split('\n')
  const out: string[] = []
  let inCode = false
  let codeBuffer: string[] = []

  const flushCode = () => {
    if (codeBuffer.length > 0) {
      out.push(
        `<pre class="my-2 overflow-x-auto rounded bg-muted/60 px-3 py-2 text-[11px]"><code>${escape(codeBuffer.join('\n'))}</code></pre>`
      )
      codeBuffer = []
    }
  }

  for (const raw of lines) {
    if (raw.startsWith('```')) {
      if (inCode) {
        flushCode()
        inCode = false
      } else {
        inCode = true
      }
      continue
    }
    if (inCode) {
      codeBuffer.push(raw)
      continue
    }

    const trimmed = raw.trimEnd()
    if (!trimmed) {
      out.push('<div class="h-2"></div>')
      continue
    }
    // Headings
    const h = /^(#{1,6})\s+(.*)$/.exec(trimmed)
    if (h) {
      const level = Math.min(h[1].length, 3) + 2 // h3/h4/h5,避免破坏弹窗层级
      out.push(`<h${level} class="mt-3 text-sm font-semibold">${escape(h[2])}</h${level}>`)
      continue
    }
    // List item
    if (/^\s*[-*]\s+/.test(trimmed)) {
      const item = trimmed.replace(/^\s*[-*]\s+/, '')
      out.push(`<div class="ml-4 list-disc pl-1 text-[12px] leading-relaxed before:content-['•'] before:mr-2 before:text-muted-foreground">${inlineFormat(escape(item))}</div>`)
      continue
    }
    out.push(`<p class="text-[12px] leading-relaxed">${inlineFormat(escape(trimmed))}</p>`)
  }
  flushCode()
  return out.join('\n')
}

function inlineFormat(escaped: string): string {
  // link [text](url)
  let out = escaped.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a class="text-primary underline-offset-2 hover:underline" target="_blank" rel="noopener noreferrer" href="$2">$1</a>'
  )
  // inline code `x`
  out = out.replace(
    /`([^`]+)`/g,
    '<code class="rounded bg-muted/60 px-1 py-0.5 text-[11px]">$1</code>'
  )
  // bold **x**
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  return out
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    })
  } catch {
    return iso
  }
}

/** 粗略判断是否"有更新可用":semver-ish 比较,完全为空走字符串直比。 */
function isNewerVersion(latest: string | null, current: string | null): boolean {
  if (!latest || !current) return false
  const strip = (s: string) => s.replace(/^v/i, '').trim()
  const l = strip(latest)
  const c = strip(current)
  if (l === c) return false
  const toParts = (s: string) =>
    s.split(/[-+.]/).map((p) => (/^\d+$/.test(p) ? parseInt(p, 10) : p))
  const lp = toParts(l)
  const cp = toParts(c)
  const len = Math.max(lp.length, cp.length)
  for (let i = 0; i < len; i++) {
    const a = lp[i]
    const b = cp[i]
    if (a === b) continue
    if (typeof a === 'number' && typeof b === 'number') return a > b
    // 混合型直接降级到字符串比较
    return String(a ?? '') > String(b ?? '')
  }
  return false
}

export function ChangelogDialog({ open, onOpenChange }: Props) {
  const t = useT()
  const [releases, setReleases] = useState<GithubRelease[]>([])
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const loadedOnceRef = useRef(false)

  const currentVersion =
    (import.meta.env.VITE_APP_VERSION as string | undefined)?.trim() || null

  const latestRelease = releases[0] || null
  const latestVersion = latestRelease?.tag_name || null
  const hasNew = isNewerVersion(latestVersion, currentVersion)

  const loadPage = useCallback(async (p: number) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(
        `${GITHUB_API_BASE}/releases?per_page=${PAGE_SIZE}&page=${p}`,
        {
          headers: { Accept: 'application/vnd.github+json' }
        }
      )
      if (!res.ok) throw new Error(`GitHub ${res.status}`)
      const data = (await res.json()) as GithubRelease[]
      // 过滤 draft / prerelease — prerelease 仍展示,draft 一般外部看不到但保险
      const cleaned = data.filter((r) => !r.draft)
      setReleases((prev) => (p === 1 ? cleaned : [...prev, ...cleaned]))
      if (cleaned.length < PAGE_SIZE) setHasMore(false)
      setPage(p)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  // 首次打开时加载第一页(已打开过一次后切关/开不重复拉,避免浪费 GitHub 限额)
  useEffect(() => {
    if (!open) return
    if (loadedOnceRef.current) return
    loadedOnceRef.current = true
    void loadPage(1)
  }, [open, loadPage])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>{t('changelog.title')}</DialogTitle>
        </DialogHeader>

        {/* 版本对比 header:左中右三栏 —— 当前版本 / 最新版本 / 状态 CTA。
            - 已是最新:绿色 CheckCircle2 + "已是最新"
            - 有新版本:琥珀色 Sparkles + "升级到 vX" 按钮,点击跳 GitHub release
            - 版本信息不完整(如本地 dev 或 GitHub 拉失败):只显示版本号,不弹 CTA */}
        <div
          className={`flex items-center gap-3 rounded-xl border px-4 py-3.5 transition ${
            hasNew
              ? 'border-amber-500/30 bg-amber-500/5'
              : latestVersion && currentVersion
                ? 'border-emerald-500/30 bg-emerald-500/5'
                : 'border-border/60 bg-muted/30'
          }`}
        >
          <div className="flex flex-1 items-center gap-6">
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('changelog.currentVersion')}
              </span>
              <span className="font-mono text-sm font-semibold">
                {currentVersion || 'dev'}
              </span>
            </div>
            <div className="h-6 w-px bg-border/60" aria-hidden />
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('changelog.latestVersion')}
              </span>
              <span className="font-mono text-sm font-semibold">
                {latestVersion || '—'}
              </span>
            </div>
          </div>
          <div className="shrink-0">
            {latestVersion && currentVersion ? (
              hasNew ? (
                <a
                  href={latestRelease?.html_url || GITHUB_RELEASES_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-full bg-amber-500 px-3.5 py-1.5 text-[11px] font-semibold text-white shadow-sm transition hover:bg-amber-600"
                >
                  <Sparkles className="h-3.5 w-3.5" />
                  {t('changelog.newAvailable')}
                  <ArrowUpRight className="h-3.5 w-3.5" />
                </a>
              ) : (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-3 py-1.5 text-[11px] font-semibold text-emerald-700 dark:text-emerald-400">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  {t('changelog.upToDate')}
                </span>
              )
            ) : null}
          </div>
        </div>

        {/* Release 列表 */}
        <div
          className="-mr-2 max-h-[55vh] space-y-4 overflow-y-auto pr-2"
          onScroll={(e) => {
            const el = e.currentTarget
            if (
              hasMore &&
              !loading &&
              el.scrollHeight - el.scrollTop - el.clientHeight < 120
            ) {
              void loadPage(page + 1)
            }
          }}
        >
          {releases.map((r) => (
            <div
              key={r.id}
              className="rounded-lg border border-border/50 bg-card px-4 py-3"
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-semibold">
                      {r.tag_name}
                    </span>
                    {r.prerelease ? (
                      <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                        pre-release
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-0.5 text-[11px] text-muted-foreground">
                    {t('changelog.published')} {formatDate(r.published_at)}
                  </div>
                </div>
                <a
                  href={r.html_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[11px] text-primary hover:underline"
                >
                  {t('changelog.openGithub')}
                </a>
              </div>
              {r.body ? (
                <div
                  className="prose prose-sm max-w-none dark:prose-invert"
                  dangerouslySetInnerHTML={{ __html: renderMarkdownLite(r.body) }}
                />
              ) : (
                <p className="text-[12px] italic text-muted-foreground">
                  (empty release note)
                </p>
              )}
            </div>
          ))}

          {/* 底部状态 */}
          <div className="flex items-center justify-center gap-2 py-2 text-xs text-muted-foreground">
            {loading ? (
              <span>{t('changelog.loading')}</span>
            ) : error ? (
              <>
                <span className="text-destructive">{t('changelog.loadError')}</span>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7"
                  onClick={() => void loadPage(page)}
                >
                  retry
                </Button>
              </>
            ) : !hasMore ? (
              releases.length > 0 ? (
                <span>{t('changelog.noMore')}</span>
              ) : (
                <a
                  href={GITHUB_RELEASES_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline"
                >
                  {t('changelog.openGithub')}
                </a>
              )
            ) : (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-[11px]"
                onClick={() => void loadPage(page + 1)}
              >
                {t('changelog.loadMore')}
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
