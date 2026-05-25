/* BeeCount Web service worker
 *
 * 仅做"网络优先 + 静态资源兜底"，不尝试离线完整运行 —— 账本数据强依赖
 * 后端。主要价值：
 *   1. 可安装到桌面/主屏幕（PWA 基础需求）。
 *   2. 弱网 / 离线时至少能看到骨架 + 登录页 shell，而不是浏览器默认的
 *      "无法访问此网站"。
 *   3. PWA 增强 (manifest.webmanifest 配套):
 *      - POST /share-receive   → Share Target,缓存分享内容后跳 SPA 处理页
 *      - waiting/SKIP_WAITING  → 让 UI 控制更新时机,而不是默默刷新
 *
 * API 请求（/api/*）绕过 SW —— 直接去网络，避免缓存 token/敏感数据。
 */

const CACHE_VERSION = 'beecount-web-v2'
const SHARE_CACHE = 'beecount-share-target-v1'
const PRECACHE = ['/', '/index.html', '/manifest.webmanifest', '/branding/logo.svg']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(PRECACHE).catch(() => undefined))
  )
  // 不再默认 skipWaiting —— 改由前端检测到 waiting 后,通过 postMessage 主动
  // 触发 (UI 上有「检测到新版本」banner)。这样用户在编辑表单时不会被强制
  // reload 丢数据。开发模式下可以配置直接 skip,生产留给 UI 决定。
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys()
      await Promise.all(
        keys
          .filter((key) => key !== CACHE_VERSION && key !== SHARE_CACHE)
          .map((key) => caches.delete(key))
      )
      await self.clients.claim()
    })()
  )
})

// 接收 UI 触发的 SKIP_WAITING —— 用户在「检测到新版本」banner 上点击「立即
// 更新」后,前端 postMessage 进来,SW 跳过 waiting 状态并刷新页面。
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting()
  }
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  const url = new URL(req.url)

  // Share Target 接收 (POST /share-receive) —— 把 FormData 拆成多个 Response
  // 存到独立 cache,然后 303 跳到 SPA 路由 /app/share-incoming 由前端继续处理。
  // 必须用独立 cache (SHARE_CACHE),版本升级时不被 activate 清掉。
  if (
    req.method === 'POST' &&
    url.pathname === '/share-receive' &&
    url.origin === self.location.origin
  ) {
    event.respondWith(handleShareReceive(req))
    return
  }

  if (req.method !== 'GET') return

  // /api/* 永远走网络;不缓存任何带 auth 的响应。
  if (url.pathname.startsWith('/api/')) return

  // 跨域资源(CDN 字体等)也直接走网络,sw 只管自家 origin。
  if (url.origin !== self.location.origin) return

  // 导航请求 → 网络优先 + cache 兜底(保证 index.html 能在断网时打开)
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone()
          caches.open(CACHE_VERSION).then((cache) => cache.put('/index.html', copy))
          return res
        })
        .catch(() => caches.match('/index.html').then((res) => res || caches.match('/')))
    )
    return
  }

  // 静态资源 → cache 优先,网络回填
  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached
      return fetch(req)
        .then((res) => {
          if (res.ok && res.type === 'basic') {
            const copy = res.clone()
            caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy))
          }
          return res
        })
        .catch(() => cached)
    })
  )
})

/**
 * 处理 Share Target POST。把 FormData 各字段分别存到 SHARE_CACHE,SPA 路由
 * 进入 /app/share-incoming 后逐个 cache.match 取出消费。设计成单次使用 —
 * SPA 处理完会 caches.delete(SHARE_CACHE)。
 */
async function handleShareReceive(request) {
  try {
    const formData = await request.formData()
    const cache = await caches.open(SHARE_CACHE)

    // 先清掉上一次残留(如果有的话),避免多次 share 串味
    const oldKeys = await cache.keys()
    await Promise.all(oldKeys.map((k) => cache.delete(k)))

    // meta:title / text / url
    const meta = {
      title: formData.get('title') || '',
      text: formData.get('text') || '',
      url: formData.get('url') || '',
      receivedAt: Date.now(),
      fileCount: 0
    }

    // files:支持多个文件,逐个序列化到 cache (key = /share-target/file-<i>)
    // X-File-Name / X-File-Type header 携带原始文件名,前端读出来给 ImportPage
    // 或 AI 提取流。
    const files = formData.getAll('files')
    let fileIndex = 0
    for (const file of files) {
      if (file instanceof File) {
        await cache.put(
          `/share-target/file-${fileIndex}`,
          new Response(file, {
            headers: {
              'Content-Type': file.type || 'application/octet-stream',
              'X-File-Name': encodeURIComponent(file.name)
            }
          })
        )
        fileIndex += 1
      }
    }
    meta.fileCount = fileIndex

    await cache.put(
      '/share-target/meta',
      new Response(JSON.stringify(meta), {
        headers: { 'Content-Type': 'application/json' }
      })
    )

    return Response.redirect('/app/share-incoming', 303)
  } catch (err) {
    // 失败也要给个回退,不然浏览器会停在 about:blank
    // eslint-disable-next-line no-console
    console.warn('[sw] share-receive failed', err)
    return Response.redirect('/app/transactions?share-error=1', 303)
  }
}
