import { SettingsProfileAppearanceSection } from '../../components/sections/SettingsProfileAppearanceSection'

/**
 * 账号 / 外观设置页(同时挂 `/app/settings/profile` 和 `/app/settings/appearance`
 * 两条路由 —— 两个 section 合并到同一个页面,用户侧心智模型就是"我的偏好")。
 *
 * 数据完全来自 useAuth().profileMe,无自己的 state / fetch。
 */
export function SettingsProfilePage() {
  return <SettingsProfileAppearanceSection />
}
