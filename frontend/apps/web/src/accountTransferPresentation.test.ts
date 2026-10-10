import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { ReadTransaction } from '@beecount/api-client'
import { LocaleProvider } from '@beecount/ui'
import { TransactionList } from '@beecount/web-features'
import { dictionaries } from './i18n'

const repayment: ReadTransaction = {
  id: 'tx-repayment', tx_index: 1, tx_type: 'transfer', amount: 135,
  happened_at: '2026-10-10T02:48:00Z', note: '信用卡还款',
  category_name: null, category_kind: null, account_name: null,
  from_account_id: 'bank', from_account_name: '农业银行',
  to_account_id: 'card', to_account_name: '招商银行',
  tags: null, tags_list: [], attachments: null,
  last_change_id: 0,
}

function render(row: ReadTransaction, currentAccountId?: string) {
  return renderToStaticMarkup(createElement(LocaleProvider, {
    dictionaries,
    children: createElement(TransactionList, { items: [row], currentAccountId, variant: 'compact' }),
  }))
}

describe('账户还款的显示方向', () => {
  for (const [accountId, sign] of [['bank', '-'], ['card', '+']] as const) {
    it(`${accountId} 显示 ${sign}135、中性转账色和完整路径`, () => {
      const html = render(repayment, accountId)
      expect(html).toMatch(new RegExp(`text-foreground[^>]*>${sign === '+' ? '\\+' : '-'}135\\.00</span>`))
      expect(html).toContain('农业银行 → 招商银行')
      expect(html).toContain(dictionaries.en['enum.txType.transfer'])
      expect(html).toContain('(信用卡还款)')
      expect(html).not.toContain('text-income')
      expect(html).not.toContain('text-expense')
    })
  }
  it('交易页没有账户上下文时保持中性、无正负号', () => {
    expect(render(repayment)).toMatch(/text-foreground[^>]*>135\.00<\/span>/)
  })
  it('只按账户 ID 匹配，同名账户或旧缺失 ID 不猜测方向', () => {
    expect(render(repayment, 'other-card')).toMatch(/text-foreground[^>]*>135\.00<\/span>/)
    expect(render({ ...repayment, from_account_id: null, to_account_id: null }, 'card')).toMatch(/text-foreground[^>]*>135\.00<\/span>/)
  })
  it('同账户转账不误标转入或转出', () => {
    expect(render({ ...repayment, from_account_id: 'card' }, 'card')).toMatch(/text-foreground[^>]*>135\.00<\/span>/)
  })
  for (const [tx_type, sign, tone] of [['expense', '-', 'text-expense'], ['income', '+', 'text-income']] as const) {
    it(`${tx_type} 保留原有收支语义`, () => {
      const html = render({ ...repayment, tx_type }, 'card')
      expect(html).toMatch(new RegExp(`${tone}[^>]*>${sign === '+' ? '\\+' : '-'}135\\.00</span>`))
    })
  }
})
