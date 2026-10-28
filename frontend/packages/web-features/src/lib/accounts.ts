/**
 * 账户展示辅助(0027)。
 *
 * 两件事:
 *  1. 别名(alias)—— 账户名是"招商银行信用卡(1234)"这类正式名,别名是用户
 *     在列表/卡片上想看到的短名("招行卡")。展示统一走 `accountDisplayName`,
 *     没有别名时原样返回 name,调用方不需要到处写 `??`。
 *  2. 信用卡账单周期 —— 由「账单日(billing_day)」+「出账日计入当期」
 *     (billing_day_in_current)推导当前账单区间与还款日。
 */

/** 能取到别名/名称的最小形状:ReadAccount / WorkspaceAccount 都兼容。 */
export type AccountLike = {
  name?: string | null
  alias?: string | null
}

/** 账户展示名:有别名用别名,否则用账户名。 */
export function accountDisplayName(account: AccountLike | null | undefined): string {
  if (!account) return ''
  const alias = (account.alias ?? '').trim()
  if (alias) return alias
  return (account.name ?? '').trim()
}

/** 是否设了别名(且与账户名不同)—— 决定是否要在旁边补一行原名。 */
export function hasAlias(account: AccountLike | null | undefined): boolean {
  if (!account) return false
  const alias = (account.alias ?? '').trim()
  if (!alias) return false
  return alias !== (account.name ?? '').trim()
}

/** 把「日」夹到该月的合法日期(31 号在 2 月 → 取月末)。 */
export function clampDayToMonth(year: number, month1: number, day: number): Date {
  const last = new Date(year, month1, 0).getDate() // month1 的下个月第 0 天 = 本月最后一天
  return new Date(year, month1 - 1, Math.min(Math.max(day, 1), last))
}

export type StatementPeriod = {
  /** 本期账单起始日(含),本地时间 00:00。 */
  start: Date
  /** 本期账单结束日(含),本地时间 00:00。 */
  end: Date
  /** 本期账单的还款日(本地时间 00:00)。 */
  due: Date | null
  /** 距还款日天数(负数 = 已逾期)。 */
  daysToDue: number | null
  /** 该区间落在哪个月(YYYY-MM,按 end 所在月),列表分组用。 */
  monthKey: string
}

/**
 * 计算「当前账单周期」。
 *
 * 口径(与服务端 billing_day_in_current 字段注释一致):
 *  - `inCurrent = true`(默认):账单日当天消费计入**当期**账单 —— 本期 =
 *    [上期账单日 + 1 天, 本期账单日],账单日本身是本期最后一天;
 *  - `inCurrent = false`:账单日当天消费计入**下一期** —— 本期 =
 *    [本期账单日, 下期账单日 - 1 天],账单日本身是本期第一天。
 *
 * `reference` 落在哪一期就返回哪一期(所以月初未到账单日时返回的是"上一期"
 * 那条尚未出账的账单)。账单日 / 还款日未设置时返回 null。
 */
export function statementPeriod(
  billingDay: number | null | undefined,
  paymentDueDay: number | null | undefined,
  inCurrent: boolean | null | undefined,
  reference: Date = new Date(),
): StatementPeriod | null {
  const bd = Number(billingDay)
  if (!Number.isInteger(bd) || bd < 1 || bd > 31) return null
  const inCur = inCurrent !== false
  const y = reference.getFullYear()
  const m = reference.getMonth() + 1

  // 本月账单日(超出当月天数时夹到月末)
  const thisBilling = clampDayToMonth(y, m, bd)

  let start: Date
  let end: Date
  if (inCur) {
    // 本期 = (上月账单日, 本月账单日];reference ≥ 本月账单日 → 已进下一期
    const inNext = reference.getTime() >= startOfNextDay(thisBilling).getTime()
    if (!inNext) {
      const prev = addMonths(y, m, -1)
      end = thisBilling
      start = nextDay(clampDayToMonth(prev.year, prev.month, bd))
    } else {
      const next = addMonths(y, m, 1)
      start = nextDay(thisBilling)
      end = clampDayToMonth(next.year, next.month, bd)
    }
  } else {
    // 本期 = [本月账单日, 下月账单日);reference < 本月账单日 → 还在上一期
    const started = reference.getTime() >= thisBilling.getTime()
    if (started) {
      const next = addMonths(y, m, 1)
      start = thisBilling
      end = prevDay(clampDayToMonth(next.year, next.month, bd))
    } else {
      const prev = addMonths(y, m, -1)
      start = clampDayToMonth(prev.year, prev.month, bd)
      end = prevDay(thisBilling)
    }
  }

  const dueDay = Number(paymentDueDay)
  let due: Date | null = null
  let daysToDue: number | null = null
  if (Number.isInteger(dueDay) && dueDay >= 1 && dueDay <= 31) {
    // 账单月的次月还款最常见(账单日 5 号 → 还款日 25 号在次月);这里按
    // "账单区间结束日所在月的下一个月"落还款日,与主流银行一致。
    const dm = addMonths(end.getFullYear(), end.getMonth() + 1, 1)
    due = clampDayToMonth(dm.year, dm.month, dueDay)
    daysToDue = Math.round(
      (stripTime(due).getTime() - stripTime(reference).getTime()) / 86400000,
    )
  }

  return {
    start,
    end,
    due,
    daysToDue,
    monthKey: `${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, '0')}`,
  }
}

/** YYYY-MM-DD(本地时区,不走 toISOString 避免 UTC 偏移串日)。 */
export function formatDateKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`
}

/** 「MM-DD」短格式,账单区间展示用。 */
export function formatMonthDay(d: Date): string {
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// ---------- 内部:纯日期运算 ----------

function addMonths(year: number, month1: number, delta: number) {
  const idx = year * 12 + (month1 - 1) + delta
  return { year: Math.floor(idx / 12), month: (idx % 12) + 1 }
}

function stripTime(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

function nextDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)
}

function prevDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() - 1)
}

function startOfNextDay(d: Date): Date {
  return nextDay(d)
}
