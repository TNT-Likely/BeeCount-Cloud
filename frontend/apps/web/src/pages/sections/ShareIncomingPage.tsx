import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { useT, useToast } from '@beecount/ui'

import { setPendingImportFile, setPendingShareText } from '../../lib/pwa-intake'

type Stage = 'reading' | 'routing' | 'error'

const SHARE_CACHE = 'beecount-share-target-v1'
const CSV_RE = /\.(csv|tsv|xlsx?)$/i
const IMAGE_RE = /^image\//

/**
 * Share Target / File Handler 的统一着陆页 —— 用户从系统分享菜单选「蜜蜂记账」
 * 或双击 CSV 文件,浏览器都会跳到这里。本页本身不渲染业务,只做:
 *   1. 从 sw.js 的 SHARE_CACHE 读 share target 投递的 FormData
 *   2. 从 window.launchQueue 读 file handler 投递的 FileSystemHandle
 *   3. 按文件类型分发到目标页面(import / transactions),File 通过
 *      pendingImportFile 模块级单例传递(File 无法序列化进 url 或 storage)
 *   4. 处理完清空 cache,防止下次进来还看到旧内容
 *
 * 任何失败兜底跳 /app/transactions,避免用户卡在白屏。整个流程 < 200ms,
 * 用户看到的只是一次短暂的 loading 转场。
 */
export function ShareIncomingPage() {
  const t = useT()
  const toast = useToast()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [stage, setStage] = useState<Stage>('reading')
  const [errorMsg, setErrorMsg] = useState<string>('')
  const consumedRef = useRef(false)

  useEffect(() => {
    if (consumedRef.current) return
    consumedRef.current = true

    const run = async () => {
      const files: File[] = []
      let meta: { title?: string; text?: string; url?: string } = {}

      // 1) Share Target 路径:SW 把 FormData 拆到 SHARE_CACHE
      try {
        if ('caches' in window) {
          const cache = await caches.open(SHARE_CACHE)
          const metaRes = await cache.match('/share-target/meta')
          if (metaRes) {
            try {
              const parsed = await metaRes.json()
              meta = {
                title: typeof parsed.title === 'string' ? parsed.title : '',
                text: typeof parsed.text === 'string' ? parsed.text : '',
                url: typeof parsed.url === 'string' ? parsed.url : ''
              }
              const total = Number(parsed.fileCount || 0)
              for (let i = 0; i < total; i += 1) {
                const fileRes = await cache.match(`/share-target/file-${i}`)
                if (!fileRes) continue
                const blob = await fileRes.blob()
                const name = decodeURIComponent(
                  fileRes.headers.get('X-File-Name') || `share-${i}`
                )
                files.push(new File([blob], name, { type: blob.type }))
              }
            } catch {
              // meta JSON 损坏 → 直接跳到默认页,不报错
            }
            // 单次消费,清干净
            await caches.delete(SHARE_CACHE)
          }
        }
      } catch {
        // cache 访问失败不致命,继续看 launchQueue
      }

      // 2) File Handler 路径:launchQueue.setConsumer 在 main.tsx 注册,跳转
      //    本页前会把 File[] 暂存到 window.__beecountPendingFiles,这里取出。
      try {
        const pending = (window as unknown as { __beecountPendingFiles?: File[] })
          .__beecountPendingFiles
        if (Array.isArray(pending) && pending.length > 0) {
          for (const f of pending) files.push(f)
          ;(window as unknown as { __beecountPendingFiles?: File[] }).__beecountPendingFiles = []
        }
      } catch {
        // ignore
      }

      setStage('routing')

      // 3) 按类型分发
      // 3a) CSV/Excel → 走导入页(File 用 pwa-intake 单例传)
      const csvFile = files.find((f) => CSV_RE.test(f.name))
      if (csvFile) {
        setPendingImportFile(csvFile)
        toast.success(t('pwa.share.routedToImport', { name: csvFile.name }))
        navigate('/app/import', { replace: true })
        return
      }

      // 3b) 图片 → 暂无 web 端 AI 提取能力,给用户提示后跳到「新建交易」
      //     让用户手动填(原图保存为附件的能力等后端 attachment API 接入再加)
      const imageFile = files.find((f) => IMAGE_RE.test(f.type))
      if (imageFile) {
        toast.info(t('pwa.share.imageNotYet'))
        navigate('/app/transactions?action=quick-add&source=share-image', { replace: true })
        return
      }

      // 3c) 纯文本 / URL → 把 meta 暂存,跳「新建交易」预填备注
      const noteSource = [meta.title, meta.text, meta.url].filter(Boolean).join(' ').trim()
      if (noteSource) {
        setPendingShareText(meta)
        navigate('/app/transactions?action=quick-add&source=share-text', { replace: true })
        return
      }

      // 3d) 完全没拿到内容(可能是冷启动后直接访问本路由)→ 默认跳 overview
      const source = searchParams.get('source')
      if (source === 'file-handler') {
        // file handler 但没文件:说明 launchQueue 还没来得及 fire,等一帧再 fallback
        setTimeout(() => navigate('/app/overview', { replace: true }), 200)
        return
      }
      navigate('/app/overview', { replace: true })
    }

    run().catch((err) => {
      setStage('error')
      setErrorMsg(String(err))
      // 5 秒后兜底退回 overview,不让用户卡死
      setTimeout(() => navigate('/app/overview', { replace: true }), 5000)
    })
  }, [navigate, searchParams, t, toast])

  return (
    <div className="flex h-full min-h-[40vh] items-center justify-center">
      <div className="space-y-3 text-center">
        <div className="mx-auto h-6 w-6 animate-spin rounded-full border-2 border-muted border-t-primary" />
        <p className="text-sm text-muted-foreground">
          {stage === 'error'
            ? t('pwa.share.error', { reason: errorMsg })
            : stage === 'routing'
              ? t('pwa.share.routing')
              : t('pwa.share.reading')}
        </p>
      </div>
    </div>
  )
}
