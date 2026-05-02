import { LogOut } from 'lucide-react'

import type { AppSection, NavItem } from '@beecount/web-features'
import { useT } from '@beecount/ui'

/**
 * 头像悬浮下拉菜单 —— 从 AppPage.tsx 抽出独立组件。
 *
 * 分组结构(从上到下):
 *   - 头部:display_name + email
 *   - Tools:预算 / 账本
 *   - Settings:个人资料 / AI / 健康 / 设备(通过 avatarMenuItems 动态传入)
 *   - Admin(仅 isAdmin):用户管理
 *   - Info:更新日志 / GitHub 仓库外链
 *   - Actions:退出登录
 *
 * 行为:跟原 inline 实现一致 —— pure CSS group-hover + focus-within,
 * hover 进 avatar 包裹区打开,离开后 150ms 淡出关闭。菜单里按钮的 active
 * 态跟 `currentSection` 比对。
 */
interface Props {
  profileMe: {
    email: string
    display_name: string | null
    avatar_url: string | null
    avatar_version: number | null
  }
  currentSection: AppSection
  isAdminUser: boolean
  avatarMenuItems: NavItem[]
  onNavigate: (section: AppSection) => void
  onLogout: () => void
  onOpenChangelog: () => void
}

export function AvatarDropdown({
  profileMe,
  currentSection,
  isAdminUser,
  avatarMenuItems,
  onNavigate,
  onLogout,
  onOpenChangelog,
}: Props) {
  const t = useT()

  const displayName = profileMe.display_name || t('shell.userDefault')
  const avatarSrc = withAvatarCacheBust(profileMe.avatar_url, profileMe.avatar_version)

  return (
    <div className="group relative" tabIndex={-1}>
      <button
        type="button"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
        title={profileMe.display_name || profileMe.email}
      >
        {avatarSrc ? (
          <img
            // key 跟 avatar_version 绑定:服务端 bump 版本号后 React 会重挂载
            // <img>,彻底绕开浏览器 disk cache 把旧帧当新 URL 继续复用的场景
            key={profileMe.avatar_version ?? 0}
            src={avatarSrc}
            alt=""
            className="h-8 w-8 rounded-full border border-border/40 object-cover"
          />
        ) : (
          <div className="flex h-8 w-8 items-center justify-center rounded-full border border-border/40 bg-muted text-[11px] font-semibold text-muted-foreground">
            {profileMe.email.slice(0, 1).toUpperCase()}
          </div>
        )}
      </button>
      {/* 悬浮面板 —— 默认透明不接收指针,hover/focus 状态打开 */}
      <div className="invisible absolute right-0 top-full z-50 w-60 pt-2 opacity-0 transition-[opacity,visibility] duration-150 group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100">
        <div className="rounded-xl border border-border/60 bg-card/95 p-1.5 shadow-xl backdrop-blur">
          {/* 头部:用户身份 */}
          <div className="px-2 py-2">
            <div className="truncate text-[13px] font-semibold text-foreground">
              {displayName}
            </div>
            <div className="truncate text-[11px] font-normal text-muted-foreground">
              {profileMe.email}
            </div>
          </div>
          <div className="mx-1 h-px bg-border/60" />

          {/* Tools 组:预算 + 账本。访问频率低,不进顶部 nav */}
          <GroupLabel>{t('nav.group.tools')}</GroupLabel>
          <MenuButton
            active={currentSection === 'budgets'}
            onClick={() => onNavigate('budgets')}
          >
            {t('nav.budgets')}
          </MenuButton>
          <MenuButton
            active={currentSection === 'ledgers'}
            onClick={() => onNavigate('ledgers')}
          >
            {t('nav.ledgers')}
          </MenuButton>

          <Divider />

          {/* Settings 组:avatarMenuItems 动态传入 */}
          <GroupLabel>{t('nav.group.settings')}</GroupLabel>
          {avatarMenuItems.map((item) => (
            <MenuButton
              key={item.key}
              active={currentSection === item.key}
              onClick={() => onNavigate(item.key)}
            >
              {t(item.labelKey)}
            </MenuButton>
          ))}

          {/* Admin 组(仅 admin) */}
          {isAdminUser ? (
            <>
              <Divider />
              <GroupLabel>{t('nav.group.admin')}</GroupLabel>
              <MenuButton
                active={currentSection === 'admin-users'}
                onClick={() => onNavigate('admin-users')}
              >
                {t('nav.users')}
              </MenuButton>
              <MenuButton
                active={currentSection === 'admin-backup'}
                onClick={() => onNavigate('admin-backup')}
              >
                {t('nav.backup')}
              </MenuButton>
            </>
          ) : null}

          {/* Info 组:更新日志 / GitHub */}
          <Divider />
          <GroupLabel>{t('avatar.group.info')}</GroupLabel>
          <MenuButton onClick={onOpenChangelog}>
            {t('avatar.changelog')}
          </MenuButton>
          <a
            className="block rounded-lg px-2.5 py-2 text-[12px] text-muted-foreground hover:bg-primary/15 hover:text-primary"
            href="https://github.com/TNT-Likely/BeeCount-Cloud"
            target="_blank"
            rel="noopener noreferrer"
          >
            {t('avatar.github')}
          </a>

          {/* Actions:logout */}
          <Divider />
          <GroupLabel>{t('avatar.group.actions')}</GroupLabel>
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12px] text-destructive hover:bg-destructive/10"
            onClick={onLogout}
          >
            <LogOut className="h-3.5 w-3.5" />
            {t('shell.logout')}
          </button>
        </div>
      </div>
    </div>
  )
}

/** 头像 URL cache-bust:服务端 bump version 时拼 `?v=<version>` 让浏览器
 *  disk cache 失效(不走 `key={version}` 只是 React 层重挂,不一定能迫使
 *  浏览器重下资源;两层兜底才稳)。 */
function withAvatarCacheBust(
  url: string | null | undefined,
  version: number | null | undefined,
): string {
  if (!url) return ''
  if (version == null) return url
  const separator = url.includes('?') ? '&' : '?'
  if (/[?&]v=\d+/.test(url)) {
    return url.replace(/([?&])v=\d+/, `$1v=${version}`)
  }
  return `${url}${separator}v=${version}`
}

// --- 小工具组件,本文件内自用,不 export ---

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-1 pb-1 pt-1 text-[10px] uppercase tracking-wider text-muted-foreground">
      {children}
    </div>
  )
}

function Divider() {
  return <div className="mx-1 my-1 h-px bg-border/60" />
}

function MenuButton({
  children,
  active,
  onClick,
}: {
  children: React.ReactNode
  active?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={`block w-full rounded-lg px-2.5 py-2 text-left text-[12px] ${
        active
          ? 'bg-primary/10 text-primary'
          : 'text-muted-foreground hover:bg-primary/15 hover:text-primary'
      }`}
      onClick={onClick}
    >
      {children}
    </button>
  )
}
