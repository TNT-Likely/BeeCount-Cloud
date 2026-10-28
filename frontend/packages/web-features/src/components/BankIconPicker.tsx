/**
 * 账户图标选择器(0026):开户行未自动匹配时,用户从内置图标库里手动挑一个。
 *
 * 对话框形态:搜索框 + 分组网格。顶部「使用默认(按类型)」清除手动选择。
 */
import { useMemo, useState } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  useT
} from '@beecount/ui'

import { BankIconBadge } from './BankIconBadge'
import {
  BANK_ICONS,
  BANK_ICON_GROUPS,
  filterBankIcons,
  type BankIcon,
  type BankIconGroup
} from '../lib/bankIcons'

export function BankIconPicker({
  open,
  current,
  onSelect,
  onOpenChange
}: {
  open: boolean
  /** 当前选中的 icon key(高亮)。 */
  current: string | null
  /** 选中回调;传 null = 清除手动选择回退默认。 */
  onSelect: (icon: string | null) => void
  onOpenChange: (open: boolean) => void
}) {
  const t = useT()
  const [query, setQuery] = useState('')

  const filtered = useMemo(() => filterBankIcons(query), [query])
  const grouped = useMemo(() => {
    const map = new Map<BankIconGroup, BankIcon[]>()
    for (const icon of filtered) {
      const list = map.get(icon.group) ?? ([] as BankIcon[])
      list.push(icon)
      map.set(icon.group, list)
    }
    return map
  }, [filtered])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('accounts.icon.pickerTitle')}</DialogTitle>
        </DialogHeader>
        <Input
          placeholder={t('accounts.icon.searchPlaceholder')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          type="button"
          onClick={() => {
            onSelect(null)
            onOpenChange(false)
          }}
          className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors hover:bg-muted/40 ${
            !current ? 'border-primary bg-primary/5' : 'border-border/60'
          }`}
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-muted text-[10px] text-muted-foreground">
            {t('accounts.icon.defaultBadge')}
          </span>
          <span className="text-sm">{t('accounts.icon.useDefault')}</span>
        </button>
        {BANK_ICON_GROUPS.map(({ group, i18nKey }) => {
          const icons = grouped.get(group)
          if (!icons || icons.length === 0) return null
          return (
            <div key={group} className="space-y-2">
              <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {t(i18nKey)}
              </div>
              <div className="grid grid-cols-5 gap-2 sm:grid-cols-6">
                {icons.map((icon) => (
                  <button
                    key={icon.key}
                    type="button"
                    title={icon.label}
                    onClick={() => {
                      onSelect(icon.key)
                      onOpenChange(false)
                    }}
                    className={`flex flex-col items-center gap-1 rounded-lg border p-1.5 transition-colors hover:bg-muted/40 ${
                      current === icon.key ? 'border-primary bg-primary/5' : 'border-transparent'
                    }`}
                  >
                    <BankIconBadge icon={icon.key} size={28} />
                    <span className="w-full truncate text-center text-[9px] text-muted-foreground">
                      {icon.label}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )
        })}
        {filtered.length === 0 ? (
          <div className="py-6 text-center text-sm text-muted-foreground">
            {t('accounts.icon.noResult')}
          </div>
        ) : null}
        <DialogFooter>
          <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
