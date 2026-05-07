import { useCallback, useRef, useState } from 'react'
import { Upload } from 'lucide-react'

import { useT } from '@beecount/ui'

interface Props {
  onSelect: (file: File) => void
  disabled?: boolean
  accept?: string
}

/**
 * 拖拽 + 点选 文件选择区。
 *
 * 移动端没有 dragover 概念,但 hidden input + 整块点击 → 系统文件选择仍可
 * 用,所以两种交互可共存,不需要 media query 切换。
 */
export function FileDropZone({
  onSelect,
  disabled = false,
  accept = '.csv,.tsv,.xlsx',
}: Props) {
  const t = useT()
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [hover, setHover] = useState(false)

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return
      onSelect(files[0])
    },
    [onSelect],
  )

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => !disabled && inputRef.current?.click()}
      onKeyDown={(e) => {
        if (disabled) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          inputRef.current?.click()
        }
      }}
      onDragEnter={(e) => {
        e.preventDefault()
        if (!disabled) setHover(true)
      }}
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled) setHover(true)
      }}
      onDragLeave={() => setHover(false)}
      onDrop={(e) => {
        e.preventDefault()
        setHover(false)
        if (disabled) return
        handleFiles(e.dataTransfer.files)
      }}
      className={`group flex min-h-[160px] cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-6 text-center transition ${
        hover
          ? 'border-primary bg-primary/10'
          : 'border-border/60 bg-muted/20 hover:border-primary/40 hover:bg-muted/40'
      } ${disabled ? 'cursor-not-allowed opacity-60' : ''}`}
      aria-disabled={disabled}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          handleFiles(e.target.files)
          e.target.value = ''
        }}
        disabled={disabled}
      />
      <Upload className="h-7 w-7 text-muted-foreground transition group-hover:text-primary" />
      <p className="text-sm font-medium">{t('import.drop.title')}</p>
      <p className="text-xs text-muted-foreground">
        {t('import.drop.formats')}
      </p>
      <p className="text-[11px] text-muted-foreground">
        {t('import.drop.sizeLimit')}
      </p>
    </div>
  )
}
