import { Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'

import { useAuth } from '../../context/AuthContext'

/**
 * AI 配置只读页面 —— 读 profileMe.ai_config 渲染当前 mobile 同步过来的
 * 策略 / 能力绑定 / 服务商列表 / 自定义提示词。web 不提供编辑,避免跟
 * mobile 写冲突(mobile 才有模型测试 / provider 绑定 UI)。
 */
export function SettingsAiPage() {
  const t = useT()
  const { profileMe } = useAuth()

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('ai.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">{t('ai.desc')}</p>
        {!profileMe?.ai_config ? (
          <p className="text-sm text-muted-foreground">{t('ai.empty')}</p>
        ) : (
          <AIConfigReadOnly config={profileMe.ai_config} />
        )}
      </CardContent>
    </Card>
  )
}

function maskApiKey(key: unknown): string | null {
  if (typeof key !== 'string' || key.trim().length === 0) return null
  const s = key.trim()
  if (s.length <= 8) return '•'.repeat(s.length)
  return `${s.slice(0, 4)}•••${s.slice(-4)}`
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function AIConfigReadOnly({ config }: { config: Record<string, any> }) {
  const t = useT()
  const providers = Array.isArray(config.providers) ? config.providers : []
  const binding =
    typeof config.binding === 'object' && config.binding !== null
      ? (config.binding as Record<string, unknown>)
      : {}
  const providerNameById = new Map<string, string>()
  for (const p of providers) {
    if (p && typeof p === 'object' && typeof p.id === 'string') {
      providerNameById.set(p.id, typeof p.name === 'string' ? p.name : p.id)
    }
  }

  const capability = [
    { key: 'textProviderId', label: t('ai.binding.text') },
    { key: 'visionProviderId', label: t('ai.binding.vision') },
    { key: 'speechProviderId', label: t('ai.binding.speech') }
  ]

  const onOff = (v: unknown) =>
    v === true ? t('common.on') : v === false ? t('common.off') : t('common.dash')

  return (
    <div className="space-y-4">
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{t('ai.strategy.label')}</p>
          <p className="mt-1 text-sm font-medium">{config.strategy || t('common.dash')}</p>
        </div>
        <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{t('ai.billExtraction.label')}</p>
          <p className="mt-1 text-sm font-medium">{onOff(config.bill_extraction_enabled)}</p>
        </div>
        <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{t('ai.useVision.label')}</p>
          <p className="mt-1 text-sm font-medium">{onOff(config.use_vision)}</p>
        </div>
      </div>

      <div>
        <p className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('ai.binding.title')}
        </p>
        <div className="space-y-1.5">
          {capability.map((cap) => {
            const providerId = binding[cap.key] as string | undefined
            const name = providerId
              ? providerNameById.get(providerId) || providerId
              : t('common.dash')
            return (
              <div
                key={cap.key}
                className="flex items-center justify-between rounded-md border border-border/60 bg-muted/10 px-3 py-1.5"
              >
                <span className="text-sm">{cap.label}</span>
                <span className="text-sm text-muted-foreground">{name}</span>
              </div>
            )
          })}
        </div>
      </div>

      <div>
        <p className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('ai.providers.title')} ({providers.length})
        </p>
        <div className="space-y-2">
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          {providers.map((p: any, idx: number) => {
            if (!p || typeof p !== 'object') return null
            const name = typeof p.name === 'string' ? p.name : t('ai.providers.unnamed')
            const apiKeyMasked = maskApiKey(p.apiKey)
            return (
              <div
                key={typeof p.id === 'string' ? p.id : idx}
                className="rounded-lg border border-border/60 bg-muted/10 px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{name}</span>
                  {p.isBuiltIn ? (
                    <span className="rounded-full border border-border/60 bg-card px-2 py-0.5 text-[10px] uppercase tracking-wider">
                      {t('ai.providers.badge.builtin')}
                    </span>
                  ) : null}
                </div>
                <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
                  <span className="text-muted-foreground">{t('ai.providers.field.apiKey')}</span>
                  <span className="font-mono">{apiKeyMasked || t('common.unset')}</span>
                  {p.baseUrl ? (
                    <>
                      <span className="text-muted-foreground">{t('ai.providers.field.baseUrl')}</span>
                      <span className="truncate font-mono">{String(p.baseUrl)}</span>
                    </>
                  ) : null}
                  {p.textModel ? (
                    <>
                      <span className="text-muted-foreground">{t('ai.providers.field.textModel')}</span>
                      <span>{String(p.textModel)}</span>
                    </>
                  ) : null}
                  {p.visionModel ? (
                    <>
                      <span className="text-muted-foreground">{t('ai.providers.field.visionModel')}</span>
                      <span>{String(p.visionModel)}</span>
                    </>
                  ) : null}
                  {p.audioModel ? (
                    <>
                      <span className="text-muted-foreground">{t('ai.providers.field.audioModel')}</span>
                      <span>{String(p.audioModel)}</span>
                    </>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {typeof config.custom_prompt === 'string' && config.custom_prompt.trim().length > 0 ? (
        <div>
          <p className="mb-2 text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('ai.customPrompt.title')}
          </p>
          <pre className="whitespace-pre-wrap break-words rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-[12px]">
            {config.custom_prompt}
          </pre>
        </div>
      ) : null}
    </div>
  )
}
