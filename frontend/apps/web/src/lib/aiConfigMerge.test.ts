import { describe, expect, it } from 'vitest'

import { applyDeleteFallback, capabilitiesBoundTo, mergeAiConfig } from './aiConfigMerge'

describe('mergeAiConfig', () => {
  it('returns providers/binding defaults when current and patch are both empty;does NOT default mobile-only fields', () => {
    const next = mergeAiConfig(null, {}) as Record<string, any>
    expect(next.providers).toEqual([])
    expect(next.binding).toEqual({})
    // 关键回归:不给 mobile-only 字段写默认 — 否则会清掉 mobile 端真实值
    expect(next.custom_prompt).toBeUndefined()
    expect(next.strategy).toBeUndefined()
    expect(next.bill_extraction_enabled).toBeUndefined()
    expect(next.use_vision).toBeUndefined()
  })

  it('does NOT wipe mobile-only fields when patching providers (regression test)', () => {
    // 模拟 mobile 刚 push 的 ai_config(server 返给 web),web 再加个 provider
    const current = {
      providers: [],
      binding: {},
      custom_prompt: 'mobile 设的 prompt',
      strategy: 'cloud_first',
      bill_extraction_enabled: true,
      use_vision: true,
    }
    const next = mergeAiConfig(current, {
      providers: [{ id: 'a', name: 'A' }],
    })
    expect(next.providers).toHaveLength(1)
    // 这些字段必须原样保留 — 否则 PATCH 后 mobile 会被覆盖成空
    expect(next.custom_prompt).toBe('mobile 设的 prompt')
    expect(next.strategy).toBe('cloud_first')
    expect(next.bill_extraction_enabled).toBe(true)
    expect(next.use_vision).toBe(true)
  })

  it('does NOT add empty string for missing custom_prompt (would wipe mobile)', () => {
    // 关键场景:current 完全没 custom_prompt 字段(server 此前没存过)
    const current = { providers: [], binding: {} }
    const next = mergeAiConfig(current, { providers: [{ id: 'a', name: 'A' }] }) as Record<string, any>
    // 输出不应该有 custom_prompt: '' — 否则 mobile applyFromServer 会写空
    expect('custom_prompt' in next).toBe(false)
    expect('strategy' in next).toBe(false)
    expect('bill_extraction_enabled' in next).toBe(false)
    expect('use_vision' in next).toBe(false)
  })

  it('preserves current binding / custom_prompt when patching only providers', () => {
    const current = {
      providers: [{ id: 'a', name: 'A', createdAt: '2026-01-01T00:00:00.000Z' }],
      binding: { textProviderId: 'a' },
      custom_prompt: 'hello',
      strategy: 'fast',
      bill_extraction_enabled: true,
      use_vision: true,
    }
    const next = mergeAiConfig(current, {
      providers: [
        { id: 'a', name: 'A', createdAt: '2026-01-01T00:00:00.000Z' },
        { id: 'b', name: 'B', createdAt: '2026-02-01T00:00:00.000Z' },
      ],
    }) as Record<string, any>
    expect(next.providers).toHaveLength(2)
    expect(next.binding).toEqual({ textProviderId: 'a' })
    expect(next.custom_prompt).toBe('hello')
    expect(next.strategy).toBe('fast')
    expect(next.bill_extraction_enabled).toBe(true)
    expect(next.use_vision).toBe(true)
  })

  it('explicit patch field still overrides current', () => {
    const current = { providers: [], binding: {}, custom_prompt: 'old' }
    const next = mergeAiConfig(current, { custom_prompt: 'new' }) as Record<string, any>
    expect(next.custom_prompt).toBe('new')
  })

  it('replaces binding and providers together when both in patch', () => {
    const current = {
      providers: [{ id: 'a', name: 'A' }],
      binding: { textProviderId: 'a' },
      custom_prompt: 'kept',
    }
    const next = mergeAiConfig(current, {
      providers: [{ id: 'b', name: 'B' }],
      binding: { textProviderId: 'b', visionProviderId: 'b' },
    })
    expect(next.providers).toEqual([{ id: 'b', name: 'B' }])
    expect(next.binding).toEqual({ textProviderId: 'b', visionProviderId: 'b' })
    expect(next.custom_prompt).toBe('kept')
  })

  it('passes through unknown future fields from current', () => {
    // 模拟 mobile 加了新字段 future_flag,server 同步给 web,
    // web 改 providers 时该字段不应该被吹掉
    const current = {
      providers: [],
      binding: {},
      custom_prompt: '',
      future_flag: 'experimental',
      another_unknown: { nested: true },
    }
    const next: any = mergeAiConfig(current, { providers: [{ id: 'x', name: 'X' }] })
    expect(next.future_flag).toBe('experimental')
    expect(next.another_unknown).toEqual({ nested: true })
    expect(next.providers).toHaveLength(1)
  })
})

describe('applyDeleteFallback', () => {
  const builtIn = { id: 'zhipu_glm', name: '智谱GLM', isBuiltIn: true }
  const custom1 = { id: 'cust1', name: 'Custom1' }
  const custom2 = { id: 'cust2', name: 'Custom2' }

  it('removes provider and falls back binding to zhipu_glm when bound', () => {
    const providers = [builtIn, custom1, custom2]
    const binding = { textProviderId: 'cust1', visionProviderId: 'cust1', speechProviderId: 'cust2' }
    const result = applyDeleteFallback(providers, binding, 'cust1')
    expect(result).not.toBeNull()
    expect(result!.providers).toHaveLength(2)
    expect(result!.providers.find((p) => p.id === 'cust1')).toBeUndefined()
    expect(result!.binding.textProviderId).toBe('zhipu_glm')
    expect(result!.binding.visionProviderId).toBe('zhipu_glm')
    // speech 没绑 cust1,应该保留 cust2
    expect(result!.binding.speechProviderId).toBe('cust2')
  })

  it('only removes provider when not bound (binding unchanged)', () => {
    const providers = [builtIn, custom1, custom2]
    const binding = { textProviderId: 'zhipu_glm', visionProviderId: 'zhipu_glm', speechProviderId: 'zhipu_glm' }
    const result = applyDeleteFallback(providers, binding, 'cust1')
    expect(result).not.toBeNull()
    expect(result!.providers).toHaveLength(2)
    expect(result!.binding).toEqual(binding)
  })

  it('returns null when target is built-in (hard refuse)', () => {
    const providers = [builtIn, custom1]
    const binding = { textProviderId: 'cust1' }
    const result = applyDeleteFallback(providers, binding, 'zhipu_glm')
    expect(result).toBeNull()
  })

  it('returns null when target id not found (defensive)', () => {
    const providers = [builtIn, custom1]
    const result = applyDeleteFallback(providers, {}, 'does_not_exist')
    expect(result).toBeNull()
  })
})

describe('capabilitiesBoundTo', () => {
  it('returns empty when binding is null', () => {
    expect(capabilitiesBoundTo(null, 'x')).toEqual([])
  })

  it('returns capabilities matching providerId', () => {
    const binding = {
      textProviderId: 'a',
      visionProviderId: 'a',
      speechProviderId: 'b',
    }
    expect(capabilitiesBoundTo(binding, 'a')).toEqual(['text', 'vision'])
    expect(capabilitiesBoundTo(binding, 'b')).toEqual(['speech'])
    expect(capabilitiesBoundTo(binding, 'c')).toEqual([])
  })
})
