import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { toPng } from 'html-to-image'
import QRCode from 'qrcode'
import { useT } from '@beecount/ui'

import type { AnnualReportData } from '../data'
import { TKEY } from '../i18n'

const currencySymbol = (code: string) =>
  ({ CNY: '¥', USD: '$', EUR: '€', GBP: '£', JPY: '¥', HKD: 'HK$' } as Record<string, string>)[code] || code + ' '

export type PosterDialogProps = {
  data: AnnualReportData
  open: boolean
  onClose: () => void
  /** 海报底部的 url 文案(也用于二维码),默认当前 origin */
  url?: string
}

/**
 * 海报弹窗:9:16 portrait 海报 + 下载 PNG 按钮。
 * 海报结构:头部 logo + 年份大字 / 中部核心数据 / 下部成就 / 底部二维码。
 */
export function PosterDialog({ data, open, onClose, url }: PosterDialogProps) {
  const t = useT()
  const posterRef = useRef<HTMLDivElement>(null)
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const sym = currencySymbol(data.ledgerCurrency)

  const link = url || (typeof window !== 'undefined' ? window.location.origin : 'https://beecount.app')

  useEffect(() => {
    if (!open) return
    QRCode.toDataURL(link, {
      width: 240,
      margin: 0,
      color: { dark: '#1A1612', light: '#FFFFFF' },
    }).then(setQrDataUrl).catch(() => setQrDataUrl(null))
  }, [open, link])

  const handleDownload = async () => {
    if (!posterRef.current) return
    setDownloading(true)
    try {
      const dataUrl = await toPng(posterRef.current, {
        cacheBust: true,
        pixelRatio: 2,
        backgroundColor: '#1A1612',
      })
      const a = document.createElement('a')
      a.href = dataUrl
      a.download = `beecount-${data.year}-${data.ledgerName.replace(/\s+/g, '-')}.png`
      a.click()
    } catch (e) {
      console.error('[poster] download failed', e)
    } finally {
      setDownloading(false)
    }
  }

  // 取一个分类作为海报亮点
  const topCat = data.topExpenseCategories[0]
  const topCatPct = topCat ? topCat.percent.toFixed(0) : '0'

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-[200] flex items-center justify-center overflow-auto bg-black/80 p-4 backdrop-blur-md"
          onClick={onClose}
        >
          <motion.div
            initial={{ opacity: 0, y: 24, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.96 }}
            transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
            className="flex max-h-full flex-col items-center gap-4"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 海报本体(9:16) */}
            <div
              ref={posterRef}
              className="relative aspect-[9/16] w-[min(72vh,360px)] overflow-hidden rounded-2xl"
              style={{
                background:
                  'linear-gradient(170deg, #2D1F0E 0%, #1A1612 50%, #1F1208 100%)',
              }}
            >
              {/* 装饰光晕 */}
              <div
                className="absolute -top-20 left-1/2 h-72 w-72 -translate-x-1/2 rounded-full opacity-50 blur-3xl"
                style={{ background: 'radial-gradient(circle, #F59E0B, transparent 70%)' }}
              />
              <div
                className="absolute -bottom-32 -right-16 h-72 w-72 rounded-full opacity-40 blur-3xl"
                style={{ background: 'radial-gradient(circle, #FBBF24, transparent 70%)' }}
              />

              <div className="relative z-10 flex h-full flex-col px-6 py-7">
                {/* Header */}
                <div className="flex items-center gap-2">
                  <span className="text-2xl">🐝</span>
                  <div className="flex flex-col">
                    <span className="text-[10px] font-medium uppercase tracking-widest text-amber-300/80">
                      BeeCount Annual
                    </span>
                    <span className="text-xs text-white/60">{data.ledgerName}</span>
                  </div>
                </div>

                {/* 年份大字 */}
                <div className="mt-4">
                  <div
                    className="font-serif text-[5.4rem] font-black leading-none tracking-tight"
                    style={{
                      background:
                        'linear-gradient(135deg, #FCD34D 0%, #F59E0B 50%, #B45309 100%)',
                      WebkitBackgroundClip: 'text',
                      WebkitTextFillColor: 'transparent',
                      backgroundClip: 'text',
                    }}
                  >
                    {data.year}
                  </div>
                  <div className="mt-1 text-sm text-white/50">
                    {t(TKEY.title)}
                  </div>
                </div>

                {/* 数据格 */}
                <div className="mt-6 grid grid-cols-2 gap-4">
                  <PosterStat
                    label={t(TKEY.page2RecordsLabel)}
                    value={data.totalRecords.toString()}
                    color="#FBBF24"
                  />
                  <PosterStat
                    label={t(TKEY.page2DaysLabel)}
                    value={`${data.recordingDays}/${data.totalDays}`}
                    color="#FB923C"
                  />
                  <PosterStat
                    label={t(TKEY.page2ExpenseLabel)}
                    value={`${sym}${Math.round(data.totalExpense).toLocaleString()}`}
                    color="#F87171"
                  />
                  <PosterStat
                    label={t(TKEY.page9StreakLabel)}
                    value={`${data.maxConsecutiveDays} 🔥`}
                    color="#FACC15"
                  />
                </div>

                {/* 亮点行 */}
                {topCat && (
                  <div className="mt-5 rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2.5">
                    <div className="text-[10px] uppercase tracking-widest text-amber-300/70">
                      Top Category
                    </div>
                    <div className="mt-0.5 flex items-baseline justify-between">
                      <span className="text-sm font-bold text-white">
                        {topCat.name}
                      </span>
                      <span className="text-xs text-white/60">{topCatPct}%</span>
                    </div>
                  </div>
                )}

                {/* 成就 */}
                {data.achievements.length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-1.5">
                    {data.achievements.slice(0, 6).map((a) => (
                      <span
                        key={a.id}
                        className={`rounded-full px-2 py-0.5 text-[9px] font-medium ${
                          a.rare
                            ? 'border border-amber-300/40 bg-amber-300/10 text-amber-200'
                            : 'border border-white/15 bg-white/5 text-white/70'
                        }`}
                      >
                        {t(a.titleKey)}
                      </span>
                    ))}
                  </div>
                )}

                <div className="flex-1" />

                {/* 底部 QR 码 */}
                <div className="flex items-end justify-between">
                  <div className="flex flex-col">
                    <span className="text-[10px] font-medium uppercase tracking-widest text-white/40">
                      Powered by
                    </span>
                    <span className="text-sm font-bold text-amber-300">BeeCount</span>
                    <span className="mt-0.5 text-[10px] text-white/40">
                      {link.replace(/^https?:\/\//, '')}
                    </span>
                  </div>
                  {qrDataUrl && (
                    <div className="rounded-md bg-white p-1">
                      <img src={qrDataUrl} alt="QR" className="block h-14 w-14" />
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* 操作按钮 */}
            <div className="flex gap-3">
              <button
                type="button"
                onClick={handleDownload}
                disabled={downloading}
                className="inline-flex items-center gap-2 rounded-full bg-amber-400 px-6 py-2.5 text-sm font-semibold text-black transition hover:bg-amber-300 disabled:opacity-60"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                {downloading ? '...' : t(TKEY.posterDownload)}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/5 px-6 py-2.5 text-sm font-semibold text-white backdrop-blur-sm transition hover:bg-white/10"
              >
                {t(TKEY.closeButton)}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

function PosterStat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-widest text-white/40">{label}</span>
      <span className="text-xl font-bold tabular-nums" style={{ color }}>
        {value}
      </span>
    </div>
  )
}
