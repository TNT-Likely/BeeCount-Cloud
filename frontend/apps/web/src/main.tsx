import React from 'react'
import { createRoot } from 'react-dom/client'
import {
  LocaleProvider,
  ThemeProvider,
  applyDocumentLocale,
  applyTheme,
  initialLocale,
  initialThemeMode
} from '@beecount/ui'

import { App } from './App'
import { dictionaries } from './i18n'
import './styles.css'

applyTheme(initialThemeMode())
applyDocumentLocale(initialLocale())

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LocaleProvider dictionaries={dictionaries}>
      <ThemeProvider>
        <App />
      </ThemeProvider>
    </LocaleProvider>
  </React.StrictMode>
)
