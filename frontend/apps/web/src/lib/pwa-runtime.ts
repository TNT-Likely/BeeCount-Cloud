/**
 * 运行时判定:用户当前是「PWA standalone 模式」还是「普通浏览器 tab」。
 *
 * 用途:有些 PWA 增强能力(比如安装提示)在已经装了 PWA 的窗口里再弹一遍
 * 是冗余的;反过来,有些通知(比如 SW 更新)对两种模式都有价值。本工具
 * 让上层组件按需 gate,而不是一刀切。
 *
 * 判定依据(按可靠性排序):
 *   1. display-mode media query — 标准,Chromium/Firefox/Edge 装 PWA 后命中
 *   2. navigator.standalone — iOS Safari 非标准属性,唯一可靠的 iOS 判定
 *   3. document.referrer android-app:// — Android Trusted Web Activity
 *
 * 任一命中即认为是 PWA 运行模式。所有判定都做 typeof / 防御写法,SSR /
 * 老浏览器不会炸。
 */

/** 当前是否运行在 PWA standalone 窗口里(已安装并从 dock / 主屏启动) */
export function isStandalonePwa(): boolean {
  if (typeof window === 'undefined') return false

  // 1) display-mode:Chromium 系 + Firefox + Edge 装 PWA 后命中。三个值都
  //    算 standalone — minimal-ui 是 PWA 但有一点点浏览器 chrome;
  //    window-controls-overlay 是桌面 PWA 自定义标题栏。
  try {
    if (window.matchMedia('(display-mode: standalone)').matches) return true
    if (window.matchMedia('(display-mode: window-controls-overlay)').matches) return true
    if (window.matchMedia('(display-mode: minimal-ui)').matches) return true
  } catch {
    // matchMedia 不可用 → 继续后面的判定
  }

  // 2) iOS Safari:navigator.standalone 是非标准属性,但 iOS 添加到主屏后
  //    唯一可靠的信号。types 上不在 Navigator 里,这里 cast 取。
  const nav = window.navigator as { standalone?: boolean }
  if (nav.standalone === true) return true

  // 3) Android TWA:从 android-app:// scheme 启动的 referrer
  if (typeof document !== 'undefined' && document.referrer.startsWith('android-app://')) {
    return true
  }

  return false
}

/** 普通浏览器 tab(没装 PWA 或装了但从浏览器打开),取反 isStandalonePwa() */
export function isBrowserTab(): boolean {
  return !isStandalonePwa()
}
