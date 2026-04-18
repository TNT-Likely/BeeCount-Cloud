/* BeeCount Web service worker
 *
 * 仅做"网络优先 + 静态资源兜底"，不尝试离线完整运行 —— 账本数据强依赖
 * 后端。主要价值：
 *   1. 可安装到桌面/主屏幕（PWA 基础需求）。
 *   2. 弱网 / 离线时至少能看到骨架 + 登录页 shell，而不是浏览器默认的
 *      "无法访问此网站"。
 *
 * API 请求（/api/*）绕过 SW —— 直接去网络，避免缓存 token/敏感数据。
 */

const CACHE_VERSION = 'beecount-web-v1'
const PRECACHE = ['/', '/index.html', '/manifest.webmanifest', '/branding/logo.svg']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(PRECACHE).catch(() => undefined))
  )
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_VERSION)
          .map((key) => caches.delete(key))
      )
    )
  )
  self.clients.claim()
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return

  const url = new URL(req.url)

  // /api/* 永远走网络；不缓存任何带 auth 的响应。
  if (url.pathname.startsWith('/api/')) return

  // 跨域资源（CDN 字体等）也直接走网络，sw 只管自家 origin。
  if (url.origin !== self.location.origin) return

  // 导航请求 → 网络优先 + cache 兜底（保证 index.html 能在断网时打开）
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

  // 静态资源 → cache 优先，网络回填
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
