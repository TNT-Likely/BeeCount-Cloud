import type { LocaleDictionaries } from '@beecount/ui'

import en from './en'
import zhCN from './zh-CN'
import zhTW from './zh-TW'

export const dictionaries: LocaleDictionaries = {
  en,
  'zh-CN': zhCN,
  'zh-TW': zhTW
}

export type TranslationKey = keyof typeof en
