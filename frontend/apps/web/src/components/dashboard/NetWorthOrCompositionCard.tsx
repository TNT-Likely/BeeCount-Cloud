import { useEffect, useState } from 'react'
import type { NetWorthHistory, WorkspaceAccount } from '@beecount/api-client'
import { useT } from '@beecount/ui'

import { AssetCompositionDonut } from './AssetCompositionDonut'
import { NetWorthTrend } from './NetWorthTrend'
import { ASSET_VIEW_KEY, type AssetView } from '../../lib/assetViewPrefs'

// 设备级持久化用户在「走势 / 构成」之间的选择(key/类型见 assetViewPrefs)。
// 与 AccountsPage 折算卡内的 tab 复用同一份状态;本卡默认 'trend'。
function readView(): AssetView {
  try {
    return localStorage.getItem(ASSET_VIEW_KEY) === 'composition' ? 'composition' : 'trend'
  } catch {
    return 'trend'
  }
}

/**
 * 资产页「走势 / 构成」切换卡 —— 顶部一行分段切换,下面渲染 NetWorthTrend(净值
 * 走势)或 AssetCompositionDonut(资产构成)。两个子组件各自自带 Card 外壳,所以
 * 本组件不再包一层 Card,只渲染 toggle + 选中的子组件,避免卡中卡双重边框。
 * 选择持久化到 localStorage,默认「走势」。
 */
export function NetWorthOrCompositionCard({
  netWorthHistory,
  accounts
}: {
  netWorthHistory: NetWorthHistory | null
  accounts: WorkspaceAccount[]
}) {
  const t = useT()
  const [view, setView] = useState<AssetView>(() => readView())

  useEffect(() => {
    try {
      localStorage.setItem(ASSET_VIEW_KEY, view)
    } catch {
      // localStorage 超配额 / 私密模式 —— 持久化丢了也不致命。
    }
  }, [view])

  return (
    <div>
      <div className="mb-2 flex justify-end gap-1">
        {(['trend', 'composition'] as AssetView[]).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => setView(v)}
            className={`rounded-full px-2 py-0.5 text-[11px] ${
              view === v ? 'bg-primary/15 text-primary' : 'text-muted-foreground'
            }`}
          >
            {t(`accounts.trendOrComposition.${v}`)}
          </button>
        ))}
      </div>
      {view === 'trend' ? (
        <NetWorthTrend data={netWorthHistory} />
      ) : (
        <AssetCompositionDonut accounts={accounts} />
      )}
    </div>
  )
}
