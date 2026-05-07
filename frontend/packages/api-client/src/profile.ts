import { API_BASE, authedGet, authedPatch, resolveApiUrl } from './http'
import { extractApiError } from './errors'
import type { AIConfig, ProfileAppearance, ProfileMe } from './types'

export async function fetchProfileMe(token: string): Promise<ProfileMe> {
  const profile = await authedGet<ProfileMe>('/profile/me', token)
  return {
    ...profile,
    avatar_url: resolveApiUrl(profile.avatar_url)
  }
}

/**
 * PATCH /profile/me — 更新当前用户的偏好。
 *
 * 字段都可选 — 只发改动的,server 用 `is not None` 判断决定哪些字段写库,
 * 跟 mobile 端 partial-update 行为一致。修改成功后 server 广播
 * `profile_change` WS,跨端实时刷新。
 */
export async function patchProfileMe(
  token: string,
  payload: {
    display_name?: string
    /** 收支配色:true = 红色收入/绿色支出,false = 反之。 */
    income_is_red?: boolean
    /** 主题色 hex,例如 `#FF9800`。 */
    theme_primary_color?: string
    /** 外观偏好(header_decoration_style / compact_amount / show_transaction_time)。 */
    appearance?: ProfileAppearance
    /** AI 配置整体替换 —— **整体**替换语义,server 直接覆盖。调用方必须先读
     *  当前 profile.ai_config,merge 后整体推,否则 mobile-only 字段
     *  (custom_prompt / strategy / bill_extraction_enabled / use_vision)
     *  会被默认值覆盖。见 lib/aiConfigMerge.ts。 */
    ai_config?: AIConfig | Record<string, any>
  }
): Promise<ProfileMe> {
  const profile = await authedPatch<ProfileMe>('/profile/me', token, payload)
  return {
    ...profile,
    avatar_url: resolveApiUrl(profile.avatar_url)
  }
}

export type UploadAvatarResponse = ProfileMe & {
  /** server 写完后递增的 avatar_version,客户端用来 cache-bust。 */
  avatar_version: number
}

/**
 * POST /profile/avatar — 上传新头像(`multipart/form-data`)。
 *
 * - 字段名 `file`(跟 mobile / server 约定)
 * - server 写盘后 bump `avatar_version` + 广播 `profile_change`
 * - 不做客户端裁剪 — 跟 mobile 行为对齐(用户传啥存啥)
 */
export async function uploadProfileAvatar(
  token: string,
  file: File
): Promise<UploadAvatarResponse> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(`${API_BASE}/profile/avatar`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form
  })
  if (!response.ok) {
    throw await extractApiError(response)
  }
  const profile = (await response.json()) as UploadAvatarResponse
  return {
    ...profile,
    avatar_url: resolveApiUrl(profile.avatar_url)
  }
}
