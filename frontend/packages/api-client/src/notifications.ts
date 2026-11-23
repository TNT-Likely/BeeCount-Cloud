import { authedGet, authedPost } from './http'

export type CloudNotification = {
  id: string
  type: 'category_missing_parent'
  unread: boolean
  count: number
  categories: { id: string; name: string | null; parent_name: string | null }[]
}

export function fetchNotifications(token: string): Promise<CloudNotification[]> {
  return authedGet('/notifications', token)
}

export function markNotificationsRead(token: string, ids: string[]): Promise<void> {
  return authedPost('/notifications/read', token, { ids })
}
