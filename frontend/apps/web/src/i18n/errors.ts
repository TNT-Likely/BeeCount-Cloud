import type { TranslateParams } from '@beecount/ui'
import { ApiError } from '@beecount/api-client'


const ERROR_KEYS: Record<string, string> = {
  AUTH_INVALID_CREDENTIALS: 'error.AUTH_INVALID_CREDENTIALS',
  INTERNAL_ERROR: 'error.INTERNAL_ERROR',
  WRITE_CONFLICT: 'error.WRITE_CONFLICT',
  WRITE_ROLE_FORBIDDEN: 'error.WRITE_ROLE_FORBIDDEN',
  SHARE_ROLE_FORBIDDEN: 'error.SHARE_ROLE_FORBIDDEN',
  SYNC_VIEWER_WRITE_FORBIDDEN: 'error.SYNC_VIEWER_WRITE_FORBIDDEN',
  SYNC_LEDGER_WRITE_FORBIDDEN: 'error.SYNC_LEDGER_WRITE_FORBIDDEN',
  AUTH_INSUFFICIENT_SCOPE: 'error.AUTH_INSUFFICIENT_SCOPE',
  ADMIN_FORBIDDEN: 'error.ADMIN_FORBIDDEN',
  RATE_LIMITED: 'error.RATE_LIMITED',
  LEDGER_ALREADY_EXISTS: 'error.LEDGER_ALREADY_EXISTS',
  ENTITY_NOT_FOUND: 'error.ENTITY_NOT_FOUND',
  WRITE_VALIDATION_FAILED: 'error.WRITE_VALIDATION_FAILED',
  USER_EMAIL_EXISTS: 'error.USER_EMAIL_EXISTS',
  USER_PASSWORD_TOO_SHORT: 'error.USER_PASSWORD_TOO_SHORT',
  SHARE_MEMBER_USER_NOT_FOUND: 'error.SHARE_MEMBER_USER_NOT_FOUND',
  ADMIN_USER_DELETE_SELF_FORBIDDEN: 'error.ADMIN_USER_DELETE_SELF_FORBIDDEN',
  ADMIN_USER_DELETE_LAST_ADMIN_FORBIDDEN: 'error.ADMIN_USER_DELETE_LAST_ADMIN_FORBIDDEN'
}

type Translator = (key: string, params?: TranslateParams) => string

function resolveApiErrorMessage(err: ApiError, t: Translator): string {
  if (!err.code) return err.message
  const key = ERROR_KEYS[err.code] || 'error.default'
  if (err.code === 'WRITE_CONFLICT') {
    return t(key, {
      latestChangeId: err.latestChangeId ?? 'unknown',
      latestServerTimestamp: err.latestServerTimestamp ?? 'unknown'
    })
  }
  return t(key)
}

export function localizeError(err: unknown, t: Translator): string {
  if (err instanceof ApiError) {
    return resolveApiErrorMessage(err, t)
  }
  if (err instanceof Error) return err.message
  return t('error.default')
}
