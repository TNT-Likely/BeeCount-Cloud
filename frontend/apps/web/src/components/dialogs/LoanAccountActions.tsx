import { useEffect, useState } from 'react'
import { fetchWorkspaceAccounts, updateAccount, type WorkspaceAccount } from '@beecount/api-client'
import { Button, Dialog, DialogContent, DialogHeader, DialogTitle, Input, Label, useT, useToast } from '@beecount/ui'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { dispatchOpenNewTx } from '../../lib/txDialogEvents'
import { localizeError } from '../../i18n/errors'

type Loan = Pick<WorkspaceAccount, 'id' | 'name' | 'currency' | 'initial_balance' | 'loan_balance_version' | 'loan_balance_since'> & { balance?: number | null }

export function LoanAccountActions({ account, onClose, onAccountChange }: {
  account: Loan
  onClose: () => void
  onAccountChange?: (account: WorkspaceAccount) => void
}) {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { ledgers, activeLedgerId } = useLedgers()
  const { retryOnConflict } = useLedgerWrite()
  const [review, setReview] = useState(false)
  const [debt, setDebt] = useState(String(Math.abs(account.balance ?? account.initial_balance ?? 0)))
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    setDebt(String(Math.abs(account.balance ?? account.initial_balance ?? 0)))
    setReview(false)
  }, [account.id, account.balance, account.initial_balance])
  const ready = account.loan_balance_version === 1
  const personalLedger = ledgers.find((row) => row.ledger_id === activeLedgerId && row.role === 'owner' && !row.is_shared)
    || ledgers.find((row) => row.role === 'owner' && !row.is_shared)
  const openTransfer = (repayment: boolean) => {
    if (!personalLedger) { toast.error(t('error.LOAN_PERSONAL_LEDGER_ONLY'), t('notice.error')); return }
    onClose()
    dispatchOpenNewTx({ ledgerId: personalLedger.ledger_id, txType: 'transfer', currencyCode: account.currency || 'CNY',
      ...(repayment ? { toAccountName: account.name } : { fromAccountName: account.name }) })
  }
  const save = async () => {
    const amount = Number(debt)
    if (!debt.trim() || !Number.isFinite(amount) || amount < 0) return
    const ledger = personalLedger
    if (!ledger) return
    setSaving(true)
    try {
      await retryOnConflict(ledger.ledger_id, (base) => updateAccount(token, ledger.ledger_id, account.id, base, {
        loan_confirmed_debt: amount,
      }))
      const rows = await fetchWorkspaceAccounts(token, { limit: 500 })
      const updated = rows.find((row) => row.id === account.id)
      if (updated) onAccountChange?.(updated)
      setReview(false)
    } catch (error) {
      toast.error(localizeError(error, t), t('notice.error'))
    } finally { setSaving(false) }
  }
  return <div className="space-y-2 border-b px-6 py-4">
    {ready ? <div className="flex gap-2">
      <Button onClick={() => openTransfer(true)}>{t('accounts.loan.repay')}</Button>
      <Button variant="outline" onClick={() => openTransfer(false)}>{t('accounts.loan.borrow')}</Button>
      <Button variant="ghost" onClick={() => setReview(true)}>{t('accounts.loan.calibrate')}</Button>
    </div> : <Button onClick={() => setReview(true)}>{t('accounts.loan.review')}</Button>}
    <p className="text-xs text-muted-foreground">{t(ready ? 'accounts.loan.principalHint' : 'accounts.loan.reviewHint')}</p>
    <Dialog open={review} onOpenChange={setReview}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t(ready ? 'accounts.loan.calibrate' : 'accounts.loan.review')}</DialogTitle></DialogHeader>
        <p className="text-sm text-muted-foreground">{t(ready ? 'accounts.loan.calibrationHint' : 'accounts.loan.reviewHint')}</p>
        <Label>{t('accounts.loan.currentDebt')}</Label>
        <Input type="number" min="0" step="0.01" value={debt} onChange={(event) => setDebt(event.target.value)} />
        <Button disabled={saving} onClick={() => void save()}>{t('common.confirm')}</Button>
      </DialogContent>
    </Dialog>
  </div>
}
