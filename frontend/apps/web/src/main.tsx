import React from 'react'
import { createRoot } from 'react-dom/client'
import {
  LocaleProvider,
  ThemeProvider,
  ToastProvider,
  applyDocumentLocale,
  applyTheme,
  initialLocale,
  initialThemeMode
} from '@beecount/ui'

import { App } from './App'
import { dictionaries } from './i18n'
import './styles.css'

applyTheme(initialThemeMode())
applyDocumentLocale(initialLocale())

// 注册 PWA service worker：生产 + dev 都注册，dev 下 sw 只管静态资源不影响
// HMR（/api 被跳过）。原生 fetch API 里 MIME 不对 iOS 会拒绝，所以指定 scope。
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/sw.js', { scope: '/' })
      .catch((err) => {
        // 不影响主流程，只在 dev 控制台留个记录。
        // eslint-disable-next-line no-console
        console.warn('[pwa] sw register failed', err)
      })
  })
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LocaleProvider dictionaries={dictionaries}>
      <ThemeProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </ThemeProvider>
    </LocaleProvider>
  </React.StrictMode>
)
