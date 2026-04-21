import type { ReadBudget, WorkspaceCategory } from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'
import { Amount, CategoryIcon } from '@beecount/web-features'

import { useLedgers } from '../../context/LedgersContext'

interface Props {
  budgets: ReadBudget[]
  categories: WorkspaceCategory[]
  categoryIconPreviewByFileId: Record<string, string>
}

/**
 * 预算 section —— 从 AppPage.tsx 抽出独立组件。
 *
 * 每条预算卡:
 *   - 左侧彩色 chip + Material Symbols 图标(分类走 icon_cloud_file_id /
 *     icon_type / icon 三路优先级;总预算走 wallet);
 *   - 中间分类名(含 disabled 徽章)、周期 + 起始日;
 *   - 右侧金额 + "总预算 / 分类预算" 类型标签。
 *
 * 布局响应式 2-3 列 grid,hover 轻高亮。
 */
export function BudgetsSection({
  budgets,
  categories,
  categoryIconPreviewByFileId,
}: Props) {
  const t = useT()
  const { activeLedgerId, currency } = useLedgers()

  return (
    <div className="space-y-4">
      <Card className="bc-panel">
        <CardHeader>
          <CardTitle>{t('nav.budgets')}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-4 text-xs text-muted-foreground">{t('budgets.desc')}</p>
          {!activeLedgerId ? (
            <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
          ) : budgets.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('budgets.empty')}</p>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {budgets.map((b) => {
                const isTotal = b.type === 'total'
                const cat = !isTotal
                  ? categories.find((c) => c.id === b.category_id)
                  : null
                const iconName = isTotal ? 'wallet' : cat?.icon
                const iconType = cat?.icon_type || 'material'
                const iconFileId = cat?.icon_cloud_file_id || null
                const title = isTotal
                  ? t('budgets.label.allLedger')
                  : (cat?.name ||
                      b.category_name ||
                      t('budgets.label.unknownCategory'))
                const periodLabel =
                  b.period === 'monthly' ? t('budgets.period.monthly')
                  : b.period === 'weekly' ? t('budgets.period.weekly')
                  : b.period === 'yearly' ? t('budgets.period.yearly')
                  : b.period
                return (
                  <div
                    key={b.id}
                    className="group flex items-center gap-3 rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm"
                  >
                    <span
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                      aria-hidden
                    >
                      <CategoryIcon
                        icon={iconName}
                        iconType={iconType}
                        iconCloudFileId={iconFileId}
                        iconPreviewUrlByFileId={categoryIconPreviewByFileId}
                        size={24}
                      />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-semibold">{title}</span>
                        {!b.enabled ? (
                          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            {t('budgets.disabled')}
                          </span>
                        ) : null}
                      </div>
                      <div className="mt-0.5 text-[11px] text-muted-foreground">
                        {periodLabel}
                        {' · '}
                        {t('budgets.startDay').replace('{day}', String(b.start_day))}
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <Amount
                        value={b.amount}
                        currency={currency}
                        size="md"
                        bold
                        tone="default"
                      />
                      <div className="mt-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                        {isTotal
                          ? t('budgets.type.total')
                          : t('budgets.type.category')}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
