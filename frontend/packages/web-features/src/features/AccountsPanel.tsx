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
import type { AccountForm } from '../forms'

type AssetGroup = {
  type: string
  label: string
  color: string
  isLiability: boolean
  rows: ReadAccount[]
  subtotal: number
}

type AssetSummary = {
  assetTotal: number
  liabilityTotal: number
  netWorth: number
}

type MobileStyleAssetsProps = {
  groups: AssetGroup[]
  summary: AssetSummary
  canManage: boolean
  onEdit: (row: ReadAccount) => void
  onDelete?: (row: ReadAccount) => void
  /** 点卡片（非编辑/删除按钮）：外层用来打开"账户详情+交易列表"弹窗。 */
  onClickAccount?: (row: ReadAccount) => void
}

/**
 * 对齐 mobile accounts_page.dart 的展示：顶部是净值 hero（资产/负债/净值）+
 * 下面分类型折叠分组。每个分组是一个带左色带的 section，里面 row 是横向
 * 卡片：左侧 emoji 类型图标 + 账户名，右侧金额。跟 mobile 上的 ListTile 风格
 * 一致，和标签页的小卡片网格做出明显区分。
 */
function MobileStyleAssets({
  groups,
  summary,
  canManage,
  onEdit,
  onDelete,
  onClickAccount
}: MobileStyleAssetsProps) {
  const t = useT()
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
      {/* 第一行：汇总 hero + 构成饼图，左右分列 */}
      <div className="grid gap-3 lg:grid-cols-[1.1fr_1fr]">
        <AssetsSummaryHero summary={summary} />
        <AssetsCompositionMini
          groups={groups}
          totalAbs={summary.assetTotal + summary.liabilityTotal}
        />
      </div>

      {/* 下面是分组 + 真实卡片风格的子项列表 */}
      <div className="space-y-4">
        {groups.map((group) => {
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
                  <Amount
                    value={group.subtotal}
                    size="xl"
                    bold
                    tone={group.isLiability ? 'negative' : 'default'}
                  />
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
function AssetsSummaryHero({ summary }: { summary: AssetSummary }) {
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
              value={summary.liabilityTotal}
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
function AssetsCompositionMini({
  groups,
  totalAbs
}: {
  groups: AssetGroup[]
  totalAbs: number
}) {
  const t = useT()
  const data = groups.map((g) => ({
    type: g.type,
    label: g.label,
    color: g.color,
    value: g.subtotal
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
    <div className="overflow-hidden rounded-2xl border border-border/50 bg-card/80 p-5">
      <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.22em] text-muted-foreground">
        {t('accounts.composition')}
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
              <Amount value={totalAbs} size="md" bold className="mt-0.5" />
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
const LIABILITY_TYPES = new Set(['credit_card', 'loan'])

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
  onClickAccount
}: AccountsPanelProps) {
  const t = useT()
  const [open, setOpen] = useState(false)

  const summary = useMemo(() => {
    let assetTotal = 0
    let liabilityTotal = 0
    for (const row of rows) {
      // 优先用 server 聚合后的 balance（含所有交易）；老接口 / 无 tx 则回退到
      // initialBalance。负债类 balance 通常是负数，用绝对值统计总欠款。
      const stats = row as ReadAccount & AccountStats
      const raw =
        typeof stats.balance === 'number' && stats.balance !== null
          ? stats.balance
          : row.initial_balance ?? 0
      const bal = Math.abs(raw)
      if (LIABILITY_TYPES.has(row.account_type || '')) liabilityTotal += bal
      else assetTotal += bal
    }
    return { assetTotal, liabilityTotal, netWorth: assetTotal - liabilityTotal }
  }, [rows])

  // 按类型分组 + 排序（日常类型在前，估值在后，跟 mobile 的 group 顺序一致）
  const grouped = useMemo(() => {
    const order: string[] = [
      ...TRADABLE_TYPES.map((x) => x.value),
      ...VALUATION_TYPES.map((x) => x.value)
    ]
    const buckets: Record<string, ReadAccount[]> = {}
    for (const row of rows) {
      const key = row.account_type || 'other'
      buckets[key] = buckets[key] || []
      buckets[key].push(row)
    }
    return order
      .filter((type) => (buckets[type] || []).length > 0)
      .map((type) => ({
        type,
        label: accountTypeLabel(t, type),
        color: TYPE_COLORS[type] || '#94a3b8',
        isLiability: LIABILITY_TYPES.has(type),
        rows: (buckets[type] || []).sort((a, b) => a.name.localeCompare(b.name)),
        subtotal: (buckets[type] || []).reduce((s, r) => {
          const stats = r as ReadAccount & AccountStats
          const raw =
            typeof stats.balance === 'number' && stats.balance !== null
              ? stats.balance
              : r.initial_balance ?? 0
          return s + Math.abs(raw)
        }, 0)
      }))
  }, [rows, t])

  return (
    <>
      {/* 卡片式布局不再套 ListTableShell 的灰色 header；hero 已经自带标题级
          视觉锚，再加一个"资产管理"横条显得冗余。 */}
      {rows.length === 0 ? (
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
      ) : (
        <MobileStyleAssets
          groups={grouped}
          summary={summary}
          canManage={canManage}
          onEdit={(row) => {
            onEdit(row)
            setOpen(true)
          }}
          onDelete={onDelete}
          onClickAccount={onClickAccount}
        />
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
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
                <Select value={form.account_type || 'cash'} onValueChange={(value) => onFormChange({ ...form, account_type: value })}>
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
                <Select value={form.currency || 'CNY'} onValueChange={(value) => onFormChange({ ...form, currency: value })}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('accounts.placeholder.currency')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="CNY">CNY</SelectItem>
                    <SelectItem value="USD">USD</SelectItem>
                    <SelectItem value="HKD">HKD</SelectItem>
                    <SelectItem value="EUR">EUR</SelectItem>
                    <SelectItem value="JPY">JPY</SelectItem>
                  </SelectContent>
                </Select>
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
