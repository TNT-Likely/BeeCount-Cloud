import { describe, expect, it, vi } from 'vitest'

import { createSingleFlight } from '@beecount/web-features'

describe('createSingleFlight(#450 提交防连点)', () => {
  it('执行未完成时的第二次调用不执行、返回 undefined', async () => {
    let calls = 0
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const guard = createSingleFlight()
    const save = () =>
      guard(async () => {
        calls++
        await gate
        return true
      })

    const first = save()
    const second = save() // 连点:第一次网络请求还没回来

    expect(await second).toBeUndefined()
    release()
    expect(await first).toBe(true)
    expect(calls).toBe(1)
  })

  it('上一次完成后可以再次执行', async () => {
    let calls = 0
    const guard = createSingleFlight()
    const save = () =>
      guard(async () => {
        calls++
        return calls
      })

    expect(await save()).toBe(1)
    expect(await save()).toBe(2)
  })

  it('执行抛错后守卫释放,可以重试', async () => {
    let calls = 0
    const guard = createSingleFlight()
    const save = () =>
      guard(async () => {
        calls++
        if (calls === 1) throw new Error('boom')
        return true
      })

    await expect(save()).rejects.toThrow('boom')
    expect(await save()).toBe(true)
    expect(calls).toBe(2)
  })

  it('inFlight 变化按 true→false 顺序通知(成功路径)', async () => {
    const onInFlightChange = vi.fn()
    const guard = createSingleFlight(onInFlightChange)

    await guard(async () => true)

    expect(onInFlightChange.mock.calls).toEqual([[true], [false]])
  })

  it('inFlight 变化在抛错路径也回到 false', async () => {
    const onInFlightChange = vi.fn()
    const guard = createSingleFlight(onInFlightChange)

    await expect(
      guard(async () => {
        throw new Error('boom')
      }),
    ).rejects.toThrow('boom')
    expect(onInFlightChange.mock.calls).toEqual([[true], [false]])
  })

  it('被挡掉的调用不触发 inFlight 通知', async () => {
    const onInFlightChange = vi.fn()
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const guard = createSingleFlight(onInFlightChange)
    const save = () =>
      guard(async () => {
        await gate
        return true
      })

    const first = save()
    await save() // 被挡

    expect(onInFlightChange.mock.calls).toEqual([[true]])
    release()
    await first
    expect(onInFlightChange.mock.calls).toEqual([[true], [false]])
  })

  it('不同 guard 实例互不影响', async () => {
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const guardA = createSingleFlight()
    const guardB = createSingleFlight()

    const first = guardA(async () => {
      await gate
      return 'a'
    })
    expect(await guardB(async () => 'b')).toBe('b')

    release()
    expect(await first).toBe('a')
  })

  it('同步返回值(非 Promise)也支持', async () => {
    const guard = createSingleFlight()
    expect(await guard(() => false)).toBe(false)
  })
})
