import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState
} from 'react'

import {
  applyTheme,
  initialThemeMode,
  persistTheme,
  resolveTheme,
  type ThemeMode
} from './theme-script'

type ThemeContextValue = {
  mode: ThemeMode
  resolved: 'light' | 'dark'
  setMode: (mode: ThemeMode) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: PropsWithChildren) {
  const [mode, setModeState] = useState<ThemeMode>(() => initialThemeMode())
  const [resolved, setResolved] = useState<'light' | 'dark'>(() => resolveTheme(mode))

  const setMode = useCallback((nextMode: ThemeMode) => {
    setModeState(nextMode)
    persistTheme(nextMode)
    applyTheme(nextMode)
    setResolved(resolveTheme(nextMode))
  }, [])

  useEffect(() => {
    applyTheme(mode)
    setResolved(resolveTheme(mode))
    if (typeof window === 'undefined') return
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => {
      if (mode === 'system') {
        applyTheme('system')
        setResolved(resolveTheme('system'))
      }
    }
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [mode])

  const value = useMemo(
    () => ({
      mode,
      resolved,
      setMode
    }),
    [mode, resolved, setMode]
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    throw new Error('useTheme must be used within ThemeProvider')
  }
  return ctx
}
