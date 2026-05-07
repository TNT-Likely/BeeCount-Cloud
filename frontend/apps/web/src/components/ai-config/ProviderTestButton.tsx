import { useState } from 'react'
import { AlertCircle, CheckCircle2, Loader2, Play } from 'lucide-react'

import { Button, useT } from '@beecount/ui'
import {
  type AIProvider,
  type TestProviderCapability,
  type TestProviderErrorCode,
  type TestProviderResult,
  testProvider,
} from '@beecount/api-client'

import { useAuth } from '../../context/AuthContext'

type Status = 'idle' | 'running' | 'success' | 'fail'

export type ProviderTestResultSummary = TestProviderResult

interface Props {
  provider: AIProvider
  capability: TestProviderCapability
  /** 父组件想感知结果(用于一键测试聚合 / 错误显示)时透传。 */
  onResult?: (capability: TestProviderCapability, result: TestProviderResult) => void
  /** 没配置对应 model → 按钮变 disabled 加 dash 提示。 */
  disabled?: boolean
  /** 一键测试场景下父级控制 status,组件外部驱动。不传则自管。 */
  externalStatus?: Status
  externalResult?: TestProviderResult | null
}

/**
 * 单能力测试按钮 —— loading / ✓ 通过 / ✗ 失败 三态。
 *
 * 失败时按钮 hover 显示 error_message;父组件可通过 onResult 拿 detail 自己
 * 渲染更显眼的 inline error。本期默认就 hover tooltip(原生 title),够用。
 *
 * 跟 mobile `ai_provider_manage_page.dart` 的「一键测试」+ 单能力测试按钮
 * 视觉对齐(loading / ✓ / ✗ / 重试)。
 */
export function ProviderTestButton({
  provider,
  capability,
  onResult,
  disabled = false,
  externalStatus,
  externalResult,
}: Props) {
  const t = useT()
  const { token } = useAuth()
  const [internalStatus, setInternalStatus] = useState<Status>('idle')
  const [internalResult, setInternalResult] = useState<TestProviderResult | null>(null)

  const status = externalStatus ?? internalStatus
  const result = externalResult ?? internalResult

  const run = async () => {
    setInternalStatus('running')
    setInternalResult(null)
    try {
      const r = await testProvider(token, { provider, capability })
      setInternalResult(r)
      setInternalStatus(r.success ? 'success' : 'fail')
      onResult?.(capability, r)
    } catch (err) {
      // 全局速率限制 / 网络异常 → 抛 ApiError(包括 429 RATE_LIMITED)
      const fallback: TestProviderResult = {
        success: false,
        error_code: 'AI_TEST_RATE_LIMITED',
        error_message: err instanceof Error ? err.message : String(err),
        latency_ms: 0,
        preview: '',
      }
      setInternalResult(fallback)
      setInternalStatus('fail')
      onResult?.(capability, fallback)
    }
  }

  if (disabled) {
    return (
      <span
        className="inline-flex h-7 items-center gap-1 rounded-md border border-dashed border-border/60 px-2 text-[11px] text-muted-foreground"
        title={t('ai.editor.test.disabled.noModel') as string}
      >
        —
      </span>
    )
  }

  const errorMessage =
    result && !result.success
      ? mapErrorMessage(t, result.error_code as TestProviderErrorCode | null, result.error_message)
      : ''

  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className={`h-7 gap-1 px-2 text-[11px] ${
        status === 'success'
          ? 'border-emerald-500/40 text-emerald-600 dark:text-emerald-400'
          : ''
      } ${
        status === 'fail'
          ? 'border-destructive/40 text-destructive hover:text-destructive'
          : ''
      }`}
      onClick={() => void run()}
      disabled={status === 'running'}
      title={
        status === 'fail'
          ? errorMessage
          : status === 'success' && result
            ? t('ai.editor.test.success', { latency: result.latency_ms }) as string
            : (t('ai.editor.test.run') as string)
      }
    >
      {status === 'running' ? (
        <Loader2 className="h-3 w-3 animate-spin" />
      ) : status === 'success' ? (
        <CheckCircle2 className="h-3 w-3" />
      ) : status === 'fail' ? (
        <AlertCircle className="h-3 w-3" />
      ) : (
        <Play className="h-3 w-3" />
      )}
      {status === 'running'
        ? t('ai.editor.test.running')
        : status === 'success'
          ? t('ai.editor.test.success', { latency: result?.latency_ms ?? 0 })
          : status === 'fail'
            ? t('ai.editor.test.fail')
            : t('ai.editor.test.run')}
    </Button>
  )
}

function mapErrorMessage(
  t: ReturnType<typeof useT>,
  code: TestProviderErrorCode | null,
  fallback: string | null | undefined,
): string {
  if (code) {
    const key = `ai.editor.test.error.${code}`
    const localized = t(key)
    // i18n 没命中 key 时 useT 返回 key 本身,fallback 到 server message
    if (localized !== key) return localized as string
  }
  return fallback || (t('ai.editor.test.error.AI_TEST_UNKNOWN') as string)
}
