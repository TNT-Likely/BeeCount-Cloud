import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { API_BASE, clearStoredSession, configureHttp, getStoredUserId, refreshAuth } from '@beecount/api-client'
import { useT } from '@beecount/ui'

import { AppShell } from './app/AppShell'
import { LicenseGate } from './app/LicenseGate'
import { RequireAuth } from './app/router'
import { LoginPage } from './pages/LoginPage'
import { SsoCallbackPage } from './pages/SsoCallbackPage'
import { clearCursor } from './state/sync-client'

// Section 页面全部懒加载 — 首屏只下载当前 route 需要的 chunk,显著降低
// 首次进入 /app/overview 的 JS 体积。每个页面会是独立 chunk,后续切到
// 其他 section 时按需 fetch。
const TransactionsPage = lazy(() =>
  import('./pages/sections/TransactionsPage').then((m) => ({ default: m.TransactionsPage })),
)
const AccountsPage = lazy(() =>
  import('./pages/sections/AccountsPage').then((m) => ({ default: m.AccountsPage })),
)
const AdminBackupPage = lazy(() =>
  import('./pages/sections/AdminBackupPage').then((m) => ({ default: m.AdminBackupPage })),
)
const AdminDataCleanupPage = lazy(() =>
  import('./pages/sections/AdminDataCleanupPage').then((m) => ({
    default: m.AdminDataCleanupPage,
  })),
)
const AdminScheduledJobsPage = lazy(() =>
  import('./pages/sections/AdminScheduledJobsPage').then((m) => ({
    default: m.AdminScheduledJobsPage,
  })),
)
const AdminAppVersionPage = lazy(() =>
  import('./pages/sections/AdminAppVersionPage').then((m) => ({
    default: m.AdminAppVersionPage,
  })),
)
const AdminLicensesPage = lazy(() =>
  import('./pages/sections/AdminLicensesPage').then((m) => ({ default: m.AdminLicensesPage })),
)
const AdminBroadcastsPage = lazy(() =>
  import('./pages/sections/AdminBroadcastsPage').then((m) => ({ default: m.AdminBroadcastsPage })),
)
const AdminUsersPage = lazy(() =>
  import('./pages/sections/AdminUsersPage').then((m) => ({ default: m.AdminUsersPage })),
)
const BudgetsPage = lazy(() =>
  import('./pages/sections/BudgetsPage').then((m) => ({ default: m.BudgetsPage })),
)
const CalendarPage = lazy(() =>
  import('./pages/sections/CalendarPage').then((m) => ({ default: m.CalendarPage })),
)
const CategoriesPage = lazy(() =>
  import('./pages/sections/CategoriesPage').then((m) => ({ default: m.CategoriesPage })),
)
const LedgersPage = lazy(() =>
  import('./pages/sections/LedgersPage').then((m) => ({ default: m.LedgersPage })),
)
const OverviewPage = lazy(() =>
  import('./pages/sections/OverviewPage').then((m) => ({ default: m.OverviewPage })),
)
const SettingsAiPage = lazy(() =>
  import('./pages/sections/SettingsAiPage').then((m) => ({ default: m.SettingsAiPage })),
)
const SettingsDevicesPage = lazy(() =>
  import('./pages/sections/SettingsDevicesPage').then((m) => ({ default: m.SettingsDevicesPage })),
)
const SettingsPatsPage = lazy(() =>
  import('./pages/sections/SettingsPatsPage').then((m) => ({ default: m.SettingsPatsPage })),
)
const SettingsHealthPage = lazy(() =>
  import('./pages/sections/SettingsHealthPage').then((m) => ({ default: m.SettingsHealthPage })),
)
const SettingsProfilePage = lazy(() =>
  import('./pages/sections/SettingsProfilePage').then((m) => ({ default: m.SettingsProfilePage })),
)
const TagsPage = lazy(() =>
  import('./pages/sections/TagsPage').then((m) => ({ default: m.TagsPage })),
)
const ProjectsPage = lazy(() =>
  import('./pages/sections/ProjectsPage').then((m) => ({ default: m.ProjectsPage })),
)
const ComparisonReportPage = lazy(() =>
  import('./pages/sections/ComparisonReportPage').then((m) => ({ default: m.ComparisonReportPage })),
)
const ImportPage = lazy(() =>
  import('./pages/sections/ImportPage').then((m) => ({ default: m.ImportPage })),
)
const RecurringRulesPage = lazy(() =>
  import('./pages/sections/RecurringRulesPage').then((m) => ({ default: m.RecurringRulesPage })),
)
const InstallmentPlansPage = lazy(() =>
  import('./pages/sections/InstallmentPlansPage').then((m) => ({
    default: m.InstallmentPlansPage,
  })),
)
const DebtsPage = lazy(() =>
  import('./pages/sections/DebtsPage').then((m) => ({ default: m.DebtsPage })),
)
const RealizedPnlPage = lazy(() =>
  import('./pages/sections/RealizedPnlPage').then((m) => ({ default: m.RealizedPnlPage })),
)
const AdminSecurityDataSourcePage = lazy(() =>
  import('./pages/sections/AdminSecurityDataSourcePage').then((m) => ({
    default: m.AdminSecurityDataSourcePage,
  })),
)
const InvestmentsPage = lazy(() =>
  import('./pages/sections/InvestmentsPage').then((m) => ({ default: m.InvestmentsPage })),
)
const TxTemplatesPage = lazy(() =>
  import('./pages/sections/TxTemplatesPage').then((m) => ({ default: m.TxTemplatesPage })),
)
const ShareIncomingPage = lazy(() =>
  import('./pages/sections/ShareIncomingPage').then((m) => ({ default: m.ShareIncomingPage })),
)

/** 路由切换时的 Suspense fallback。section 切换通常 < 200ms,加个轻量
 *  loading shell 避免白屏闪烁。 */
function RouteFallback() {
  return (
    <div className="flex h-32 items-center justify-center">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-muted border-t-primary" />
    </div>
  )
}

const LEGACY_TOKEN_KEY = 'beecount.token'
const TOKEN_KEY = `beecount.token.${API_BASE}`

/**
 * 登录成功(或已带 token 时又访问 /login)后该导去哪 —— 优先读
 * `location.state.from`(未登录时被 `RequireAuth` 导到 /login 前的原始
 * location,含 querystring),没有才 fallback `/app/overview`。任何「未登录
 * 时点了带参数的深链」情境都会受惠(例如 SwipeSmart 一键记账、Web Share
 * Target),否则深链带的参数在这里就丢光了
 * (docs/PH15_SWIPESMART_QUICKADD_SD.md §1.3/§3.6)。
 */
function loggedInRedirectTarget(location: { state: unknown }): string {
  const from = (location.state as { from?: { pathname: string; search: string } } | null)?.from
  return from ? `${from.pathname}${from.search}` : '/app/overview'
}

/**
 * 清掉 per-user 作用域的 localStorage 键 —— 仅限承载"账户数据缓存/选择"
 * 的键,不要碰 `primaryColor` / `theme` / `locale` 这些跨用户的偏好。
 * 多用户切换时避免 User A 残留的 activeLedger / txFilter 被 User B 读到。
 */
function clearUserScopedStorage(userId: string): void {
  if (typeof window === 'undefined' || !userId) return
  try {
    window.localStorage.removeItem(`beecount.active-ledger.${userId}`)
    const prefixes = [`beecount:web:txFilter:v1:${userId}:`, `beecount:web:cardPaymentAccount:v1:${userId}:`]
    const doomed: string[] = []
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i)
      if (key && prefixes.some((prefix) => key.startsWith(prefix))) doomed.push(key)
    }
    for (const key of doomed) window.localStorage.removeItem(key)
  } catch {
    // localStorage 在 private mode / 超配额时可能抛异常,忽略即可。
  }
}

export function App() {
  const t = useT()

  useEffect(() => {
    document.title = t('shell.docTitle')
  }, [t])

  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  )
}

function AppRoutes() {
  const navigate = useNavigate()
  const location = useLocation()
  const [token, setToken] = useState<string>(() => {
    const scoped = localStorage.getItem(TOKEN_KEY)
    if (scoped) return scoped
    return localStorage.getItem(LEGACY_TOKEN_KEY) || ''
  })

  useEffect(() => {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token)
      localStorage.removeItem(LEGACY_TOKEN_KEY)
    } else {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(LEGACY_TOKEN_KEY)
    }
  }, [token])

  const handleLogout = useCallback(() => {
    const prev = getStoredUserId()
    if (prev) {
      clearCursor(prev)
      clearUserScopedStorage(prev)
    }
    clearStoredSession()
    setToken('')
    navigate('/login', { replace: true })
  }, [navigate])

  // 授權失效通知計數(docs/LICENSE_KEYS.md):http 層偵測到 402 /
  // LICENSE_REQUIRED 就 +1,LicenseGate 看到數值變化就切回金鑰輸入頁。用計數
  // 而不是 boolean,是因為閘門自己會在啟用成功後切回 app,不需要這裡重設。
  const [licenseLapseSignal, setLicenseLapseSignal] = useState(0)

  useEffect(() => {
    configureHttp({
      refreshToken: async () => {
        const fresh = await refreshAuth()
        setToken(fresh)
        return fresh
      },
      onLogout: handleLogout,
      onLicenseRequired: () => setLicenseLapseSignal((n) => n + 1)
    })
    return () => {
      configureHttp({ refreshToken: null, onLogout: null, onLicenseRequired: null })
    }
  }, [handleLogout])

  // Nested routes:AppShell 作为 /app 父路由的 element,其 <Outlet /> 渲染
  // 当前子路由,切换 section 时 AppShell 不 unmount —— profileMe / ledgers
  // 等全局数据跨页面保持。所有 section 都有独立 Page,挂到 Outlet 下。
  // LicenseGate 夾在 RequireAuth 跟 AppShell 之間:沒有有效授權時整頁換成
  // 金鑰輸入畫面,AppShell 完全不掛載。
  const shellElement = (
    <RequireAuth isAuthed={!!token}>
      <LicenseGate token={token} onLogout={handleLogout} lapseSignal={licenseLapseSignal}>
        <AppShell token={token} onLogout={handleLogout} />
      </LicenseGate>
    </RequireAuth>
  )

  return (
    <Routes>
      <Route
        path="/login"
        element={
          token ? (
            <Navigate to={loggedInRedirectTarget(location)} replace />
          ) : (
            <LoginPage
              ssoRedirectPath={loggedInRedirectTarget(location)}
              onLoggedIn={(nextToken) => {
                // 只更新 token,不在这里额外 navigate() —— 上面这行
                // `token ? <Navigate .../> : ...` 会在 token 变 truthy 后的下
                // 一次渲染自己接手导航。曾经在这里加过一次 `navigate(target, {
                // replace: true })`,跟这个三元表达式的声明式 Navigate 抢跑:
                // React 18 把 `setToken` 触发的重渲染跟这次 imperative navigate
                // 批在一起,声明式那支(当时还固定写 `/app/overview`)有时会晚一步
                // 把刚 navigate 过去的 `state.from` 目标地址又覆盖回总览页 ——
                // 实测点 SwipeSmart 深链会先跳 /login、登入后却落在 /app/overview
                // 而不是原始深链。拆成单一入口(这里只管 setToken,目标地址交给
                // 上面那行统一算)才不会有两边各算一次目标地址的竞态。
                setToken(nextToken)
              }}
            />
          )
        }
      />
      {/* /auth/sso/callback 换完 token 后 302 到这里(fragment 带 token,
          见 SsoCallbackPage 注释)。不判断 `token` 短路 — SSO 落地时
          `token` 通常还是空,这个页面自己负责 setToken + navigate。 */}
      <Route
        path="/login/sso-complete"
        element={<SsoCallbackPage onLoggedIn={(nextToken) => setToken(nextToken)} />}
      />
      <Route path="/app" element={shellElement}>
        <Route index element={<Navigate to="overview" replace />} />
        <Route
          path="overview"
          element={
            <Suspense fallback={<RouteFallback />}>
              <OverviewPage />
            </Suspense>
          }
        />
        <Route
          path="transactions"
          element={
            <Suspense fallback={<RouteFallback />}>
              <TransactionsPage />
            </Suspense>
          }
        />
        <Route
          path="calendar"
          element={
            <Suspense fallback={<RouteFallback />}>
              <CalendarPage />
            </Suspense>
          }
        />
        <Route
          path="ledgers"
          element={
            <Suspense fallback={<RouteFallback />}>
              <LedgersPage />
            </Suspense>
          }
        />
        <Route
          path="budgets"
          element={
            <Suspense fallback={<RouteFallback />}>
              <BudgetsPage />
            </Suspense>
          }
        />
        <Route
          path="recurring-rules"
          element={
            <Suspense fallback={<RouteFallback />}>
              <RecurringRulesPage />
            </Suspense>
          }
        />
        <Route
          path="installment-plans"
          element={
            <Suspense fallback={<RouteFallback />}>
              <InstallmentPlansPage />
            </Suspense>
          }
        />
        <Route
          path="debts"
          element={
            <Suspense fallback={<RouteFallback />}>
              <DebtsPage />
            </Suspense>
          }
        />
        <Route
          path="investments"
          element={
            <Suspense fallback={<RouteFallback />}>
              <InvestmentsPage />
            </Suspense>
          }
        />
        <Route
          path="realized-pnl"
          element={
            <Suspense fallback={<RouteFallback />}>
              <RealizedPnlPage />
            </Suspense>
          }
        />
        <Route
          path="tx-templates"
          element={
            <Suspense fallback={<RouteFallback />}>
              <TxTemplatesPage />
            </Suspense>
          }
        />
        <Route
          path="accounts"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AccountsPage />
            </Suspense>
          }
        />
        <Route
          path="categories"
          element={
            <Suspense fallback={<RouteFallback />}>
              <CategoriesPage />
            </Suspense>
          }
        />
        <Route
          path="tags"
          element={
            <Suspense fallback={<RouteFallback />}>
              <TagsPage />
            </Suspense>
          }
        />
        <Route
          path="projects"
          element={
            <Suspense fallback={<RouteFallback />}>
              <ProjectsPage />
            </Suspense>
          }
        />
        <Route
          path="comparison-report"
          element={
            <Suspense fallback={<RouteFallback />}>
              <ComparisonReportPage />
            </Suspense>
          }
        />
        <Route
          path="import"
          element={
            <Suspense fallback={<RouteFallback />}>
              <ImportPage />
            </Suspense>
          }
        />
        <Route
          path="share-incoming"
          element={
            <Suspense fallback={<RouteFallback />}>
              <ShareIncomingPage />
            </Suspense>
          }
        />
        <Route
          path="admin/users"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminUsersPage />
            </Suspense>
          }
        />
        <Route
          path="admin/backup"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminBackupPage />
            </Suspense>
          }
        />
        <Route
          path="admin/data-cleanup"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminDataCleanupPage />
            </Suspense>
          }
        />
        <Route
          path="admin/scheduled-jobs"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminScheduledJobsPage />
            </Suspense>
          }
        />
        <Route
          path="admin/app-version"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminAppVersionPage />
            </Suspense>
          }
        />
        <Route
          path="admin/security-data-source"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminSecurityDataSourcePage />
            </Suspense>
          }
        />
        <Route
          path="admin/licenses"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminLicensesPage />
            </Suspense>
          }
        />
        <Route
          path="admin/broadcasts"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminBroadcastsPage />
            </Suspense>
          }
        />
        <Route
          path="settings/profile"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsProfilePage />
            </Suspense>
          }
        />
        <Route
          path="settings/appearance"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsProfilePage />
            </Suspense>
          }
        />
        <Route
          path="settings/ai"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsAiPage />
            </Suspense>
          }
        />
        <Route
          path="settings/health"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsHealthPage />
            </Suspense>
          }
        />
        <Route
          path="settings/devices"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsDevicesPage />
            </Suspense>
          }
        />
        <Route
          path="settings/developer"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SettingsPatsPage />
            </Suspense>
          }
        />
        {/* legacy 深链 /app/:ledgerId/... 目前直接 fall-through 到 transactions */}
        <Route path="*" element={<Navigate to="/app/overview" replace />} />
      </Route>
      <Route path="/" element={<Navigate to={token ? '/app/overview' : '/login'} replace />} />
      <Route path="*" element={<Navigate to={token ? '/app/overview' : '/login'} replace />} />
    </Routes>
  )
}

// LegacyAppPage 和 useLegacyRoute 桥已随阶段 3 T15 移除 —— 所有 section 都是
// 独立 Page,直接挂到 react-router 的 Outlet 下。
