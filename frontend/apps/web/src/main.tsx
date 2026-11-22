import React from 'react'
import { createRoot } from 'react-dom/client'
import {
  LocaleProvider,
  PrimaryColorProvider,
  ThemeProvider,
  ToastProvider,
  applyDocumentLocale,
  applyPrimaryColor,
  applyTheme,
  initialLocale,
  initialPrimaryColor,
  initialThemeMode
} from '@beecount/ui'

import { App } from './App'
import { WebConfigProvider } from './context/WebConfigContext'
import { dictionaries } from './i18n'
import { setupInstallPrompt } from './lib/pwa-install'
import { setupLaunchQueue } from './lib/pwa-launch'
import { setupServiceWorkerUpdates } from './lib/pwa-sw-update'
import './styles.css'

applyTheme(initialThemeMode())
applyDocumentLocale(initialLocale())
// 启动时立刻 apply primary color，避免 React hydration 前首屏闪烁默认金色。
applyPrimaryColor(initialPrimaryColor())

// File Handler:订阅 launchQueue,接住「双击 .csv 用蜜蜂记账打开」的文件
setupLaunchQueue()

// PWA install prompt:接住 beforeinstallprompt,延后到 engagement 达标再弹
setupInstallPrompt()

// PWA service worker 只在生产注册。
// dev 下不能再注册:sw.js 对同源静态资源是 cache-first 且 CACHE_VERSION 固定,
// 会把 Vite 模块(/src/*、/node_modules/.vite/deps/react.js?v=HASH)永久缓存;
// Vite 重启或改 config 后预构建 hash 变化,缓存里的旧模块仍引用
// react.js?v=OLD,与新模块的 react.js?v=NEW 同时加载 → 两份 React →
// Invalid hook call / useState null → 白屏。且 caches.match 不看刷新请求头,
// Ctrl+F5 也清不掉。dev 下的历史 SW 由 vite.config.ts 注入的清理脚本注销。
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  // 原生 fetch API 里 MIME 不对 iOS 会拒绝,所以指定 scope。
  // 注册成功后挂上 update lifecycle —— 检测到 waiting SW 时通过自定义事件
  // 通知 React 层,弹「新版本可用」banner(详见 pwa-sw-update.ts)。
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js', { scope: '/' })
      .then((registration) => {
        setupServiceWorkerUpdates(registration)
      })
      .catch((err) => {
        // 不影响主流程，只在控制台留个记录。
        // eslint-disable-next-line no-console
        console.warn('[pwa] sw register failed', err)
      })
  })
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LocaleProvider dictionaries={dictionaries}>
      <ThemeProvider>
        <PrimaryColorProvider>
          <ToastProvider>
            <WebConfigProvider>
              <App />
            </WebConfigProvider>
          </ToastProvider>
        </PrimaryColorProvider>
      </ThemeProvider>
    </LocaleProvider>
  </React.StrictMode>
)
