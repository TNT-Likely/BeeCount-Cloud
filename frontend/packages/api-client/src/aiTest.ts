/**
 * `POST /ai/test-provider` 客户端 —— 测试某个 AI 服务商在指定 capability
 * 上是否可用。
 *
 * 设计:.docs/web-ai-config-edit.md §2.4 / §3.6
 *
 * 失败用 `200 + body.success=false + error_code` 而不是抛 ApiError —— 测试
 * 是诊断工具,前端按 enum 渲染 i18n 友好提示。仅 server 速率限制(429)会
 * 抛 ApiError,这种情况调用方需要 catch 并显示 RATE_LIMITED toast。
 */
import { authedPost } from './http'
import type { AIProvider } from './types'

export type TestProviderCapability = 'text' | 'vision' | 'speech'

export type TestProviderErrorCode =
  | 'AI_TEST_AUTH'
  | 'AI_TEST_MODEL_NOT_FOUND'
  | 'AI_TEST_TIMEOUT'
  | 'AI_TEST_NETWORK'
  | 'AI_TEST_RATE_LIMITED'
  | 'AI_TEST_MISSING_FIELDS'
  | 'AI_TEST_UNKNOWN'

export type TestProviderResult = {
  success: boolean
  error_code?: TestProviderErrorCode | null
  error_message?: string | null
  latency_ms: number
  preview: string
}

export async function testProvider(
  token: string,
  options: {
    /** 完整 provider 配置 — 不依赖 server 已存的版本,允许 dialog 里"先测再存"。 */
    provider: AIProvider
    capability: TestProviderCapability
  }
): Promise<TestProviderResult> {
  return authedPost<TestProviderResult>('/ai/test-provider', token, {
    provider: options.provider,
    capability: options.capability,
  })
}
