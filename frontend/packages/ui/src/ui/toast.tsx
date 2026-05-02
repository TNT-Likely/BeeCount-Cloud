import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { cn } from '../lib/cn'

type ToastVariant = 'default' | 'success' | 'error' | 'warning' | 'info'

export interface ToastItem {
  id: string
  title?: string
  description: string
  variant: ToastVariant
  durationMs: number
}

interface ToastContextValue {
  show: (item: Omit<ToastItem, 'id'>) => string
  dismiss: (id: string) => void
  success: (description: string, title?: string) => string
  error: (description: string, title?: string) => string
  warning: (description: string, title?: string) => string
  info: (description: string, title?: string) => string
}

const ToastContext = createContext<ToastContextValue | null>(null)

const DEFAULT_DURATION: Record<ToastVariant, number> = {
  default: 3000,
  success: 3000,
  info: 3500,
  warning: 5000,
  error: 6000
}

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  const dismiss = useCallback((id: string) => {
    const t = timersRef.current.get(id)
    if (t) {
      clearTimeout(t)
      timersRef.current.delete(id)
    }
    setToasts((prev) => prev.filter((it) => it.id !== id))
  }, [])

  const show = useCallback(
    (item: Omit<ToastItem, 'id'>): string => {
      const id = makeId()
      const next: ToastItem = { id, ...item }
      setToasts((prev) => [...prev, next])
      const timer = setTimeout(() => dismiss(id), item.durationMs)
      timersRef.current.set(id, timer)
      return id
    },
    [dismiss]
  )

  useEffect(() => {
    const timers = timersRef.current
    return () => {
      timers.forEach((t) => clearTimeout(t))
      timers.clear()
    }
  }, [])

  const helpers: ToastContextValue = {
    show,
    dismiss,
    success: (description, title) =>
      show({ description, title, variant: 'success', durationMs: DEFAULT_DURATION.success }),
    error: (description, title) =>
      show({ description, title, variant: 'error', durationMs: DEFAULT_DURATION.error }),
    warning: (description, title) =>
      show({ description, title, variant: 'warning', durationMs: DEFAULT_DURATION.warning }),
    info: (description, title) =>
      show({ description, title, variant: 'info', durationMs: DEFAULT_DURATION.info })
  }

  return (
    <ToastContext.Provider value={helpers}>
      {children}
      <div className="pointer-events-none fixed right-4 top-4 z-[100] flex w-80 flex-col gap-2 sm:right-6 sm:top-6">
        {toasts.map((t) => (
          <ToastView key={t.id} item={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

function ToastView({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  return (
    <div
      role={item.variant === 'error' ? 'alert' : 'status'}
      className={cn(
        // 不透明 — 之前 dark 用 /40 alpha 让背景透出来,在深色 UI 上文字
        // 跟下层内容混在一起难看 + 在浅色截图上更糟。改成全不透明。
        'pointer-events-auto animate-in slide-in-from-right-4 rounded-md border px-4 py-3 shadow-lg',
        'bg-card text-card-foreground',
        item.variant === 'success' && 'border-emerald-500/60 bg-emerald-50 text-emerald-950 dark:border-emerald-400/60 dark:bg-emerald-900 dark:text-emerald-50',
        item.variant === 'error' && 'border-red-500/60 bg-red-50 text-red-950 dark:border-red-400/60 dark:bg-red-900 dark:text-red-50',
        item.variant === 'warning' && 'border-amber-500/60 bg-amber-50 text-amber-950 dark:border-amber-400/60 dark:bg-amber-900 dark:text-amber-50',
        item.variant === 'info' && 'border-blue-500/60 bg-blue-50 text-blue-950 dark:border-blue-400/60 dark:bg-blue-900 dark:text-blue-50'
      )}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          {item.title && <div className="mb-0.5 text-sm font-semibold">{item.title}</div>}
          <div className="break-words text-sm leading-snug">{item.description}</div>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 rounded p-0.5 text-current/70 transition hover:text-current focus:outline-none"
          aria-label="dismiss"
        >
          ×
        </button>
      </div>
    </div>
  )
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    throw new Error('useToast() must be used inside <ToastProvider>')
  }
  return ctx
}
