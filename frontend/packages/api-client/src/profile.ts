import { authedGet, authedPatch, resolveApiUrl } from './http'
import type { ProfileMe } from './types'

export async function fetchProfileMe(token: string): Promise<ProfileMe> {
  const profile = await authedGet<ProfileMe>('/profile/me', token)
  return {
    ...profile,
    avatar_url: resolveApiUrl(profile.avatar_url)
  }
}

export async function patchProfileMe(
  token: string,
  payload: {
    display_name: string
  }
): Promise<ProfileMe> {
  const profile = await authedPatch<ProfileMe>('/profile/me', token, payload)
  return {
    ...profile,
    avatar_url: resolveApiUrl(profile.avatar_url)
  }
}
