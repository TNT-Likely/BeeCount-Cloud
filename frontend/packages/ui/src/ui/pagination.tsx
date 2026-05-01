import { useEffect, useState } from 'react'

import { useT } from '../locale/LocaleProvider'

import { Button } from './button'
import { Input } from './input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './select'

const DEFAULT_PAGE_SIZES = [20, 50, 100] as const

type PaginationProps = {
  /** 当前页(1-based)。 */
  page: number
  /** 每页大小。 */
  pageSize: number
  /** 总条数,用于推导总页数 + summary 范围。 */
  total: number
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  /** 可选页大小预设,默认 [20, 50, 100]。 */
  pageSizes?: readonly number[]
  /** mobile 上 summary 在控件上一行;sm+ 单行。整体容器允许 caller 加 padding。 */
  className?: string
}

/**
 * 标准分页组件 —— summary + 页大小 select + 上一页 / 页码输入 / 下一页。
 *
 * 设计要点:
 * - 页码 input 用本地 state(`pageInputValue`)解耦输入过程,失焦/Enter 才
 *   commit,避免每按一次数字就触发 fetch。
 * - 外部 `page` 变化(prev/next 点击 / filter 重置回 1)通过 useEffect 同步
 *   回 input 显示。
 * - clamp 到 `[1, totalPages]`,空字符串/NaN 回退到当前页。
 */
export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizes = DEFAULT_PAGE_SIZES,
  className,
}: PaginationProps) {
  const t = useT()
  const totalPages = Math.max(1, Math.ceil(total / Math.max(pageSize, 1)))
  const safePage = Math.min(Math.max(page, 1), totalPages)
  const rangeStart = total === 0 ? 0 : (safePage - 1) * pageSize + 1
  const rangeEnd = total === 0 ? 0 : Math.min(total, safePage * pageSize)

  const [pageInputValue, setPageInputValue] = useState(`${safePage}`)
  useEffect(() => {
    setPageInputValue(`${safePage}`)
  }, [safePage])

  const commitPageInput = () => {
    const parsed = Number((pageInputValue || '').trim())
    if (!Number.isFinite(parsed)) {
      setPageInputValue(`${safePage}`)
      return
    }
    const clamped = Math.min(Math.max(Math.round(parsed), 1), totalPages)
    setPageInputValue(`${clamped}`)
    if (clamped !== safePage) onPageChange(clamped)
  }

  return (
    <div
      className={[
        'flex flex-col gap-2 border-t border-border/60 px-3 py-3',
        'sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-3',
        className || '',
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <p className="text-xs text-muted-foreground">
        {t('pagination.summary', { start: rangeStart, end: rangeEnd, total })}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={`${pageSize}`}
          onValueChange={(value) => onPageSizeChange(Number(value))}
        >
          <SelectTrigger className="h-8 w-[96px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {pageSizes.map((size) => (
              <SelectItem key={size} value={`${size}`}>
                {t('pagination.perPage', { size })}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          className="h-8 px-3"
          disabled={safePage <= 1}
          size="sm"
          variant="outline"
          onClick={() => onPageChange(safePage - 1)}
        >
          {t('pagination.prev')}
        </Button>
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <Input
            className="h-8 w-[56px] text-center"
            type="number"
            inputMode="numeric"
            min={1}
            max={totalPages}
            value={pageInputValue}
            onChange={(event) => setPageInputValue(event.target.value)}
            onBlur={commitPageInput}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                commitPageInput()
              }
            }}
          />
          <span>/ {totalPages}</span>
        </div>
        <Button
          className="h-8 px-3"
          disabled={safePage >= totalPages}
          size="sm"
          variant="outline"
          onClick={() => onPageChange(safePage + 1)}
        >
          {t('pagination.next')}
        </Button>
      </div>
    </div>
  )
}
