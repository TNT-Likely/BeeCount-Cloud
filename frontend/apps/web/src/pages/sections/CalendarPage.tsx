import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  fetchWorkspaceAnalytics,
  fetchWorkspaceTransactions,
  type WorkspaceAnalytics,
  type WorkspaceAnalyticsSeriesItem,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import { Button, Card, CardContent, useT } from '@beecount/ui'
import { ChevronLeft, ChevronRight, Info, Plus, Sparkles } from 'lucide-react'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { dispatchOpenDetailTx, dispatchOpenNewTx } from '../../lib/txDialogEvents'

// ============================================================================
// 日历视图 — 见 .docs/web-calendar-view-design.md
// ============================================================================
// 关键决策:
// - 路径独立 /app/calendar(不挤进 TransactionsPage 1966 行 god component)
// - 数据复用 server `scope=month` 的日级 series,无需后端改动
// - 单元格颜色:净值色块,深度 ∝ |当日净值| / 月最大净值,跟随用户红/绿偏好
// - 跨月填充行(prev/next month spillover)— dim 30% 透明
// - URL state ?m=YYYY-MM&d=YYYY-MM-DD,刷新 / 分享链接行为自然

type DayBucket = {
  /** YYYY-MM-DD */
  dateKey: string
  income: number
  expense: number
  net: number
  txCount: number
}

const ZH_WEEK = ['日', '一', '二', '三', '四', '五', '六']
const EN_WEEK = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function todayKey(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function thisMonthKey(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function parseMonth(monthKey: string): { year: number; month: number } {
  const [y, m] = monthKey.split('-').map(Number)
  return { year: y, month: m }
}

function shiftMonth(monthKey: string, delta: number): string {
  const { year, month } = parseMonth(monthKey)
  const total = year * 12 + (month - 1) + delta
  const nextYear = Math.floor(total / 12)
  const nextMonth = (total % 12) + 1
  return `${nextYear}-${String(nextMonth).padStart(2, '0')}`
}

/**
 * 给定 YYYY-MM 和 weekStartsOn(0=Sun, 1=Mon),返回 6 行 7 列共 42 格的 dateKey 数组。
 * 跨月日填充:第一周前面用上月末几天,最后一周后面用下月头几天。
 */
function buildMonthGrid(
  monthKey: string,
  weekStartsOn: 0 | 1,
): { dateKeys: string[]; firstOfMonth: string; lastOfMonth: string } {
  const { year, month } = parseMonth(monthKey)
  const firstDay = new Date(year, month - 1, 1)
  const lastDay = new Date(year, month, 0)
  const totalDays = lastDay.getDate()

  // first day's weekday: 0=Sun..6=Sat. 我们要把它对齐到 weekStartsOn 列。
  const firstWeekday = firstDay.getDay()
  const leadingCount = (firstWeekday - weekStartsOn + 7) % 7

  const dateKeys: string[] = []

  // 前置月填充
  for (let i = leadingCount; i > 0; i--) {
    const d = new Date(year, month - 1, 1 - i)
    dateKeys.push(formatDateKey(d))
  }
  // 当月
  for (let i = 1; i <= totalDays; i++) {
    dateKeys.push(`${year}-${String(month).padStart(2, '0')}-${String(i).padStart(2, '0')}`)
  }
  // 后置月填充至 6 行 (42 格,避免月历高度跳变)
  while (dateKeys.length < 42) {
    const last = dateKeys[dateKeys.length - 1]
    const [y, m, d] = last.split('-').map(Number)
    const next = new Date(y, m - 1, d + 1)
    dateKeys.push(formatDateKey(next))
  }

  return {
    dateKeys,
    firstOfMonth: `${year}-${String(month).padStart(2, '0')}-01`,
    lastOfMonth: `${year}-${String(month).padStart(2, '0')}-${String(totalDays).padStart(2, '0')}`,
  }
}

function formatDateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function isSameMonth(dateKey: string, monthKey: string): boolean {
  return dateKey.startsWith(monthKey)
}

/**
 * 把 server 返回的 series(bucket=YYYY-MM-DD)折叠成 Map<dateKey, DayBucket>。
 * server 返回 income / expense 但没有 tx_count;我们用 max(income,expense)>0 标记
 * "有交易",txCount 留 undefined,真要数得拉 tx 列表(选中日时再拉)。
 */
function aggregateByDay(series: WorkspaceAnalyticsSeriesItem[]): Map<string, DayBucket> {
  const map = new Map<string, DayBucket>()
  for (const item of series) {
    const income = item.income || 0
    const expense = item.expense || 0
    if (income === 0 && expense === 0) continue
    map.set(item.bucket, {
      dateKey: item.bucket,
      income,
      expense,
      net: income - expense,
      txCount: 0,  // 不知道,UI 显示时降级为不显示
    })
  }
  return map
}

export function CalendarPage() {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId, currency } = useLedgers()
  const [searchParams, setSearchParams] = useSearchParams()

  // ====== 状态:focusedMonth / selectedDate(URL state 同步)======
  const monthFromUrl = searchParams.get('m')
  const dateFromUrl = searchParams.get('d')
  const [focusedMonth, setFocusedMonth] = useState<string>(
    monthFromUrl && /^\d{4}-\d{2}$/.test(monthFromUrl) ? monthFromUrl : thisMonthKey(),
  )
  const [selectedDate, setSelectedDate] = useState<string>(
    dateFromUrl && /^\d{4}-\d{2}-\d{2}$/.test(dateFromUrl) ? dateFromUrl : todayKey(),
  )

  // 周起始:locale 推断,中文一,其他日。可以未来通过设置切换。
  const weekStartsOn: 0 | 1 = useMemo(() => {
    const lang = (typeof navigator !== 'undefined' ? navigator.language : 'zh').toLowerCase()
    return lang.startsWith('zh') ? 1 : 0
  }, [])
  const weekHeaders = weekStartsOn === 1
    ? [...ZH_WEEK.slice(1), ZH_WEEK[0]]
    : weekStartsOn === 0 && (typeof navigator !== 'undefined' ? navigator.language : 'zh').toLowerCase().startsWith('zh')
      ? ZH_WEEK
      : EN_WEEK

  // ====== URL 同步:focusedMonth / selectedDate 改变写回 URL ======
  useEffect(() => {
    const next = new URLSearchParams(searchParams)
    if (next.get('m') !== focusedMonth) next.set('m', focusedMonth)
    if (selectedDate && next.get('d') !== selectedDate) next.set('d', selectedDate)
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedMonth, selectedDate])

  // ====== 数据:当月 analytics(日级 series)======
  const cacheBucket = `calendar:${activeLedgerId || '__none__'}:${focusedMonth}`
  const [monthData, setMonthData] = usePageCache<WorkspaceAnalytics | null>(cacheBucket, null)
  const [loadingMonth, setLoadingMonth] = useState(false)

  const loadMonth = useCallback(async () => {
    if (!activeLedgerId) return
    setLoadingMonth(true)
    try {
      const tzOffsetMinutes = -new Date().getTimezoneOffset()
      const data = await fetchWorkspaceAnalytics(token, {
        scope: 'month',
        period: focusedMonth,
        ledgerId: activeLedgerId,
        tzOffsetMinutes,
      })
      setMonthData(data)
    } catch {
      // 静默,UI 显示空月历不阻塞
    } finally {
      setLoadingMonth(false)
    }
  }, [token, activeLedgerId, focusedMonth, setMonthData])

  useEffect(() => {
    void loadMonth()
  }, [loadMonth])

  // 同步 ws 推送时刷新当月
  useSyncRefresh(() => {
    void loadMonth()
  })

  // ====== 数据:选中日 tx 列表 ======
  const [dayTxs, setDayTxs] = useState<WorkspaceTransaction[]>([])
  const [loadingDay, setLoadingDay] = useState(false)

  const loadDay = useCallback(async () => {
    if (!activeLedgerId || !selectedDate) {
      setDayTxs([])
      return
    }
    setLoadingDay(true)
    try {
      // dateFrom = 选中日 00:00 本地;dateTo = 次日 00:00(独占)
      const [y, m, d] = selectedDate.split('-').map(Number)
      const start = new Date(y, m - 1, d, 0, 0, 0).toISOString()
      const end = new Date(y, m - 1, d + 1, 0, 0, 0).toISOString()
      const data = await fetchWorkspaceTransactions(token, {
        ledgerId: activeLedgerId,
        dateFrom: start,
        dateTo: end,
        limit: 200,
      })
      setDayTxs(data.items || [])
    } catch {
      setDayTxs([])
    } finally {
      setLoadingDay(false)
    }
  }, [token, activeLedgerId, selectedDate])

  useEffect(() => {
    void loadDay()
  }, [loadDay])

  useSyncRefresh(() => {
    void loadDay()
  })

  // ====== 派生:每日 bucket map + 月最大净值绝对值(色块强度归一化)======
  const dayBuckets = useMemo(
    () => aggregateByDay(monthData?.series || []),
    [monthData],
  )
  const monthMaxAbsNet = useMemo(() => {
    let max = 0
    for (const bucket of dayBuckets.values()) {
      const abs = Math.abs(bucket.net)
      if (abs > max) max = abs
    }
    return max
  }, [dayBuckets])

  // ====== 月历格子 ======
  const grid = useMemo(
    () => buildMonthGrid(focusedMonth, weekStartsOn),
    [focusedMonth, weekStartsOn],
  )

  // ====== 选中日汇总(从 dayBuckets 取或从 dayTxs 算)======
  const selectedDayBucket = selectedDate ? dayBuckets.get(selectedDate) : undefined

  // ====== 切月 / 跳今天 ======
  const goPrevMonth = useCallback(() => {
    setFocusedMonth((cur) => shiftMonth(cur, -1))
  }, [])
  const goNextMonth = useCallback(() => {
    setFocusedMonth((cur) => shiftMonth(cur, 1))
  }, [])
  const goToday = useCallback(() => {
    const today = todayKey()
    setFocusedMonth(thisMonthKey())
    setSelectedDate(today)
  }, [])

  // ====== 加 tx 入口 ======
  // GlobalEditDialogs 在 AppShell 顶层全局监听,日历页直接派发新建事件即可,
  // 不离开当前页。happened_at 预填为选中日 12:00 本地时间(比 00:00 更友好,
  // 不会落在"夜里 12 点"歧义点)。
  const handleAddTxOnSelectedDay = useCallback(() => {
    if (!selectedDate) return
    const [y, m, d] = selectedDate.split('-').map(Number)
    const happenedAt = new Date(y, m - 1, d, 12, 0, 0).toISOString()
    dispatchOpenNewTx({ happenedAt, ledgerId: activeLedgerId || undefined })
  }, [selectedDate, activeLedgerId])

  // ====== 键盘导航 ======
  // ↑↓←→ 天/周移动,跨月自动切 focusedMonth;N 新增,T 今天,⌘[ ⌘] 翻月。
  // 焦点在 input/textarea 时不触发(用户可能在某个对话框内输入数字)。
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target) {
        const tag = target.tagName
        if (
          tag === 'INPUT' ||
          tag === 'TEXTAREA' ||
          tag === 'SELECT' ||
          target.isContentEditable
        ) {
          return
        }
      }

      // ⌘[ / ⌘] = 翻月
      if ((e.metaKey || e.ctrlKey) && e.key === '[') {
        e.preventDefault()
        goPrevMonth()
        return
      }
      if ((e.metaKey || e.ctrlKey) && e.key === ']') {
        e.preventDefault()
        goNextMonth()
        return
      }

      // 其它修饰键 = 不处理(避免跟系统快捷键冲突)
      if (e.metaKey || e.ctrlKey || e.altKey) return

      switch (e.key) {
        case 'ArrowLeft':
          e.preventDefault()
          shiftSelectedDate(-1)
          break
        case 'ArrowRight':
          e.preventDefault()
          shiftSelectedDate(1)
          break
        case 'ArrowUp':
          e.preventDefault()
          shiftSelectedDate(-7)
          break
        case 'ArrowDown':
          e.preventDefault()
          shiftSelectedDate(7)
          break
        case 't':
        case 'T':
          e.preventDefault()
          goToday()
          break
        case 'n':
        case 'N':
          e.preventDefault()
          handleAddTxOnSelectedDay()
          break
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDate, focusedMonth])

  function shiftSelectedDate(deltaDays: number) {
    if (!selectedDate) {
      setSelectedDate(todayKey())
      return
    }
    const [y, m, d] = selectedDate.split('-').map(Number)
    const next = new Date(y, m - 1, d + deltaDays)
    const nextKey = formatDateKey(next)
    setSelectedDate(nextKey)
    if (!isSameMonth(nextKey, focusedMonth)) {
      setFocusedMonth(nextKey.slice(0, 7))
    }
  }

  // ====== render ======
  const monthLabel = useMemo(() => {
    const { year, month } = parseMonth(focusedMonth)
    return t('calendar.monthLabel', { year: String(year), month: String(month) })
  }, [focusedMonth, t])

  // 月度空态判断:加载完毕但当月零交易 → 引导用户记一笔
  const monthIsEmpty =
    !loadingMonth &&
    monthData !== null &&
    dayBuckets.size === 0

  return (
    <div className="space-y-4">
      {/* 顶部 toolbar — prev/today/next 合并成 pill 组,月份字号加大 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-bold tracking-tight">{monthLabel}</h1>
          {loadingMonth && (
            <span className="inline-flex h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
          )}
        </div>
        <div className="inline-flex items-center overflow-hidden rounded-lg border border-border/60 bg-card shadow-sm">
          <button
            type="button"
            onClick={goPrevMonth}
            aria-label={t('calendar.prevMonth')}
            className="flex h-8 w-8 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={goToday}
            className="h-8 border-x border-border/40 px-3 text-xs font-medium text-foreground transition-colors hover:bg-muted"
          >
            {t('calendar.today')}
          </button>
          <button
            type="button"
            onClick={goNextMonth}
            aria-label={t('calendar.nextMonth')}
            className="flex h-8 w-8 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* 引导 hint band:色块图例 + 操作提示。
       *  色点用固定 block 元素 + inline style 给颜色,保证 rgb var 解析后能看到。
       *  文案改成「收入色 / 支出色」 — 之前用「净收入 / 净支出」是会计术语,用户难以秒懂。 */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-lg border border-border/40 bg-muted/30 px-4 py-2.5 text-[11px] text-muted-foreground">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="inline-flex items-center gap-1.5">
            <Info className="h-3.5 w-3.5 text-muted-foreground/60" aria-hidden />
            {t('calendar.legend.title')}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="block h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: 'rgb(var(--income-rgb))' }}
              aria-hidden
            />
            {t('calendar.legend.income')}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="block h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: 'rgb(var(--expense-rgb))' }}
              aria-hidden
            />
            {t('calendar.legend.expense')}
          </span>
        </div>
        <div className="hidden items-center gap-1 md:inline-flex">
          {t('calendar.legend.shortcuts')}
          <Kbd>↑↓←→</Kbd>
          <Kbd>N</Kbd>
          <Kbd>T</Kbd>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        {/* 月历主体 */}
        <Card className="bc-panel overflow-hidden">
          <CardContent className="relative p-4">
            {/* 周名行 */}
            <div className="mb-3 grid grid-cols-7 gap-1.5">
              {weekHeaders.map((label, idx) => (
                <div
                  key={`${label}-${idx}`}
                  className="text-center text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/80"
                >
                  {label}
                </div>
              ))}
            </div>
            {/* 日格 */}
            <div className="grid grid-cols-7 gap-1.5">
              {grid.dateKeys.map((dateKey) => (
                <DayCell
                  key={dateKey}
                  dateKey={dateKey}
                  bucket={dayBuckets.get(dateKey)}
                  inFocusedMonth={isSameMonth(dateKey, focusedMonth)}
                  isToday={dateKey === todayKey()}
                  isSelected={dateKey === selectedDate}
                  monthMaxAbsNet={monthMaxAbsNet}
                  currency={currency}
                  onSelect={() => {
                    setSelectedDate(dateKey)
                    if (!isSameMonth(dateKey, focusedMonth)) {
                      setFocusedMonth(dateKey.slice(0, 7))
                    }
                  }}
                />
              ))}
            </div>

            {/* 月度空态:覆盖在 grid 上方,引导用户记第一笔 */}
            {monthIsEmpty && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-4">
                <div className="pointer-events-auto flex max-w-[260px] flex-col items-center gap-2 rounded-xl border border-border/60 bg-card/95 p-4 text-center shadow-lg backdrop-blur">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
                    <Sparkles className="h-5 w-5" />
                  </div>
                  <p className="text-sm font-medium text-foreground">
                    {t('calendar.empty.monthTitle')}
                  </p>
                  <p className="text-[11px] leading-relaxed text-muted-foreground">
                    {t('calendar.empty.monthHint')}
                  </p>
                  <Button size="sm" onClick={handleAddTxOnSelectedDay}>
                    <Plus className="mr-1 h-3.5 w-3.5" />
                    {t('calendar.empty.monthCta')}
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* 侧边日详情 */}
        <Card className="bc-panel overflow-hidden">
          <CardContent className="p-0">
            <DayDetailHeader
              dateKey={selectedDate}
              bucket={selectedDayBucket}
              currency={currency}
              onAdd={handleAddTxOnSelectedDay}
            />
            <DayTxList txs={dayTxs} loading={loadingDay} currency={currency} />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

// 头部 hint band 里用的 kbd 小标签
function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex h-4 min-w-[1rem] items-center justify-center rounded border border-border/60 bg-card px-1 text-[10px] font-mono font-medium text-foreground/80">
      {children}
    </kbd>
  )
}

// ============================================================================
// 日格单元格
// ============================================================================

type DayCellProps = {
  dateKey: string
  bucket: DayBucket | undefined
  inFocusedMonth: boolean
  isToday: boolean
  isSelected: boolean
  monthMaxAbsNet: number
  currency: string
  onSelect: () => void
}

function DayCell({
  dateKey,
  bucket,
  inFocusedMonth,
  isToday,
  isSelected,
  monthMaxAbsNet,
  onSelect,
}: DayCellProps) {
  const dayNum = Number(dateKey.slice(8))

  // 周末判断:0=周日 / 6=周六 → 给一个非常微弱的灰底,跟工作日区分但不抢戏
  const weekday = useMemo(() => {
    const [y, m, d] = dateKey.split('-').map(Number)
    return new Date(y, m - 1, d).getDay()
  }, [dateKey])
  const isWeekend = weekday === 0 || weekday === 6

  // 整格 tint 强度:有交易时 0.10-0.45 归一化。最低 0.10 保证哪怕极小金额也能
  // 明显看出"这一天有交易",最高 0.45 让月度最大值的格子明显突出又不会太抢眼。
  // 之前 0.06-0.22 实际渲染太淡,跟没有 tint 看起来差不多。
  const intensity = useMemo(() => {
    if (!bucket || monthMaxAbsNet === 0) return 0
    const ratio = Math.min(1, Math.abs(bucket.net) / monthMaxAbsNet)
    // 用平方根缩放强度,中等金额的差异更明显,避免一两笔超大金额把所有格子压成最浅色
    return 0.1 + Math.sqrt(ratio) * 0.35
  }, [bucket, monthMaxAbsNet])

  // 选用 income / expense 颜色 — 根据净值正负
  const colorVar = bucket && bucket.net > 0 ? '--income-rgb' : '--expense-rgb'

  // 整格背景:色块 tint(有交易时);否则周末有 subtle muted 底。
  // CSS 颜色用 `rgb(...) / alpha` 语法(空格 + 斜杠),不能用 `rgba(var(...), 0.15)`
  // —— 因为 var 展开后是空格分隔的"r g b",跟逗号 alpha 混用浏览器会拒收 → 整格透明,
  // 之前页面色块不可见就是这个 bug。
  const cellStyle: CSSProperties = bucket
    ? { backgroundColor: `rgb(var(${colorVar}) / ${intensity})` }
    : {}

  return (
    <button
      type="button"
      onClick={onSelect}
      style={cellStyle}
      className={[
        'group relative flex min-h-[76px] flex-col items-stretch overflow-hidden rounded-lg border p-2 text-left transition-all duration-200',
        isSelected
          ? 'scale-[1.03] border-primary shadow-md ring-2 ring-primary/30'
          : 'border-border/50 hover:scale-[1.01] hover:border-foreground/40 hover:shadow-sm',
        inFocusedMonth ? '' : 'opacity-30',
        isToday && !isSelected ? 'ring-1 ring-primary/40' : '',
        // 周末柔和底色(仅对无交易日生效;有交易的格子优先 tint 色)
        !bucket && isWeekend && inFocusedMonth ? 'bg-muted/30' : '',
      ].join(' ')}
      aria-label={dateKey}
      aria-selected={isSelected}
    >
      {/* 日期数字 + 角标 */}
      <div className="flex items-center justify-between">
        <span
          className={[
            'inline-flex h-6 min-w-[1.5rem] items-center justify-center text-xs font-semibold tabular-nums',
            isToday
              ? 'rounded-full bg-primary px-1.5 text-primary-foreground shadow-sm ring-2 ring-primary/20'
              : isWeekend && inFocusedMonth
                ? 'text-foreground/70'
                : 'text-foreground/90',
          ].join(' ')}
        >
          {dayNum}
        </span>
        {/* 角标:今日(无交易)用脉冲圆点 / 有交易用净值色圆点 */}
        {bucket && !isToday && (
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ backgroundColor: `rgb(var(${colorVar}))` }}
            aria-hidden
          />
        )}
        {isToday && !bucket && (
          <span
            className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary/60"
            aria-hidden
          />
        )}
      </div>

      {/* 净值数字 — 弱化样式,主视觉是整格 tint */}
      {bucket && (
        <div className="mt-auto pt-1 text-[11px] font-semibold tabular-nums">
          <span
            className={
              bucket.net >= 0
                ? 'text-[rgb(var(--income-rgb))]'
                : 'text-[rgb(var(--expense-rgb))]'
            }
          >
            {bucket.net >= 0 ? '+' : ''}
            {compactNum(bucket.net)}
          </span>
        </div>
      )}
    </button>
  )
}

// 单元格内的紧凑数字格式化:千 = k,万 = w(中文习惯)
function compactNum(value: number): string {
  const abs = Math.abs(value)
  if (abs < 1000) return value.toFixed(0)
  if (abs < 10000) return `${(value / 1000).toFixed(1)}k`
  return `${(value / 10000).toFixed(1)}w`
}

// ============================================================================
// 选中日详情:Header + tx 列表
// ============================================================================

type DayDetailHeaderProps = {
  dateKey: string
  bucket: DayBucket | undefined
  currency: string
  onAdd: () => void
}

function DayDetailHeader({ dateKey, bucket, currency, onAdd }: DayDetailHeaderProps) {
  const t = useT()
  const { dateBig, dateSub } = useMemo(() => {
    const [y, m, d] = dateKey.split('-').map(Number)
    const date = new Date(y, m - 1, d)
    const weekday = date.toLocaleDateString(undefined, { weekday: 'long' })
    return {
      dateBig: `${m}/${d}`,
      dateSub: `${y} · ${weekday}`,
    }
  }, [dateKey])

  // Hero 段:有交易时按净值正负染上柔和的渐变背景,无交易则纯色
  const netColorVar =
    bucket && bucket.net >= 0 ? '--income-rgb' : bucket ? '--expense-rgb' : null
  const heroBgStyle: CSSProperties = netColorVar
    ? {
        // 空格 rgb + 斜杠 alpha,跟 DayCell 的 cellStyle 同一原因
        backgroundImage: `linear-gradient(135deg, rgb(var(${netColorVar}) / 0.12) 0%, rgb(var(${netColorVar}) / 0.02) 60%, transparent 100%)`,
      }
    : {}

  return (
    <div className="border-b border-border/40">
      {/* Hero:日期 + 净值大字 */}
      <div className="relative px-5 pb-4 pt-5" style={heroBgStyle}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-3xl font-bold leading-none tracking-tight tabular-nums">
              {dateBig}
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">{dateSub}</div>
          </div>
          <Button size="sm" onClick={onAdd} className="shrink-0">
            <Plus className="mr-1 h-3.5 w-3.5" />
            {t('calendar.addTx')}
          </Button>
        </div>

        {bucket ? (
          <div className="mt-4">
            <div
              className={[
                'text-2xl font-bold tabular-nums',
                bucket.net >= 0
                  ? 'text-[rgb(var(--income-rgb))]'
                  : 'text-[rgb(var(--expense-rgb))]',
              ].join(' ')}
            >
              {bucket.net >= 0 ? '+' : ''}
              {signedAmount(bucket.net, currency)}
            </div>
            <div className="mt-1 flex items-center gap-3 text-[11px] text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <span
                  className="h-1.5 w-1.5 rounded-full bg-[rgb(var(--income-rgb))]"
                  aria-hidden
                />
                {t('calendar.income')} {currency} {bucket.income.toFixed(2)}
              </span>
              <span className="inline-flex items-center gap-1">
                <span
                  className="h-1.5 w-1.5 rounded-full bg-[rgb(var(--expense-rgb))]"
                  aria-hidden
                />
                {t('calendar.expense')} {currency} {bucket.expense.toFixed(2)}
              </span>
            </div>
          </div>
        ) : (
          <div className="mt-4 text-xs text-muted-foreground">
            {t('calendar.empty.day')}
          </div>
        )}
      </div>
    </div>
  )
}

function signedAmount(value: number, currency: string): string {
  return `${currency} ${Math.abs(value).toFixed(2)}`
}

type DayTxListProps = {
  txs: WorkspaceTransaction[]
  loading: boolean
  currency: string
}

function DayTxList({ txs, loading, currency }: DayTxListProps) {
  const t = useT()
  if (loading) {
    return (
      <div className="space-y-2 px-5 py-4">
        <div className="h-12 animate-pulse rounded-md bg-muted/40" />
        <div className="h-12 animate-pulse rounded-md bg-muted/40" />
        <div className="h-12 animate-pulse rounded-md bg-muted/40" />
      </div>
    )
  }
  // 选中日无交易 → 直接 return null。hero 段已经显示「当日无交易」+ 右上角「+ 新增」
  // 按钮,这里再放一个 CTA 是重复入口。
  if (txs.length === 0) return null
  return (
    <ul className="divide-y divide-border/40 px-2 py-1">
      {txs.map((tx) => (
        <li key={tx.id}>
          <button
            type="button"
            onClick={() => dispatchOpenDetailTx(tx)}
            className="group flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors hover:bg-muted/40"
          >
            {/* 类型色条 */}
            <span
              className={[
                'block h-8 w-1 shrink-0 rounded-full',
                tx.tx_type === 'income'
                  ? 'bg-[rgb(var(--income-rgb))]'
                  : tx.tx_type === 'expense'
                    ? 'bg-[rgb(var(--expense-rgb))]'
                    : 'bg-muted-foreground/40',
              ].join(' ')}
              aria-hidden
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">
                {tx.category_name || (tx.tx_type === 'transfer' ? '↔︎' : '—')}
              </div>
              <div className="truncate text-[11px] text-muted-foreground">
                {tx.account_name || tx.from_account_name || '—'}
                {tx.note ? ` · ${tx.note}` : ''}
              </div>
            </div>
            <div
              className={[
                'shrink-0 text-sm font-semibold tabular-nums',
                tx.tx_type === 'income'
                  ? 'text-[rgb(var(--income-rgb))]'
                  : tx.tx_type === 'expense'
                    ? 'text-[rgb(var(--expense-rgb))]'
                    : 'text-foreground',
              ].join(' ')}
            >
              {tx.tx_type === 'income' ? '+' : tx.tx_type === 'expense' ? '-' : ''}
              {currency} {Number(tx.amount).toFixed(2)}
            </div>
          </button>
        </li>
      ))}
    </ul>
  )
}
