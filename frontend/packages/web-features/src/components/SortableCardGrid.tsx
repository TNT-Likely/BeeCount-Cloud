import type { ReactNode } from 'react'

import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent
} from '@dnd-kit/core'
import { rectSortingStrategy, sortableKeyboardCoordinates, SortableContext, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { EyeOff, GripVertical } from 'lucide-react'

export interface SortableCardGridItem {
  id: string
  /** 真 = 佔滿整列(兩欄);否則半寬。 */
  full?: boolean
  label: string
}

interface Props {
  items: SortableCardGridItem[]
  /** 拖曳放開後:把 `fromId` 移到 `toId` 的位置。 */
  onMove: (fromId: string, toId: string) => void
  onHide: (id: string) => void
  renderCard: (id: string) => ReactNode
  dragLabel: string
  hideLabel: string
}

/**
 * 首頁「自訂版面」編輯模式的可拖曳卡片網格(2026-10-03)。
 *
 * 單一網格、全域排序(不分區):`rectSortingStrategy` 支援一列兩欄的格狀排序。
 * 卡片本體照常渲染但 `pointer-events-none`,避免誤點;拖曳把手與「隱藏」按鈕在
 * 卡片上方的工具列。PointerSensor 要移動 4px 才啟動(讓按鈕點擊不被當成拖曳),
 * 並支援鍵盤(把手聚焦後空白鍵拾起、方向鍵移動)。
 */
export function SortableCardGrid({ items, onMove, onHide, renderCard, dragLabel, hideLabel }: Props) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    onMove(String(active.id), String(over.id))
  }
  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={items.map((i) => i.id)} strategy={rectSortingStrategy}>
        <div className="grid gap-4 lg:grid-cols-2">
          {items.map((item) => (
            <SortableCardShell
              key={item.id}
              item={item}
              onHide={onHide}
              dragLabel={dragLabel}
              hideLabel={hideLabel}
            >
              {renderCard(item.id)}
            </SortableCardShell>
          ))}
        </div>
      </SortableContext>
    </DndContext>
  )
}

function SortableCardShell({
  item,
  onHide,
  dragLabel,
  hideLabel,
  children
}: {
  item: SortableCardGridItem
  onHide: (id: string) => void
  dragLabel: string
  hideLabel: string
  children: ReactNode
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: item.id })
  return (
    <div
      ref={setNodeRef}
      data-testid={`dash-card-${item.id}`}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.55 : 1,
        zIndex: isDragging ? 20 : undefined
      }}
      className={`min-w-0 rounded-2xl border border-dashed border-primary/50 bg-background/40 p-2 ${
        item.full ? 'lg:col-span-2' : ''
      }`}
    >
      <div className="mb-2 flex items-center gap-2 px-1">
        <button
          type="button"
          aria-label={`${dragLabel}: ${item.label}`}
          {...attributes}
          {...listeners}
          className="flex shrink-0 cursor-grab touch-none items-center gap-1 rounded-md border border-border/60 bg-card px-2 py-1 text-xs text-muted-foreground hover:bg-accent active:cursor-grabbing"
        >
          <GripVertical size={14} />
          <span className="truncate">{dragLabel}</span>
        </button>
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{item.label}</span>
        <button
          type="button"
          aria-label={`${hideLabel}: ${item.label}`}
          onClick={() => onHide(item.id)}
          className="flex shrink-0 items-center gap-1 rounded-md border border-border/60 bg-card px-2 py-1 text-xs text-muted-foreground hover:bg-accent"
        >
          <EyeOff size={14} />
          <span>{hideLabel}</span>
        </button>
      </div>
      <div className="pointer-events-none select-none">{children}</div>
    </div>
  )
}
