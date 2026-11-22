import { useEffect, useRef, useState } from 'react'
import { Eye, EyeOff, ImagePlus, Plus, Trash2 } from 'lucide-react'

import {
  CONTACT_METHOD_TYPES,
  maskCardNo,
  type ContactMethodType,
  type PartyBankCard,
  type PartyContactMethod,
  type PartyProfile,
} from '@beecount/api-client'
import {
  Button,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
} from '@beecount/ui'

/** 头像上传 / 预览由宿主注入：web 层用 useAttachmentCache + uploadTransactionImage。 */
type PreviewFn = (fileId: string) => Promise<string | null>

/**
 * 圆形头像。没有图片时退回首字母 + 底色，联系人卡片、成员卡片、编辑弹窗共用。
 */
export function PartyAvatar({
  fileId,
  name,
  color = '#94a3b8',
  size = 36,
  resolvePreview,
}: {
  fileId?: string | null
  name?: string
  color?: string
  size?: number
  resolvePreview?: PreviewFn
}) {
  const [url, setUrl] = useState<string | null>(null)
  const id = (fileId || '').trim()

  useEffect(() => {
    let cancelled = false
    if (!id || !resolvePreview) {
      setUrl(null)
      return
    }
    void resolvePreview(id).then((next) => {
      if (!cancelled) setUrl(next)
    })
    return () => { cancelled = true }
  }, [id, resolvePreview])

  return (
    <span
      className="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full border border-border/60 text-sm font-bold text-white"
      style={{ width: size, height: size, background: url ? undefined : color, fontSize: size * 0.4 }}
    >
      {url ? (
        <img src={url} alt="" className="h-full w-full object-cover" />
      ) : (
        (name || '?').trim().slice(0, 1).toUpperCase()
      )}
    </span>
  )
}

type EditorProps = {
  value: PartyProfile
  onChange: (next: PartyProfile) => void
  /** 上传头像 → attachment file id。 */
  upload: (file: File) => Promise<string>
  resolvePreview: PreviewFn
  disabled?: boolean
  /** 无头像时的首字母占位。 */
  name?: string
}

/**
 * 关联人资料编辑器：头像 + 可增删的联系方式(类型/内容/备注) + 可增删的银行卡。
 * 联系人与成员共用，值用 PartyProfile 整体受控，方便父弹窗保存时一次提交。
 */
export function PartyProfileEditor({
  value, onChange, upload, resolvePreview, disabled, name,
}: EditorProps) {
  const t = useT()
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [failed, setFailed] = useState(false)
  const methods = value.contactMethods
  const cards = value.bankCards
  const patch = (next: Partial<PartyProfile>) => onChange({ ...value, ...next })

  const pickFile = async (file: File | undefined) => {
    if (!file) return
    setUploading(true)
    setFailed(false)
    try {
      const id = await upload(file)
      if (!id) throw new Error('empty file id')
      patch({ avatarFileId: id })
    } catch {
      setFailed(true)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const setMethod = (index: number, next: Partial<PartyContactMethod>) =>
    patch({ contactMethods: methods.map((m, i) => (i === index ? { ...m, ...next } : m)) })
  const setCard = (index: number, next: Partial<PartyBankCard>) =>
    patch({ bankCards: cards.map((c, i) => (i === index ? { ...c, ...next } : c)) })

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <Label>{t('party.avatar')}</Label>
        <div className="flex items-center gap-3">
          <PartyAvatar
            fileId={value.avatarFileId}
            name={name}
            size={56}
            resolvePreview={resolvePreview}
          />
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => void pickFile(e.target.files?.[0])}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={disabled || uploading}
              onClick={() => fileRef.current?.click()}
            >
              <ImagePlus className="mr-1 h-4 w-4" />
              {uploading ? t('notice.loading') : t('party.avatar.upload')}
            </Button>
            {value.avatarFileId ? (
              <Button
                size="sm"
                variant="ghost"
                disabled={disabled}
                onClick={() => patch({ avatarFileId: null })}
              >
                {t('party.avatar.remove')}
              </Button>
            ) : null}
          </div>
        </div>
        <p className={`text-xs ${failed ? 'text-destructive' : 'text-muted-foreground'}`}>
          {failed ? t('party.uploadFailed') : t('party.avatar.hint')}
        </p>
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Label>{t('party.methods')} · {methods.length}</Label>
          <Button
            size="sm"
            variant="outline"
            disabled={disabled}
            onClick={() =>
              patch({ contactMethods: [...methods, { type: 'phone', value: '', label: '' }] })
            }
          >
            <Plus className="mr-1 h-4 w-4" />{t('party.methods.add')}
          </Button>
        </div>
        {methods.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t('party.methods.empty')}</p>
        ) : (
          <div className="space-y-2">
            {methods.map((m, i) => (
              <div key={i} className="grid grid-cols-[100px_1fr_1fr_auto] items-center gap-2">
                <Select
                  value={m.type}
                  disabled={disabled}
                  onValueChange={(v) => setMethod(i, { type: v as ContactMethodType })}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CONTACT_METHOD_TYPES.map((tp) => (
                      <SelectItem key={tp} value={tp}>{t(`party.method.${tp}`)}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input
                  value={m.value}
                  disabled={disabled}
                  placeholder={t('party.methods.value') as string}
                  onChange={(e) => setMethod(i, { value: e.target.value })}
                />
                <Input
                  value={m.label}
                  disabled={disabled}
                  placeholder={t('party.methods.label') as string}
                  onChange={(e) => setMethod(i, { label: e.target.value })}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  className="px-2 text-destructive"
                  disabled={disabled}
                  onClick={() => patch({ contactMethods: methods.filter((_, j) => j !== i) })}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Label>{t('party.cards')} · {cards.length}</Label>
          <Button
            size="sm"
            variant="outline"
            disabled={disabled}
            onClick={() =>
              patch({ bankCards: [...cards, { bank: '', cardNo: '', holder: '', note: '' }] })
            }
          >
            <Plus className="mr-1 h-4 w-4" />{t('party.cards.add')}
          </Button>
        </div>
        {cards.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t('party.cards.empty')}</p>
        ) : (
          <div className="space-y-2">
            {cards.map((c, i) => (
              <div key={i} className="space-y-2 rounded-lg border border-border p-2">
                <div className="grid grid-cols-2 gap-2">
                  <Input
                    value={c.bank}
                    disabled={disabled}
                    placeholder={t('party.cards.bank') as string}
                    onChange={(e) => setCard(i, { bank: e.target.value })}
                  />
                  <Input
                    value={c.cardNo}
                    disabled={disabled}
                    placeholder={t('party.cards.cardNo') as string}
                    onChange={(e) => setCard(i, { cardNo: e.target.value })}
                  />
                  <Input
                    value={c.holder}
                    disabled={disabled}
                    placeholder={t('party.cards.holder') as string}
                    onChange={(e) => setCard(i, { holder: e.target.value })}
                  />
                  <Input
                    value={c.note}
                    disabled={disabled}
                    placeholder={t('party.cards.note') as string}
                    onChange={(e) => setCard(i, { note: e.target.value })}
                  />
                </div>
                <div className="flex justify-end">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="px-2 text-destructive"
                    disabled={disabled}
                    onClick={() => patch({ bankCards: cards.filter((_, j) => j !== i) })}
                  >
                    <Trash2 className="mr-1 h-4 w-4" />{t('confirm.delete')}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * 只读资料视图：联系人详情 / 成员详情展示。卡号默认脱敏，点眼睛图标可临时
 * 显示完整卡号；头像点击不做交互（缩略图只用于识别）。
 */
export function PartyProfileView({
  profile,
  name,
  color,
  resolvePreview,
}: {
  profile: PartyProfile | null
  name?: string
  color?: string
  resolvePreview: PreviewFn
}) {
  const t = useT()
  const [revealed, setRevealed] = useState<Set<number>>(new Set())
  if (!profile) return null
  const hasContent = profile.contactMethods.length > 0 || profile.bankCards.length > 0
  if (!hasContent && !profile.avatarFileId) return null

  const toggle = (i: number) =>
    setRevealed((prev) => {
      const next = new Set(prev)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <PartyAvatar
          fileId={profile.avatarFileId}
          name={name}
          color={color}
          size={48}
          resolvePreview={resolvePreview}
        />
        <span className="text-sm font-medium">{name}</span>
      </div>
      {profile.contactMethods.length > 0 ? (
        <div className="space-y-1">
          <div className="text-xs font-medium text-muted-foreground">{t('party.methods')}</div>
          <div className="divide-y divide-border rounded-lg border border-border">
            {profile.contactMethods.map((m, i) => (
              <div key={i} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 px-3 py-2 text-sm">
                <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                  {t(`party.method.${m.type}`)}
                </span>
                <span className="font-medium">{m.value}</span>
                {m.label ? <span className="text-xs text-muted-foreground">· {m.label}</span> : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {profile.bankCards.length > 0 ? (
        <div className="space-y-1">
          <div className="text-xs font-medium text-muted-foreground">{t('party.cards')}</div>
          <div className="divide-y divide-border rounded-lg border border-border">
            {profile.bankCards.map((c, i) => (
              <div key={i} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="font-medium">{[c.bank, c.holder].filter(Boolean).join(' · ') || t('party.cards')}</div>
                  {c.cardNo ? (
                    <div className="font-mono text-xs text-muted-foreground">
                      {revealed.has(i) ? c.cardNo : maskCardNo(c.cardNo)}
                    </div>
                  ) : null}
                  {c.note ? <div className="text-xs text-muted-foreground">{c.note}</div> : null}
                </div>
                {c.cardNo ? (
                  <button
                    type="button"
                    className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground"
                    title={(revealed.has(i) ? t('party.cards.hide') : t('party.cards.show')) as string}
                    onClick={() => toggle(i)}
                  >
                    {revealed.has(i) ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}
