import { useState } from 'react'
import { Button, Dialog, DialogContent, DialogHeader, DialogTitle, Input, useLocale, useT } from '@beecount/ui'
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'

export function YearMonthPicker({ year, month, onChange }: {
  year: number | null; month: number | null
  onChange: (year: number | null, month: number | null) => void
}) {
  const t = useT()
  const { locale } = useLocale()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(String(year ?? new Date().getFullYear()))
  const number = Number(draft)
  const valid = Number.isInteger(number) && number >= 1900 && number <= 9998
  const label = year === null ? t('attachments.allDates') : new Intl.DateTimeFormat(locale, month === null ? { year: 'numeric' } : { year: 'numeric', month: 'long' }).format(new Date(year, (month ?? 1) - 1, 1))
  const choose = (nextYear: number | null, nextMonth: number | null) => { onChange(nextYear, nextMonth); setOpen(false) }
  return <>
    <Button variant="outline" className="justify-start" aria-label={t('attachments.date')} onClick={() => { setDraft(String(year ?? new Date().getFullYear())); setOpen(true) }}><CalendarDays className="mr-2 h-4 w-4" />{label}</Button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-w-sm"><DialogHeader><DialogTitle>{t('attachments.date')}</DialogTitle></DialogHeader>
      <div className="flex items-center gap-2"><Button variant="outline" size="sm" aria-label={t('attachments.previousYear')} disabled={!valid || number <= 1900} onClick={() => setDraft(String(number - 1))}><ChevronLeft className="h-4 w-4" /></Button><Input aria-label={t('attachments.year')} className="text-center" type="number" min={1900} max={9998} value={draft} onChange={e => setDraft(e.target.value)} /><Button variant="outline" size="sm" aria-label={t('attachments.nextYear')} disabled={!valid || number >= 9998} onClick={() => setDraft(String(number + 1))}><ChevronRight className="h-4 w-4" /></Button></div>
      <div className="grid grid-cols-3 gap-2">{Array.from({ length: 12 }, (_, i) => i + 1).map(value => <Button key={value} variant={year === number && month === value ? 'default' : 'outline'} disabled={!valid} onClick={() => choose(number, value)}>{new Intl.DateTimeFormat(locale, { month: 'short' }).format(new Date(2020, value - 1, 1))}</Button>)}</div>
      <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={!valid} onClick={() => choose(number, null)}>{t('attachments.wholeYear')}</Button><Button variant="outline" onClick={() => { const now = new Date(); choose(now.getFullYear(), now.getMonth() + 1) }}>{t('attachments.thisMonth')}</Button><Button variant="ghost" onClick={() => choose(null, null)}>{t('attachments.allDates')}</Button></div>
    </DialogContent></Dialog>
  </>
}
