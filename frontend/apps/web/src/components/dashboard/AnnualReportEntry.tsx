import { useCallback, useEffect, useState } from 'react'

import { fetchWorkspaceLedgerCounts } from '@beecount/api-client'
import {
  AnnualReportPage,
  fetchAnnualReportData,
  type AnnualReportData,
  ANNUAL_REPORT_TKEY as TKEY,
} from '@beecount/web-features'
import { useT } from '@beecount/ui'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'

export type AnnualReportLauncherProps = {
  open: boolean
  onClose: () => void
}

/**
 * 年度报告启动器 — 受控组件,从 Avatar dropdown 触发。
 *
 * 流程:
 *   open=true → 显示「选择年份」mini-dialog → 选年 → 拉数据(loading 态)
 *     → 数据足够 → AnnualReportPage 全屏 carousel
 *     → 数据不足(< 30 笔) → 提示后退出
 *
 * 不在页面上常驻,只在 open 时挂载。Year picker 用 fixed overlay 形式,
 * 避免依赖父级布局。
 */
export function AnnualReportLauncher({ open, onClose }: AnnualReportLauncherProps) {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId, currentLedger, currency } = useLedgers()

  const [phase, setPhase] = useState<'picker' | 'loading' | 'report'>('picker')
  const [data, setData] = useState<AnnualReportData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [yearOptions, setYearOptions] = useState<number[] | null>(null)

  const thisYear = new Date().getFullYear()

  // open 切到 false 时复位
  useEffect(() => {
    if (!open) {
      setPhase('picker')
      setData(null)
      setError(null)
      setYearOptions(null)
    }
  }, [open])

  // 打开时按 first_tx_at 算可选年份范围
  useEffect(() => {
    if (!open || !token || !activeLedgerId) return
    let cancelled = false
    fetchWorkspaceLedgerCounts(token, { ledgerId: activeLedgerId })
      .then((counts) => {
        if (cancelled) return
        const firstYear = counts.first_tx_at
          ? new Date(counts.first_tx_at).getFullYear()
          : thisYear
        const years: number[] = []
        for (let y = thisYear; y >= firstYear; y--) years.push(y)
        setYearOptions(years.length > 0 ? years : [thisYear])
      })
      .catch(() => {
        if (cancelled) return
        // 拉失败时退化成只显示当前年
        setYearOptions([thisYear])
      })
    return () => {
      cancelled = true
    }
  }, [open, token, activeLedgerId, thisYear])

  const handlePick = useCallback(
    async (year: number) => {
      if (!token || !activeLedgerId) return
      setPhase('loading')
      setError(null)
      try {
        const ledger = {
          id: activeLedgerId,
          name: currentLedger?.ledger_name || '',
          currency: currency || 'CNY',
        }
        const d = await fetchAnnualReportData(token, ledger, year)
        if (!d.hasSufficientData) {
          setError(`${t(TKEY.insufficientDataTitle)} — ${t(TKEY.insufficientDataBody)}`)
          setPhase('picker')
          return
        }
        setData(d)
        setPhase('report')
      } catch (e) {
        console.error('[annual-report] fetch failed', e)
        setError(t(TKEY.entryBannerError))
        setPhase('picker')
      }
    },
    [token, activeLedgerId, currentLedger?.ledger_name, currency, t],
  )

  if (!open) return null

  // 报告全屏:覆盖整个页面
  if (phase === 'report' && data) {
    return <AnnualReportPage data={data} onClose={onClose} />
  }

  // Year picker / loading 态:小弹窗
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-sm rounded-2xl border border-border/60 bg-card p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-1 text-base font-semibold text-foreground">
          {t(TKEY.entryBannerTitle).replace(/^🐝\s*/, '')}
        </h3>
        <p className="mb-5 text-xs text-muted-foreground">{t(TKEY.entryBannerSubtitle)}</p>

        {phase === 'loading' ? (
          <div className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
            <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
              <circle cx="12" cy="12" r="9" strokeOpacity="0.3" />
              <path d="M21 12a9 9 0 0 0-9-9" />
            </svg>
            {t(TKEY.entryBannerLoading)}
          </div>
        ) : yearOptions === null ? (
          <div className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
            <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
              <circle cx="12" cy="12" r="9" strokeOpacity="0.3" />
              <path d="M21 12a9 9 0 0 0-9-9" />
            </svg>
          </div>
        ) : (
          <>
            <div className="grid max-h-60 grid-cols-3 gap-2 overflow-y-auto pr-1">
              {yearOptions.map((year, idx) => (
                <button
                  key={year}
                  type="button"
                  onClick={() => handlePick(year)}
                  className={`rounded-xl border px-3 py-3 text-center transition ${
                    idx === 0
                      ? 'border-primary/40 bg-primary/10 text-primary hover:bg-primary/20'
                      : 'border-border/60 text-foreground hover:border-primary/30 hover:bg-primary/10'
                  }`}
                >
                  <div className="text-base font-bold tabular-nums">{year}</div>
                </button>
              ))}
            </div>
            {error && (
              <p className="mt-4 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {error}
              </p>
            )}
            <div className="mt-5 flex justify-end">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg px-4 py-2 text-xs text-muted-foreground hover:bg-muted"
              >
                {t('dialog.cancel')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
