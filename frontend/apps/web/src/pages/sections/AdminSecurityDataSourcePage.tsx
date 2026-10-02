import { useCallback, useEffect, useRef, useState } from 'react'
import { Database, RefreshCcw } from 'lucide-react'

import {
  fetchSecurityDataSource,
  testSecurityDataSource,
  updateSecurityDataSource,
  type SecurityDataSourceConfig,
  type SecurityDataSourceProvider,
} from '@beecount/api-client'
import { Button, Card, CardContent, Input, useT, useToast } from '@beecount/ui'

import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'

/**
 * 管理員 · 股票資料來源(Phase 3,docs/STOCK_HOLDINGS_SD.md §11)。版面/權限守衛
 * 比照 `AdminAppVersionPage`:draft state + 首次載入 fetch。
 *
 * API key 刻意不回顯(server 只回 `api_key_set` 布林),所以 draft 永遠從空字串開始,
 * 空白 = 不變更;要移除只能按「清除 API key」(PUT clear_api_key)。
 */

const PROVIDERS: SecurityDataSourceProvider[] = ['free', 'twelvedata']

/**
 * 防禦性遮罩:httpx 的錯誤訊息會帶完整 URL(含 `apikey=...`),server 若原樣存進
 * `last_test_error` / 回在 `message`,顯示前先把金鑰參數換掉,避免在畫面/截圖洩漏。
 * (治本要在 server 端處理,這裡只是最後一道。)
 */
export function redactApiKeyParam(text: string): string {
  return text.replace(/(api_?key=)[^&\s'"]+/gi, '$1***')
}

export function AdminSecurityDataSourcePage() {
  const t = useT()
  const toast = useToast()
  const { token, isAdmin, isAdminResolved } = useAuth()

  const [config, setConfig] = useState<SecurityDataSourceConfig | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [providerDraft, setProviderDraft] = useState<SecurityDataSourceProvider>('free')
  const [apiKeyDraft, setApiKeyDraft] = useState('')

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  const applyConfig = useCallback((row: SecurityDataSourceConfig) => {
    setConfig(row)
    setProviderDraft(row.provider)
    setApiKeyDraft('')
  }, [])

  // toast/t 的引用會隨每次顯示 toast 改變;載入 effect 若依賴它,每跳一次 toast 就會
  // 重新 fetch 並把使用者還沒送出的 draft 蓋掉,所以用 ref 取最新的 notifyError。
  const notifyErrorRef = useRef(notifyError)
  notifyErrorRef.current = notifyError

  const refresh = useCallback(async () => {
    if (!isAdmin) return
    setLoading(true)
    try {
      applyConfig(await fetchSecurityDataSource(token))
    } catch (err) {
      notifyErrorRef.current(err)
    } finally {
      setLoading(false)
    }
  }, [token, isAdmin, applyConfig])

  useEffect(() => {
    if (!isAdminResolved || !isAdmin) return
    void refresh()
  }, [isAdminResolved, isAdmin, refresh])

  const handleSave = useCallback(async () => {
    const key = apiKeyDraft.trim()
    // server 在 provider=twelvedata 但沒有已存 key 時會回 400,先在前端擋並給明確文案。
    if (providerDraft === 'twelvedata' && !key && !config?.api_key_set) {
      toast.error(t('admin.securityDataSource.error.keyRequired'), t('notice.error'))
      return
    }
    setSaving(true)
    try {
      const updated = await updateSecurityDataSource(token, {
        provider: providerDraft,
        ...(key ? { api_key: key } : {}),
      })
      applyConfig(updated)
      toast.success(t('admin.securityDataSource.notice.saved'), t('notice.success'))
    } catch (err) {
      notifyError(err)
    } finally {
      setSaving(false)
    }
  }, [token, providerDraft, apiKeyDraft, config, applyConfig, toast, t, notifyError])

  const handleClearKey = useCallback(async () => {
    setSaving(true)
    try {
      // server 規則:provider=twelvedata 時不能沒有 key,所以清除 key 同時切回免費來源。
      const switchToFree = config?.provider === 'twelvedata'
      const updated = await updateSecurityDataSource(token, {
        clear_api_key: true,
        ...(switchToFree ? { provider: 'free' as const } : {}),
      })
      applyConfig(updated)
      toast.success(
        t(switchToFree ? 'admin.securityDataSource.notice.keyClearedFree' : 'admin.securityDataSource.notice.keyCleared'),
        t('notice.success'),
      )
    } catch (err) {
      notifyError(err)
    } finally {
      setSaving(false)
    }
  }, [token, config, applyConfig, toast, t, notifyError])

  const handleTest = useCallback(async () => {
    setTesting(true)
    try {
      const result = await testSecurityDataSource(token)
      if (result.ok) {
        toast.success(
          t('admin.securityDataSource.notice.testOk', {
            price: result.price === null ? '—' : String(result.price),
          }),
          t('notice.success'),
        )
      } else {
        toast.error(
          t('admin.securityDataSource.notice.testFailed', { message: redactApiKeyParam(result.message) }),
          t('notice.error'),
        )
      }
      // 重新載入以更新「上次測試時間/錯誤」;不用 applyConfig 以免清掉使用者還沒送出的輸入。
      const row = await fetchSecurityDataSource(token)
      setConfig(row)
    } catch (err) {
      notifyError(err)
    } finally {
      setTesting(false)
    }
  }, [token, toast, t, notifyError])

  const formatDateTime = (iso: string | null | undefined): string => {
    if (!iso) return t('admin.securityDataSource.never')
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return t('admin.securityDataSource.never')
    return d.toLocaleString()
  }

  if (!isAdminResolved) {
    return null
  }

  if (!isAdmin) {
    return (
      <Card className="bc-panel">
        <CardContent className="py-6">
          <p className="text-center text-sm text-muted-foreground">{t('admin.users.noPermission')}</p>
        </CardContent>
      </Card>
    )
  }

  const busy = saving || testing

  return (
    <div className="space-y-4">
      <Card className="bc-panel">
        <CardContent className="flex items-center justify-between gap-4 py-4">
          <div className="flex items-center gap-3">
            <Database className="h-5 w-5 text-primary" />
            <div>
              <h3 className="text-sm font-medium">{t('admin.securityDataSource.title')}</h3>
              <p className="text-xs text-muted-foreground">{t('admin.securityDataSource.subtitle')}</p>
            </div>
          </div>
          <Button size="sm" variant="outline" onClick={() => void refresh()} disabled={loading}>
            <RefreshCcw className={`mr-1.5 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            {t('admin.securityDataSource.refresh')}
          </Button>
        </CardContent>
      </Card>

      <Card className="bc-panel">
        <CardContent className="space-y-4 py-4">
          <fieldset className="space-y-2" disabled={busy}>
            <legend className="text-xs text-muted-foreground">{t('admin.securityDataSource.provider')}</legend>
            {PROVIDERS.map((p) => (
              <label
                key={p}
                className={`flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm ${
                  providerDraft === p ? 'border-primary bg-primary/10' : 'border-input hover:bg-accent/40'
                }`}
              >
                <input
                  type="radio"
                  name="security-data-source-provider"
                  value={p}
                  checked={providerDraft === p}
                  onChange={() => setProviderDraft(p)}
                />
                {t(`admin.securityDataSource.provider.${p}`)}
              </label>
            ))}
          </fieldset>

          <div className="space-y-1">
            <label className="text-xs text-muted-foreground" htmlFor="security-data-source-key">
              {t('admin.securityDataSource.apiKey')}
            </label>
            <div className="flex items-center gap-2">
              <Input
                id="security-data-source-key"
                type="password"
                autoComplete="new-password"
                className="h-9"
                value={apiKeyDraft}
                onChange={(e) => setApiKeyDraft(e.target.value)}
                placeholder={config?.api_key_set ? t('admin.securityDataSource.apiKeySetPlaceholder') : ''}
                disabled={busy}
              />
              {config?.api_key_set ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="shrink-0"
                  onClick={() => void handleClearKey()}
                  disabled={busy}
                >
                  {t('admin.securityDataSource.clearApiKey')}
                </Button>
              ) : null}
            </div>
            <p className="text-xs text-muted-foreground">{t('admin.securityDataSource.hint')}</p>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-border/50 pt-3 text-xs text-muted-foreground">
            <span>
              {t('admin.securityDataSource.lastTestAt')}: {formatDateTime(config?.last_test_at)}
            </span>
            {config?.last_test_error ? (
              <span className="text-destructive">
                {t('admin.securityDataSource.lastTestError')}: {redactApiKeyParam(config.last_test_error)}
              </span>
            ) : null}
          </div>

          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => void handleSave()} disabled={busy}>
              {t('admin.securityDataSource.save')}
            </Button>
            <Button size="sm" variant="outline" onClick={() => void handleTest()} disabled={busy}>
              {testing ? t('admin.securityDataSource.testing') : t('admin.securityDataSource.test')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
