/**
 * PWA 自定义安装提示 —— 接住浏览器抛出的 `beforeinstallprompt`,延后到合适
 * 时机弹自己的 banner,而不是依赖浏览器原生那个不太显眼的「+」icon。
 *
 * Engagement gate:用户在 7 天内进入应用 ≥ 3 次才弹,避免新用户第一次访问
 * 就被催安装。次数存 localStorage(`beecount.pwa.visit-count`),date 滑窗 7d。
 *
 * 用户点「不再提示」→ 永久不再弹（localStorage 标记）。点 X 关 → 下次进来
 * 还会弹,直到接受或永久关闭。
 *
 * 已安装(display-mode: standalone)的 PWA 不会再抛 beforeinstallprompt,所以
 * 这里不需要额外的"已安装"判断。
 */

declare global {
  interface WindowEventMap {
    beforeinstallprompt: BeforeInstallPromptEvent
  }
}

interface BeforeInstallPromptEvent extends Event {
  readonly platforms: string[]
  prompt: () => Promise<void>
  readonly userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>
}

export const PWA_INSTALL_AVAILABLE_EVENT = 'pwa:install-available'
const VISIT_COUNT_KEY = 'beecount.pwa.visit-count'
const VISIT_FIRST_AT_KEY = 'beecount.pwa.visit-first-at'
const PERMANENTLY_DISMISSED_KEY = 'beecount.pwa.install-never'
const MIN_VISITS = 3
const WINDOW_DAYS = 7

let cachedPrompt: BeforeInstallPromptEvent | null = null

export function setupInstallPrompt(): void {
  if (typeof window === 'undefined') return

  // 记一下本次访问 —— 7 天滑窗,过期重置
  recordVisit()

  window.addEventListener('beforeinstallprompt', (event) => {
    // 关键:阻止浏览器自己弹默认安装条,后面我们自己决定时机
    event.preventDefault()
    cachedPrompt = event

    // 已被用户永久拒绝过 → 不广播
    if (localStorage.getItem(PERMANENTLY_DISMISSED_KEY) === '1') return

    // engagement 没达标 → 暂存 event,等达标再广播
    if (!hasReachedEngagement()) return

    window.dispatchEvent(new CustomEvent(PWA_INSTALL_AVAILABLE_EVENT))
  })
}

/** UI 调用 —— 用户在 banner 上点「添加到桌面」 */
export async function triggerInstallPrompt(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  if (!cachedPrompt) return 'unavailable'
  try {
    await cachedPrompt.prompt()
    const choice = await cachedPrompt.userChoice
    cachedPrompt = null
    return choice.outcome
  } catch {
    return 'unavailable'
  }
}

/** UI 调用 —— 用户点「不再提示」,永久关闭 */
export function neverShowInstallPrompt(): void {
  localStorage.setItem(PERMANENTLY_DISMISSED_KEY, '1')
}

/** 已经达到 engagement 门槛 → 可以广播提示 */
export function hasReachedEngagement(): boolean {
  try {
    const firstAt = Number(localStorage.getItem(VISIT_FIRST_AT_KEY) || '0')
    const count = Number(localStorage.getItem(VISIT_COUNT_KEY) || '0')
    if (!firstAt || !count) return false
    const ageDays = (Date.now() - firstAt) / (1000 * 60 * 60 * 24)
    if (ageDays > WINDOW_DAYS) return false
    return count >= MIN_VISITS
  } catch {
    return false
  }
}

function recordVisit(): void {
  try {
    const now = Date.now()
    const firstAt = Number(localStorage.getItem(VISIT_FIRST_AT_KEY) || '0')
    const ageDays = firstAt ? (now - firstAt) / (1000 * 60 * 60 * 24) : Infinity

    if (!firstAt || ageDays > WINDOW_DAYS) {
      localStorage.setItem(VISIT_FIRST_AT_KEY, String(now))
      localStorage.setItem(VISIT_COUNT_KEY, '1')
      return
    }
    const count = Number(localStorage.getItem(VISIT_COUNT_KEY) || '0')
    localStorage.setItem(VISIT_COUNT_KEY, String(count + 1))
  } catch {
    // localStorage 不可用(隐私模式 / quota 满)→ install banner 直接放弃,
    // 不影响主流程
  }
}
