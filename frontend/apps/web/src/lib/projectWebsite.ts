export function projectWebsiteUrl(path: string, locale: string): string {
  const prefix = locale.startsWith('zh') ? '' : '/en'
  return `https://count.beejz.com${prefix}${path}`
}
