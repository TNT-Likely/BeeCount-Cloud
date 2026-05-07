import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  useT,
} from '@beecount/ui'
import type { ImportTransactionSample } from '@beecount/api-client'

interface Props {
  samples: ImportTransactionSample[]
  totalRows: number
}

/**
 * 解析后的前 10 笔交易预览 —— 让用户在确认之前能看到"长这样"。
 *
 * 不满意 → 点顶部「编辑映射」改 mapping → 重 preview → 这里跟着变。
 */
export function TransactionsPreviewCard({ samples, totalRows }: Props) {
  const t = useT()
  return (
    <Card className="bc-panel">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">{t('import.preview.title')}</CardTitle>
        <span className="text-[11px] text-muted-foreground">
          {t('import.preview.showing', {
            shown: samples.length,
            total: totalRows,
          })}
        </span>
      </CardHeader>
      <CardContent className="p-0">
        {samples.length === 0 ? (
          <p className="px-4 py-8 text-center text-xs text-muted-foreground">
            {t('import.preview.empty')}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead className="bg-muted/30 text-[10px] uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left">#</th>
                  <th className="px-3 py-2 text-left">{t('import.preview.col.time')}</th>
                  <th className="px-3 py-2 text-left">{t('import.preview.col.type')}</th>
                  <th className="px-3 py-2 text-right">{t('import.preview.col.amount')}</th>
                  <th className="px-3 py-2 text-left">{t('import.preview.col.category')}</th>
                  <th className="px-3 py-2 text-left">{t('import.preview.col.account')}</th>
                  <th className="px-3 py-2 text-left">{t('import.preview.col.tags')}</th>
                  <th className="px-3 py-2 text-left">{t('import.preview.col.note')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {samples.map((tx, idx) => (
                  <tr key={idx} className="hover:bg-muted/20">
                    <td className="px-3 py-1.5 text-muted-foreground">
                      L{tx.source_row_number}
                    </td>
                    <td className="px-3 py-1.5 font-mono">{formatDate(tx.happened_at)}</td>
                    <td className="px-3 py-1.5">
                      <TypeChip type={tx.tx_type} />
                    </td>
                    <td className={`px-3 py-1.5 text-right font-mono tabular-nums ${
                      tx.tx_type === 'expense'
                        ? 'text-expense'
                        : tx.tx_type === 'income'
                          ? 'text-income'
                          : ''
                    }`}>
                      {tx.tx_type === 'expense' ? '−' : tx.tx_type === 'income' ? '+' : ''}
                      {formatAmount(tx.amount)}
                    </td>
                    <td className="px-3 py-1.5">
                      {tx.parent_category_name ? (
                        <span className="text-muted-foreground">
                          {tx.parent_category_name} /
                        </span>
                      ) : null}
                      {tx.category_name || ''}
                    </td>
                    <td className="px-3 py-1.5">
                      {tx.tx_type === 'transfer'
                        ? `${tx.from_account_name || '-'} → ${tx.to_account_name || '-'}`
                        : tx.account_name || ''}
                    </td>
                    <td className="px-3 py-1.5">
                      {tx.tag_names.length > 0 ? (
                        <span className="flex flex-wrap gap-1">
                          {tx.tag_names.map((tag) => (
                            <span
                              key={tag}
                              className="rounded border border-border/60 px-1 py-0.5 text-[10px] text-muted-foreground"
                            >
                              {tag}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 max-w-[200px] truncate text-muted-foreground" title={tx.note || ''}>
                      {tx.note || ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function TypeChip({ type }: { type: string }) {
  const t = useT()
  const classes =
    type === 'expense'
      ? 'border-expense/40 text-expense'
      : type === 'income'
        ? 'border-income/40 text-income'
        : 'border-border/60 text-muted-foreground'
  return (
    <span className={`inline-block rounded border px-1.5 text-[10px] ${classes}`}>
      {t(`enum.txType.${type}`)}
    </span>
  )
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    const yyyy = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const mi = String(d.getMinutes()).padStart(2, '0')
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}`
  } catch {
    return iso
  }
}

function formatAmount(raw: string): string {
  const n = Number(raw)
  if (Number.isNaN(n)) return raw
  return Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}
