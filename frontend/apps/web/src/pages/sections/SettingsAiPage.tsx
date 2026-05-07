import { useCallback, useState } from 'react'

import { Card, CardContent, useT, useToast } from '@beecount/ui'
import { type AIConfig, patchProfileMe } from '@beecount/api-client'

import { CapabilityBindingCard } from '../../components/ai-config/CapabilityBindingCard'
import { ProvidersCard } from '../../components/ai-config/ProvidersCard'
import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'

/**
 * AI 配置编辑页 —— providers CRUD + capability binding + 高级字段只读折叠。
 *
 * 设计:.docs/web-ai-config-edit.md
 *
 * 关键模式:`saveAiConfig(next: AIConfig)` 是统一入口,所有子卡片改完都
 * 调它做整体 PATCH,不直接碰 server。这样:
 *  - 整体替换语义集中(避免子卡各自漏字段)
 *  - saving lock 集中(改 provider 期间禁用 binding select 等)
 *  - server 广播 profile_change,WS 通道触发 refreshProfile,跨端同步
 */
export function SettingsAiPage() {
  const t = useT()
  const toast = useToast()
  const { token, profileMe, refreshProfile } = useAuth()
  const [saving, setSaving] = useState(false)

  const config = profileMe?.ai_config ?? null

  const saveAiConfig = useCallback(
    async (next: AIConfig) => {
      if (saving) return
      setSaving(true)
      try {
        await patchProfileMe(token, { ai_config: next })
        await refreshProfile()
        toast.success(t('ai.editor.saved'))
      } catch (err) {
        toast.error(localizeError(err, t))
      } finally {
        setSaving(false)
      }
    },
    [saving, token, refreshProfile, toast, t],
  )

  return (
    <div className="space-y-4">
      <ProvidersCard config={config} saving={saving} onSave={saveAiConfig} />
      <CapabilityBindingCard config={config} saving={saving} onSave={saveAiConfig} />

      {!profileMe?.ai_config ? (
        <Card className="bc-panel">
          <CardContent className="px-6 py-4 text-xs text-muted-foreground">
            {t('ai.empty')}
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
