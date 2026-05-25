/**
 * PWA service worker 升级通知 lifecycle —— 桥接 SW 的「检测到 waiting 版本」
 * 信号到 React 层。
 *
 * 之前 sw.js 在 install 阶段 self.skipWaiting() 直接抢占,用户哪怕在填表单
 * 都会被无声 reload。现在 SW 改为不主动 skipWaiting,这里检测到 waiting 后:
 *   1. 派发 CustomEvent 'pwa:sw-update-available'
 *   2. App 顶部的 SwUpdateBanner 监听,弹「检测到新版本」+「立即更新」按钮
 *   3. 用户点击后这里再通过 postMessage 向 waiting SW 发 SKIP_WAITING
 *   4. SW skipWaiting + 自动 controllerchange,刷新页面拿新版
 *
 * 检测三种触发条件,任一命中都把信号传出去:
 *   A. register 时已经有 waiting:页面进来时 SW 早就升级好了,只是没激活
 *   B. updatefound + installed + 有 controller:运行中检测到新版本下载完
 *   C. controllerchange:SW 主动 takeover(skipWaiting 之后),触发 reload
 */

export const SW_UPDATE_EVENT = 'pwa:sw-update-available'
export const SW_UPDATE_ACCEPT_EVENT = 'pwa:sw-update-accept'

let activeRegistration: ServiceWorkerRegistration | null = null

export function setupServiceWorkerUpdates(registration: ServiceWorkerRegistration): void {
  activeRegistration = registration

  // A) 一进来就有 waiting:说明上次访问时 SW 已经 install 完但没激活
  if (registration.waiting && navigator.serviceWorker.controller) {
    dispatchUpdateAvailable()
  }

  // B) 运行中 update found
  registration.addEventListener('updatefound', () => {
    const sw = registration.installing
    if (!sw) return
    sw.addEventListener('statechange', () => {
      if (sw.state === 'installed' && navigator.serviceWorker.controller) {
        dispatchUpdateAvailable()
      }
    })
  })

  // C) controller 切换 → 用户点了「立即更新」后,SW skipWaiting,这里
  //    captured 一次 reload。refreshing 守一下避免循环 reload(StrictMode
  //    场景下 listener 可能短暂被加两遍)。
  let refreshing = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (refreshing) return
    refreshing = true
    window.location.reload()
  })

  // UI 侧确认「立即更新」后回调进来:posetMessage 给 waiting SW
  window.addEventListener(SW_UPDATE_ACCEPT_EVENT, () => {
    const waiting = activeRegistration?.waiting
    if (!waiting) return
    waiting.postMessage({ type: 'SKIP_WAITING' })
  })

  // 后台周期性检查更新 —— 标签页存活时每 30 分钟跑一次 registration.update(),
  // 这样长期挂着的标签页也能及时看到新版本(浏览器默认只在导航时检查)。
  // 不在乎失败,静默吞掉。
  const POLL_MS = 30 * 60 * 1000
  setInterval(() => {
    activeRegistration?.update().catch(() => undefined)
  }, POLL_MS)
}

function dispatchUpdateAvailable(): void {
  window.dispatchEvent(new CustomEvent(SW_UPDATE_EVENT))
}

/** 给 UI 用 —— 用户点「立即更新」时调,会触发 SW 替换 + 页面 reload */
export function acceptServiceWorkerUpdate(): void {
  window.dispatchEvent(new CustomEvent(SW_UPDATE_ACCEPT_EVENT))
}
