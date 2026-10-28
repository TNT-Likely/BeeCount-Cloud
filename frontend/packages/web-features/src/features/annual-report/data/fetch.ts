/**
 * 拉取 + 聚合 — 高层入口。UI 层只用这个,内部走 api-client + aggregate。
 *
 * 一年内事务量典型 1-3K 笔,分页拉取(api-client `fetchWorkspaceTransactions`
 * 默认 limit 200),全部读完再聚合。读取失败不返 null,而是 throw,UI 层
 * try / catch 给出错提示。
 */
import { fetchWorkspaceTransactions, type WorkspaceTransaction } from '@beecount/api-client'

import { netExpenseAmount } from '../../../lib/refunds'
import { aggregate } from './aggregate'
import type { AnnualReportData, TransactionLite } from './types'

const PAGE_SIZE = 500

export async function fetchAnnualReportData(
  token: string,
  ledger: { id: string; name: string; currency: string },
  year: number,
): Promise<AnnualReportData> {
  // 时间窗口:本年 + 去年(用于 YoY 对比),取年初到年末(独占下界)
  const thisYearFrom = `${year}-01-01T00:00:00.000Z`
  const thisYearTo = `${year + 1}-01-01T00:00:00.000Z`
  const prevYearFrom = `${year - 1}-01-01T00:00:00.000Z`
  const prevYearTo = `${year}-01-01T00:00:00.000Z`

  const [thisYear, prevYear] = await Promise.all([
    fetchAllPaged(token, ledger.id, thisYearFrom, thisYearTo),
    fetchAllPaged(token, ledger.id, prevYearFrom, prevYearTo),
  ])

  return aggregate({
    thisYearTxs: thisYear.filter(isReportTransaction).map(toLite),
    prevYearTxs: prevYear.filter(isReportTransaction).map(toLite),
    year,
    ledger,
  })
}

function isReportTransaction(
  t: WorkspaceTransaction,
): t is WorkspaceTransaction & {
  tx_type: 'expense' | 'income' | 'transfer'
} {
  return !t.exclude_from_stats
}

async function fetchAllPaged(
  token: string,
  ledgerId: string,
  dateFrom: string,
  dateTo: string,
): Promise<WorkspaceTransaction[]> {
  const all: WorkspaceTransaction[] = []
  let offset = 0
  while (true) {
    const page = await fetchWorkspaceTransactions(token, {
      ledgerId,
      dateFrom,
      dateTo,
      limit: PAGE_SIZE,
      offset,
    })
    all.push(...page.items)
    if (page.items.length < PAGE_SIZE) break
    offset += PAGE_SIZE
    // 安全保护:超过 1 万笔(年度极少见)就停,避免无限循环
    if (offset >= 10000) break
  }
  return all
}

function toLite(
  t: WorkspaceTransaction & { tx_type: 'expense' | 'income' | 'transfer' },
): TransactionLite {
  // 「支出统计不含已退款」:支出行取净支出额(扣有效退款合计),年度报告
  // 所有支出聚合(总额 / 月度 / 分类 / 时段 / 单日 / 标签)自动排除已退款
  // 部分,无需在 aggregate 里逐处改。收入 / 转账无退款,保持原额。
  const amount =
    t.tx_type === 'expense' ? netExpenseAmount(t) : t.amount
  return {
    id: t.id,
    txType: t.tx_type,
    amount,
    happenedAt: t.happened_at,
    note: t.note,
    categoryName: t.category_name,
    categoryKind: t.category_kind,
    accountName: t.account_name,
    tagsList: t.tags_list ?? [],
  }
}
