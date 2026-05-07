import { useCallback, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Pencil } from 'lucide-react'

import {
  Button,
  Card,
  CardContent,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
  useToast,
} from '@beecount/ui'
import {
  type ImportFieldMapping,
  type ImportSummary,
  previewImport,
  uploadImport,
} from '@beecount/api-client'

import { FieldMappingDialog } from '../../components/import/FieldMappingDialog'
import { FileDropZone } from '../../components/import/FileDropZone'
import { ImportProgressDialog } from '../../components/import/ImportProgressDialog'
import { ImportStatsCard } from '../../components/import/ImportStatsCard'
import { TransactionsPreviewCard } from '../../components/import/TransactionsPreviewCard'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { localizeError } from '../../i18n/errors'

type Phase = 'idle' | 'uploading' | 'preview' | 'executing'

/**
 * 账本导入页 —— 设计 .docs/web-ledger-import.md
 *
 * 上传后默认显示**预览**:统计数字 + 实际交易(前 10 笔)。**映射默认折叠
 * 成顶部一个小标签**(自动识别 ✓ + 编辑按钮),不满意再点编辑出 dialog 改。
 * 「导入目标 / 冲突策略」做成紧凑一行,不抢主视野。
 */
export function ImportPage() {
  const t = useT()
  const toast = useToast()
  const navigate = useNavigate()
  const { token } = useAuth()
  const { ledgers, activeLedgerId } = useLedgers()
  const [searchParams] = useSearchParams()

  const [phase, setPhase] = useState<Phase>('idle')
  const [summary, setSummary] = useState<ImportSummary | null>(null)
  const [executeOpen, setExecuteOpen] = useState(false)
  const [mappingDialogOpen, setMappingDialogOpen] = useState(false)

  const initialLedger = searchParams.get('ledger') || activeLedgerId || ''

  const handleSelectFile = useCallback(
    async (file: File) => {
      setPhase('uploading')
      try {
        const sum = await uploadImport(token, {
          file,
          targetLedgerId: initialLedger || null,
        })
        setSummary(sum)
        setPhase('preview')
      } catch (err) {
        setPhase('idle')
        toast.error(localizeError(err, t))
      }
    },
    [token, initialLedger, toast, t],
  )

  const refreshPreview = useCallback(
    async (
      patch: {
        mapping?: ImportFieldMapping
        targetLedgerId?: string | null
        dedupStrategy?: 'skip_duplicates' | 'insert_all'
        autoTagNames?: string[]
      },
    ) => {
      if (!summary) return
      setPhase('uploading')
      try {
        const sum = await previewImport(token, summary.import_token, patch)
        setSummary(sum)
        setPhase('preview')
      } catch (err) {
        setPhase('preview')
        toast.error(localizeError(err, t))
      }
    },
    [summary, token, toast, t],
  )

  const handleExecute = () => {
    if (!summary) return
    if (!summary.target_ledger_id) {
      toast.error(t('import.exec.needLedger'))
      return
    }
    if (summary.stats.parse_errors_total > 0) {
      toast.error(t('import.exec.fixErrorsFirst'))
      return
    }
    setExecuteOpen(true)
    setPhase('executing')
  }

  const onSuccess = useCallback(
    (data: { created_tx_count: number; skipped_count: number }) => {
      toast.success(
        t('import.exec.successToast', {
          created: data.created_tx_count,
          skipped: data.skipped_count,
        }),
      )
    },
    [toast, t],
  )

  const requiredComplete = !!(
    summary?.current_mapping.tx_type &&
    summary?.current_mapping.amount &&
    summary?.current_mapping.happened_at
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Button
          size="icon"
          variant="ghost"
          className="h-8 w-8"
          onClick={() => navigate(-1)}
          aria-label={t('common.back') as string}
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h1 className="text-lg font-semibold">{t('import.pageTitle')}</h1>
      </div>

      {phase === 'idle' || (phase === 'uploading' && !summary) ? (
        <Card className="bc-panel">
          <CardContent className="space-y-3 p-6">
            <FileDropZone onSelect={handleSelectFile} disabled={phase === 'uploading'} />
            {phase === 'uploading' ? (
              <p className="text-center text-xs text-muted-foreground">
                {t('import.uploading')}
              </p>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {summary ? (
        <>
          {/* 紧凑信息栏:映射状态徽章(必填不全 → 红色) + 编辑映射按钮。
              「检测到来源」徽章去掉 — sniff 不一定对(用户清洗过的 BeeCount
              文件可能被识别成 alipay),展示反而误导。来源仅做内部分发用。 */}
          <Card className="bc-panel">
            <CardContent className="flex flex-wrap items-center gap-3 p-3 text-xs">
              {requiredComplete ? (
                <span className="rounded-full border border-emerald-500/30 bg-emerald-500/5 px-2 py-0.5 text-emerald-600 dark:text-emerald-400">
                  ✓ {t('import.mappingBadge.auto')}
                </span>
              ) : (
                <span className="rounded-full border border-destructive/40 bg-destructive/5 px-2 py-0.5 text-destructive">
                  ⚠ {t('import.mappingBadge.incomplete')}
                </span>
              )}
              <Button
                size="sm"
                variant="outline"
                className="h-7"
                onClick={() => setMappingDialogOpen(true)}
                disabled={phase === 'uploading'}
              >
                <Pencil className="mr-1 h-3 w-3" />
                {t('import.mappingBadge.edit')}
              </Button>
              <span className="ml-auto text-muted-foreground">
                {t('import.detected.expiresAt', {
                  time: new Date(summary.expires_at).toLocaleTimeString(),
                })}
              </span>
            </CardContent>
          </Card>

          {/* 统计卡 */}
          <ImportStatsCard stats={summary.stats} />

          {/* 实际交易预览(前 10 笔) */}
          <TransactionsPreviewCard
            samples={summary.sample_transactions}
            totalRows={summary.stats.total_rows}
          />

          {/* 紧凑导入目标行 + 主操作 */}
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-border/60 bg-muted/20 px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">
                {t('import.target.ledger')}:
              </span>
              <Select
                value={summary.target_ledger_id || ''}
                onValueChange={(v) =>
                  void refreshPreview({ targetLedgerId: v || null })
                }
                disabled={phase === 'uploading'}
              >
                <SelectTrigger className="h-7 w-[180px] text-xs">
                  <SelectValue placeholder={t('import.target.pickLedger')} />
                </SelectTrigger>
                <SelectContent>
                  {ledgers.map((l) => (
                    <SelectItem key={l.ledger_id} value={l.ledger_id}>
                      {l.ledger_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">
                {t('import.target.dedup')}:
              </span>
              <Select
                value={summary.dedup_strategy}
                onValueChange={(v) =>
                  void refreshPreview({
                    dedupStrategy: v as 'skip_duplicates' | 'insert_all',
                  })
                }
                disabled={phase === 'uploading'}
              >
                <SelectTrigger className="h-7 w-[160px] truncate text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="skip_duplicates">
                    {t('import.target.dedup.skip')}
                  </SelectItem>
                  <SelectItem value="insert_all">
                    {t('import.target.dedup.insertAll')}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSummary(null)
                  setPhase('idle')
                }}
                disabled={phase === 'uploading' || phase === 'executing'}
              >
                {t('common.cancel')}
              </Button>
              <Button
                size="sm"
                onClick={handleExecute}
                disabled={
                  phase === 'uploading' ||
                  phase === 'executing' ||
                  !summary.target_ledger_id ||
                  summary.stats.parse_errors_total > 0
                }
              >
                {t('import.exec.button', { count: summary.stats.total_rows })}
              </Button>
            </div>
          </div>

          <FieldMappingDialog
            open={mappingDialogOpen}
            headers={summary.headers}
            suggestedMapping={summary.suggested_mapping}
            currentMapping={summary.current_mapping}
            saving={phase === 'uploading'}
            onClose={() => setMappingDialogOpen(false)}
            onApply={(mapping) => void refreshPreview({ mapping })}
          />
        </>
      ) : null}

      <ImportProgressDialog
        open={executeOpen}
        importToken={summary?.import_token || null}
        onClose={() => {
          setExecuteOpen(false)
          if (phase === 'executing') {
            navigate('/app/transactions')
          }
        }}
        onSuccess={onSuccess}
      />
    </div>
  )
}
