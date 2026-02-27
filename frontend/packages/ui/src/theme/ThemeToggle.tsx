import { Moon, Sun } from 'lucide-react'

import { Button } from '../ui/button'
import { useLocale } from '../locale/LocaleProvider'
import { useTheme } from './ThemeProvider'

export function ThemeToggle() {
  const { resolved, setMode } = useTheme()
  const { t } = useLocale()
  const nextMode = resolved === 'dark' ? 'light' : 'dark'
  const Icon = resolved === 'dark' ? Moon : Sun

  return (
    <Button
      aria-label={t('shell.theme')}
      className="h-9 w-9 bg-transparent"
      size="icon"
      title={`${t('shell.theme')}: ${resolved === 'dark' ? 'Dark' : 'Light'}`}
      type="button"
      variant="ghost"
      onClick={() => setMode(nextMode)}
    >
      <Icon className="h-4 w-4" />
    </Button>
  )
}
