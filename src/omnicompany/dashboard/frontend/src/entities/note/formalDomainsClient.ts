export type RevisionRef = { provider: string; object: string; revision: string }

export type AuthorityScope = { object: string; purpose: string; scope: string }

export type FormalDomain = {
  product_ref: { kind: string; ref: string }
  semantic_scope: string
  formal_artifact_kinds: string[]
  authority_scopes: AuthorityScope[]
  responsibility_model: { required_roles: string[] }
  adapter_ref: { kind: string; ref: string }
}

export type FormalDomainSummary = {
  material_id: string
  domain_ref: { material_id: string; revision_ref: RevisionRef }
  formal_domain: FormalDomain
}

export async function listFormalDomains(): Promise<FormalDomainSummary[]> {
  const response = await fetch('/api/boss-sight/material-domains/')
  if (!response.ok) throw new Error(`读取正式域失败：${response.status}`)
  const body = await response.json() as { status?: string; domains?: FormalDomainSummary[]; error?: string }
  if (body.status !== 'ok' || !Array.isArray(body.domains)) {
    throw new Error(body.error || '正式域返回格式无效')
  }
  return body.domains
}

export function formalDomainActionLabel(domain: FormalDomainSummary): string {
  const adapter = domain.formal_domain.adapter_ref.ref
  if (adapter.includes('material-wiki')) return '写入 OmniCompany 设计'
  if (adapter.includes('spark')) return '做进 Spark 游戏'
  if (adapter.includes('bside')) return '发布为 BSide 正式正文'
  return '写入目标产品'
}
