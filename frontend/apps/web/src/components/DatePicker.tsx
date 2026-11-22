import { useState } from 'react'
import { DayPicker } from 'react-day-picker'
import { zhCN } from 'date-fns/locale'
import { zhTW } from 'date-fns/locale'
import { Button, Dialog, DialogContent, DialogHeader, DialogTitle, useLocale, useT } from '@beecount/ui'
import { CalendarDays } from 'lucide-react'
import 'react-day-picker/style.css'

export function DatePicker({ value, onChange, label }: {
  value: string
  onChange: (v: string) => void
  label?: string
}) {
  const { locale } = useLocale()
  const t = useT()
  const [open, setOpen] = useState(false)
  const selected = value ? new Date(value + 'T00:00:00') : undefined
  const fmt = (d: Date) => {
    if (locale === 'zh-CN') return d.toISOString().slice(0, 10)
    if (locale === 'zh-TW') return d.toISOString().slice(0, 10)
    return new Intl.DateTimeFormat(locale || 'en', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(d)
  }

  return <>
    <Button variant="outline" className="justify-start w-full" onClick={() => setOpen(true)}>
      <CalendarDays className="mr-2 h-4 w-4" />
      {value || t('attachments.allDates')}
    </Button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-sm p-2">
        <DialogHeader><DialogTitle>{label ?? t('attachments.date')}</DialogTitle></DialogHeader>
        <DayPicker
          mode="single"
          selected={selected}
          onSelect={(d) => { if (d) { onChange(fmt(d)); setOpen(false) } }}
          locale={locale === 'zh-CN' ? zhCN : locale === 'zh-TW' ? zhTW : undefined}
        />
        <div className="flex justify-end gap-2 p-2">
          <Button variant="ghost" size="sm" onClick={() => { onChange(''); setOpen(false) }}>{t('attachments.allDates')}</Button>
        </div>
      </DialogContent>
    </Dialog>
  </>
}
