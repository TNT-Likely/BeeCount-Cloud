/**
 * 账户图标 / 背景选择器 —— 内置开源图标库 + 背景预设 + 自定义上传。
 *
 * 自定义图标:用户上传 SVG/PNG,由 web 层通过 onUploadCustom 回调上传到附件
 * 接口,返回 fileId;组件把它作为 icon_key + icon_type='custom' 写回表单。
 * 上传回调由调用方注入(web 层持有 token),本组件零网络依赖。
 */
import { useRef, useState } from 'react'

import { Button, Dialog, DialogContent, DialogHeader, DialogTitle } from '@beecount/ui'
import { Upload } from 'lucide-react'

import {
  ACCOUNT_BACKGROUNDS,
  ACCOUNT_ICON_GROUPS,
  BACKGROUND_MAP,
  BANK_ICONS,
  accountIconSrc,
} from '../accountIcons'
import { AccountIcon } from './AccountIcon'

export type AccountIconPickerProps = {
  iconKey: string
  iconType: string
  backgroundKey: string
  previewMap?: Record<string, string>
  onChange: (next: { icon_key: string; icon_type: string; background_key: string }) => void
  /** 上传自定义图片,返回附件 fileId;未提供则隐藏上传按钮。 */
  onUploadCustom?: (file: File) => Promise<string>
  labels?: {
    title?: string
    iconSection?: string
    backgroundSection?: string
    custom?: string
    none?: string
    uploadHint?: string
  }
}

export function AccountIconPicker({
  iconKey,
  iconType,
  backgroundKey,
  previewMap,
  onChange,
  onUploadCustom,
  labels = {},
}: AccountIconPickerProps) {
  const [open, setOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const currentIcon = iconKey
    ? iconType === 'custom'
      ? previewMap?.[iconKey]
        ? <img src={previewMap[iconKey]} width={24} height={24} className="object-contain" />
        : <span className="material-symbols-outlined">image</span>
      : <img src={accountIconSrc(iconKey, iconType)} width={24} height={24} className="object-contain" />
    : <span className="material-symbols-outlined text-muted-foreground">account_balance_wallet</span>

  const bg = backgroundKey ? BACKGROUND_MAP[backgroundKey] : null

  const handleUpload = async (file: File | undefined) => {
    if (!file || !onUploadCustom) return
    setUploading(true)
    setError('')
    try {
      const fileId = await onUploadCustom(file)
      onChange({ icon_key: fileId, icon_type: 'custom', background_key: backgroundKey })
      setOpen(false)
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e))
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="inline-flex items-center gap-2 rounded-md border border-input bg-transparent px-3 py-2 text-sm hover:bg-muted/50"
        >
          <span className="inline-flex h-6 w-6 items-center justify-center">{currentIcon}</span>
          <span>{iconKey ? (BANK_ICONS[iconKey]?.label || (iconType === 'custom' ? labels.custom || '自定义' : iconKey)) : labels.none || '默认'}</span>
        </button>
        {bg ? (
          <span
            className="inline-block h-6 w-10 rounded border border-white/20"
            style={{ background: bg.css }}
            title={bg.label}
          />
        ) : null}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          <DialogHeader><DialogTitle>{labels.title || '账户外观'}</DialogTitle></DialogHeader>

          <div className="space-y-4">
            <div>
              <p className="mb-2 text-sm font-medium">{labels.iconSection || '图标'}</p>
              <button
                type="button"
                onClick={() => onChange({ icon_key: '', icon_type: '', background_key: backgroundKey })}
                className="mb-2 rounded-md border px-3 py-1 text-xs hover:bg-muted/50"
              >
                {labels.none || '默认（自动识别）'}
              </button>
              {ACCOUNT_ICON_GROUPS.map((group) => (
                <div key={group.key} className="mb-3">
                  <p className="mb-1 text-xs text-muted-foreground">{group.label}</p>
                  <div className="grid grid-cols-6 gap-2 sm:grid-cols-8">
                    {group.icons.map((icon) => (
                      <button
                        key={icon.key}
                        type="button"
                        title={icon.label}
                        onClick={() => {
                          onChange({ icon_key: icon.key, icon_type: icon.kind, background_key: backgroundKey })
                          setOpen(false)
                        }}
                        className={`flex flex-col items-center gap-1 rounded-md border p-1.5 text-[10px] hover:bg-muted/50 ${
                          iconKey === icon.key ? 'border-primary bg-primary/10' : 'border-transparent'
                        }`}
                      >
                        <img src={icon.src} width={28} height={28} className="object-contain" alt={icon.label} />
                        <span className="w-full truncate text-center">{icon.label}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {onUploadCustom ? (
                <div className="rounded-md border border-dashed p-3">
                  <input
                    ref={fileRef}
                    type="file"
                    accept="image/svg+xml,image/png,image/webp"
                    className="hidden"
                    onChange={(e) => handleUpload(e.target.files?.[0])}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={uploading}
                    onClick={() => fileRef.current?.click()}
                  >
                    <Upload className="mr-1 h-4 w-4" />
                    {uploading ? '上传中…' : labels.custom || '上传自定义图标'}
                  </Button>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {labels.uploadHint || '支持 SVG / PNG / WebP'}
                  </p>
                  {error ? <p className="mt-1 text-xs text-destructive">{error}</p> : null}
                </div>
              ) : null}
            </div>

            <div>
              <p className="mb-2 text-sm font-medium">{labels.backgroundSection || '卡片背景'}</p>
              <div className="grid grid-cols-5 gap-2">
                <button
                  type="button"
                  onClick={() => onChange({ icon_key: iconKey, icon_type: iconType, background_key: '' })}
                  className="rounded-md border border-input px-2 py-2 text-xs hover:bg-muted/50"
                >
                  默认
                </button>
                {ACCOUNT_BACKGROUNDS.map((b) => (
                  <button
                    key={b.key}
                    type="button"
                    title={b.label}
                    onClick={() => onChange({ icon_key: iconKey, icon_type: iconType, background_key: b.key })}
                    className={`h-10 rounded-md border-2 ${backgroundKey === b.key ? 'border-primary' : 'border-transparent'}`}
                    style={{ background: b.css }}
                  />
                ))}
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
