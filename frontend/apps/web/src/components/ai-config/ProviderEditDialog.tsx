import { useEffect, useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  useT,
} from '@beecount/ui'
import {
  type AIProvider,
  BUILTIN_PROVIDER_ID,
  type TestProviderCapability,
  type TestProviderResult,
  testProvider,
} from '@beecount/api-client'

import { useAuth } from '../../context/AuthContext'

import { ProviderTestButton } from './ProviderTestButton'

interface Props {
  open: boolean
  /** null = 添加新 provider;有值 = 编辑现有 */
  initial: AIProvider | null
  saving?: boolean
  onClose: () => void
  onSave: (next: AIProvider) => Promise<void> | void
}

/**
 * 添加 / 编辑服务商 dialog —— 6 个 input + 每行 model 字段右侧测试按钮 +
 * 底部「一键测试」聚合。
 *
 * 关键约束(§2.0):内置 zhipu_glm 编辑时 `name` 字段 disabled,跟 mobile
 * 行为一致,其它字段都可改。新建场景 isBuiltIn 永远 false,id 用
 * crypto.randomUUID()。
 */
export function ProviderEditDialog({ open, initial, saving = false, onClose, onSave }: Props) {
  const t = useT()
  const { token } = useAuth()
  const isEdit = initial !== null
  const isBuiltIn = initial?.isBuiltIn === true

  const [name, setName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [textModel, setTextModel] = useState('')
  const [visionModel, setVisionModel] = useState('')
  const [audioModel, setAudioModel] = useState('')
  // 测试结果按 capability 缓存,form 改动后清空(测的是旧值,不再有效)
  const [testResults, setTestResults] = useState<Record<TestProviderCapability, TestProviderResult | null>>({
    text: null,
    vision: null,
    speech: null,
  })
  const [runAllStatus, setRunAllStatus] = useState<'idle' | 'running'>('idle')

  // open 切换时重新初始化 form
  useEffect(() => {
    if (!open) return
    setName(initial?.name ?? '')
    setApiKey(initial?.apiKey ?? '')
    setBaseUrl(initial?.baseUrl ?? '')
    setTextModel(initial?.textModel ?? '')
    setVisionModel(initial?.visionModel ?? '')
    setAudioModel(initial?.audioModel ?? '')
    setTestResults({ text: null, vision: null, speech: null })
    setRunAllStatus('idle')
  }, [open, initial])

  // 当前 form 拼出来的 provider snapshot —— 测试按钮用,允许"先填再测再存"
  const draftProvider = useMemo<AIProvider>(
    () => ({
      id: initial?.id || 'draft',
      name: name.trim(),
      isBuiltIn,
      apiKey: apiKey.trim(),
      baseUrl: baseUrl.trim(),
      textModel: textModel.trim(),
      visionModel: visionModel.trim(),
      audioModel: audioModel.trim(),
      createdAt: initial?.createdAt,
    }),
    [initial, name, apiKey, baseUrl, textModel, visionModel, audioModel, isBuiltIn],
  )

  // form 改动 → 清测试结果(旧值不再有效)
  useEffect(() => {
    setTestResults({ text: null, vision: null, speech: null })
  }, [apiKey, baseUrl, textModel, visionModel, audioModel])

  const canSave =
    name.trim().length > 0 && apiKey.trim().length > 0 && baseUrl.trim().length > 0

  const handleSave = async () => {
    if (!canSave || saving) return
    const id =
      initial?.id ||
      (typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `prov_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`)
    const next: AIProvider = {
      id,
      name: name.trim(),
      isBuiltIn,
      apiKey: apiKey.trim(),
      baseUrl: baseUrl.trim(),
      textModel: textModel.trim(),
      visionModel: visionModel.trim(),
      audioModel: audioModel.trim(),
      createdAt: initial?.createdAt || new Date().toISOString(),
    }
    await onSave(next)
  }

  const runAll = async () => {
    setRunAllStatus('running')
    setTestResults({ text: null, vision: null, speech: null })
    // 串行:跑完一个再下一个,避免并发触发上游限流
    const capabilities: TestProviderCapability[] = []
    if (textModel.trim()) capabilities.push('text')
    if (visionModel.trim()) capabilities.push('vision')
    if (audioModel.trim()) capabilities.push('speech')
    if (capabilities.length === 0) {
      setRunAllStatus('idle')
      return
    }
    for (const cap of capabilities) {
      try {
        const r = await testProvider(token, { provider: draftProvider, capability: cap })
        setTestResults((prev) => ({ ...prev, [cap]: r }))
        if (r.success === false && r.error_code === 'AI_TEST_RATE_LIMITED') break
      } catch (err) {
        const errResult: TestProviderResult = {
          success: false,
          error_code: 'AI_TEST_RATE_LIMITED',
          error_message: err instanceof Error ? err.message : String(err),
          latency_ms: 0,
          preview: '',
        }
        setTestResults((prev) => ({ ...prev, [cap]: errResult }))
        break
      }
    }
    setRunAllStatus('idle')
  }

  // 聚合 summary
  const runAllSummary = (() => {
    const tested = (
      ['text', 'vision', 'speech'] as TestProviderCapability[]
    ).filter((c) => testResults[c] !== null)
    if (tested.length === 0) return null
    const passed = tested.filter((c) => testResults[c]?.success).length
    return { tested: tested.length, passed }
  })()

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !saving && onClose()}>
      <DialogContent className="max-w-md gap-0 p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="text-base">
            {isEdit ? t('ai.editor.providers.edit') : t('ai.editor.providers.add')}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3 px-6 py-5">
          <Field label={t('ai.editor.providers.field.name')} required>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={saving || isBuiltIn}
              maxLength={50}
              placeholder={isBuiltIn ? '智谱GLM' : 'My Provider'}
            />
            {isBuiltIn ? (
              <p className="mt-1 text-[10px] text-muted-foreground">
                {t('ai.editor.providers.builtin.locked')}
              </p>
            ) : null}
          </Field>

          <Field label={t('ai.editor.providers.field.apiKey')} required>
            <Input
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              disabled={saving}
              placeholder="sk-…"
              autoComplete="off"
              spellCheck={false}
              className="font-mono text-xs"
            />
          </Field>

          <Field label={t('ai.editor.providers.field.baseUrl')} required>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              disabled={saving || initial?.id === BUILTIN_PROVIDER_ID}
              placeholder="https://api.example.com/v1"
              className="font-mono text-xs"
            />
          </Field>

          {/* model 行 —— 每行右侧 ProviderTestButton */}
          <ModelFieldWithTest
            label={t('ai.editor.providers.field.textModel')}
            value={textModel}
            onChange={setTextModel}
            disabled={saving}
            provider={draftProvider}
            capability="text"
            externalResult={testResults.text}
            externalStatus={resolveStatus(testResults.text, runAllStatus === 'running' && !!textModel.trim() && testResults.text === null)}
            onResult={(cap, r) => setTestResults((prev) => ({ ...prev, [cap]: r }))}
          />
          <ModelFieldWithTest
            label={t('ai.editor.providers.field.visionModel')}
            value={visionModel}
            onChange={setVisionModel}
            disabled={saving}
            provider={draftProvider}
            capability="vision"
            externalResult={testResults.vision}
            externalStatus={resolveStatus(testResults.vision, runAllStatus === 'running' && !!visionModel.trim() && testResults.vision === null)}
            onResult={(cap, r) => setTestResults((prev) => ({ ...prev, [cap]: r }))}
          />
          <ModelFieldWithTest
            label={t('ai.editor.providers.field.audioModel')}
            value={audioModel}
            onChange={setAudioModel}
            disabled={saving}
            provider={draftProvider}
            capability="speech"
            externalResult={testResults.speech}
            externalStatus={resolveStatus(testResults.speech, runAllStatus === 'running' && !!audioModel.trim() && testResults.speech === null)}
            onResult={(cap, r) => setTestResults((prev) => ({ ...prev, [cap]: r }))}
          />

          {/* 一键测试 + 聚合 summary */}
          <div className="flex items-center justify-between rounded-md border border-border/60 bg-muted/20 px-3 py-2">
            <span className="text-[11px] text-muted-foreground">
              {runAllSummary
                ? t('ai.editor.test.runAllSummary', {
                    passed: runAllSummary.passed,
                    total: runAllSummary.tested,
                  })
                : t('ai.editor.test.runAll')}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 text-[11px]"
              onClick={() => void runAll()}
              disabled={
                saving ||
                runAllStatus === 'running' ||
                (!textModel.trim() && !visionModel.trim() && !audioModel.trim())
              }
            >
              {runAllStatus === 'running' ? (
                <>
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                  {t('ai.editor.test.running')}
                </>
              ) : (
                t('ai.editor.test.runAll')
              )}
            </Button>
          </div>
        </div>

        <DialogFooter className="border-t border-border/60 bg-muted/20 px-6 py-3">
          <Button variant="outline" size="sm" onClick={onClose} disabled={saving}>
            {t('common.cancel')}
          </Button>
          <Button size="sm" onClick={() => void handleSave()} disabled={!canSave || saving}>
            {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Field({
  label,
  required,
  children,
}: {
  label: string
  required?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1">
      <Label className="text-[11px]">
        {label}
        {required ? <span className="ml-0.5 text-destructive">*</span> : null}
      </Label>
      {children}
    </div>
  )
}

function ModelFieldWithTest({
  label,
  value,
  onChange,
  disabled,
  provider,
  capability,
  externalStatus,
  externalResult,
  onResult,
}: {
  label: string
  value: string
  onChange: (next: string) => void
  disabled?: boolean
  provider: AIProvider
  capability: TestProviderCapability
  externalStatus?: 'idle' | 'running' | 'success' | 'fail'
  externalResult?: TestProviderResult | null
  onResult: (cap: TestProviderCapability, result: TestProviderResult) => void
}) {
  const t = useT()
  // 测试失败时把错误信息(i18n + 上游 message)直接展示在字段下方,不靠 hover
  const failed = externalResult !== null && externalResult !== undefined && !externalResult.success
  const errorLine = failed ? formatError(t, externalResult) : ''
  return (
    <div className="space-y-1">
      <Label className="text-[11px]">{label}</Label>
      <div className="flex items-center gap-2">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className="flex-1 font-mono text-xs"
          placeholder="(optional)"
        />
        <ProviderTestButton
          provider={provider}
          capability={capability}
          disabled={!value.trim() || !provider.apiKey || !provider.baseUrl}
          externalStatus={externalStatus}
          externalResult={externalResult}
          onResult={onResult}
        />
      </div>
      {failed ? (
        <p className="break-words text-[11px] text-destructive">{errorLine}</p>
      ) : null}
    </div>
  )
}

function formatError(
  t: ReturnType<typeof useT>,
  result: TestProviderResult | null | undefined,
): string {
  if (!result) return ''
  const code = result.error_code
  let localized = ''
  if (code) {
    const key = `ai.editor.test.error.${code}`
    const v = t(key)
    if (v !== key) localized = v as string
  }
  const detail = (result.error_message || '').trim()
  // 优先显示 i18n 文案;detail 拼在后面给排查用(限长避免炸 UI)
  if (localized && detail) {
    return `${localized} — ${detail.slice(0, 200)}`
  }
  return localized || detail.slice(0, 200) || (t('ai.editor.test.error.AI_TEST_UNKNOWN') as string)
}

function resolveStatus(
  result: TestProviderResult | null,
  isRunningExternal: boolean,
): 'idle' | 'running' | 'success' | 'fail' | undefined {
  if (isRunningExternal) return 'running'
  if (result === null) return undefined
  return result.success ? 'success' : 'fail'
}

