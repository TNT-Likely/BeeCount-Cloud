export const THEME_STORAGE_KEY = 'beecount.theme'

export type ThemeMode = 'light' | 'dark' | 'system'

function resolveSystemTheme(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function resolveTheme(mode: ThemeMode): 'light' | 'dark' {
  return mode === 'system' ? resolveSystemTheme() : mode
}

export function applyTheme(mode: ThemeMode): void {
  if (typeof document === 'undefined') return
  const target = resolveTheme(mode)
  const root = document.documentElement
  root.classList.remove('light', 'dark')
  root.classList.add(target)
  root.setAttribute('data-theme', target)
  root.style.colorScheme = target
}

export function initialThemeMode(): ThemeMode {
  if (typeof window === 'undefined') return 'system'
  const raw = window.localStorage.getItem(THEME_STORAGE_KEY)
  if (raw === 'light' || raw === 'dark' || raw === 'system') {
    return raw
  }
  return 'system'
}

export function persistTheme(mode: ThemeMode): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(THEME_STORAGE_KEY, mode)
}
