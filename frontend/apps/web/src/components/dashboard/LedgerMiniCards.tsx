import type { ReadLedger } from '@beecount/api-client'
import { Badge, Button, Card, CardContent } from '@beecount/ui'

interface Props {
  ledgers: ReadLedger[]
  activeLedgerId?: string | null
  onSelectLedger: (id: string) => void
  onDeleteLedger?: (id: string) => void
}

export function LedgerMiniCards({ ledgers, activeLedgerId, onSelectLedger, onDeleteLedger }: Props) {
  if (ledgers.length === 0) {
    return (
      <Card className="bc-panel">
        <CardContent className="py-10 text-center text-xs text-muted-foreground">
          尚未创建账本
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {ledgers.map((ledger) => {
        const active = ledger.ledger_id === activeLedgerId
        const net = ledger.balance
        return (
          <Card
            key={ledger.ledger_id}
            className={`group relative overflow-hidden border-border/60 transition ${
              active
                ? 'border-primary/60 ring-2 ring-primary/30 shadow-md'
                : 'hover:border-primary/40 hover:shadow-sm'
            }`}
          >
            <div
              className={`pointer-events-none absolute inset-0 bg-gradient-to-br transition-opacity ${
                active
                  ? 'from-primary/12 via-transparent to-secondary/10 opacity-100'
                  : 'from-primary/8 via-transparent to-transparent opacity-50 group-hover:opacity-100'
              }`}
              aria-hidden
            />
            <CardContent className="relative pt-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <h3 className="truncate text-sm font-semibold">{ledger.ledger_name}</h3>
                    {active ? (
                      <Badge variant="default" className="h-4 px-1.5 text-[9px]">
                        当前
                      </Badge>
                    ) : null}
                  </div>
                  <div className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                    {ledger.currency} · {ledger.transaction_count} 笔
                  </div>
                </div>
                {onDeleteLedger ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation()
                      onDeleteLedger(ledger.ledger_id)
                    }}
                    aria-label="删除账本"
                  >
                    ×
                  </Button>
                ) : null}
              </div>
              <div className={`mt-3 text-2xl font-bold tracking-tight ${
                net >= 0 ? 'text-income' : 'text-expense'
              }`}>
                {net.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
                <div className="rounded bg-income/10 px-2 py-1 text-income">
                  ↑ {ledger.income_total.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}
                </div>
                <div className="rounded bg-expense/10 px-2 py-1 text-expense">
                  ↓ {ledger.expense_total.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}
                </div>
              </div>
              {!active ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-3 h-7 w-full text-xs"
                  onClick={() => onSelectLedger(ledger.ledger_id)}
                >
                  设为当前账本
                </Button>
              ) : (
                <div className="mt-3 h-7" />
              )}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
