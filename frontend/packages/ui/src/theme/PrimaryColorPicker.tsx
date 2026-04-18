import { Check } from 'lucide-react'

import { PRIMARY_COLOR_PRESETS } from './primary-color-script'
import { usePrimaryColor } from './PrimaryColorProvider'

interface Props {
  /** 色板下方是否展示 `<input type="color">` 自定义色。默认 true。 */
  allowCustom?: boolean
  className?: string
}

/**
 * 预设色板（9 格）+ 可选自定义色。每个 preset 是一个小圆，选中的带 check 图标。
 * 放在头像下拉或 settings-profile 页面里都合适。
 */
export function PrimaryColorPicker({ allowCustom = true, className }: Props) {
  const { color, setColor } = usePrimaryColor()
  return (
    <div className={className}>
      <div className="grid grid-cols-5 gap-2">
        {PRIMARY_COLOR_PRESETS.map((preset) => {
          const selected = preset.toLowerCase() === color.toLowerCase()
          return (
            <button
              key={preset}
              type="button"
              aria-label={`主题色 ${preset}`}
              onClick={() => setColor(preset)}
              className={`flex h-8 w-8 items-center justify-center rounded-full border shadow-sm transition-transform hover:scale-110 ${
                selected ? 'border-foreground/40 ring-2 ring-foreground/50' : 'border-border/60'
              }`}
              style={{ background: preset }}
            >
              {selected ? <Check className="h-4 w-4 text-white drop-shadow" /> : null}
            </button>
          )
        })}
      </div>
      {allowCustom ? (
        <label className="mt-3 flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className="uppercase tracking-wider">自定义</span>
          <input
            type="color"
            value={color}
            onChange={(e) => setColor(e.target.value)}
            className="h-7 w-12 cursor-pointer rounded border border-border/60 bg-transparent"
          />
          <span className="font-mono text-[11px]">{color.toUpperCase()}</span>
        </label>
      ) : null}
    </div>
  )
}
