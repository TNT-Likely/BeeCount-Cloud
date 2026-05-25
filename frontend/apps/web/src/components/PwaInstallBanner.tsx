import { useEffect, useState } from 'react'
import { Download, X } from 'lucide-react'

import { Button, useT, useToast } from '@beecount/ui'

import {
  neverShowInstallPrompt,
  PWA_INSTALL_AVAILABLE_EVENT,
  triggerInstallPrompt,
} from '../lib/pwa-install'
import { isStandalonePwa } from '../lib/pwa-runtime'

/**
 * 「添加到桌面?」 banner —— 取代浏览器默认的浅色提示条。
 *
 * 显示规则(详见 pwa-install.ts):
 *   - 用户在 7 天内访问 ≥ 3 次才会广播 PWA_INSTALL_AVAILABLE_EVENT
 *   - 永久拒绝过的用户(neverShow)不再广播
 *
 * 三个按钮:
 *   - 添加 → 调浏览器原生 install API
 *   - 关闭 → 本次会话不再弹(sessionStorage)
 *   - 不再提示 → 永久不再弹(localStorage,跨会话)
 */
const SESSION_DISMISS_KEY = 'beecount.pwa.install-dismissed-session'

export function PwaInstallBanner() {
  const t = useT()
  const toast = useToast()
  const [visible, setVisible] = useState(false)
  const [installing, setInstalling] = useState(false)

  useEffect(() => {
    if (sessionStorage.getItem(SESSION_DISMISS_KEY) === '1') return
    // 已经在 PWA 模式里了(从 dock / 主屏启动),不需要再引导安装。
    // 理论上浏览器在这种状态下也不会再 fire beforeinstallprompt,这里
    // 显式 gate 一道做防御,避免假阳性弹出。
    if (isStandalonePwa()) return

    const onAvailable = () => setVisible(true)
    window.addEventListener(PWA_INSTALL_AVAILABLE_EVENT, onAvailable)
    return () => window.removeEventListener(PWA_INSTALL_AVAILABLE_EVENT, onAvailable)
  }, [])

  if (!visible) return null

  return (
    <div className="fixed bottom-20 left-1/2 z-50 -translate-x-1/2 px-3 sm:bottom-6 sm:right-4 sm:left-auto sm:translate-x-0">
      <div className="flex max-w-md items-center gap-3 rounded-lg border border-border bg-background/95 px-4 py-3 shadow-lg backdrop-blur">
        <Download className="h-5 w-5 shrink-0 text-primary" />
        <div className="flex-1 text-sm">
          <p className="font-medium">{t('pwa.install.title')}</p>
          <p className="text-xs text-muted-foreground">{t('pwa.install.subtitle')}</p>
        </div>
        <div className="flex shrink-0 flex-col gap-1">
          <Button
            size="sm"
            className="h-7 px-3"
            disabled={installing}
            onClick={async () => {
              setInstalling(true)
              const outcome = await triggerInstallPrompt()
              setInstalling(false)
              if (outcome === 'accepted') {
                toast.success(t('pwa.install.accepted'))
              }
              setVisible(false)
            }}
          >
            {t('pwa.install.apply')}
          </Button>
          <button
            type="button"
            className="text-[11px] text-muted-foreground hover:text-foreground"
            onClick={() => {
              neverShowInstallPrompt()
              setVisible(false)
            }}
          >
            {t('pwa.install.never')}
          </button>
        </div>
        <button
          type="button"
          className="absolute right-1 top-1 rounded p-1 text-muted-foreground hover:bg-muted"
          aria-label={t('common.close') as string}
          onClick={() => {
            sessionStorage.setItem(SESSION_DISMISS_KEY, '1')
            setVisible(false)
          }}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
