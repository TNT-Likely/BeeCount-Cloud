/**
 * 账户图标渲染 —— 内置 SVG 库 / 自定义上传图标 / 自动推导 fallback。
 *
 * - icon_type='custom' 且 icon_key 为附件 fileId 时,从 preview map 取 blob URL;
 * - 否则按 icon_key 从内置库 accountIconSrc 取 URL;
 * - 未设置 icon_key 时按 bank_name + account_type 推导;
 * - 任何加载失败回退到通用银行卡 SVG。
 */
import { useState } from 'react'

import { accountIconSrc, inferAccountIconKey } from '../accountIcons'

export type AccountIconProps = {
  iconKey?: string | null
  iconType?: string | null
  bankName?: string | null
  accountType?: string | null
  /** custom fileId → blob URL(由 web 层 AttachmentCache 预取后传入)。 */
  previewMap?: Record<string, string>
  size?: number
  className?: string
  title?: string
}

export function AccountIcon({
  iconKey,
  iconType,
  bankName,
  accountType,
  previewMap,
  size = 20,
  className = '',
  title,
}: AccountIconProps) {
  const [failed, setFailed] = useState(false)

  let src: string
  if (iconType === 'custom' && iconKey) {
    src = previewMap?.[iconKey] || ''
  } else if (iconKey && !failed) {
    src = accountIconSrc(iconKey, iconType)
  } else {
    const inferred = inferAccountIconKey(bankName, accountType)
    src = accountIconSrc(inferred.key, inferred.kind)
  }

  // custom 还没加载出来(undefined/空)→ 用推导图标占位,避免闪空白。
  if (!src) {
    const inferred = inferAccountIconKey(bankName, accountType)
    src = accountIconSrc(inferred.key, inferred.kind)
  }

  return (
    <img
      src={src}
      width={size}
      height={size}
      alt={title || ''}
      title={title || undefined}
      className={`inline-block shrink-0 object-contain ${className}`}
      style={{ width: size, height: size }}
      onError={() => setFailed(true)}
      draggable={false}
    />
  )
}
