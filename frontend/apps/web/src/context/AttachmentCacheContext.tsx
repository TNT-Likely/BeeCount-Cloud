import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { downloadAttachment } from '@beecount/api-client'

import { useAuth } from './AuthContext'

const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'webp', 'gif', 'heic'])

function isPreviewableImage(mimeType: string | null, fileName: string | null): boolean {
  if (mimeType && mimeType.trim().toLowerCase().startsWith('image/')) return true
  const normalizedName = (fileName || '').trim().toLowerCase()
  const extension = normalizedName.includes('.') ? normalizedName.split('.').pop() || '' : ''
  return IMAGE_EXTENSIONS.has(extension)
}

/**
 * 全局附件 blob URL cache —— 分类自定义图标 / 交易附件预览 / 账户头像等
 * 所有 "拿 fileId 下载然后 objectURL 展示" 的场景都走这里。
 *
 * 行为:
 *   - 同一 fileId 只下载一次,结果 cache 到进程寿命周期
 *   - 下载 failure / 非图片类型 → entry 依然会写入(null),避免重试轰炸
 *   - Provider unmount 时统一 revokeObjectURL 释放内存
 *
 * 不处理:
 *   - LRU 淘汰(web 端图标量级很小,几十到几百个 blob 不会撑爆)
 *   - 跨标签页共享(每个 tab 自己一份,简单可预期)
 *
 * 为什么不做 Suspense 风格的 API:
 *   消费方都是列表里的条目,List 里抛 Promise 不好控 fallback 粒度。现在
 *   暴露 `previewMap[fileId]` 同步读 + `ensureLoaded(fileId)` 后台触发,
 *   UI 第一次渲染不自带 blob(走默认图标 fallback),拉回来 set state 后
 *   自然 re-render 显示 —— 跟之前 AppPage 的行为一致。
 */
export interface AttachmentCacheContextValue {
  /** fileId → blob URL(`blob:xxx`)。查不到就是还没下载或下载失败。 */
  previewMap: Record<string, string>
  /** 触发下载(如果没下载过);image 类型才会产生 blob URL。 */
  ensureLoaded: (fileId: string) => void
  /** 批量触发 —— 列表进入时一次性把所有可见行的 fileId 推进来。 */
  ensureLoadedMany: (fileIds: string[]) => void
}

const AttachmentCacheContext = createContext<AttachmentCacheContextValue | null>(null)

/**
 * 挂在 AppShell 下,全局单例。`useAuth().token` 用来请求下载;token 变化
 * 时会重建 Provider(不清理旧 cache,保持 blob 有效 —— 同一账号切刷新 token
 * 不应当让用户看到图标闪烁)。
 */
export function AttachmentCacheProvider({ children }: { children: ReactNode }) {
  const { token } = useAuth()
  const [previewMap, setPreviewMap] = useState<Record<string, string>>({})
  const inflightRef = useRef<Set<string>>(new Set())
  const previewMapRef = useRef(previewMap)
  previewMapRef.current = previewMap

  const ensureLoaded = useCallback(
    (fileId: string) => {
      const normalized = fileId.trim()
      if (!normalized) return
      if (previewMapRef.current[normalized] !== undefined) return
      if (inflightRef.current.has(normalized)) return
      inflightRef.current.add(normalized)
      void (async () => {
        try {
          const response = await downloadAttachment(token, normalized)
          if (!isPreviewableImage(response.mimeType, response.fileName)) {
            // 不是图片:写空串标记"已探测过,不再重试"。
            setPreviewMap((prev) => ({ ...prev, [normalized]: '' }))
            return
          }
          const url = URL.createObjectURL(response.blob)
          setPreviewMap((prev) => {
            if (prev[normalized]) {
              URL.revokeObjectURL(url)
              return prev
            }
            return { ...prev, [normalized]: url }
          })
        } catch {
          // 下载失败也写空串,防止 UI 跟着 retry 风暴
          setPreviewMap((prev) => ({ ...prev, [normalized]: '' }))
        } finally {
          inflightRef.current.delete(normalized)
        }
      })()
    },
    [token]
  )

  const ensureLoadedMany = useCallback(
    (fileIds: string[]) => {
      for (const id of fileIds) ensureLoaded(id)
    },
    [ensureLoaded]
  )

  // Provider unmount(登出 / 切换用户)时一次性释放所有 blob URL。
  useEffect(() => {
    return () => {
      for (const url of Object.values(previewMapRef.current)) {
        if (url) URL.revokeObjectURL(url)
      }
    }
  }, [])

  const value = useMemo<AttachmentCacheContextValue>(
    () => ({ previewMap, ensureLoaded, ensureLoadedMany }),
    [previewMap, ensureLoaded, ensureLoadedMany]
  )

  return (
    <AttachmentCacheContext.Provider value={value}>{children}</AttachmentCacheContext.Provider>
  )
}

export function useAttachmentCache(): AttachmentCacheContextValue {
  const ctx = useContext(AttachmentCacheContext)
  if (!ctx) throw new Error('useAttachmentCache must be used inside <AttachmentCacheProvider>')
  return ctx
}
