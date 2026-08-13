import { describe, expect, it } from 'vitest'

import en from '../i18n/en'
import zhCN from '../i18n/zh-CN'
import zhTW from '../i18n/zh-TW'
import {
  HEADER_SKINS,
  HEADER_SKIN_GROUP_ORDER,
  boundPrimaryOf,
  headerSkinById,
  headerSkinGroupLabelKey,
  headerSkinLabelKey,
} from './header-skins'

const LOCALES = { en, 'zh-CN': zhCN, 'zh-TW': zhTW } as const

describe('header skin catalog', () => {
  it('每款皮肤在三种语言里都有文案', () => {
    // 加皮肤时最容易漏的就是文案 —— 漏了下拉里会显示成裸 key。
    const keys = [
      'profile.sync.headerSkin.none',
      'profile.sync.headerSkin.animated',
      'profile.sync.headerSkin.boundPalette',
      ...HEADER_SKIN_GROUP_ORDER.map(headerSkinGroupLabelKey),
      ...HEADER_SKINS.map((s) => headerSkinLabelKey(s.id)),
    ]
    for (const [locale, dict] of Object.entries(LOCALES)) {
      const missing = keys.filter((k) => !(k in dict))
      expect(missing, `${locale} 缺文案`).toEqual([])
    }
  })

  it('id 唯一,分组都在已声明的顺序里', () => {
    const ids = HEADER_SKINS.map((s) => s.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const skin of HEADER_SKINS) {
      expect(HEADER_SKIN_GROUP_ORDER).toContain(skin.group)
    }
  })

  it('绑定色是合法的 6 位大写 hex', () => {
    // 这个值会原样 PATCH 给 server 的 theme_primary_color,
    // 格式写错会让 mobile 端解析失败、主题色回落默认蜜黄。
    for (const skin of HEADER_SKINS) {
      if (!skin.boundPrimary) continue
      expect(skin.boundPrimary, skin.id).toMatch(/^#[0-9A-F]{6}$/)
    }
  })

  it('自带配色的皮肤能查到绑定色,跟随主题色的查不到', () => {
    // 周年两款都绑定,值必须和 mobile 侧完全一致(蛋糕 _kCakeCandleL /
    // 星座 BeeTheme.honeyGold),对不上会导致两端结算出不同主题色
    expect(boundPrimaryOf('anniv_cake')).toBe('#FF7A45')
    expect(boundPrimaryOf('anniversary')).toBe('#F8C91C')
    // 经典皮肤跟随用户主题色
    expect(boundPrimaryOf('aurora')).toBeUndefined()
    // 未知 id(比如 mobile 新加了 web 还没跟上)不能抛,静默返回 undefined
    expect(boundPrimaryOf('not_a_skin')).toBeUndefined()
    expect(headerSkinById('not_a_skin')).toBeUndefined()
  })

  it('两款周年皮肤置顶且都是动态', () => {
    expect(HEADER_SKIN_GROUP_ORDER[0]).toBe('anniversary')
    const anniv = HEADER_SKINS.filter((s) => s.group === 'anniversary')
    expect(anniv.map((s) => s.id)).toEqual(['anniversary', 'anniv_cake'])
    for (const s of anniv) expect(s.animated).toBe(true)
  })
})
