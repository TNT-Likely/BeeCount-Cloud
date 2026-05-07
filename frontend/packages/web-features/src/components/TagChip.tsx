import type { CSSProperties, MouseEvent, ReactNode } from 'react'

import { tagChipOutlineStyle } from '../lib/tagColorPalette'

interface Props {
  name: string
  /** 标签 hex 颜色;空 → 走默认 border 色,不上色。 */
  color?: string | null
  /** 点击行为(可选)。提供时 chip 自带 hover/cursor 样式。 */
  onClick?: (name: string) => void
  className?: string
  /** 末尾插槽,例如 selected 状态的 ✓ 图标。 */
  trailing?: ReactNode
  /** 透传 title(默认 = name,长名截断时 hover 看全称)。 */
  title?: string
}

/**
 * 通用 tag chip(outline 风格)。文字 = tag 颜色,边框 + 背景半透明同色。
 * 跟交易行 / 详情弹窗里的 tag chip 视觉一致 —— 改样式只在这里改。
 *
 * 选用 outline 风格(而不是 solid 填充)的原因:
 *  - 信息密度场景 chip 多,solid 会跟周围其它 UI 抢视觉,outline 更克制
 *  - 跟移动端 TxDetailPage 的 tag chip 视觉对齐
 *
 * solid 填充风格(背景大色块)目前只在 picker / filter trigger 用,见
 * `TagSelector` / `TransactionsPanel` —— 那两处尺寸更大、单选语义,样式
 * 不复用。
 */
export function TagChip({
  name,
  color,
  onClick,
  className,
  trailing,
  title
}: Props) {
  // 没色 → 走主题色(`--primary` CSS 变量)。跟 mobile app 行为对齐:无色
  // tag 渲染成当前主题色的 outline-soft chip,跟随用户的主题色变化,不会
  // 突兀也不会变成"灰扑扑没辨识度"的状态。
  const style: CSSProperties = tagChipOutlineStyle(color) ?? {
    color: 'hsl(var(--primary))',
    borderColor: 'hsl(var(--primary) / 0.4)',
    background: 'hsl(var(--primary) / 0.1)'
  }
  const clickable = Boolean(onClick)
  const handleClick = (event: MouseEvent<HTMLSpanElement>) => {
    if (!onClick) return
    event.stopPropagation()
    onClick(name)
  }
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium leading-none ${
        clickable ? 'cursor-pointer hover:brightness-110' : ''
      } ${className ?? ''}`}
      style={style}
      onClick={handleClick}
      title={title ?? name}
    >
      {name}
      {trailing}
    </span>
  )
}
