import { useEffect, useMemo, useRef, useState } from 'react'

import {
  closestCenter,
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent
} from '@dnd-kit/core'
import { arrayMove, SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { GripVertical } from 'lucide-react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT
} from '@beecount/ui'

import type { ExchangeRateOverride, ExchangeRatesResponse, ReadAccount } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import {
  AccountListRow,
  accountTypeLabel,
  buildAccountChildrenMap,
  TYPE_COLORS,
  TypeIcon
} from '../components/AccountListRow'
import { AvatarCropDialog, type AvatarCropSource } from '../components/AvatarCropDialog'
import { CurrencySelectorTrigger } from '../components/CurrencySelector'
import type { AccountForm } from '../forms'
import { accountDefaults } from '../forms'
import {
  accountBalance,
  type AssetGroup,
  type AssetSummary,
  buildDoubleCountedChildIds,
  buildParentChildrenMap,
  computeCurrencySummary,
  LIABILITY_TYPES,
  resolveRowDisplayType,
  splitByCurrency
} from '../lib/assetAggregation'


type MobileStyleAssetsProps = {
  /** 按币种切分后的汇总(每币种各自 summary + 构成饼图)。单币种时只有 1 条。 */
  byCurrency: CurrencyBucket[]
  /** 底部分组列表:跨币种按类型分组,每组小计按币种拆。 */
  listGroups: AssetGroup[]
  /** 账户隐藏(issue #240):已隐藏的账户原始行,渲染在所有在用分组之后的
   *  「已隐藏」折叠分区里;不参与 byCurrency/listGroups 的分组展示。 */
  hiddenRows: ReadAccount[]
  canManage: boolean
  onEdit: (row: ReadAccount) => void
  onDelete?: (row: ReadAccount) => void
  /** 点卡片（非编辑/删除按钮）：外层用来打开"账户详情+交易列表"弹窗。 */
  onClickAccount?: (row: ReadAccount) => void
  /** "新建账户"按钮回调 — 渲染在 stats 卡片下方,跟分组列表之间。 */
  onCreate?: () => void
  /** true 时跳过多币种「每币种一张卡」网格区(折算汇总视图接管了多币种展示);
   *  账户列表/新建按钮等其余内容照常。缺省 false —— 其它调用方零影响。 */
  hideCurrencyCards?: boolean
  /** 账户隐藏(issue #240):底部「已隐藏」分区里,每张隐藏卡的快捷「恢复」
   *  按钮回调(不经编辑弹窗,直接 PATCH hidden=false)。不传则不渲染该按钮。 */
  onRestore?: (row: ReadAccount) => void
  /** 帳戶頭像(2026-08-02 補強):`avatar_cloud_file_id` → 已加载好的 blob URL。
   *  跟 CategoryIcon 同款模式 —— 调用方(AccountsPage)负责从
   *  AttachmentCacheContext 拉取,这里只读不拉取。 */
  avatarPreviewUrlByFileId?: Record<string, string>
  /** 外幣列原幣顯示 + 快速換算按鈕(2026-08-12 使用者回報群組合計跨幣別裸加
   *  的 bug 之後一併補上):使用者主幣種 + 已 fetch 好的匯率/手動 override。
   *  三者缺一就不顯示幣別代碼標籤/換算按鈕(調用方尚未接線,或單一幣種帳戶
   *  沒有折算需求時零影響)。跟 `AccountsPage.tsx` 折算彙總卡共用同一份
   *  fetch,不在這裡重新拉。 */
  baseCurrency?: string
  fxRates?: ExchangeRatesResponse | null
  fxOverrides?: ExchangeRateOverride[]
  /** 帳戶清單拖曳排序(2026-09-05):true 時每個分組改用可拖曳的清單渲染,
   *  取代原本的靜態 AccountListRow(編輯/刪除/展開等互動暫時停用)。 */
  editingOrder?: boolean
  /** 「編輯排序」/「完成」切換按鈕回調,渲染在「新建帳戶」按鈕左側。不傳
   *  則不渲染切換按鈕(調用方尚未接線時零影響)。 */
  onToggleEditingOrder?: () => void
  /** 外層拖曳:重排某個分類裡「頂層帳戶」(獨立帳戶 + 合併帳單主帳戶)彼此
   *  的順序,`orderedRows` 是拖曳完成後的新順序。 */
  onReorderBlocks?: (type: string, orderedRows: ReadAccount[]) => void
  /** 內層拖曳:重排某個合併帳單主帳戶底下子帳戶彼此的順序。 */
  onReorderChildren?: (parentId: string, orderedRows: ReadAccount[]) => void
}

/**
 * 对齐 mobile accounts_page.dart 的展示：顶部是净值 hero（资产/负债/净值）+
 * 下面分类型折叠分组。每个分组是一个带左色带的 section，里面 row 是横向
 * 卡片：左侧 emoji 类型图标 + 账户名，右侧金额。跟 mobile 上的 ListTile 风格
 * 一致，和标签页的小卡片网格做出明显区分。
 */
function MobileStyleAssets({
  byCurrency,
  listGroups,
  hiddenRows,
  canManage,
  onEdit,
  onDelete,
  onClickAccount,
  onCreate,
  hideCurrencyCards = false,
  onRestore,
  avatarPreviewUrlByFileId,
  baseCurrency,
  fxRates,
  fxOverrides,
  editingOrder = false,
  onToggleEditingOrder,
  onReorderBlocks,
  onReorderChildren
}: MobileStyleAssetsProps) {
  const t = useT()
  // 多币种 → 每币种一张卡;单币种 → 维持原 hero + 饼图。底部列表小计是否带币种
  // 符号也跟这个走(多币种才需要符号消歧)。
  const multiCurrency = byCurrency.length > 1
  const single = byCurrency[0]
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const toggle = (type: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })

  // 帳戶群組巢狀(需求 #8,2026-08):`parent_account_id` 指向某個
  // account_group 的子帳戶(信用卡/銀行卡都可能掛靠),縮排渲染在主帳戶列
  // 下方,不再跟其它同類型帳戶並列在同一層——`listGroups` 是跨幣別按
  // account_type 分好的桶,子帳戶原本會出現在自己 type 的桶裡(例如信用卡桶),
  // 這裡把它們從所有桶的第一層拿掉,只保留在主帳戶列的巢狀清單裡渲染一次。
  const { childrenByParent, childIds } = useMemo(
    () => buildAccountChildrenMap(listGroups),
    [listGroups]
  )

  return (
    <div className="space-y-4">
      {/* 第一行的资产概览(单币种 hero+饼图 / 多币种每币种一张卡)。
          hideCurrencyCards=true 时整块跳过 —— 上层资产页统一用「折算汇总卡」
          接管净值/资产负债/构成展示(单币种亦然),这里只剩账户列表 + 新建按钮,
          避免与汇总卡重复出 hero / 构成。缺省 false 时维持原样(其它调用方零影响)。 */}
      {hideCurrencyCards ? null : multiCurrency ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {byCurrency.map((entry) => (
            <CurrencyAssetCard key={entry.currency} entry={entry} />
          ))}
        </div>
      ) : single ? (
        <div className="grid gap-3 lg:grid-cols-[1.1fr_1fr]">
          <AssetsSummaryHero summary={single.summary} currency={single.currency} />
          <AssetsCompositionMini
            groups={single.groups}
            currency={single.currency}
          />
        </div>
      ) : null}

      {onCreate ? (
        <div className="flex items-center justify-end gap-2">
          {onToggleEditingOrder ? (
            <Button
              size="sm"
              variant="outline"
              disabled={!canManage}
              onClick={onToggleEditingOrder}
            >
              {editingOrder ? t('common.done') : t('accounts.button.editOrder')}
            </Button>
          ) : null}
          <Button size="sm" disabled={!canManage} onClick={onCreate}>
            {t('accounts.button.create')}
          </Button>
        </div>
      ) : null}

      {/* 下面是分组 + 真实卡片风格的子项列表 */}
      <div className="space-y-4">
        {listGroups.map((group) => {
          const isCollapsed = collapsed.has(group.type)
          // 徽章数字要跟这个分组实际渲染出来的顶层列数一致 —— 掛靠到别的
          // account_group 底下、缩排渲染在别处的子帳戶不算在这个分组头上,
          // 否则会出现「信用卡(2)」但只看到 1 行的落差。
          const topLevelCount = group.rows.filter((row) => !childIds.has(row.id)).length
          // 这个 type 桶里的帳戶全部是別的 account_group 的子帳戶(縮排渲染
          // 在别处)时,顶层空了,整个分组头就没必要再出现一个空殼。
          if (topLevelCount === 0) return null
          return (
            <div
              key={group.type}
              className="overflow-hidden rounded-2xl border border-border/50 bg-card/60"
            >
              <button
                type="button"
                onClick={() => toggle(group.type)}
                className="relative flex w-full items-center justify-between gap-3 overflow-hidden px-5 py-3.5 text-left transition-colors hover:bg-muted/20"
              >
                <div
                  className="pointer-events-none absolute inset-x-0 top-0 h-[3px]"
                  style={{ background: group.color }}
                  aria-hidden
                />
                <div className="relative flex items-center gap-3">
                  <div
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
                    style={{ background: `${group.color}18`, border: `1px solid ${group.color}40` }}
                  >
                    <TypeIcon type={group.type} size={24} />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[15px] font-semibold">{group.label}</span>
                      <span className="rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                        {topLevelCount}
                      </span>
                      {group.isLiability ? (
                        <span className="rounded-md border border-destructive/40 bg-destructive/10 px-1.5 py-0.5 text-[10px] leading-none text-destructive">
                          {t('accounts.badge.liability')}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {/* 淨額為正(溢繳,例如信用卡繳超額)時不該再說「合計欠款」——
                          文案跟著色調(見下方 Amount tone)一起翻轉才不會自相矛盾。 */}
                      {group.isLiability && group.subtotals.every((st) => st.value <= 0)
                        ? t('accounts.totalOwed')
                        : t('accounts.totalBalance')}
                    </div>
                  </div>
                </div>
                <div className="relative flex items-center gap-3">
                  {/* 小计按币种逐条展示 —— 单币种 1 条(同原样);该组跨币种时各币种
                      一行,绝不相加。多币种页统一带币种符号消歧。 */}
                  <div className="flex flex-col items-end gap-0.5">
                    {group.subtotals.map((st) => (
                      <Amount
                        key={st.currency}
                        value={group.isLiability ? Math.abs(st.value) : st.value}
                        currency={st.currency}
                        showCurrency={multiCurrency}
                        size={group.subtotals.length > 1 ? 'md' : 'xl'}
                        bold
                        // 負債分組小計帶符號展示色調:欠款(負)才是警示紅,溢繳(正,
                        // 例如信用卡繳超額)經濟上是資產頭寸,不該跟欠款同一種色
                        // (2026-08-12 使用者回報:信用卡溢繳 500 卻顯示欠款配色)。
                        tone={group.isLiability ? (st.value > 0 ? 'positive' : 'negative') : 'default'}
                      />
                    ))}
                  </div>
                  <span
                    className={`text-xl text-muted-foreground transition-transform ${
                      isCollapsed ? '' : 'rotate-90'
                    }`}
                    aria-hidden
                  >
                    ›
                  </span>
                </div>
              </button>
              {!isCollapsed ? (
                editingOrder ? (
                  <ReorderableGroupRows
                    group={group}
                    childrenByParent={childrenByParent}
                    childIds={childIds}
                    onReorderBlocks={onReorderBlocks}
                    onReorderChildren={onReorderChildren}
                  />
                ) : (
                  <div className="divide-y divide-border/40 border-t border-border/40">
                    {group.rows
                      .filter((row) => !childIds.has(row.id))
                      .map((row) => (
                        <AccountListRow
                          key={row.id}
                          row={row}
                          color={group.color}
                          isLiability={group.isLiability}
                          canManage={canManage}
                          onEdit={onEdit}
                          onDelete={onDelete}
                          onClick={onClickAccount}
                          avatarPreviewUrlByFileId={avatarPreviewUrlByFileId}
                          childRows={childrenByParent.get(row.id)}
                          baseCurrency={baseCurrency}
                          fxRates={fxRates}
                          fxOverrides={fxOverrides}
                          amountSize="lg"
                        />
                      ))}
                  </div>
                )
              ) : null}
            </div>
          )
        })}
      </div>

      {/* 账户隐藏(issue #240):所有在用分组之后,「已隐藏」折叠分区(默认折叠)。
          净资产/资产构成(上面的 hero + 饼图)已按 D1 用全量 rows 计算,不受此分区影响。 */}
      <HiddenAccountsSection
        rows={hiddenRows}
        canManage={canManage}
        onEdit={onEdit}
        onRestore={onRestore}
        onClickAccount={onClickAccount}
      />
    </div>
  )
}

/**
 * 帳戶清單拖曳排序(2026-09-05)單一列的外觀:名稱 + 圖示 + 子帳戶數量徽章,
 * 右側拖曳手把(`GripVertical`)——只有從手把才能拖動,列本身不可點擊/編輯/
 * 刪除,避免跟其它手勢衝突,對齊 App 端 `_ReorderableAccountRow` 的設計。
 */
function ReorderableRow({
  row,
  color,
  depth = 0,
  childCount
}: {
  row: ReadAccount
  color: string
  depth?: number
  childCount?: number
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: row.id
  })
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1
  }
  return (
    <div
      ref={setNodeRef}
      style={{ ...style, paddingLeft: `${14 + depth * 22}px` }}
      className="flex items-center justify-between gap-3 border-b border-border/30 bg-card py-2.5 pr-3 last:border-b-0"
    >
      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        <div
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
          style={{ background: `${color}18`, border: `1px solid ${color}40` }}
        >
          <TypeIcon type={row.account_type || 'other'} size={18} />
        </div>
        <div className="flex min-w-0 flex-1 items-center gap-1.5">
          <span className="truncate text-sm font-medium">{row.name}</span>
          {childCount ? (
            <span className="shrink-0 rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              {childCount}
            </span>
          ) : null}
        </div>
      </div>
      <button
        type="button"
        {...attributes}
        {...listeners}
        className="shrink-0 cursor-grab touch-none p-1 text-muted-foreground active:cursor-grabbing"
      >
        <GripVertical size={16} />
      </button>
    </div>
  )
}

/**
 * 帳戶清單拖曳排序(2026-09-05)一個分組的可拖曳清單:外層 `SortableContext`
 * 拖曳「頂層帳戶」(獨立帳戶 + 合併帳單主帳戶)彼此順序,有子帳戶的區塊在
 * 下方再包一層 `SortableContext`,只重排該區塊自己的子帳戶——跟 App 端
 * `_buildReorderableBlocks` 的兩層設計對齊。單一 `DndContext` 同時包住兩層,
 * `handleDragEnd` 自己判斷 active/over 落在哪一層清單裡再分派對應 callback,
 * 而不是靠巢狀 `SortableContext` 自動處理跨層拖曳(dnd-kit 本身不支援)。
 */
function ReorderableGroupRows({
  group,
  childrenByParent,
  childIds,
  onReorderBlocks,
  onReorderChildren
}: {
  group: AssetGroup
  childrenByParent: Map<string, ReadAccount[]>
  childIds: Set<string>
  onReorderBlocks?: (type: string, orderedRows: ReadAccount[]) => void
  onReorderChildren?: (parentId: string, orderedRows: ReadAccount[]) => void
}) {
  const topLevelRows = useMemo(
    () => group.rows.filter((row) => !childIds.has(row.id)),
    [group.rows, childIds]
  )
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const activeId = String(active.id)
    const overId = String(over.id)

    const topIds = topLevelRows.map((r) => r.id)
    if (topIds.includes(activeId) && topIds.includes(overId)) {
      const oldIndex = topIds.indexOf(activeId)
      const newIndex = topIds.indexOf(overId)
      onReorderBlocks?.(group.type, arrayMove(topLevelRows, oldIndex, newIndex))
      return
    }
    for (const [parentId, children] of childrenByParent.entries()) {
      const ids = children.map((c) => c.id)
      if (ids.includes(activeId) && ids.includes(overId)) {
        const oldIndex = ids.indexOf(activeId)
        const newIndex = ids.indexOf(overId)
        onReorderChildren?.(parentId, arrayMove(children, oldIndex, newIndex))
        return
      }
    }
  }

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={topLevelRows.map((r) => r.id)} strategy={verticalListSortingStrategy}>
        <div className="divide-y divide-border/40 border-t border-border/40">
          {topLevelRows.map((row) => {
            const children = childrenByParent.get(row.id)
            return (
              <div key={row.id}>
                <ReorderableRow row={row} color={group.color} childCount={children?.length} />
                {children && children.length > 0 ? (
                  <SortableContext
                    items={children.map((c) => c.id)}
                    strategy={verticalListSortingStrategy}
                  >
                    <div>
                      {children.map((child) => (
                        <ReorderableRow
                          key={child.id}
                          row={child}
                          color={TYPE_COLORS[child.account_type || 'other'] || group.color}
                          depth={1}
                        />
                      ))}
                    </div>
                  </SortableContext>
                ) : null}
              </div>
            )
          })}
        </div>
      </SortableContext>
    </DndContext>
  )
}

/**
 * 「已隐藏」分区 —— 置于所有在用分组之后,默认折叠;分区头露 count + 按币种
 * 小计(对账用,不跨币种相加)。行内弱化展示(降不透明度),点行名进详情看历史,
 * 「恢复」按钮直接 PATCH hidden=false 回到在用分区(不经编辑弹窗)。
 */
function HiddenAccountsSection({
  rows,
  canManage,
  onEdit,
  onRestore,
  onClickAccount
}: {
  rows: ReadAccount[]
  canManage: boolean
  onEdit: (row: ReadAccount) => void
  onRestore?: (row: ReadAccount) => void
  onClickAccount?: (row: ReadAccount) => void
}) {
  const t = useT()
  const [collapsed, setCollapsed] = useState(true)

  if (rows.length === 0) return null

  // 小计按币种分别累加,绝不跨币种相加(与 computeTypeGroups 同口径)。主帳戶
  // 的 balance 已含子帳戶加總,子帳戶要排除避免重複計(見
  // buildDoubleCountedChildIds 註解)。
  const doubleCountedIds = buildDoubleCountedChildIds(rows)
  const byCurrency = new Map<string, number>()
  for (const row of rows) {
    if (doubleCountedIds.has(row.id)) continue
    const cur = (row.currency || 'CNY').toUpperCase()
    byCurrency.set(cur, (byCurrency.get(cur) ?? 0) + accountBalance(row))
  }
  const subtotals = [...byCurrency.entries()]
  const sortedRows = rows.slice().sort((a, b) => a.name.localeCompare(b.name))

  return (
    <div className="overflow-hidden rounded-2xl border border-dashed border-border/50 bg-muted/10">
      <button
        type="button"
        onClick={() => setCollapsed((prev) => !prev)}
        className="flex w-full items-center justify-between gap-3 px-5 py-3 text-left transition-colors hover:bg-muted/20"
      >
        <span className="text-[13px] font-medium text-muted-foreground">
          {t('accounts.hidden.sectionTitle', { count: rows.length })}
        </span>
        <div className="flex items-center gap-3">
          <div className="flex flex-col items-end gap-0.5">
            {subtotals.map(([cur, value]) => (
              <Amount
                key={cur}
                value={value}
                currency={cur}
                showCurrency={subtotals.length > 1}
                size="sm"
                className="text-muted-foreground"
              />
            ))}
          </div>
          <span
            className={`text-lg text-muted-foreground transition-transform ${
              collapsed ? '' : 'rotate-90'
            }`}
            aria-hidden
          >
            ›
          </span>
        </div>
      </button>
      {!collapsed ? (
        <div className="divide-y divide-border/40 border-t border-border/40">
          {sortedRows.map((row) => (
            <div
              key={row.id}
              className="flex items-center justify-between gap-3 px-5 py-2.5 opacity-70 transition-opacity hover:opacity-100"
            >
              <button
                type="button"
                onClick={() => onClickAccount?.(row)}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
              >
                <TypeIcon type={row.account_type || 'other'} size={20} />
                <span className="truncate text-sm">{row.name}</span>
                <span className="shrink-0 rounded bg-muted px-1 py-[1px] text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                  {t('accounts.hidden.badge')}
                </span>
              </button>
              <div className="flex shrink-0 items-center gap-2">
                <Amount
                  value={accountBalance(row)}
                  currency={row.currency || 'CNY'}
                  size="sm"
                  className="text-muted-foreground"
                />
                <button
                  type="button"
                  disabled={!canManage}
                  onClick={() => onEdit(row)}
                  className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:pointer-events-none disabled:opacity-40"
                >
                  {t('common.edit')}
                </button>
                {onRestore ? (
                  <button
                    type="button"
                    disabled={!canManage}
                    onClick={() => onRestore(row)}
                    className="rounded-md border border-primary/40 px-2 py-1 text-xs font-medium text-primary hover:bg-primary/10 disabled:pointer-events-none disabled:opacity-40"
                  >
                    {t('accounts.hidden.restore')}
                  </button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

/**
 * 资产总览 hero：大号净值 + 资产 / 负债两行。跟 overview 页的 OverviewHero
 * 区别在于不接 period income/expense，只展示 account 聚合后的静态净值。
 */
function AssetsSummaryHero({
  summary,
  currency
}: {
  summary: AssetSummary
  currency: string
}) {
  const t = useT()
  return (
    <div className="relative overflow-hidden rounded-2xl border border-primary/30">
      <div
        className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/20 via-primary/5 to-transparent"
        aria-hidden
      />
      <div
        className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-primary/25 blur-3xl"
        aria-hidden
      />
      <div className="relative p-6">
        <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
          {t('accounts.netWorth')}
        </div>
        <Amount
          value={summary.netWorth}
          currency={currency}
          size="4xl"
          bold
          showCurrency
          tone={summary.netWorth >= 0 ? 'positive' : 'negative'}
          className="mt-2 block font-black tracking-tight"
        />
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-emerald-600/80 dark:text-emerald-400/80">
              {t('accounts.assets')}
            </div>
            <Amount
              value={summary.assetTotal}
              currency={currency}
              size="xl"
              bold
              showCurrency
              tone="positive"
              className="mt-0.5 block"
            />
          </div>
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-3 py-2">
            <div className="text-[10px] uppercase tracking-wider text-rose-600/80 dark:text-rose-400/80">
              {t('accounts.liabilities')}
            </div>
            <Amount
              value={Math.abs(summary.liabilityTotal)}
              currency={currency}
              size="xl"
              bold
              showCurrency
              tone={summary.liabilityTotal > 0 ? 'positive' : 'negative'}
              className="mt-0.5 block"
            />
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * 资产构成迷你饼图：基于分组的 color + subtotal，不引第三方图表库，纯 SVG
 * conic-gradient 做分段圆环 + 左侧 legend。够快、够轻、跟配色系统一致。
 */
export function AssetsCompositionMini({
  groups,
  currency,
  showCurrency = false,
  embedded = false,
  title,
  approx = false
}: {
  groups: AssetGroup[]
  currency: string
  /** 中心总额是否带币种符号(多币种卡内需要,单币种页保持原样不带)。 */
  showCurrency?: boolean
  /** 嵌在币种卡里时去掉自身的边框/卡片底色,避免双层卡片。 */
  embedded?: boolean
  /** 标题文案覆盖,缺省走 accounts.composition(折算汇总视图传"资产构成(折X)")。 */
  title?: string
  /** true 时中心合计金额前加「≈」前缀,用于折算汇总视图;分币种卡(原币)不传,缺省 false。 */
  approx?: boolean
}) {
  const t = useT()
  // 「资产构成」只含资产头寸：负债类型（信用卡/贷款）里真正欠款的部分不进饼图 ——
  // 它体现在「负债」汇总里，不属于资产构成。但同一组里溢缴（正值,例如信用卡缴超额）
  // 经济上是资产头寸,要跟 computeCurrencySummary 的 assetTotal 拆法保持一致地计入
  // 饼图,否则会出现「资产」方块 1500 但饼图中心合计仍是 1000 的自相矛盾
  // (2026-08-12 使用者回报)。逐账户拆分、且要排除主帐户/子帐户重复计的部分
  // （group.rows 里父子都在,金额已回填到父身上，子帐户不能再算一次）。
  const data = groups
    .map((g) => {
      if (!g.isLiability) {
        return { type: g.type, label: g.label, color: g.color, value: Math.abs(g.subtotals.reduce((s, x) => s + x.value, 0)) }
      }
      const doubleCounted = buildDoubleCountedChildIds(g.rows)
      const overpaid = g.rows.reduce(
        (s, r) => (doubleCounted.has(r.id) ? s : s + Math.max(0, accountBalance(r))),
        0
      )
      return overpaid > 0 ? { type: g.type, label: g.label, color: g.color, value: overpaid } : null
    })
    .filter((d): d is { type: string; label: string; color: string; value: number } => d !== null)
  // 中心合计 / 扇区 / 百分比分母都用「资产合计」（资产组之和）—— 绝不把 |负债|
  // 算进来，否则信用卡等负债会被计入资产构成（这正是之前的 bug）。
  const assetTotal = data.reduce((s, d) => s + d.value, 0)
  const total = assetTotal > 0 ? assetTotal : 1
  // conic-gradient 分段
  let acc = 0
  const stops: string[] = []
  for (const d of data) {
    const start = (acc / total) * 100
    acc += d.value
    const end = (acc / total) * 100
    stops.push(`${d.color} ${start.toFixed(3)}% ${end.toFixed(3)}%`)
  }
  const gradient = stops.length > 0
    ? `conic-gradient(from -90deg, ${stops.join(',')})`
    : 'hsl(var(--muted))'

  return (
    <div
      className={
        embedded
          ? 'px-5 pb-5'
          : 'overflow-hidden rounded-2xl border border-border/50 bg-card/80 p-5'
      }
    >
      <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
        {title ?? t('accounts.composition')}
      </div>
      {data.length === 0 ? (
        <div className="flex h-40 items-center justify-center text-xs text-muted-foreground">
          {t('accounts.empty.noData')}
        </div>
      ) : (
        <div className="flex items-center gap-5">
          {/* 环 */}
          <div className="relative h-36 w-36 shrink-0">
            <div
              className="absolute inset-0 rounded-full"
              style={{ background: gradient }}
              aria-hidden
            />
            {/* 内白（跟随卡片背景）掏出甜甜圈 */}
            <div className="absolute inset-[18%] rounded-full bg-card" aria-hidden />
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {t('common.total')}
              </div>
              <div className="mt-0.5 flex items-baseline gap-0.5">
                {approx ? (
                  <span className="font-mono text-[10px] text-muted-foreground">≈</span>
                ) : null}
                <Amount
                  value={assetTotal}
                  currency={currency}
                  showCurrency={showCurrency}
                  size="md"
                  bold
                />
              </div>
            </div>
          </div>
          {/* legend */}
          <ul className="min-w-0 flex-1 space-y-1.5">
            {data.map((d) => {
              const pct = assetTotal > 0 ? (d.value / assetTotal) * 100 : 0
              return (
                <li key={d.type} className="flex items-center gap-2 text-xs">
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-sm"
                    style={{ background: d.color }}
                  />
                  <span className="flex-1 truncate">{d.label}</span>
                  <span className="font-mono tabular-nums text-muted-foreground">
                    {pct.toFixed(1)}%
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

// 与 mobile 端 accounts_page.dart / account_edit_page.dart 对齐的账户类型分组。
// label 由 accountTypeLabel() 走 i18n 查 accountType.<value>,这里只保留 value
// 顺序——顺序决定了分组/下拉里的展示顺序。
const TRADABLE_TYPES: { value: string }[] = [
  { value: 'cash' },
  { value: 'bank_card' },
  { value: 'credit_card' },
  { value: 'account_group' },
  { value: 'alipay' },
  { value: 'wechat' },
  { value: 'other' }
]
const VALUATION_TYPES: { value: string }[] = [
  { value: 'real_estate' },
  { value: 'vehicle' },
  { value: 'investment' },
  { value: 'insurance' },
  { value: 'social_fund' },
  { value: 'loan' }
]

// ── 多币种聚合 ────────────────────────────────────────────────────────────
// 铁律:资产统计绝不跨币种相加($1000 不是 ¥1000)。所有汇总先按币种切分再各算各
// 的:单币种(绝大多数)维持单一 hero + 饼图;多币种则每币种一张卡 + 各自饼图。
// 没有汇率基建、也不做换算 —— 宁可不给单一总额,也不给一个错的合并数字。

/** 一种币种的聚合结果:净值汇总 + 该币种内按类型分组(组里带饼图所需 subtotal)。 */
export type CurrencyBucket = {
  currency: string
  summary: AssetSummary
  groups: AssetGroup[]
}

// 类型展示顺序:可交易在前、估值在后,跟编辑弹窗里的分组顺序一致。
const ACCOUNT_ORDER: string[] = [
  ...TRADABLE_TYPES.map((x) => x.value),
  ...VALUATION_TYPES.map((x) => x.value)
]

/** 按账户类型分组。每组小计再按币种拆:同一类型若混多币种(只会出现在底部跨币种
 *  列表),各币种独立累计、不相加。单币种入参时每组只有 1 条 subtotal。 */
export function computeTypeGroups(rows: readonly ReadAccount[], t: (k: string) => string): AssetGroup[] {
  // 需求 #1(Phase 17):挂信用卡/银行子帐户的 account_group 主帐户按子帐户
  // 内容归到对应分组,不再永远自成一个独立的「主帐户」分组
  // (见 resolveRowDisplayType/resolveAccountGroupDisplayType)。
  const childrenByParent = buildParentChildrenMap(rows)
  // 主帳戶(account_group)的 balance 已含子帳戶加總(見
  // buildDoubleCountedChildIds 註解),子帳戶跟主帳戶又常被歸進同一個
  // type 分組(單一子帳戶類型時,見 resolveAccountGroupDisplayType)——分組
  // 小計加總時子帳戶要排除,否則「合計餘額」會把子帳戶的錢再加一次而變兩倍。
  const doubleCountedIds = buildDoubleCountedChildIds(rows)
  const buckets: Record<string, ReadAccount[]> = {}
  for (const row of rows) {
    const key = resolveRowDisplayType(row, childrenByParent)
    buckets[key] = buckets[key] || []
    buckets[key].push(row)
  }
  return ACCOUNT_ORDER.filter((type) => (buckets[type] || []).length > 0).map((type) => {
    // 帳戶清單拖曳排序(2026-09-05):照 sort_order 排,null(舊資料/舊版
    // App)排到最後,同 sort_order/都缺值時用名稱當 tiebreaker——跟後端
    // read/ledgers.py 的排序規則對齊,確保 App/Web 看到同一個順序。
    const groupRows = (buckets[type] || [])
      .slice()
      .sort((a, b) => {
        const orderA = a.sort_order ?? Number.MAX_SAFE_INTEGER
        const orderB = b.sort_order ?? Number.MAX_SAFE_INTEGER
        if (orderA !== orderB) return orderA - orderB
        return a.name.localeCompare(b.name)
      })
    const isLiability = LIABILITY_TYPES.has(type)
    // 小计带符号累加(与 computeCurrencySummary 同口径)——溢缴的卡会抵销欠款。
    // 展示"共欠"时由渲染处对组合计取 abs,绝不逐账户 abs(否则 +10w 卡 + −20w 贷
    // 会显示成欠 30w)。
    const byCur = new Map<string, number>()
    for (const r of groupRows) {
      if (doubleCountedIds.has(r.id)) continue
      const cur = (r.currency || 'CNY').toUpperCase()
      byCur.set(cur, (byCur.get(cur) ?? 0) + accountBalance(r))
    }
    return {
      type,
      label: accountTypeLabel(t, type),
      color: TYPE_COLORS[type] || '#94a3b8',
      isLiability,
      rows: groupRows,
      subtotals: [...byCur.entries()].map(([currency, value]) => ({ currency, value }))
    }
  })
}

/**
 * 多币种时:每种币种一张卡 —— 顶部币种 badge + 净值,中间资产/负债,底部该币种
 * 自己的构成饼图。金额全部带该币种符号,绝不跟其它币种混。
 */
export function CurrencyAssetCard({ entry }: { entry: CurrencyBucket }) {
  const t = useT()
  const { currency, summary, groups } = entry
  return (
    <div className="flex flex-col overflow-hidden rounded-2xl border border-border/50 bg-card/60">
      <div className="flex items-center justify-between gap-2 border-b border-border/40 px-4 py-3">
        <span className="rounded-md bg-primary/10 px-2 py-0.5 text-xs font-semibold tracking-wide text-primary">
          {currency}
        </span>
        <div className="min-w-0 text-right">
          <div className="text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
            {t('accounts.netWorth')}
          </div>
          <Amount
            value={summary.netWorth}
            currency={currency}
            showCurrency
            size="2xl"
            bold
            tone={summary.netWorth >= 0 ? 'positive' : 'negative'}
            className="block"
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 px-4 py-3">
        <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/5 px-2.5 py-1.5">
          <div className="text-[9px] uppercase tracking-wider text-emerald-600/80 dark:text-emerald-400/80">
            {t('accounts.assets')}
          </div>
          <Amount
            value={summary.assetTotal}
            currency={currency}
            showCurrency
            size="md"
            bold
            tone="positive"
            className="mt-0.5 block"
          />
        </div>
        <div className="rounded-lg border border-rose-500/25 bg-rose-500/5 px-2.5 py-1.5">
          <div className="text-[9px] uppercase tracking-wider text-rose-600/80 dark:text-rose-400/80">
            {t('accounts.liabilities')}
          </div>
          <Amount
            value={Math.abs(summary.liabilityTotal)}
            currency={currency}
            showCurrency
            size="md"
            bold
            tone={summary.liabilityTotal > 0 ? 'positive' : 'negative'}
            className="mt-0.5 block"
          />
        </div>
      </div>
      <AssetsCompositionMini
        groups={groups}
        currency={currency}
        showCurrency
        embedded
      />
    </div>
  )
}

type AccountsPanelProps = {
  form: AccountForm
  rows: ReadAccount[]
  canManage: boolean
  showCreatorColumn?: boolean
  onFormChange: (next: AccountForm) => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onEdit: (row: ReadAccount) => void
  onDelete?: (row: ReadAccount) => void
  onClickAccount?: (row: ReadAccount) => void
  /** true 时跳过多币种「每币种一张卡」网格区(用于折算汇总视图);缺省 false,
   *  其它调用方零影响。详见 MobileStyleAssets。 */
  hideCurrencyCards?: boolean
  /** 账户隐藏(issue #240):底部「已隐藏」分区每张卡的快捷「恢复」按钮回调。
   *  不传则该按钮不渲染(调用方尚未接线时零影响)。 */
  onRestore?: (row: ReadAccount) => void
  /** 编辑弹窗的 open 状态本来完全是本组件内部 state(点行内「编辑」才会
   *  setOpen(true))。跨页面场景(比如信用卡帐单卡片的「前往帳戶設定」,
   *  经全局事件把 form 灌进 onFormChange 后)没有「点行」这个动作可以顺手
   *  setOpen —— 每次这个数字变化(不是数值本身,是"变了没有")就强制打开
   *  一次弹窗。不传則完全不影响现有行為(2026-08-02 用户反馈"前往帳戶設定"
   *  点了没反应,根因就是这条路径漏了 setOpen)。 */
  openSignal?: number
  /** 帳戶頭像(2026-08-02 補強):`avatar_cloud_file_id` → 已加载好的 blob
   *  URL,透传给 BankCardTile 渲染卡面照片。 */
  avatarPreviewUrlByFileId?: Record<string, string>
  /** 帳戶頭像上传回调 —— 编辑弹窗里选文件后调用,返回 {fileId, sha256} 写进
   *  form;不传则不渲染上传 UI(调用方尚未接线时零影响,同 CategoriesPanel
   *  的 onUploadIcon 模式)。 */
  onUploadAvatar?: (file: File) => Promise<{ fileId: string; sha256: string } | null>
  /** 外幣列原幣顯示 + 快速換算按鈕(2026-08-12):透傳給 MobileStyleAssets →
   *  AccountListRow,詳見那邊的註解。全部不傳時零影響。 */
  baseCurrency?: string
  fxRates?: ExchangeRatesResponse | null
  fxOverrides?: ExchangeRateOverride[]
  /** 外層拖曳:重排某個分類裡「頂層帳戶」彼此的順序。不傳則不渲染「編輯
   *  排序」切換按鈕(調用方尚未接線時零影響)。 */
  onReorderBlocks?: (type: string, orderedRows: ReadAccount[]) => void
  /** 內層拖曳:重排某個合併帳單主帳戶底下子帳戶彼此的順序。 */
  onReorderChildren?: (parentId: string, orderedRows: ReadAccount[]) => void
}

export function AccountsPanel({
  form,
  rows,
  canManage,
  showCreatorColumn = false,
  onFormChange,
  onSave,
  onReset,
  onEdit,
  onDelete,
  onClickAccount,
  hideCurrencyCards = false,
  onRestore,
  openSignal,
  avatarPreviewUrlByFileId,
  onUploadAvatar,
  baseCurrency,
  fxRates,
  fxOverrides,
  onReorderBlocks,
  onReorderChildren
}: AccountsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  // 帳戶清單拖曳排序(2026-09-05):切換模式的 state 放在這裡(跟 open 同層
  // 級)——只是個 UI 開關,不需要提升到 AccountsPage。
  const [editingOrder, setEditingOrder] = useState(false)
  // 帳戶頭像裁剪(2026-09-01 補強):選檔後不直接上傳,先跳裁剪彈窗,確認後才
  // 呼叫 onUploadAvatar,固定 4:3 對齊現有卡片預覽形狀。
  const [avatarCropSource, setAvatarCropSource] = useState<AvatarCropSource | null>(null)
  const prevOpenSignalRef = useRef(openSignal)
  useEffect(() => {
    if (openSignal !== undefined && openSignal !== prevOpenSignalRef.current) {
      setOpen(true)
    }
    prevOpenSignalRef.current = openSignal
  }, [openSignal])

  // 账户隐藏(issue #240):净资产 hero / 资产构成饼图按 D1 用全量 rows 计算
  // (隐藏不改「钱在哪」);只有「底部分组列表」拆成在用/已隐藏两部分展示。
  const visibleRows = useMemo(() => rows.filter((row) => !row.hidden), [rows])
  const hiddenRows = useMemo(() => rows.filter((row) => row.hidden), [rows])

  // 按币种切分后再聚合 —— 资产统计绝不跨币种相加(见 computeCurrencySummary)。
  // 单币种(绝大多数场景)→ currencyBuckets 只有 1 条,顶部展示完全维持原样。
  // 用全量 rows(含隐藏)算,对齐 D1:隐藏账户仍计入净资产/资产构成。
  // 納入總餘額(Phase 18):这个过滤独立于 hidden/visibleRows 切分——一个
  // 账户可以「隐藏但仍计入总额」,也可以「显示但不计入总额」,两个开关互不
  // 耦合。只影响顶部净资产 hero / 资产构成饼图,不影响下方 listGroups。
  const totalIncludedRows = useMemo(
    () => rows.filter((row) => row.include_in_total !== false),
    [rows]
  )
  const currencyBuckets = useMemo<CurrencyBucket[]>(() => {
    return [...splitByCurrency(totalIncludedRows).entries()]
      .map(([currency, curRows]) => ({
        currency,
        summary: computeCurrencySummary(curRows),
        groups: computeTypeGroups(curRows, t)
      }))
      // 体量大的币种排前面(资产 + |负债|)
      .sort(
        (a, b) =>
          b.summary.assetTotal +
          Math.abs(b.summary.liabilityTotal) -
          (a.summary.assetTotal + Math.abs(a.summary.liabilityTotal))
      )
  }, [totalIncludedRows, t])

  // 底部列表:跨币种按类型分组(每组小计按币种拆,见 computeTypeGroups)。
  // 只用在用账户 —— 隐藏账户退场到底部「已隐藏」分区(HiddenAccountsSection)。
  const listGroups = useMemo(() => computeTypeGroups(visibleRows, t), [visibleRows, t])

  // 顶部"新建账户"按钮 —— rows 空时也要显示,否则首次使用没法建账户。
  // 复用现有 dialog,form 重置成 defaults 让 dialog 进入 create 模式。
  const handleOpenCreate = () => {
    onFormChange(accountDefaults())
    setOpen(true)
  }

  return (
    <>
      {/* 卡片式布局不再套 ListTableShell 的灰色 header；hero 已经自带标题级
          视觉锚，再加一个"资产管理"横条显得冗余。
          有数据时:button 在 stats 卡片下方(MobileStyleAssets 内部);
          空数据时:把 button 显示在 EmptyState 上方,引导首次创建。 */}
      {rows.length === 0 ? (
        <>
          <div className="mb-3 flex items-center justify-end">
            <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
              {t('accounts.button.create')}
            </Button>
          </div>
          <EmptyState
            icon={
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
                   strokeLinejoin="round">
                <rect x="2" y="5" width="20" height="14" rx="2" />
                <path d="M2 10h20" />
                <path d="M6 15h4" />
              </svg>
            }
            title={t('accounts.empty.title')}
            description={t('accounts.empty.desc')}
          />
        </>
      ) : (
        <MobileStyleAssets
          byCurrency={currencyBuckets}
          listGroups={listGroups}
          hiddenRows={hiddenRows}
          canManage={canManage}
          onEdit={(row) => {
            onEdit(row)
            setOpen(true)
          }}
          onDelete={onDelete}
          onClickAccount={onClickAccount}
          onCreate={handleOpenCreate}
          hideCurrencyCards={hideCurrencyCards}
          onRestore={onRestore}
          avatarPreviewUrlByFileId={avatarPreviewUrlByFileId}
          baseCurrency={baseCurrency}
          fxRates={fxRates}
          fxOverrides={fxOverrides}
          editingOrder={editingOrder}
          onToggleEditingOrder={
            onReorderBlocks ? () => setEditingOrder((prev) => !prev) : undefined
          }
          onReorderBlocks={onReorderBlocks}
          onReorderChildren={onReorderChildren}
        />
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[88vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{form.editingId ? t('accounts.button.update') : t('accounts.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('accounts.table.name')}</Label>
              <Input
                placeholder={t('accounts.placeholder.name')}
                value={form.name}
                onChange={(e) => onFormChange({ ...form, name: e.target.value })}
              />
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <Label>{t('accounts.table.type')}</Label>
                {/* 编辑模式下:可交易类型不能改成估值类型(对齐 mobile
                    account_edit_page disabled 逻辑)。新建时无限制。 */}
                <Select
                  value={form.account_type || 'cash'}
                  onValueChange={(value) => {
                    if (form.editingId) {
                      const wasTradable = TRADABLE_TYPES.some((x) => x.value === form.account_type)
                      const isValuation = VALUATION_TYPES.some((x) => x.value === value)
                      if (wasTradable && isValuation) return
                    }
                    // 额度/帳單日/還款日:account_group 自己身上,或沒有掛靠
                    // 任何群組的獨立信用卡(§2.9 2026-08-02 第二輪放寬——單卡
                    // 也該有群組的全部功能)才會顯示這組欄位;切到不符合的
                    // 类型时清空,避免留着不会显示又不会被清掉的死值。
                    const showsBilling = (ty: string, parentId: string) =>
                      ty === 'account_group' || (ty === 'credit_card' && !parentId)
                    const next: AccountForm = { ...form, account_type: value }
                    if (
                      showsBilling(form.account_type, form.parent_account_id) &&
                      !showsBilling(value, form.parent_account_id)
                    ) {
                      next.credit_limit = ''
                      next.billing_day = ''
                      next.payment_due_day = ''
                    }
                    // 离开 credit_card / bank_card → 清空「掛靠主帳戶」选择
                    // (信用卡与银行账户都可以掛靠群組)。
                    if (
                      (form.account_type === 'credit_card' || form.account_type === 'bank_card') &&
                      value !== 'credit_card' &&
                      value !== 'bank_card'
                    ) {
                      next.parent_account_id = ''
                    }
                    // 离开 bank_card / credit_card / account_group → 清空银行卡元信息
                    const wasBankOrCredit =
                      form.account_type === 'bank_card' ||
                      form.account_type === 'credit_card' ||
                      form.account_type === 'account_group'
                    const isBankOrCredit =
                      value === 'bank_card' || value === 'credit_card' || value === 'account_group'
                    if (wasBankOrCredit && !isBankOrCredit) {
                      next.bank_name = ''
                      next.card_last_four = ''
                    }
                    // 股票持股(2026-09-28):新建的投資理財帳戶預設「不納入
                    // 總餘額」——股票市值另外顯示在「投資市值(預估)」卡,不
                    // 混進淨資產(對齊 App account_edit_page._selectType)。只影響
                    // 新建,編輯既有帳戶不動使用者原本的設定。
                    if (!form.editingId) {
                      next.include_in_total = value !== 'investment'
                    }
                    onFormChange(next)
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('accounts.placeholder.type')} />
                  </SelectTrigger>
                  <SelectContent className="max-h-80">
                    <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                      {t('accounts.group.tradable')}
                    </div>
                    {TRADABLE_TYPES.map((ty) => (
                      <SelectItem key={ty.value} value={ty.value}>
                        {accountTypeLabel(t, ty.value)}
                      </SelectItem>
                    ))}
                    <div className="mt-1 border-t border-border/50 px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                      {t('accounts.group.valuation')}
                    </div>
                    {VALUATION_TYPES.map((ty) => (
                      <SelectItem key={ty.value} value={ty.value}>
                        {accountTypeLabel(t, ty.value)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('accounts.table.currency')}</Label>
                {/* 复用 CurrencySelectorTrigger:点开后弹搜索 + 区域分组 dialog。
                    页面层(AccountsPage)负责"已有交易则锁定币种"的判断,这里
                    只是个普通选择器。 */}
                <CurrencySelectorTrigger
                  value={form.currency || 'TWD'}
                  onChange={(code) => onFormChange({ ...form, currency: code })}
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label>{t('accounts.table.init')}</Label>
              <Input
                placeholder={t('accounts.placeholder.initialBalance')}
                value={form.initial_balance}
                onChange={(e) => onFormChange({ ...form, initial_balance: e.target.value })}
              />
            </div>

            {/* 額度/帳單日/還款日(§2.9 Phase 4,2026-08-02 改版為群組模型,
                同日第二輪放寬到單卡):account_group 是純管理容器,這些欄位
                設在群組自己身上,子帳戶(實體信用卡)沿用群組的結帳週期,
                不再各自設定;沒有掛靠任何群組的獨立信用卡則直接設在自己
                身上(自己既是「群組」也是唯一「成員」)。一旦選了掛靠某個
                群組,這組欄位就不再顯示(改用群組共用的)。 */}
            {form.account_type === 'account_group' ||
            (form.account_type === 'credit_card' && !form.parent_account_id) ? (
              <div className="rounded-md border border-border/50 bg-muted/20 p-3 space-y-3">
                <div className="text-xs font-semibold text-muted-foreground">
                  {form.account_type === 'account_group'
                    ? t('accounts.section.accountGroup')
                    : t('accounts.section.standaloneCardBilling')}
                </div>
                <div className="grid gap-3 md:grid-cols-3">
                  <div className="space-y-1">
                    <Label>{t('accounts.field.creditLimit')}</Label>
                    <Input
                      type="number"
                      inputMode="decimal"
                      placeholder="0"
                      value={form.credit_limit}
                      onChange={(e) => onFormChange({ ...form, credit_limit: e.target.value })}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label>{t('accounts.field.billingDay')}</Label>
                    <Input
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={31}
                      placeholder="1-31"
                      value={form.billing_day}
                      onChange={(e) => onFormChange({ ...form, billing_day: e.target.value })}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label>{t('accounts.field.paymentDueDay')}</Label>
                    <Input
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={31}
                      placeholder="1-31"
                      value={form.payment_due_day}
                      onChange={(e) => onFormChange({ ...form, payment_due_day: e.target.value })}
                    />
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  {form.account_type === 'account_group'
                    ? t('accounts.section.accountGroupHint')
                    : t('accounts.section.standaloneCardBillingHint')}
                </p>

                {/* 自動扣繳(§2.9,2026-08-04 改版):開關 + 來源帳戶,不再是
                    一條完整的週期性收支規則。到了繳款截止日,系統會直接從
                    這裡選的帳戶轉帳繳清應繳金額(帳戶有錢的前提下)。 */}
                <div className="space-y-2 border-t border-border/50 pt-3">
                  <div className="flex items-center justify-between">
                    <div className="min-w-0 pr-3">
                      <p className="text-sm font-medium">{t('accounts.autoPay.toggleLabel')}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {t('accounts.autoPay.toggleHint')}
                      </p>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={form.auto_pay_enabled}
                      aria-label={t('accounts.autoPay.toggleLabel') as string}
                      onClick={() =>
                        onFormChange({ ...form, auto_pay_enabled: !form.auto_pay_enabled })
                      }
                      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                        form.auto_pay_enabled ? 'bg-primary' : 'bg-muted-foreground/30'
                      }`}
                    >
                      <span
                        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                          form.auto_pay_enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
                        }`}
                      />
                    </button>
                  </div>
                  {form.auto_pay_enabled ? (
                    <div className="space-y-1">
                      <Label>{t('accounts.autoPay.sourceAccount')}</Label>
                      <Select
                        value={form.auto_pay_from_account_id || '__none__'}
                        onValueChange={(value) =>
                          onFormChange({
                            ...form,
                            auto_pay_from_account_id: value === '__none__' ? '' : value,
                          })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue placeholder={t('accounts.autoPay.sourceAccountPlaceholder')} />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="__none__">
                            {t('accounts.autoPay.sourceAccountPlaceholder')}
                          </SelectItem>
                          {rows
                            .filter(
                              (r) =>
                                r.account_type !== 'account_group' && r.id !== form.editingId,
                            )
                            .map((r) => (
                              <SelectItem key={r.id} value={r.id}>
                                {r.name}
                              </SelectItem>
                            ))}
                        </SelectContent>
                      </Select>
                    </div>
                  ) : null}
                </div>
              </div>
            ) : null}

            {/* 信用卡 / 銀行帳戶:掛靠主帳戶(§2.9 Phase 4 群組模型)——只能選
                account_group 类型的帳戶,信用卡/銀行帳戶自己不能再被拿来当主帳戶。 */}
            {form.account_type === 'credit_card' || form.account_type === 'bank_card' ? (
              <div className="space-y-1">
                <Label>{t('accounts.field.parentAccount')}</Label>
                <Select
                  value={form.parent_account_id || '__none__'}
                  onValueChange={(value) => {
                    const parentId = value === '__none__' ? '' : value
                    // 掛上群組後,這張卡不再是「獨立信用卡」,自己的額度/
                    // 帳單日/還款日欄位不會再顯示(改用群組共用的),清空
                    // 避免留死值。
                    if (parentId) {
                      onFormChange({
                        ...form,
                        parent_account_id: parentId,
                        credit_limit: '',
                        billing_day: '',
                        payment_due_day: '',
                      })
                    } else {
                      onFormChange({ ...form, parent_account_id: parentId })
                    }
                  }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('accounts.field.parentAccountNone')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">{t('accounts.field.parentAccountNone')}</SelectItem>
                    {rows
                      .filter((r) => r.account_type === 'account_group')
                      .map((r) => (
                        <SelectItem key={r.id} value={r.id}>
                          {r.name}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">{t('accounts.field.parentAccountHint')}</p>
              </div>
            ) : null}

            {/* 银行卡 / 信用卡 / 主帳戶 元信息:开户行 + 卡号后四位。 */}
            {form.account_type === 'bank_card' ||
            form.account_type === 'credit_card' ||
            form.account_type === 'account_group' ? (
              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-1">
                  <Label>{t('accounts.field.bankName')}</Label>
                  <Input
                    placeholder={t('accounts.field.bankNameHint')}
                    value={form.bank_name}
                    onChange={(e) => onFormChange({ ...form, bank_name: e.target.value })}
                  />
                </div>
                <div className="space-y-1">
                  <Label>{t('accounts.field.cardLastFour')}</Label>
                  <Input
                    inputMode="numeric"
                    maxLength={4}
                    placeholder="****"
                    value={form.card_last_four}
                    onChange={(e) => {
                      // 只接受数字,最多 4 位 — 跟 mobile 一致(maxLength: 4)
                      const next = e.target.value.replace(/\D/g, '').slice(0, 4)
                      onFormChange({ ...form, card_last_four: next })
                    }}
                  />
                </div>
              </div>
            ) : null}

            {/* 帳戶頭像(2026-08-02 補強):所有帳戶類型都可以設,使用者反饋光靠
                bank_name 文字看不出是哪張卡。 */}
            {onUploadAvatar ? (
              <div className="space-y-1">
                <Label>{t('accounts.field.avatar')}</Label>
                <div className="flex items-center gap-3">
                  {form.avatar_cloud_file_id && avatarPreviewUrlByFileId?.[form.avatar_cloud_file_id] ? (
                    <button
                      type="button"
                      title={t('avatarCrop.editHint') as string}
                      onClick={() =>
                        setAvatarCropSource({ url: avatarPreviewUrlByFileId[form.avatar_cloud_file_id] })
                      }
                      className="shrink-0 rounded-md ring-1 ring-border transition hover:opacity-80"
                    >
                      <img
                        alt=""
                        src={avatarPreviewUrlByFileId[form.avatar_cloud_file_id]}
                        className="h-12 w-16 rounded-md object-cover"
                      />
                    </button>
                  ) : null}
                  <input
                    type="file"
                    accept="image/*"
                    className="text-sm"
                    onChange={(e) => {
                      const file = e.target.files?.[0]
                      e.currentTarget.value = ''
                      if (!file) return
                      setAvatarCropSource(file)
                    }}
                  />
                  {form.avatar_cloud_file_id ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        onFormChange({ ...form, avatar_cloud_file_id: '', avatar_cloud_sha256: '' })
                      }
                    >
                      {t('common.remove')}
                    </Button>
                  ) : null}
                </div>
              </div>
            ) : null}

            {/* 备注 — 所有类型可填。 */}
            <div className="space-y-1">
              <Label>{t('accounts.field.note')}</Label>
              <textarea
                className="flex min-h-[60px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                placeholder={t('accounts.field.noteHint')}
                rows={3}
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>

            {/* 账户隐藏(issue #240):只在编辑已有账户时提供切换 —— 新建账户
                隐藏没有产品意义(对齐 mobile:入口在账户编辑页)。切换保存后经
                写端点反向生成同步变更,App 端正常 pull 收敛。 */}
            {form.editingId ? (
              <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
                <div className="min-w-0 pr-3">
                  <p className="text-sm font-medium">{t('accounts.hidden.toggleLabel')}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {t('accounts.hidden.toggleHint')}
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={form.hidden}
                  aria-label={t('accounts.hidden.toggleLabel') as string}
                  onClick={() => onFormChange({ ...form, hidden: !form.hidden })}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                    form.hidden ? 'bg-primary' : 'bg-muted-foreground/30'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                      form.hidden ? 'translate-x-[18px]' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>
            ) : null}

            {/* 納入總餘額(Phase 18):對齊 Moze,跟 hidden 是兩個獨立維度
                (封存/隱藏管「要不要出現在列表」,這個開關管「要不要計入總
                數」)。新建/編輯都可設,預設開啟。 */}
            <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <div className="min-w-0 pr-3">
                <p className="text-sm font-medium">{t('accounts.includeInTotal.toggleLabel')}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t('accounts.includeInTotal.toggleHint')}
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={form.include_in_total}
                aria-label={t('accounts.includeInTotal.toggleLabel') as string}
                onClick={() => onFormChange({ ...form, include_in_total: !form.include_in_total })}
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                  form.include_in_total ? 'bg-primary' : 'bg-muted-foreground/30'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    form.include_in_total ? 'translate-x-[18px]' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                onReset()
                setOpen(false)
              }}
            >
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={!canManage}
              onClick={async () => {
                const success = await onSave()
                if (success) {
                  setOpen(false)
                }
              }}
            >
              {form.editingId ? t('accounts.button.update') : t('accounts.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {onUploadAvatar ? (
        <AvatarCropDialog
          source={avatarCropSource}
          aspect={4 / 3}
          cropShape="rect"
          onCancel={() => setAvatarCropSource(null)}
          onConfirm={async (croppedFile) => {
            const res = await onUploadAvatar(croppedFile)
            setAvatarCropSource(null)
            if (res) {
              onFormChange({
                ...form,
                avatar_cloud_file_id: res.fileId,
                avatar_cloud_sha256: res.sha256
              })
            }
          }}
        />
      ) : null}
    </>
  )
}
