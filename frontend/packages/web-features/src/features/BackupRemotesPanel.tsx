import { useEffect, useState } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
} from '@beecount/ui'

import type {
  BackupRemote,
  BackupRemoteCreatePayload,
  BackupRemoteUpdatePayload,
} from '@beecount/api-client'

import { ConfirmDialog } from '../components/ConfirmDialog'

/**
 * 生成 base64-url 安全的随机密语,默认 32 字符。
 * 用 crypto.getRandomValues 拿真随机字节,base64 编码后转成 url-safe。
 */
function generateRandomPassphrase(length = 32): string {
  // base64 每 3 字节产 4 字符 — 算需要多少字节
  const byteCount = Math.ceil((length * 3) / 4)
  const bytes = new Uint8Array(byteCount)
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < byteCount; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  let s = ''
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i])
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '').slice(0, length)
}

/**
 * 字段类型:
 *   - text: 普通输入框
 *   - password: 敏感字段(后端会 obscure 后落 rclone.conf)
 *   - select: 下拉单选(s3 provider 之类的预定义枚举)
 */
type FieldSpec = {
  key: string
  label: string
  placeholder?: string
  hint?: string
  /** 'password' = 敏感(编辑时不回填,留空表示"不修改") */
  type?: 'text' | 'password' | 'select'
  /** type='select' 时的下拉项 */
  options?: Array<{ value: string; label: string }>
  /** 仅在 condition 字段(同 form 内)等于某值时显示。简单的 single-key
   *  匹配,够用。 */
  showWhen?: { key: string; equals: string | string[] }
}

// rclone s3 backend 支持的 provider 列表 — 不是自由文本,具体值要求 rclone
// 内部能识别(参考 rclone source: backend/s3/s3.go 的 providers map)。
const S3_PROVIDERS: Array<{ value: string; label: string }> = [
  { value: 'AWS', label: 'AWS' },
  { value: 'Cloudflare', label: 'Cloudflare R2' },
  { value: 'Alibaba', label: '阿里云 OSS' },
  { value: 'Tencent', label: '腾讯云 COS' },
  { value: 'Backblaze', label: 'Backblaze B2 (S3 API)' },
  { value: 'Wasabi', label: 'Wasabi' },
  { value: 'DigitalOcean', label: 'DigitalOcean Spaces' },
  { value: 'Minio', label: 'MinIO 自建' },
  { value: 'IBMCOS', label: 'IBM COS' },
  { value: 'Other', label: '其它(自定义 endpoint)' },
]

const BACKEND_FIELDS: Record<string, FieldSpec[]> = {
  s3: [
    {
      key: 'provider',
      label: 'Provider',
      type: 'select',
      options: S3_PROVIDERS,
      hint: 'R2 选 Cloudflare;阿里云 OSS 选 Alibaba;自建 MinIO 选 Minio;不在列表里选 Other 然后填 endpoint。',
    },
    { key: 'access_key_id', label: 'Access Key ID' },
    { key: 'secret_access_key', label: 'Secret Access Key', type: 'password' },
    {
      key: 'bucket',
      label: 'Bucket',
      placeholder: 'my-backup-bucket',
      hint: '存储桶名称。备份文件会落到 <bucket>/<timestamp>.tar.gz。R2 / S3 必填。',
    },
    {
      key: 'region',
      label: 'Region',
      placeholder: 'us-east-1 / 留空(R2 / MinIO 不需要)',
      hint: 'AWS 必填(如 us-east-1 / ap-east-1)。R2 可填 auto 也可留空。MinIO / Other 通常不需要。',
    },
    {
      key: 'endpoint',
      label: 'Endpoint',
      placeholder: 'https://<account-id>.r2.cloudflarestorage.com',
      hint: 'R2:https://<account-id>.r2.cloudflarestorage.com。AWS 留空(自动选 region)。MinIO:你的服务器地址。',
      showWhen: {
        key: 'provider',
        equals: ['Cloudflare', 'Alibaba', 'Tencent', 'Wasabi', 'DigitalOcean', 'Minio', 'IBMCOS', 'Other'],
      },
    },
  ],
  b2: [
    { key: 'account', label: 'Account ID' },
    { key: 'key', label: 'Application Key', type: 'password' },
    {
      key: 'bucket',
      label: 'Bucket',
      placeholder: 'my-backup-bucket',
      hint: '存储桶名称,备份文件会落到 <bucket>/<timestamp>.tar.gz。',
    },
  ],
  drive: [
    { key: 'client_id', label: 'Client ID' },
    { key: 'client_secret', label: 'Client Secret', type: 'password' },
    { key: 'token', label: 'OAuth Token (JSON)', type: 'password' },
  ],
  onedrive: [
    { key: 'client_id', label: 'Client ID' },
    { key: 'client_secret', label: 'Client Secret', type: 'password' },
    { key: 'token', label: 'OAuth Token (JSON)', type: 'password' },
  ],
  dropbox: [
    { key: 'client_id', label: 'Client ID' },
    { key: 'client_secret', label: 'Client Secret', type: 'password' },
    { key: 'token', label: 'OAuth Token (JSON)', type: 'password' },
  ],
  webdav: [
    { key: 'url', label: 'URL', placeholder: 'https://example.com/dav' },
    { key: 'vendor', label: 'Vendor', placeholder: 'nextcloud / owncloud / other' },
    { key: 'user', label: 'User' },
    { key: 'pass', label: 'Password', type: 'password' },
  ],
  sftp: [
    { key: 'host', label: 'Host' },
    { key: 'port', label: 'Port', placeholder: '22' },
    { key: 'user', label: 'User' },
    { key: 'pass', label: 'Password', type: 'password' },
  ],
  ftp: [
    { key: 'host', label: 'Host' },
    { key: 'port', label: 'Port', placeholder: '21' },
    { key: 'user', label: 'User' },
    { key: 'pass', label: 'Password', type: 'password' },
  ],
  alias: [
    { key: 'remote', label: 'Target remote', placeholder: 'other-remote:path' },
  ],
  local: [],
}

const BACKEND_LABELS: Record<string, string> = {
  s3: 'S3 兼容(AWS / Cloudflare R2 / Aliyun OSS / MinIO ...)',
  b2: 'Backblaze B2',
  drive: 'Google Drive',
  onedrive: 'OneDrive',
  dropbox: 'Dropbox',
  webdav: 'WebDAV (Nextcloud / 自建)',
  sftp: 'SFTP',
  ftp: 'FTP',
  alias: 'Alias (path 别名)',
  local: 'Local 路径(同机)',
}

type Props = {
  remotes: BackupRemote[]
  onCreate: (payload: BackupRemoteCreatePayload) => Promise<boolean>
  onUpdate: (id: number, payload: BackupRemoteUpdatePayload) => Promise<boolean>
  onTest: (id: number) => Promise<void>
  onDelete: (id: number) => Promise<void>
  /** 编辑前拉明文配置(含敏感字段)— 让用户能看到、改 secret_access_key 等。 */
  onReveal?: (id: number) => Promise<Record<string, string>>
}

export function BackupRemotesPanel({
  remotes,
  onCreate,
  onUpdate,
  onTest,
  onDelete,
  onReveal,
}: Props) {
  const t = useT()
  const [editingRemote, setEditingRemote] = useState<BackupRemote | null | 'new'>(null)
  const [pendingDelete, setPendingDelete] = useState<BackupRemote | null>(null)
  const [deleting, setDeleting] = useState(false)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          {t('backup.remote.desc')}
        </p>
        <Button size="sm" onClick={() => setEditingRemote('new')}>
          {t('backup.remote.button.create')}
        </Button>
      </div>

      {remotes.length === 0 ? (
        <EmptyState
          icon={
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
          }
          title={t('backup.remote.empty.title')}
          description={t('backup.remote.empty.desc')}
        />
      ) : (
        <div className="space-y-2">
          {remotes.map((r) => (
            <div
              key={r.id}
              className="flex items-center justify-between rounded-md border border-border/60 bg-card p-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{r.name}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-mono">
                    {r.backend_type}
                  </span>
                  {r.encrypted ? (
                    <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-600 dark:text-emerald-400">
                      🔒 {t('backup.remote.encrypted')}
                    </span>
                  ) : null}
                  {r.last_test_ok === true ? (
                    <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-600 dark:text-emerald-400">
                      ✓ {t('backup.remote.testOk')}
                    </span>
                  ) : r.last_test_ok === false ? (
                    <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-600 dark:text-red-400">
                      ✗ {t('backup.remote.testFail')}
                    </span>
                  ) : null}
                </div>
                {r.last_test_error ? (
                  <div className="mt-0.5 truncate text-[11px] text-red-500">
                    {r.last_test_error}
                  </div>
                ) : null}
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void onTest(r.id)}
                  className="h-7 px-2 text-xs"
                >
                  {t('backup.remote.button.test')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditingRemote(r)}
                  className="h-7 px-2 text-xs"
                >
                  {t('common.edit')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setPendingDelete(r)}
                  className="h-7 px-2 text-xs text-red-500"
                >
                  {t('common.delete')}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <RemoteEditDialog
        open={editingRemote !== null}
        existing={editingRemote === 'new' ? null : editingRemote}
        onClose={() => setEditingRemote(null)}
        onCreate={onCreate}
        onUpdate={onUpdate}
        onReveal={onReveal}
      />
      <ConfirmDialog
        open={pendingDelete !== null}
        loading={deleting}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={async () => {
          if (!pendingDelete) return
          setDeleting(true)
          try {
            await onDelete(pendingDelete.id)
            setPendingDelete(null)
          } finally {
            setDeleting(false)
          }
        }}
        title={t('backup.remote.delete.title')}
        description={t('backup.remote.delete.confirm', { name: pendingDelete?.name || '' })}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}

function RemoteEditDialog({
  open,
  existing,
  onClose,
  onCreate,
  onUpdate,
  onReveal,
}: {
  open: boolean
  existing: BackupRemote | null
  onClose: () => void
  onCreate: (payload: BackupRemoteCreatePayload) => Promise<boolean>
  onUpdate: (id: number, payload: BackupRemoteUpdatePayload) => Promise<boolean>
  onReveal?: (id: number) => Promise<Record<string, string>>
}) {
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        {open ? (
          <RemoteEditForm
            key={existing ? `edit-${existing.id}` : 'new'}
            existing={existing}
            onClose={onClose}
            onCreate={onCreate}
            onUpdate={onUpdate}
            onReveal={onReveal}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function RemoteEditForm({
  existing,
  onClose,
  onCreate,
  onUpdate,
  onReveal,
}: {
  existing: BackupRemote | null
  onClose: () => void
  onCreate: (payload: BackupRemoteCreatePayload) => Promise<boolean>
  onUpdate: (id: number, payload: BackupRemoteUpdatePayload) => Promise<boolean>
  onReveal?: (id: number) => Promise<Record<string, string>>
}) {
  const t = useT()
  const isEdit = existing !== null
  const [name, setName] = useState(existing?.name || '')
  const [backend, setBackend] = useState(existing?.backend_type || 's3')
  // 编辑模式下,先用 config_summary 里非敏感字段做初值;然后异步 reveal
  // 把敏感字段(secret_access_key 等)填进来。这样 dialog 一开就能看见 + 改。
  const initialConfig = (() => {
    if (!existing?.config_summary) return {}
    const out: Record<string, string> = {}
    for (const [k, v] of Object.entries(existing.config_summary)) {
      if (typeof v === 'string' && v !== '******') {
        out[k] = v
      } else if (typeof v === 'number') {
        out[k] = String(v)
      }
    }
    return out
  })()
  const sensitivePresent = new Set<string>()
  if (existing?.config_summary) {
    for (const [k, v] of Object.entries(existing.config_summary)) {
      if (v === '******') sensitivePresent.add(k)
    }
  }
  const [config, setConfig] = useState<Record<string, string>>(initialConfig)
  const [revealedKeys, setRevealedKeys] = useState<Set<string>>(new Set())
  const [revealing, setRevealing] = useState(false)
  // 新建时默认勾选 — 不加密的备份等于裸 tar.gz 直接对象存储,泄露场景
  // 一秒钟解压看光所有数据。强烈不推荐裸传,所以默认 true。
  // 编辑现有 remote 时保留它原来的设置。
  // Boolean() 防止 server 返 0/null/undefined 这类 falsy 但非 boolean 值
  // 让 input 误判 — 之前 ?? false 会让 existing.encrypted=0 也走 false 但
  // checkbox 不会 checked。
  const [encrypted, setEncrypted] = useState(
    existing ? Boolean(existing.encrypted) : true,
  )
  // 新建时给一个高熵随机密语 — 32 字符 base64-ish。用户能看见 + 改 + 复制
  // 到密码管理器。比 placeholder 强:placeholder 是"提示让你填",这个是
  // "已经填好了,你只需要保存它"。这个 passphrase 给 age 加密 tarball 用。
  const [agePass, setAgePass] = useState(() =>
    existing ? '' : generateRandomPassphrase(32),
  )
  const [showAgePass, setShowAgePass] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  // 进 edit 模式时,server reveal 拿明文 secret + age_passphrase 回填表单
  useEffect(() => {
    if (!existing || !onReveal) return
    let cancelled = false
    setRevealing(true)
    onReveal(existing.id)
      .then((revealed) => {
        if (cancelled) return
        const next: Record<string, string> = {}
        const revealedSet = new Set<string>()
        for (const [k, v] of Object.entries(revealed)) {
          if (k === 'age_passphrase' || k === '__encrypted__') continue
          next[k] = v ?? ''
          revealedSet.add(k)
        }
        if ('__encrypted__' in revealed) {
          setEncrypted(Boolean(revealed.__encrypted__))
        }
        setConfig(next)
        setRevealedKeys(revealedSet)
        if (typeof revealed.age_passphrase === 'string') {
          setAgePass(revealed.age_passphrase)
        }
      })
      .catch(() => {
        // reveal 失败(网络 / rclone 二进制问题)— 保留 initialConfig
      })
      .finally(() => {
        if (!cancelled) setRevealing(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existing?.id])

  useEffect(() => {
    if (existing) setBackend(existing.backend_type)
  }, [existing])

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      let ok = false
      if (existing) {
        // 编辑模式:发送所有当前 form 字段(reveal 后是明文)+ encrypted
        // 切换状态。server 重新写 rclone.conf,等价于"全量替换"。
        const payload: BackupRemoteUpdatePayload = {
          config,
          encrypted,
        }
        if (agePass) payload.age_passphrase = agePass
        ok = await onUpdate(existing.id, payload)
      } else {
        ok = await onCreate({
          name: name.trim(),
          backend_type: backend,
          config,
          encrypted,
          age_passphrase: encrypted ? agePass || null : null,
        })
      }
      if (ok) onClose()
    } finally {
      setSubmitting(false)
    }
  }

  const fields = (BACKEND_FIELDS[backend] || []).filter((f) => {
    if (!f.showWhen) return true
    const cur = config[f.showWhen.key]
    if (Array.isArray(f.showWhen.equals)) {
      return f.showWhen.equals.includes(cur || '')
    }
    return cur === f.showWhen.equals
  })

  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {isEdit
            ? t('backup.remote.edit.title')
            : t('backup.remote.create.title')}
        </DialogTitle>
      </DialogHeader>
      <div className="-mx-6 max-h-[70vh] space-y-3 overflow-y-auto px-6 [scrollbar-gutter:stable]">
        <div className="space-y-1">
          <Label>{t('backup.remote.field.name')}</Label>
          <Input
            placeholder={t('backup.remote.field.namePlaceholder')}
            value={name}
            disabled={isEdit}
            onChange={(e) => setName(e.target.value)}
          />
          <p className="text-[11px] text-muted-foreground">
            {isEdit
              ? t('backup.remote.field.nameLockedHint')
              : t('backup.remote.field.nameHint')}
          </p>
        </div>
        <div className="space-y-1">
          <Label>{t('backup.remote.field.backend')}</Label>
          <Select
            value={backend}
            disabled={isEdit}
            onValueChange={(v) => {
              setBackend(v)
              setConfig({})
            }}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(BACKEND_LABELS).map(([k, label]) => (
                <SelectItem key={k} value={k}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {isEdit ? (
            <p className="text-[11px] text-muted-foreground">
              {t('backup.remote.field.backendLockedHint')}
            </p>
          ) : null}
        </div>
        {isEdit && revealing ? (
          <p className="rounded-md border border-blue-500/30 bg-blue-500/5 px-2 py-1.5 text-[11px] text-blue-700 dark:text-blue-300">
            {t('backup.remote.field.revealing')}
          </p>
        ) : null}
        {fields.map((f) => {
          const isSensitive = f.type === 'password'
          // 编辑模式:reveal 已成功,展示明文(text input);未 reveal 或失败时
          // 仍然 password input + 留空 = 保持原值。新建模式:始终 password。
          const showAsText = isEdit && isSensitive && revealedKeys.has(f.key)
          const inputType = isSensitive
            ? showAsText
              ? 'text'
              : 'password'
            : 'text'
          return (
            <div key={f.key} className="space-y-1">
              <div className="flex items-center justify-between">
                <Label>{f.label}</Label>
                {isEdit && isSensitive && sensitivePresent.has(f.key) && !revealedKeys.has(f.key) ? (
                  <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-600 dark:text-emerald-400">
                    ✓ {t('backup.remote.field.alreadySet')}
                  </span>
                ) : null}
              </div>
              {f.type === 'select' ? (
                <Select
                  value={config[f.key] || ''}
                  onValueChange={(v) =>
                    setConfig((s) => ({ ...s, [f.key]: v }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder={f.placeholder} />
                  </SelectTrigger>
                  <SelectContent>
                    {(f.options || []).map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Input
                  type={inputType}
                  placeholder={
                    isEdit && isSensitive && !revealedKeys.has(f.key) && sensitivePresent.has(f.key)
                      ? t('backup.remote.field.passwordEditPlaceholder')
                      : f.placeholder
                  }
                  value={config[f.key] || ''}
                  onChange={(e) =>
                    setConfig((s) => ({ ...s, [f.key]: e.target.value }))
                  }
                />
              )}
              {f.hint ? (
                <p className="text-[10px] text-muted-foreground">{f.hint}</p>
              ) : null}
            </div>
          )
        })}

        {/* 加密 toggle:tar.gz 走 age 加密(passphrase 模式)。用户可以
            脱离 BeeCount 用标准 age 工具自助解密恢复。 */}
        <div className="space-y-1 rounded-md border border-emerald-500/30 bg-emerald-500/5 p-2.5">
          <label className="flex items-start gap-2 text-xs">
            <input
              type="checkbox"
              checked={encrypted}
              onChange={(e) => setEncrypted(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span className="font-medium text-emerald-700 dark:text-emerald-300">
                {t('backup.remote.crypt.enableExtra')}
              </span>
              <span className="ml-1 text-muted-foreground">
                {t('backup.remote.crypt.enableExtraHint')}
              </span>
            </span>
          </label>
          {!encrypted ? (
            <p className="ml-6 rounded bg-red-500/10 px-2 py-1 text-[11px] text-red-700 dark:text-red-300">
              ⚠️ {t('backup.remote.crypt.disabledWarn')}
            </p>
          ) : null}
        </div>

        {encrypted ? (
          <div className="space-y-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-300">
                {t('backup.remote.crypt.title')}
              </p>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[10px]"
                  onClick={() => setShowAgePass((v) => !v)}
                >
                  {showAgePass
                    ? t('backup.remote.crypt.hide')
                    : t('backup.remote.crypt.show')}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-6 px-2 text-[10px]"
                  onClick={() => setAgePass(generateRandomPassphrase(32))}
                >
                  {t('backup.remote.crypt.regenerate')}
                </Button>
              </div>
            </div>
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              {t('backup.remote.crypt.warn')}
            </p>
            <div className="space-y-1">
              <Label>{t('backup.remote.crypt.password')}</Label>
              <Input
                type={showAgePass ? 'text' : 'password'}
                value={agePass}
                onChange={(e) => setAgePass(e.target.value)}
                className="font-mono"
              />
              <p className="text-[10px] text-muted-foreground">
                {t('backup.remote.crypt.passwordHint')}
              </p>
            </div>
          </div>
        ) : null}

      </div>
      <DialogFooter>
        <Button variant="outline" disabled={submitting} onClick={onClose}>
          {t('dialog.cancel')}
        </Button>
        <Button disabled={submitting} onClick={() => void handleSubmit()}>
          {isEdit
            ? t('common.save')
            : t('backup.remote.button.create')}
        </Button>
      </DialogFooter>
    </>
  )
}
