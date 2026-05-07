/**
 * 顶部进度条 — 12 屏对应 12 个细条段。当前页满色填充,已过页半透明,未到页透明。
 * 点击任意段直跳到那一页。
 */
export type ProgressDotsProps = {
  total: number
  current: number
  onJump: (index: number) => void
  className?: string
}

export function ProgressDots({ total, current, onJump, className = '' }: ProgressDotsProps) {
  return (
    <div className={`flex items-center gap-1.5 ${className}`}>
      {Array.from({ length: total }).map((_, i) => {
        const state = i < current ? 'past' : i === current ? 'active' : 'future'
        return (
          <button
            key={i}
            type="button"
            onClick={() => onJump(i)}
            className="group h-1.5 flex-1 cursor-pointer overflow-hidden rounded-full bg-white/15 transition"
            aria-label={`Page ${i + 1}`}
          >
            <div
              className="h-full origin-left rounded-full bg-white transition-all duration-500"
              style={{
                width: state === 'past' ? '100%' : state === 'active' ? '100%' : '0%',
                opacity: state === 'past' ? 0.55 : state === 'active' ? 1 : 0,
              }}
            />
          </button>
        )
      })}
    </div>
  )
}
