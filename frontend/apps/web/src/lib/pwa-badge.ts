/**
 * PWA Badging API 封装 —— 在 dock / 任务栏图标上画一个数字徽标。
 *
 * 用途: 当用户没打开应用窗口但有需要关注的事件时(预算超支、共享账本未读、
 * 同步冲突),通过 dock badge 提示。
 *
 * 支持矩阵:
 *   - macOS Chrome/Edge/Brave (PWA)            ✅
 *   - Windows Chrome/Edge/Brave (PWA)          ✅
 *   - Android Chrome (PWA in launcher)         ✅
 *   - Safari (iOS / macOS)                     ❌ 永远 no-op
 *   - Firefox                                  ❌
 *
 * 设计:
 *   - 调用 navigator.setAppBadge 时,浏览器即使没安装也不会报错(API 是 no-op)
 *   - 不再支持时静默吞掉 - 没用户感知 = 不破坏体验
 *
 * 调用方:目前从 OverviewPage / BudgetsPage 里在 budget overspend 数量变化时
 * 调用 setAppBadge(count)。后续可扩展到 SyncSocketContext(收到 shared
 * ledger 别人新建交易)/ SyncErrorStore(有同步冲突)。
 */

// Badging API 在 lib.dom 里已经声明,但部分浏览器/平台并没实装(Safari /
// Firefox / 旧 Chrome)。运行时拿 unknown,自己 narrow 出可调函数 —— 比强
// declare 自定义 Navigator subtype 更稳,不会被 lib.dom 升级时撞类型签名。
type BadgeFn = (contents?: number) => Promise<void>
type ClearFn = () => Promise<void>

function getBadgeFns(): { set?: BadgeFn; clear?: ClearFn } {
  if (typeof navigator === 'undefined') return {}
  const nav = navigator as unknown as Record<string, unknown>
  const set = typeof nav.setAppBadge === 'function' ? (nav.setAppBadge as BadgeFn) : undefined
  const clear = typeof nav.clearAppBadge === 'function' ? (nav.clearAppBadge as ClearFn) : undefined
  return { set, clear }
}

export async function setAppBadge(count: number): Promise<void> {
  const { set, clear } = getBadgeFns()
  if (!set) return
  try {
    if (count <= 0) {
      await clear?.call(navigator)
      return
    }
    await set.call(navigator, count)
  } catch {
    // 权限/支持问题静默吞 —— badge 是装饰性增强,不能影响主业务
  }
}

export async function clearAppBadge(): Promise<void> {
  const { clear } = getBadgeFns()
  if (!clear) return
  try {
    await clear.call(navigator)
  } catch {
    // ignore
  }
}

/** 浏览器是否支持 Badging API(决定要不要走相关计算流程) */
export function isBadgingSupported(): boolean {
  return Boolean(getBadgeFns().set)
}
