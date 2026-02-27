import { Languages } from 'lucide-react'

import { Button } from '../ui/button'
import { useLocale } from './LocaleProvider'
import { type Locale } from './locale-script'

const localeItems: Locale[] = ['zh-CN', 'zh-TW', 'en']
const localeLabel: Record<Locale, string> = {
  'zh-CN': '简体',
  'zh-TW': '繁體',
  en: 'EN'
}

export function LanguageToggle() {
  const { locale, setLocale, t } = useLocale()
  const currentIndex = localeItems.indexOf(locale)
  const nextLocale = localeItems[(currentIndex + 1) % localeItems.length]

  return (
    <Button
      aria-label={t('shell.language')}
      className="h-9 w-9 bg-transparent"
      size="icon"
      title={`${t('shell.language')}: ${localeLabel[locale]}`}
      type="button"
      variant="ghost"
      onClick={() => setLocale(nextLocale)}
    >
      <Languages className="h-4 w-4" />
    </Button>
  )
}
