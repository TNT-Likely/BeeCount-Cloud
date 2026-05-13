/**
 * Personal Access Token (PAT) API client.
 *
 * PAT 是给 MCP / 外部 LLM 客户端用的长期 token。详见 BeeCount-Cloud 后端
 * `src/routers/pats.py` 和设计文档 `.docs/mcp-server-design.md`。
 *
 * **关键**:`token` 明文只在创建那一刻返回一次,UI 必须立即让用户复制保存,
 * 之后再也拿不到。列表只返 `prefix`(前 14 字符,如 `bcmcp_a1b2c3d4`)。
 */
import { authedDelete, authedGet, authedPatch, authedPost } from './http'

export type PatScope = 'mcp:read' | 'mcp:write'

export interface PatListItem {
  id: string
  name: string
  prefix: string
  scopes: PatScope[]
  expires_at: string | null
  last_used_at: string | null
  last_used_ip: string | null
  created_at: string
  revoked_at: string | null
}

export interface PatCreateRequest {
  name: string
  scopes: PatScope[]
  /** null = 永不过期。默认 90 天。 */
  expires_in_days?: number | null
}

export interface PatCreateResponse {
  id: string
  name: string
  /** 明文 token,仅在 POST 返回一次。 */
  token: string
  prefix: string
  scopes: PatScope[]
  expires_at: string | null
  created_at: string
}

export async function listPats(token: string): Promise<PatListItem[]> {
  return authedGet<PatListItem[]>('/profile/pats', token)
}

export async function createPat(
  token: string,
  payload: PatCreateRequest
): Promise<PatCreateResponse> {
  return authedPost<PatCreateResponse>('/profile/pats', token, payload)
}

/** Update a PAT's name and/or scopes. Cannot update revoked tokens. */
export async function updatePat(
  token: string,
  patId: string,
  payload: { name?: string; scopes?: PatScope[] }
): Promise<PatListItem> {
  return authedPatch<PatListItem>(`/profile/pats/${patId}`, token, payload)
}

/**
 * 双阶段语义:
 * - active token → 软撤销(server 标记 revoked_at,token 立刻失效)
 * - 已撤销 token → 物理删除(行从 DB 抹掉,列表里消失)
 *
 * UI 上一个按钮就能"先撤销再彻底删"两步走,跟 GitHub PAT 同体验。
 */
export async function revokePat(token: string, patId: string): Promise<void> {
  await authedDelete<void>(`/profile/pats/${patId}`, token)
}

// ---------------------------------------------------------------------------
// MCP 调用历史 — 由 server.py `_logged_call` 在每个 tool call 完成后异步写入
// MCPCallLog 表。30 天保留期由 server 后台 task 维护。
// ---------------------------------------------------------------------------

export interface MCPCallItem {
  id: number
  tool_name: string
  /** 'ok' | 'error' */
  status: string
  error_message: string | null
  /** 脱敏后的关键参数摘要,例如 "amount=38, tx_type=expense";note 之类敏感字段不进入 */
  args_summary: string | null
  duration_ms: number
  pat_id: string | null
  pat_prefix: string | null
  /**
   * Server 已经做完降级逻辑的客户端显示名:
   *   1. PAT 还在 → 当前 name(支持改名实时同步)
   *   2. PAT 已删 → 调用当时缓存的名字
   *   3. 都没有 → prefix
   * 前端只渲染这个字段,不再自己挑。
   */
  client_label: string | null
  /** PAT 还在不在;false 时 UI 可加"(已删除)"角标 */
  client_active: boolean
  client_ip: string | null
  /** ISO 时间(带 UTC +00:00),前端按本地 toLocaleString 显示 */
  called_at: string
}

export interface MCPCallListResponse {
  total: number
  items: MCPCallItem[]
}

export interface ListMcpCallsQuery {
  limit?: number
  offset?: number
  tool_name?: string
  /** 'ok' | 'error' */
  status?: string
  pat_id?: string
}

export async function listMcpCalls(
  token: string,
  q: ListMcpCallsQuery = {}
): Promise<MCPCallListResponse> {
  const params = new URLSearchParams()
  if (q.limit != null) params.set('limit', String(q.limit))
  if (q.offset != null) params.set('offset', String(q.offset))
  if (q.tool_name) params.set('tool_name', q.tool_name)
  if (q.status) params.set('status', q.status)
  if (q.pat_id) params.set('pat_id', q.pat_id)
  const qs = params.toString()
  return authedGet<MCPCallListResponse>(`/profile/mcp-calls${qs ? `?${qs}` : ''}`, token)
}
