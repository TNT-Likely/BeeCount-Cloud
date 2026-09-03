import type { RagIndexStatus } from '@beecount/api-client'

export type RagLatestState = 'latest' | 'outdated' | 'pending' | 'failed'

/** 将服务端检查结果归一化，保证健康页与文档搜索使用同一版本结论。 */
export function getRagLatestState(
  status: Pick<RagIndexStatus, 'is_latest' | 'last_error'> | null | undefined,
): RagLatestState {
  if (status?.last_error) return 'failed'
  if (status?.is_latest === true) return 'latest'
  if (status?.is_latest === false) return 'outdated'
  return 'pending'
}

/** 检查明确确认当前版本已是最新时，才隐藏手动更新入口。 */
export function shouldShowRagUpdate(
  status: Pick<RagIndexStatus, 'is_latest' | 'last_error'> | null | undefined,
): boolean {
  return getRagLatestState(status) !== 'latest'
}
