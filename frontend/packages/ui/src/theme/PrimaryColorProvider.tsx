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
    persistPrimaryColor(cleaned)
  }, [])

  const reset = useCallback(() => {
    setColor(DEFAULT_PRIMARY_COLOR)
  }, [setColor])

  const value = useMemo(() => ({ color, setColor, reset }), [color, setColor, reset])

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
