import { useCallback } from 'react'
import { Mic, MicOff } from 'lucide-react'

import { useT, useToast } from '@beecount/ui'

import {
  useSpeechRecognition,
  type SpeechErrorCode,
} from '../../hooks/useSpeechRecognition'

type VoiceInputButtonProps = {
  /** BCP47 语种,跟 app locale 关联(`zh-CN` / `zh-TW` / `en-US`) */
  lang: string
  /** interim transcript:实时回写输入框,让用户感觉"边说边出字" */
  onInterim: (text: string) => void
  /** final transcript:最终段落,跟最后一次 interim 内容一致 */
  onFinal: (text: string) => void
  /** 录音开始时调用,业务侧可清空旧 query / 收起 dropdown */
  onStart?: () => void
}

/**
 * ⌘K 搜索框右侧的语音输入小按钮。
 *
 * 行为约定(`.docs/web-voice-input-design.md`):
 *   - 浏览器不支持时仍然渲染按钮,但点击后 toast 提示"不支持" — 让用户知道
 *     这个能力存在,而不是无声消失被以为是 bug
 *   - 点一下开始,点一下停止;Esc 由调用方处理(取消比停止更激进)
 *   - 真正的"把文本填进输入框"由调用方在 `onInterim`/`onFinal` 里完成
 *
 * 不在按钮内部触发"自动 AI 解析" —— 那是用户手动选 action 才做的事。
 */
export function VoiceInputButton({
  lang,
  onInterim,
  onFinal,
  onStart,
}: VoiceInputButtonProps) {
  const t = useT()
  const toast = useToast()

  const handleError = useCallback(
    (code: SpeechErrorCode) => {
      // 'aborted' / 'no-speech' 是常态,不打扰
      if (code === 'aborted' || code === 'no-speech') return
      const key =
        code === 'not-allowed' || code === 'service-not-allowed'
          ? 'voice.error.denied'
          : code === 'audio-capture'
            ? 'voice.error.noMic'
            : code === 'network'
              ? 'voice.error.network'
              : 'voice.error.unknown'
      toast.error(t(key))
    },
    [toast, t],
  )

  const { state, isSupported, start, stop } = useSpeechRecognition({
    lang,
    onResult: (text, isFinal) => {
      if (isFinal) onFinal(text)
      else onInterim(text)
    },
    onError: handleError,
  })

  const listening = state === 'listening'
  const denied = state === 'denied'
  const unsupported = !isSupported

  const handleClick = useCallback(() => {
    if (unsupported) {
      toast.error(t('voice.error.unsupported'))
      return
    }
    if (listening) {
      stop()
      return
    }
    if (denied) {
      toast.error(t('voice.error.denied'))
      return
    }
    onStart?.()
    start()
  }, [unsupported, listening, denied, stop, start, onStart, toast, t])

  const label = unsupported
    ? t('voice.tooltip.unsupported')
    : listening
      ? t('voice.tooltip.stop')
      : denied
        ? t('voice.tooltip.denied')
        : t('voice.tooltip.start')

  return (
    <button
      type="button"
      onClick={handleClick}
      title={label}
      aria-label={label}
      aria-pressed={listening}
      className={
        listening
          ? 'flex h-7 w-7 items-center justify-center rounded-md bg-primary/15 text-primary transition-colors'
          : unsupported
            ? 'flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground/40 hover:text-muted-foreground'
            : denied
              ? 'flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground/60 hover:text-destructive'
              : 'flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors'
      }
    >
      {denied || unsupported ? (
        <MicOff className="h-4 w-4" />
      ) : (
        <Mic className={listening ? 'h-4 w-4 animate-pulse' : 'h-4 w-4'} />
      )}
    </button>
  )
}
