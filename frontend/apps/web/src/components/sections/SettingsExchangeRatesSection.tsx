import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, Loader2, Pencil, RefreshCw, RotateCcw, X } from 'lucide-react'

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  useT,
  useToast,
} from '@beecount/ui'
import {
  ApiError,
  deleteExchangeRateOverride,
  fetchExchangeRateOverrides,
  fetchExchangeRates,
  fetchWorkspaceAccounts,
  setExchangeRateOverride,
  type ExchangeRateOverride,
  type ExchangeRatesResponse,
} from '@beecount/api-client'
import { effectiveRateToBase } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'

/**
 * 设置 - 汇率管理小节(挂在主币种选择器同页下方)。
 *
 * - `profileMe.primary_currency` 为空 → 渲染空态提示,**不发任何请求**。
 * - 否则并行拉:base 汇率(/read/exchange-rates) + 手动 override + 账户币种去重。
 * - 每行(使用中币种 ∪ override 币种 − base):展示 `1 quote = rate base`,
 *   优先 manual override,否则代理自动值取倒数(见 effectiveRateToBase);两边
 *   都没有 → 标记「未获取」。可行内编辑改 override,或一键恢复自动。
 *
 * 折算口径只读展示,真正的资产折算卡在 AccountsPage;这里只管理"汇率值本身"。
 */
export function SettingsExchangeRatesSection() {
  const t = useT()
  const toast = useToast()
  const { token, profileMe } = useAuth()
  const base = profileMe?.primary_currency || ''

  const [auto, setAuto] = useState<ExchangeRatesResponse | null>(null)
  const [overrides, setOverrides] = useState<ExchangeRateOverride[]>([])
  const [accountCurrencies, setAccountCurrencies] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  // 行内编辑:正在编辑的 quote 币种 + 草稿值(1 quote = draft base)
  const [editingQuote, setEditingQuote] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [savingQuote, setSavingQuote] = useState<string | null>(null)

  const loadAll = useCallback(async () => {
    if (!base) return
    setLoading(true)
    try {
      const [rates, ovrs, accounts] = await Promise.all([
        fetchExchangeRates(token, base),
        fetchExchangeRateOverrides(token),
        fetchWorkspaceAccounts(token, { limit: 500 }),
      ])
      setAuto(rates)
      setOverrides(ovrs)
      const seen = new Set<string>()
      for (const a of accounts) {
        const cur = (a.currency || '').toUpperCase()
        if (cur) seen.add(cur)
      }
      setAccountCurrencies([...seen])
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setLoading(false)
    }
  }, [base, token, toast, t])

  useEffect(() => {
    void loadAll()
    // base 变化(主币种切换)时整体重拉
  }, [loadAll])

  const reloadOverrides = useCallback(async () => {
    try {
      setOverrides(await fetchExchangeRateOverrides(token))
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    }
  }, [token, toast, t])

  const handleRefreshRates = async () => {
    if (!base || refreshing) return
    setRefreshing(true)
    try {
      setAuto(await fetchExchangeRates(token, base))
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setRefreshing(false)
    }
  }

  // 行集合:使用中币种 ∪ override 币种,去掉 base 自身
  const quotes = useMemo(() => {
    const set = new Set<string>()
    for (const c of accountCurrencies) set.add(c)
    for (const o of overrides) {
      if (o.base_currency === base) set.add(o.quote_currency)
    }
    set.delete(base)
    return [...set].sort()
  }, [accountCurrencies, overrides, base])

  const startEdit = (quote: string) => {
    const eff = effectiveRateToBase(quote, base, auto, overrides)
    setDraft(eff ? String(eff.rate) : '')
    setEditingQuote(quote)
  }
  const cancelEdit = () => {
    setEditingQuote(null)
    setDraft('')
  }

  const submitEdit = async (quote: string) => {
    if (savingQuote) return
    const rate = Number(draft)
    if (!Number.isFinite(rate) || rate <= 0) {
      toast.error(t('accounts.error.balanceInvalid'), t('notice.error'))
      return
    }
    setSavingQuote(quote)
    try {
      await setExchangeRateOverride(token, {
        base_currency: base,
        quote_currency: quote,
        rate: String(rate),
      })
      await reloadOverrides()
      cancelEdit()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSavingQuote(null)
    }
  }

  const resetToAuto = async (quote: string) => {
    if (savingQuote) return
    setSavingQuote(quote)
    try {
      await deleteExchangeRateOverride(token, base, quote)
      await reloadOverrides()
      if (editingQuote === quote) cancelEdit()
    } catch (err) {
      // 404 表示 override 本不存在(已删除或从未设置),视为成功:照常 reload。
      if (err instanceof ApiError && err.status === 404) {
        await reloadOverrides()
        if (editingQuote === quote) cancelEdit()
      } else {
        toast.error(localizeError(err, t), t('notice.error'))
      }
    } finally {
      setSavingQuote(null)
    }
  }

  return (
    <Card className="bc-panel">
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle>{t('rates.title')}</CardTitle>
        {base ? (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            onClick={() => void handleRefreshRates()}
            disabled={refreshing || loading}
          >
            {refreshing ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="mr-1 h-3 w-3" />
            )}
            {t('rates.refresh')}
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {!base ? (
          <p className="rounded-lg border border-dashed border-border/60 bg-muted/20 px-4 py-6 text-center text-sm text-muted-foreground">
            {t('rates.emptyHint')}
          </p>
        ) : loading ? (
          <div className="flex items-center justify-center py-6 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        ) : quotes.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border/60 bg-muted/20 px-4 py-6 text-center text-sm text-muted-foreground">
            {t('rates.emptyHint')}
          </p>
        ) : (
          <div className="space-y-1.5">
            {quotes.map((quote) => {
              const eff = effectiveRateToBase(quote, base, auto, overrides)
              const isEditing = editingQuote === quote
              const isSaving = savingQuote === quote
              return (
                <div
                  key={quote}
                  className="rounded-lg border border-border/60 bg-muted/20 px-4 py-2.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="text-sm font-semibold">{quote}</span>
                      {eff ? (
                        <span className="text-xs text-muted-foreground">
                          1 {quote} = {eff.rate.toPrecision(6)} {base}
                        </span>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          {t('rates.notFetched')}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {eff?.source === 'manual' ? (
                        <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-medium text-primary">
                          {t('rates.sourceManual')}
                        </span>
                      ) : eff?.source === 'auto' ? (
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                          {t('rates.sourceAuto')}
                          {eff.date ? ` · ${t('rates.updatedAt', { date: eff.date })}` : ''}
                        </span>
                      ) : null}
                      {!isEditing ? (
                        <>
                          <Button
                            size="icon"
                            variant="ghost"
                            className="h-7 w-7"
                            aria-label={t('rates.edit') as string}
                            onClick={() => startEdit(quote)}
                            disabled={isSaving}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          {eff?.source === 'manual' ? (
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-7 w-7"
                              aria-label={t('rates.resetToAuto') as string}
                              onClick={() => void resetToAuto(quote)}
                              disabled={isSaving}
                            >
                              {isSaving ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <RotateCcw className="h-3.5 w-3.5" />
                              )}
                            </Button>
                          ) : null}
                        </>
                      ) : null}
                    </div>
                  </div>

                  {isEditing ? (
                    <div className="mt-2 space-y-1.5">
                      <div className="flex items-center gap-2">
                        <span className="shrink-0 text-xs text-muted-foreground">
                          1 {quote} =
                        </span>
                        <Input
                          autoFocus
                          type="number"
                          inputMode="decimal"
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault()
                              void submitEdit(quote)
                            } else if (e.key === 'Escape') {
                              e.preventDefault()
                              cancelEdit()
                            }
                          }}
                          className="h-8 max-w-[160px] text-sm"
                          disabled={isSaving}
                        />
                        <span className="shrink-0 text-xs text-muted-foreground">{base}</span>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7"
                          aria-label={t('common.save') as string}
                          onClick={() => void submitEdit(quote)}
                          disabled={isSaving}
                        >
                          {isSaving ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Check className="h-3.5 w-3.5" />
                          )}
                        </Button>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7"
                          aria-label={t('common.cancel') as string}
                          onClick={cancelEdit}
                          disabled={isSaving}
                        >
                          <X className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                      {(() => {
                        const r = Number(draft)
                        if (!Number.isFinite(r) || r <= 0) return null
                        return (
                          <p className="text-[11px] text-muted-foreground">
                            {t('rates.inverseHint', {
                              base,
                              rate: (1 / r).toPrecision(6),
                              quote,
                            })}
                          </p>
                        )
                      })()}
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        )}

        {base ? (
          <p className="pt-1 text-[11px] leading-relaxed text-muted-foreground">
            {t('rates.disclaimer')}
          </p>
        ) : null}
      </CardContent>
    </Card>
  )
}
