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
  applyPrimaryColor,
  DEFAULT_PRIMARY_COLOR,
  initialPrimaryColor,
  persistPrimaryColor
} from './primary-color-script'

type PrimaryColorContextValue = {
  color: string
  setColor: (hex: string) => void
  reset: () => void
  /** Server 下发的主题色（mobile 推上来）。无条件覆盖本地色；web 用户点
   *  picker 只是临时切换，下一次 server 推 / loadProfile 会再覆盖。
   *  单向：mobile → server → web，反向不同步。 */
  applyServerColor: (hex: string | null | undefined) => void
}

const PrimaryColorContext = createContext<PrimaryColorContextValue | null>(null)

export function PrimaryColorProvider({ children }: PropsWithChildren) {
  const [color, setColorState] = useState<string>(() => initialPrimaryColor())

  // 组件挂载时先把 localStorage 里的色应用一次，防止首帧用默认色闪烁。
  useEffect(() => {
    applyPrimaryColor(color)
    // 只跑一次，之后走 setColor 显式触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const setColor = useCallback((hex: string) => {
    const cleaned = hex.trim()
    if (!/^#[0-9a-fA-F]{6}$/.test(cleaned)) return
    setColorState(cleaned)
    applyPrimaryColor(cleaned)
    // 本地持久化仅为了下次刷新前不闪回；下一次 server 推 / loadProfile
    // 会覆盖它。web 不会回推 mobile。
    persistPrimaryColor(cleaned)
  }, [])

  const reset = useCallback(() => {
    setColor(DEFAULT_PRIMARY_COLOR)
  }, [setColor])

  const applyServerColor = useCallback((hex: string | null | undefined) => {
    if (!hex) return
    const cleaned = hex.trim()
    if (!/^#[0-9a-fA-F]{6}$/.test(cleaned)) {
      console.warn('[theme] applyServerColor invalid hex', cleaned)
      return
    }
    // 每次 server 推下来都应用：mobile 改色 → web 无条件跟上。
    // Web 本地 setColor 是"临时切换"，下一次 mobile 推送依然覆盖。
    console.info('[theme] applyServerColor apply', cleaned)
    setColorState(cleaned)
    applyPrimaryColor(cleaned)
    // 同步写 localStorage：保证页面下次加载前（loadProfile 还没返回）
    // 也是 server 值，避免短暂闪回旧色。
    persistPrimaryColor(cleaned)
  }, [])

  const value = useMemo(
    () => ({ color, setColor, reset, applyServerColor }),
    [color, setColor, reset, applyServerColor]
  )

  return (
    <PrimaryColorContext.Provider value={value}>{children}</PrimaryColorContext.Provider>
  )
}

export function usePrimaryColor(): PrimaryColorContextValue {
  const ctx = useContext(PrimaryColorContext)
  if (!ctx) {
    throw new Error('usePrimaryColor must be used within PrimaryColorProvider')
  }
  return ctx
}
