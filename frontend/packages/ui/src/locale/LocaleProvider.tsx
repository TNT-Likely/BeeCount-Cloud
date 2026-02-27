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
  applyDocumentLocale,
  initialLocale,
  persistLocale,
  type Locale
} from './locale-script'

export type LocaleMessages = Record<string, string>
export type LocaleDictionaries = Record<Locale, LocaleMessages>
export type TranslateParams = Record<string, string | number>

type LocaleContextValue = {
  locale: Locale
  setLocale: (next: Locale) => void
  t: (key: string, params?: TranslateParams) => string
}

type LocaleProviderProps = PropsWithChildren<{
  dictionaries: LocaleDictionaries
}>

const LocaleContext = createContext<LocaleContextValue | null>(null)

function applyTemplate(template: string, params?: TranslateParams): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (_match, token: string) => {
    const value = params[token]
    if (value === undefined || value === null) return ''
    return String(value)
  })
}

export function LocaleProvider({ children, dictionaries }: LocaleProviderProps) {
  const [locale, setLocaleState] = useState<Locale>(() => initialLocale())

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next)
    persistLocale(next)
    applyDocumentLocale(next)
  }, [])

  useEffect(() => {
    applyDocumentLocale(locale)
  }, [locale])

  const t = useCallback(
    (key: string, params?: TranslateParams) => {
      const active = dictionaries[locale] || dictionaries.en
      const fallback = dictionaries.en
      const template = active[key] || fallback[key] || key
      return applyTemplate(template, params)
    },
    [dictionaries, locale]
  )

  const value = useMemo(
    () => ({
      locale,
      setLocale,
      t
    }),
    [locale, setLocale, t]
  )

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
}

export function useLocale(): LocaleContextValue {
  const ctx = useContext(LocaleContext)
  if (!ctx) {
    throw new Error('useLocale must be used within LocaleProvider')
  }
  return ctx
}

export function useT(): LocaleContextValue['t'] {
  return useLocale().t
}
