/**
 * 成员图标选择器:从人物/家庭语义的小图标集里挑一个头像。
 *
 * 跟 BankIconPicker(几十个银行品牌、带搜索)不同,成员图标只有十几个、
 * 一眼能看完,所以做成纯网格 + 「用默认图标」清除项,不加搜索框。
 */
import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'

import { MEMBER_ICONS, resolveMemberIcon } from '../lib/memberIcons'

export function MemberIconPicker({
  open,
  current,
  onSelect,
  onOpenChange,
}: {
  open: boolean
  /** 当前图标 key(高亮);空 = 未设置,界面上高亮默认图标。 */
  current: string | null
  /** 选中回调;传 null = 清除,回退默认图标。 */
  onSelect: (icon: string | null) => void
  onOpenChange: (open: boolean) => void
}) {
  const t = useT()
  const active = resolveMemberIcon(current)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('members.icon.pickerTitle')}</DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-5 gap-2">
          {MEMBER_ICONS.map((name) => (
            <button
              key={name}
              type="button"
              aria-label={name}
              aria-pressed={active === name}
              onClick={() => {
                onSelect(name)
                onOpenChange(false)
              }}
              className={[
                'flex h-12 items-center justify-center rounded-lg border transition',
                active === name
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-border/60 hover:border-primary/40 hover:bg-accent/40',
              ].join(' ')}
            >
              <span className="material-symbols-outlined text-2xl">{name}</span>
            </button>
          ))}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              onSelect(null)
              onOpenChange(false)
            }}
          >
            {t('members.icon.useDefault')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 成员图标徽标:列表 / 卡片里展示成员头像(未设置时用默认人形)。 */
export function MemberIconBadge({
  icon,
  size = 24,
  className,
}: {
  icon: string | null | undefined
  size?: number
  className?: string
}) {
  return (
    <span className={`material-symbols-outlined ${className ?? ''}`} style={{ fontSize: size }}>
      {resolveMemberIcon(icon)}
    </span>
  )
}
