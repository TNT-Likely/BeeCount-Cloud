import { authedDelete, authedGet, authedPatch, authedPost } from './http'

export interface Company {
  id: string
  user_id: string
  name: string
  note: string | null
  created_at: string
  updated_at: string
}

export interface CompanyCreateBody {
  name: string
  note?: string | null
}

export interface CompanyUpdateBody {
  name?: string
  note?: string | null
}

/** 报销公司档案：用户级，跨账本复用。 */
export async function fetchCompanies(token: string): Promise<Company[]> {
  return authedGet<Company[]>('/companies', token)
}

export async function createCompany(token: string, body: CompanyCreateBody): Promise<Company> {
  return authedPost<Company>('/companies', token, body as unknown as Record<string, unknown>)
}

export async function updateCompany(
  token: string,
  id: string,
  body: CompanyUpdateBody,
): Promise<Company> {
  return authedPatch<Company>(`/companies/${encodeURIComponent(id)}`, token, body as unknown as Record<string, unknown>)
}

export async function deleteCompany(token: string, id: string): Promise<void> {
  return authedDelete<void>(`/companies/${encodeURIComponent(id)}`, token)
}
