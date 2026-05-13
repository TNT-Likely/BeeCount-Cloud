import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * 浏览器原生 STT(Web Speech API)hook。封装两件烦事:
 *   1. 各家浏览器命名/事件顺序差异(`webkitSpeechRecognition` vs
 *      `SpeechRecognition`,`onspeechend` 跟 `onend` 触发顺序不稳)
 *   2. 静音自动停 / 总时长上限 / cleanup,组件用一个 hook 就拿到完整状态机
 *
 * 隐私:音频**不离开浏览器**(Chrome 会上 Google STT 但跟 BeeCount server
 * 无关),所以 `useSpeechRecognition` 内部不发任何网络请求,也不接受 server
 * 端 fallback —— 想接 Whisper 是另一条路。
 *
 * 设计文档:`.docs/web-voice-input-design.md`。
 */

/** 当前 hook 实例的状态机。 */
export type VoiceState =
  | 'idle' // 还没开始,可以 start
  | 'listening' // 正在录,interim 持续往外推
  | 'denied' // 权限被拒
  | 'error' // 其他错误(网络 / no-speech / aborted-by-system)
  | 'unsupported' // 浏览器没有 Web Speech API

export type SpeechErrorCode =
  | 'not-allowed' // 用户拒绝麦克风
  | 'service-not-allowed' // 浏览器禁了
  | 'no-speech' // 没说话
  | 'audio-capture' // 没 mic
  | 'network' // 在线 STT 网络挂了
  | 'aborted' // 主动 abort
  | 'unknown'

export type UseSpeechRecognitionOptions = {
  /** BCP47,如 `zh-CN` / `zh-TW` / `en-US`,默认 navigator.language */
  lang?: string
  /**
   * 检测到这么久没有新 interim → 自动 stop。默认 1500ms。
   * 设 0 / 负数关闭该机制(只能手动 / max duration 停)。
   */
  silenceTimeoutMs?: number
  /** 录音总时长上限,默认 30000ms。 */
  maxDurationMs?: number
  /** 实时回调,isFinal=true 时 text 是最终段落。 */
  onResult: (text: string, isFinal: boolean) => void
  onError?: (code: SpeechErrorCode) => void
  /** stop / abort 完成后回调(总会跑一次,跟成功/失败无关)。 */
  onEnd?: () => void
}

export type UseSpeechRecognitionReturn = {
  state: VoiceState
  /** 浏览器是否原生支持(用于 silent hide UI) */
  isSupported: boolean
  /** idle → listening,触发权限请求 */
  start: () => void
  /** listening → idle,触发 onResult(final) */
  stop: () => void
  /** listening → idle,**不**触发 onResult(final),用于用户主动取消 */
  cancel: () => void
}

// 类型用 unknown 桥,真用时按需要 cast,避免 TS 报错又不污染全局
type SpeechRecognitionLike = {
  lang: string
  continuous: boolean
  interimResults: boolean
  start(): void
  stop(): void
  abort(): void
  onresult: ((ev: { resultIndex: number; results: SpeechResultListLike }) => void) | null
  onerror: ((ev: { error: string }) => void) | null
  onend: (() => void) | null
}
type SpeechResultListLike = {
  length: number
  [index: number]: { 0: { transcript: string }; isFinal: boolean }
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor
    webkitSpeechRecognition?: SpeechRecognitionCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

function mapErrorCode(raw: string): SpeechErrorCode {
  switch (raw) {
    case 'not-allowed':
    case 'service-not-allowed':
    case 'no-speech':
    case 'audio-capture':
    case 'network':
    case 'aborted':
      return raw
    default:
      return 'unknown'
  }
}

export function useSpeechRecognition(
  opts: UseSpeechRecognitionOptions
): UseSpeechRecognitionReturn {
  const {
    lang,
    silenceTimeoutMs = 1500,
    maxDurationMs = 30000,
    onResult,
    onError,
    onEnd,
  } = opts

  const Ctor = getRecognitionCtor()
  const isSupported = !!Ctor

  const [state, setState] = useState<VoiceState>(isSupported ? 'idle' : 'unsupported')

  // 用 ref 持有可变的运行时对象,避免在回调里关闭旧值
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const maxTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelledRef = useRef(false)
  const onResultRef = useRef(onResult)
  const onErrorRef = useRef(onError)
  const onEndRef = useRef(onEnd)

  // refs 跟最新 props 同步,hook 自身不重新订阅 recognition
  useEffect(() => {
    onResultRef.current = onResult
    onErrorRef.current = onError
    onEndRef.current = onEnd
  })

  const clearTimers = useCallback(() => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current)
      silenceTimerRef.current = null
    }
    if (maxTimerRef.current) {
      clearTimeout(maxTimerRef.current)
      maxTimerRef.current = null
    }
  }, [])

  const cleanup = useCallback(() => {
    clearTimers()
    const r = recognitionRef.current
    if (r) {
      r.onresult = null
      r.onerror = null
      r.onend = null
      recognitionRef.current = null
    }
  }, [clearTimers])

  const start = useCallback(() => {
    if (!Ctor || !isSupported) return
    // 已经在跑就别重入,先 cancel 旧的
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort()
      } catch {
        // ignored
      }
      cleanup()
    }
    cancelledRef.current = false
    const r = new Ctor()
    r.lang = lang || (typeof navigator !== 'undefined' ? navigator.language : 'en-US')
    r.continuous = false
    r.interimResults = true

    const scheduleSilenceStop = () => {
      if (silenceTimeoutMs <= 0) return
      if (silenceTimerRef.current) clearTimeout(silenceTimerRef.current)
      silenceTimerRef.current = setTimeout(() => {
        try {
          r.stop()
        } catch {
          // ignored
        }
      }, silenceTimeoutMs)
    }

    r.onresult = (ev) => {
      let finalText = ''
      let interimText = ''
      for (let i = ev.resultIndex; i < ev.results.length; i += 1) {
        const res = ev.results[i]
        if (res.isFinal) finalText += res[0].transcript
        else interimText += res[0].transcript
      }
      if (interimText) {
        onResultRef.current(interimText, false)
        scheduleSilenceStop()
      }
      if (finalText) {
        onResultRef.current(finalText, true)
      }
    }
    r.onerror = (ev) => {
      const code = mapErrorCode(ev.error)
      if (code === 'not-allowed' || code === 'service-not-allowed') {
        setState('denied')
      } else if (code === 'aborted' && cancelledRef.current) {
        // 用户主动 cancel,不算错误,onend 里恢复 idle
      } else {
        setState('error')
        onErrorRef.current?.(code)
      }
    }
    r.onend = () => {
      clearTimers()
      // 错误态保留,让 UI 显示原因;否则回 idle
      setState((prev) => (prev === 'denied' || prev === 'error' ? prev : 'idle'))
      onEndRef.current?.()
      cleanup()
    }

    recognitionRef.current = r
    try {
      r.start()
      setState('listening')
      // 总时长保险
      if (maxDurationMs > 0) {
        maxTimerRef.current = setTimeout(() => {
          try {
            r.stop()
          } catch {
            // ignored
          }
        }, maxDurationMs)
      }
    } catch {
      // start 抛(权限被拒 / 重复 start)— 退到 idle
      setState('idle')
      cleanup()
    }
  }, [Ctor, isSupported, lang, silenceTimeoutMs, maxDurationMs, cleanup, clearTimers])

  const stop = useCallback(() => {
    const r = recognitionRef.current
    if (!r) return
    try {
      r.stop()
    } catch {
      // ignored
    }
  }, [])

  const cancel = useCallback(() => {
    const r = recognitionRef.current
    if (!r) return
    cancelledRef.current = true
    try {
      r.abort()
    } catch {
      // ignored
    }
  }, [])

  // 组件卸载 / 切语言时彻底 abort,免得后台还在听
  useEffect(() => {
    return () => {
      const r = recognitionRef.current
      if (r) {
        try {
          r.abort()
        } catch {
          // ignored
        }
      }
      cleanup()
    }
  }, [cleanup])

  return { state, isSupported, start, stop, cancel }
}
