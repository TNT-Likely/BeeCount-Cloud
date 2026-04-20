import type {
  ReadAccount,
  WorkspaceTag,
  WorkspaceTransaction
} from '@beecount/api-client'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  useT
} from '@beecount/ui'
import { TransactionList } from '@beecount/web-features'

type AccountWithStats = ReadAccount & {
  tx_count?: number | null
  income_total?: number | null
  expense_total?: number | null
  balance?: number | null
}

interface Props {
  account: AccountWithStats | null
  transactions: WorkspaceTransaction[]
  total: number
  offset: number
  loading: boolean
  tags: WorkspaceTag[]
  onClose: () => void
  onLoadMore: (accountName: string, offset: number) => void
  onPreviewAttachment?: (ctx: unknown) => void
  resolveAttachmentPreviewUrl?: (att: unknown) => string | null
}

/** 点账户卡片弹出的详情:顶部账户名 + 当前余额/累计收入/累计支出 + 交易列表(无限滚动加载)。 */
export function AccountDetailDialog({
  account,
  transactions,
  total,
  offset,
  loading,
  tags,
  onClose,
  onLoadMore,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
}: Props) {
  const t = useT()
  return (
    <Dialog open={Boolean(account)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="truncate">{account?.name || ''}</DialogTitle>
        </DialogHeader>
        {account ? (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* 统计:优先 server 返回的 balance/income/expense,缺失时兜底 initial_balance */}
            <AccountStatsHeader account={account} t={t} />

            <div className="min-h-0 flex-1 overflow-y-auto">
              <TransactionList
                items={transactions}
                tags={tags}
                variant="compact"
                loading={loading}
                hasMore={transactions.length < total}
                onLoadMore={() => {
                  if (!loading) onLoadMore(account.name, offset)
                }}
                onPreviewAttachment={onPreviewAttachment as never}
                resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl as never}
                emptyTitle={t('transactions.empty.forAccount.title')}
              />
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function AccountStatsHeader({
  account,
  t,
}: {
  account: AccountWithStats
  t: (key: string) => string
}) {
  const hasServerStats = typeof account.balance === 'number'
  const balance = hasServerStats ? account.balance! : account.initial_balance ?? 0
  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return (
    <div className="grid grid-cols-3 gap-3 border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.currentBalance')}
        </div>
        <div
          className={`mt-0.5 font-mono text-base font-bold tabular-nums ${
            balance >= 0 ? 'text-foreground' : 'text-expense'
          }`}
        >
          {fmt(balance)}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.accumIncome')}
        </div>
        <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-income">
          {fmt(account.income_total ?? 0)}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.accumExpense')}
        </div>
        <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-expense">
          {fmt(account.expense_total ?? 0)}
        </div>
      </div>
    </div>
  )
}
