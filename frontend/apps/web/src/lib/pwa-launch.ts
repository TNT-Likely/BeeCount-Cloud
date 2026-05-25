/**
 * PWA File Handler 入口 —— 当用户双击 .csv / .xlsx 文件且本应用是默认处理
 * 程序时,浏览器把 LaunchParams 投递到 launchQueue。这里订阅消费,把 File
 * 暂存到 window.__beecountPendingFiles + 跳到 /app/share-incoming,由
 * ShareIncomingPage 统一分发。
 *
 * launchQueue 标准只有 Chromium 实现(2024+),Safari/Firefox 没有,运行时
 * `'launchQueue' in window` 守一下即可,不影响其他浏览器。
 */

declare global {
  interface Window {
    /** Chromium-only API,在不支持的浏览器中 undefined */
    launchQueue?: {
      setConsumer: (consumer: (params: LaunchParamsLike) => void | Promise<void>) => void
    }
    __beecountPendingFiles?: File[]
  }
}

interface LaunchParamsLike {
  files?: Array<{ getFile: () => Promise<File> }>
  targetURL?: string
}

export function setupLaunchQueue(): void {
  if (typeof window === 'undefined') return
  if (!('launchQueue' in window) || !window.launchQueue) return

  window.launchQueue.setConsumer(async (launchParams) => {
    if (!launchParams.files || launchParams.files.length === 0) return

    const files: File[] = []
    for (const handle of launchParams.files) {
      try {
        const file = await handle.getFile()
        files.push(file)
      } catch {
        // 单文件读失败不阻塞其它,后续仍可处理
      }
    }
    if (files.length === 0) return

    window.__beecountPendingFiles = files

    // 已经在 /app/share-incoming 时不重复跳;否则用 location.assign 走完整
    // navigation 触发 SPA 路由加载 ShareIncomingPage(react-router 在 hydrate
    // 前还没接管,这里用 location 最稳)
    if (!window.location.pathname.startsWith('/app/share-incoming')) {
      window.location.assign('/app/share-incoming?source=file-handler')
    }
  })
}
