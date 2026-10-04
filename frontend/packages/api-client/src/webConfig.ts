import { publicGet } from './http'

export interface WebConfig {
  project_partnerships_enabled: boolean
}

export function fetchWebConfig(): Promise<WebConfig> {
  return publicGet<WebConfig>('/web-config')
}
