import { createContext, useContext, type ReactNode } from 'react'

/**
 * 特殊流水「功能图标 + 颜色」偏好 overrides 的全局上下文。
 *
 * 为什么用 Context 而不是 props drill:
 *   > 同一份 `appearance.flow_icons` / `appearance.flow_icon_colors` 偏好
 *   > 在多个渲染点(交易行 TransactionRow、借款/往来时间轴、存款页等)都要用,
 *   > 且都挂在登录后的 AppShell 子树下。
 *   > 顶层(web AppShell)把 profileMe.appearance 的这两段注入一次,
 *   > 各渲染点通过 useFlowIconPrefs() / useFlowIconColorPrefs() 读取,无需层层传 props。
 *
 * 默认值 = 空对象:各渲染点用 normalizeFlowIconPrefs() / normalizeFlowIconColors()
 * 归一后自动回落到 flowIcons.ts 的内置默认图标与默认色,所以缺省 Context 时
 * 渲染完全正常(老用户零配置)。
 */
const FlowIconsContext = createContext<Record<string, string>>({})
const FlowIconColorsContext = createContext<Record<string, string>>({})

export function FlowIconsProvider({
  value,
  colors,
  children
}: {
  /** appearance.flow_icons 原始值(可能为 null / undefined / 部分 key)。 */
  value: Record<string, string> | null | undefined
  /** appearance.flow_icon_colors 原始值(可能为 null / undefined / 部分 key)。 */
  colors?: Record<string, string> | null | undefined
  children: ReactNode
}) {
  return (
    <FlowIconsContext.Provider value={value ?? {}}>
      <FlowIconColorsContext.Provider value={colors ?? {}}>
        {children}
      </FlowIconColorsContext.Provider>
    </FlowIconsContext.Provider>
  )
}

/** 读取 appearance.flow_icons 原始 overrides(可能为空,调用方自行归一)。 */
export function useFlowIconPrefs(): Record<string, string> {
  return useContext(FlowIconsContext)
}

/** 读取 appearance.flow_icon_colors 原始 overrides(可能为空,调用方自行归一)。 */
export function useFlowIconColorPrefs(): Record<string, string> {
  return useContext(FlowIconColorsContext)
}
