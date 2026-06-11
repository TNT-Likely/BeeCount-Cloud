import { useMemo, useState } from 'react'

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

import type { ReadAccount } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { CurrencySelectorTrigger } from '../components/CurrencySelector'
import type { AccountForm } from '../forms'
import { accountDefaults } from '../forms'
import {
  accountBalance,
  type AssetGroup,
  type AssetSummary,
  computeCurrencySummary,
  LIABILITY_TYPES,
  splitByCurrency
} from '../lib/assetAggregation'


type MobileStyleAssetsProps = {
  /** 按币种切分后的汇总(每币种各自 summary + 构成饼图)。单币种时只有 1 条。 */
  byCurrency: CurrencyBucket[]
  /** 底部分组列表:跨币种按类型分组,每组小计按币种拆。 */
  listGroups: AssetGroup[]
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
  canManage,
  onEdit,
  onDelete,
  onClickAccount,
  onCreate,
  hideCurrencyCards = false
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

  return (
    <div className="space-y-4">
      {/* 第一行：单币种 → 汇总 hero + 构成饼图左右分列(维持原样);
          多币种 → 每币种一张卡(各自净值 + 构成饼图),金额绝不跨币种合并。
          hideCurrencyCards=true(折算汇总视图)时跳过多币种卡网格 —— 由上层
          折算卡接管多币种展示,这里只剩账户列表。 */}
      {multiCurrency ? (
        hideCurrencyCards ? null : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {byCurrency.map((entry) => (
              <CurrencyAssetCard key={entry.currency} entry={entry} />
            ))}
          </div>
        )
      ) : single ? (
        <div className="grid gap-3 lg:grid-cols-[1.1fr_1fr]">
          <AssetsSummaryHero summary={single.summary} currency={single.currency} />
          <AssetsCompositionMini
            groups={single.groups}
            totalAbs={single.summary.assetTotal + Math.abs(single.summary.liabilityTotal)}
            currency={single.currency}
          />
        </div>
      ) : null}

      {onCreate ? (
        <div className="flex items-center justify-end">
          <Button size="sm" disabled={!canManage} onClick={onCreate}>
            {t('accounts.button.create')}
          </Button>
        </div>
      ) : null}

      {/* 下面是分组 + 真实卡片风格的子项列表 */}
      <div className="space-y-4">
        {listGroups.map((group) => {
          const isCollapsed = collapsed.has(group.type)
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
                        {group.rows.length}
                      </span>
                      {group.isLiability ? (
                        <span className="rounded-md border border-destructive/40 bg-destructive/10 px-1.5 py-0.5 text-[10px] leading-none text-destructive">
                          {t('accounts.badge.liability')}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {group.isLiability ? t('accounts.totalOwed') : t('accounts.totalBalance')}
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
                        tone={group.isLiability ? 'negative' : 'default'}
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
                <div className="grid gap-2 p-3 pt-0 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
                  {group.rows.map((row) => (
                    <BankCardTile
                      key={row.id}
                      row={row}
                      color={group.color}
                      isLiability={group.isLiability}
                      canManage={canManage}
                      onEdit={() => onEdit(row)}
                      onDelete={onDelete ? () => onDelete(row) : undefined}
                      onClick={onClickAccount ? () => onClickAccount(row) : undefined}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          )
        })}
      </div>
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
              tone="negative"
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
  totalAbs,
  currency,
  showCurrency = false,
  embedded = false,
  title,
  approx = false
}: {
  groups: AssetGroup[]
  totalAbs: number
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
  // 传进来的 groups 一定是单币种(单币种页 or 某一币种卡),subtotals 求和即该币种值。
  // 小计带符号(负债欠款为负),饼图分段要的是体量 —— 对组合计取 abs。
  const data = groups.map((g) => ({
    type: g.type,
    label: g.label,
    color: g.color,
    value: Math.abs(g.subtotals.reduce((s, x) => s + x.value, 0))
  }))
  const total = totalAbs > 0 ? totalAbs : 1
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
                  value={totalAbs}
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
              const pct = totalAbs > 0 ? (d.value / totalAbs) * 100 : 0
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

/**
 * 单个账户的"银行卡"风格卡片：渐变底 + 装饰花纹。布局对齐 mobile
 * `_AccountCard`：
 *  - 顶部：类型图标 + 账户名 + 币种 pill + 操作入口。
 *  - 正文：按账户类型分支
 *      - 估值账户（real_estate/vehicle/investment/…/loan）：单行大号"当前估值 / 当前欠款"。
 *      - 其它可交易账户：余额 / 收入 / 支出 三列。
 *    没有 stats（老接口 / 空账户）时回退到只展示初始余额。
 */
const VALUATION_TYPES_SET = new Set([
  'real_estate',
  'vehicle',
  'investment',
  'insurance',
  'social_fund',
  'loan'
])

type AccountStats = {
  balance?: number | null
  income_total?: number | null
  expense_total?: number | null
}

function BankCardTile({
  row,
  color,
  isLiability,
  canManage,
  onEdit,
  onDelete,
  onClick
}: {
  row: ReadAccount & AccountStats
  color: string
  isLiability: boolean
  canManage: boolean
  onEdit: () => void
  onDelete?: () => void
  onClick?: () => void
}) {
  const t = useT()
  const currency = row.currency || 'CNY'
  const accountType = row.account_type || 'other'
  const isValuation = VALUATION_TYPES_SET.has(accountType)
  const hasStats =
    row.balance !== null &&
    row.balance !== undefined &&
    typeof row.balance === 'number'
  // 展示余额：优先用 stats.balance（考虑所有交易后的结果），否则 initial_balance。
  const displayBalance = hasStats ? (row.balance as number) : row.initial_balance ?? 0
  // 估值账户：负债显示绝对值欠款，资产显示当前估值。
  const valuationValue = isLiability ? Math.abs(displayBalance) : displayBalance
  // 信用卡：按负债展示。已用 = max(0, -balance),可用 = 额度 - 已用(对齐 mobile）。
  const isCreditCard = accountType === 'credit_card'
  const ccLimit = typeof row.credit_limit === 'number' ? row.credit_limit : null
  const ccOwed = Math.max(0, -displayBalance)
  const ccAvailable = ccLimit !== null ? Math.max(0, ccLimit - ccOwed) : null

  return (
    <div
      className={`group relative overflow-hidden rounded-xl text-white shadow-md transition-all hover:-translate-y-0.5 hover:shadow-lg ${
        onClick ? 'cursor-pointer' : ''
      }`}
      style={{
        // 比 16:10 稍高一点，正文能放三列 stats 不挤。
        aspectRatio: '16 / 11',
        background: `linear-gradient(135deg, ${color} 0%, ${color}d9 40%, ${color}99 75%, ${color}66 100%)`,
        boxShadow: `0 4px 12px -4px ${color}66, 0 1px 2px rgba(0,0,0,0.06)`
      }}
      onClick={onClick}
    >
      {/* 装饰 1：右上大圆（mobile 同款） */}
      <div
        className="pointer-events-none absolute -right-8 -top-10 h-24 w-24 rounded-full bg-white/15"
        aria-hidden
      />
      {/* 装饰 2：左下小圆，对角呼应 */}
      <div
        className="pointer-events-none absolute -left-6 -bottom-10 h-20 w-20 rounded-full bg-white/10"
        aria-hidden
      />
      {/* 装饰 3：radial highlight */}
      <div
        className="pointer-events-none absolute inset-0 opacity-60"
        style={{
          background:
            'radial-gradient(circle at 30% 20%, rgba(255,255,255,0.22) 0%, transparent 55%)'
        }}
        aria-hidden
      />
      {/* 装饰 4：斜向细纹（激光蚀刻花纹） */}
      <svg
        className="pointer-events-none absolute inset-0 h-full w-full opacity-[0.12] mix-blend-overlay"
        viewBox="0 0 160 110"
        preserveAspectRatio="none"
        aria-hidden
      >
        <defs>
          <pattern
            id={`card-grid-${row.id}`}
            width="12"
            height="12"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(25)"
          >
            <path d="M0 0 L12 0" stroke="#fff" strokeWidth="0.5" opacity="0.6" />
          </pattern>
        </defs>
        <rect width="160" height="110" fill={`url(#card-grid-${row.id})`} />
      </svg>
      {/* 装饰 5：斜向磨砂反光条 */}
      <div
        className="pointer-events-none absolute -left-1/4 top-0 h-full w-1/2 opacity-30"
        style={{
          background:
            'linear-gradient(100deg, transparent 0%, rgba(255,255,255,0.25) 50%, transparent 100%)'
        }}
        aria-hidden
      />

      <div className="relative flex h-full flex-col p-2.5">
        {/* 顶部：类型图标 + 账户名 + 币种 pill */}
        <div className="flex items-center gap-1.5">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-white/95 shadow-sm ring-1 ring-white/50">
            <TypeIcon type={accountType} size={16} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-[12px] font-semibold leading-tight drop-shadow-sm">
              {row.name}
            </div>
          </div>
          <span className="shrink-0 rounded bg-white/25 px-1 py-[1px] text-[9px] font-semibold tracking-wider">
            {currency}
          </span>
        </div>

        {/* 正文：按类型切换布局 */}
        {isValuation ? (
          <div className="mt-auto">
            <div className="text-[9px] uppercase tracking-[0.15em] text-white/75">
              {isLiability ? t('accounts.bankcard.currentOwed') : t('accounts.bankcard.currentValue')}
            </div>
            <Amount
              value={valuationValue}
              currency={currency}
              showCurrency
              bold
              className="mt-0.5 block text-[18px] leading-tight drop-shadow text-white"
            />
          </div>
        ) : isCreditCard ? (
          ccLimit !== null ? (
            <div className="mt-auto grid grid-cols-3 gap-1 rounded-md bg-black/15 px-2 py-1.5 backdrop-blur-[1px]">
              <StatCell
                label={t('accounts.field.creditLimit')}
                value={ccLimit}
                currency={currency}
              />
              <StatCell
                label={t('accounts.bankcard.creditUsed')}
                value={ccOwed}
                currency={currency}
              />
              <StatCell
                label={t('accounts.bankcard.creditAvailable')}
                value={ccAvailable as number}
                currency={currency}
              />
            </div>
          ) : (
            <div className="mt-auto">
              <div className="text-[9px] uppercase tracking-[0.15em] text-white/75">
                {t('accounts.bankcard.currentOwed')}
              </div>
              <Amount
                value={ccOwed}
                currency={currency}
                showCurrency
                bold
                className="mt-0.5 block text-[18px] leading-tight drop-shadow text-white"
              />
            </div>
          )
        ) : hasStats ? (
          <div className="mt-auto grid grid-cols-3 gap-1 rounded-md bg-black/15 px-2 py-1.5 backdrop-blur-[1px]">
            <StatCell
              label={t('accounts.bankcard.balance')}
              value={displayBalance}
              currency={currency}
              tone={displayBalance < 0 ? 'warn' : 'default'}
            />
            <StatCell
              label={t('accounts.bankcard.income')}
              value={row.income_total ?? 0}
              currency={currency}
            />
            <StatCell
              label={t('accounts.bankcard.expense')}
              value={row.expense_total ?? 0}
              currency={currency}
            />
          </div>
        ) : (
          <div className="mt-auto">
            <div className="text-[9px] uppercase tracking-[0.15em] text-white/75">
              {isLiability ? t('accounts.bankcard.owedLabel') : t('accounts.bankcard.balanceLabel')}
            </div>
            <Amount
              value={displayBalance}
              currency={currency}
              showCurrency
              bold
              className="mt-0.5 block text-[16px] leading-tight drop-shadow text-white"
            />
          </div>
        )}
      </div>

      {/* hover 操作按钮浮层（右上角，避开正文 stats） */}
      <div className="absolute right-1.5 top-9 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          type="button"
          disabled={!canManage}
          onClick={(event) => {
            event.stopPropagation()
            onEdit()
          }}
          className="rounded bg-black/35 px-1.5 py-0.5 text-[10px] text-white backdrop-blur hover:bg-primary/80"
        >
          {t('common.edit')}
        </button>
        {onDelete ? (
          <button
            type="button"
            disabled={!canManage}
            onClick={(event) => {
              event.stopPropagation()
              onDelete()
            }}
            className="rounded bg-black/35 px-1.5 py-0.5 text-[10px] text-white backdrop-blur hover:bg-destructive/60"
          >
            {t('common.delete')}
          </button>
        ) : null}
      </div>
    </div>
  )
}

function StatCell({
  label,
  value,
  currency,
  tone = 'default'
}: {
  label: string
  value: number
  currency: string
  tone?: 'default' | 'warn'
}) {
  return (
    <div className="flex min-w-0 flex-col items-start gap-[1px]">
      <span className="text-[9px] uppercase tracking-wider text-white/70">{label}</span>
      <Amount
        value={value}
        currency={currency}
        showCurrency={false}
        bold
        size="xs"
        className={`leading-tight drop-shadow-sm ${
          tone === 'warn' ? 'text-amber-100' : 'text-white'
        }`}
      />
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

// 账户类型 → 品牌 SVG 图标路径。SVG 已从 BeeCount (mobile) `assets/icons/*.svg`
// 拷到 `web/public/icons/account/`，公共资源目录直接通过 URL 访问即可（不用
// 打包到 bundle）。`other` 回退到 `other_account.svg`，其它直接同名。
const TYPE_ICON_URL: Record<string, string> = {
  cash: '/icons/account/cash.svg',
  bank_card: '/icons/account/bank_card.svg',
  credit_card: '/icons/account/credit_card.svg',
  alipay: '/icons/account/alipay.svg',
  wechat: '/icons/account/wechat.svg',
  other: '/icons/account/other_account.svg',
  real_estate: '/icons/account/real_estate.svg',
  vehicle: '/icons/account/vehicle.svg',
  investment: '/icons/account/investment.svg',
  insurance: '/icons/account/insurance.svg',
  social_fund: '/icons/account/social_fund.svg',
  loan: '/icons/account/loan.svg'
}

function TypeIcon({ type, size = 28 }: { type: string; size?: number }) {
  const src = TYPE_ICON_URL[type] || TYPE_ICON_URL.other
  return (
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      className="block select-none"
      draggable={false}
    />
  )
}

// 每种账户类型对应的品牌色，用于卡片边框/渐变。与 AssetCompositionDonut
// 的配色保持一致，这样 overview 的饼图和这里的分组颜色呼应。
const TYPE_COLORS: Record<string, string> = {
  cash: '#10b981',
  bank_card: '#3b82f6',
  credit_card: '#ef4444',
  alipay: '#06b6d4',
  wechat: '#22c55e',
  other: '#64748b',
  real_estate: '#8b5cf6',
  vehicle: '#f59e0b',
  investment: '#ec4899',
  insurance: '#14b8a6',
  social_fund: '#84cc16',
  loan: '#dc2626'
}

/** 账户类型 label i18n 查找:先看 accountType.<value> key,回退到原始 value。
 *  参数 tt 是 useT() 返回的查找函数。 */
function accountTypeLabel(tt: (k: string) => string, value?: string | null): string {
  if (!value) return '-'
  const key = `accountType.${value}`
  const translated = tt(key)
  // useT 没命中的 key 会把 key 原样返回,说明当前 locale 没定义
  if (translated === key) return value
  return translated
}

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
export function computeTypeGroups(rows: ReadAccount[], t: (k: string) => string): AssetGroup[] {
  const buckets: Record<string, ReadAccount[]> = {}
  for (const row of rows) {
    const key = row.account_type || 'other'
    buckets[key] = buckets[key] || []
    buckets[key].push(row)
  }
  return ACCOUNT_ORDER.filter((type) => (buckets[type] || []).length > 0).map((type) => {
    const groupRows = (buckets[type] || []).slice().sort((a, b) => a.name.localeCompare(b.name))
    const isLiability = LIABILITY_TYPES.has(type)
    // 小计带符号累加(与 computeCurrencySummary 同口径)——溢缴的卡会抵销欠款。
    // 展示"共欠"时由渲染处对组合计取 abs,绝不逐账户 abs(否则 +10w 卡 + −20w 贷
    // 会显示成欠 30w)。
    const byCur = new Map<string, number>()
    for (const r of groupRows) {
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
  const totalAbs = summary.assetTotal + Math.abs(summary.liabilityTotal)
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
            tone="negative"
            className="mt-0.5 block"
          />
        </div>
      </div>
      <AssetsCompositionMini
        groups={groups}
        totalAbs={totalAbs}
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
  hideCurrencyCards = false
}: AccountsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)

  // 按币种切分后再聚合 —— 资产统计绝不跨币种相加(见 computeCurrencySummary)。
  // 单币种(绝大多数场景)→ currencyBuckets 只有 1 条,顶部展示完全维持原样。
  const currencyBuckets = useMemo<CurrencyBucket[]>(() => {
    return [...splitByCurrency(rows).entries()]
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
  }, [rows, t])

  // 底部列表:跨币种按类型分组(每组小计按币种拆,见 computeTypeGroups)。
  const listGroups = useMemo(() => computeTypeGroups(rows, t), [rows, t])

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
          canManage={canManage}
          onEdit={(row) => {
            onEdit(row)
            setOpen(true)
          }}
          onDelete={onDelete}
          onClickAccount={onClickAccount}
          onCreate={handleOpenCreate}
          hideCurrencyCards={hideCurrencyCards}
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
                    // 离开 credit_card → 清空信用卡专属字段
                    const next: AccountForm = { ...form, account_type: value }
                    if (form.account_type === 'credit_card' && value !== 'credit_card') {
                      next.credit_limit = ''
                      next.billing_day = ''
                      next.payment_due_day = ''
                    }
                    // 离开 bank_card / credit_card → 清空银行卡元信息
                    const wasBankOrCredit = form.account_type === 'bank_card' || form.account_type === 'credit_card'
                    const isBankOrCredit = value === 'bank_card' || value === 'credit_card'
                    if (wasBankOrCredit && !isBankOrCredit) {
                      next.bank_name = ''
                      next.card_last_four = ''
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
                  value={form.currency || 'CNY'}
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

            {/* 信用卡专属:信用额度 + 账单日 + 还款日(对齐 mobile credit_card
                section)。还款提醒是 mobile 本地 SharedPreferences 不走 server,
                web 暂不支持。 */}
            {form.account_type === 'credit_card' ? (
              <div className="rounded-md border border-border/50 bg-muted/20 p-3 space-y-3">
                <div className="text-xs font-semibold text-muted-foreground">
                  {t('accounts.section.creditCard')}
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
              </div>
            ) : null}

            {/* 银行卡 / 信用卡 元信息:开户行 + 卡号后四位。 */}
            {form.account_type === 'bank_card' || form.account_type === 'credit_card' ? (
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
    </>
  )
}
