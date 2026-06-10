import { authedDelete, authedGet, authedPut } from './http'
import type { ExchangeRateOverride, ExchangeRatesResponse } from './types'

export async function fetchExchangeRates(token: string, base: string): Promise<ExchangeRatesResponse> {
  return authedGet<ExchangeRatesResponse>(`/read/exchange-rates?base=${encodeURIComponent(base)}`, token)
}

export async function fetchExchangeRateOverrides(token: string): Promise<ExchangeRateOverride[]> {
  return authedGet<ExchangeRateOverride[]>('/read/exchange-rate-overrides', token)
}

export async function setExchangeRateOverride(
  token: string,
  payload: { base_currency: string; quote_currency: string; rate: string }
): Promise<{ sync_id: string }> {
  return authedPut<{ sync_id: string }>('/write/exchange-rate-overrides', token, payload)
}

export async function deleteExchangeRateOverride(
  token: string, baseCurrency: string, quoteCurrency: string
): Promise<{ sync_id: string }> {
  return authedDelete<{ sync_id: string }>(
    `/write/exchange-rate-overrides?base_currency=${encodeURIComponent(baseCurrency)}&quote_currency=${encodeURIComponent(quoteCurrency)}`,
    token
  )
}
