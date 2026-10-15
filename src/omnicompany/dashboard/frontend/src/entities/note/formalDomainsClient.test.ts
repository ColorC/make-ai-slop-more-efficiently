import { afterEach, describe, expect, it, vi } from 'vitest'
import { formalDomainActionLabel, listFormalDomains, type FormalDomainSummary } from './formalDomainsClient'

const domain: FormalDomainSummary = {
  material_id: 'omni-product-family.domain.product-design',
  domain_ref: {
    material_id: 'omni-product-family.domain.product-design',
    revision_ref: { provider: 'profile', object: 'product-design', revision: 'v1' },
  },
  formal_domain: {
    product_ref: { kind: 'product', ref: 'omnicompany:omni-product-family' },
    semantic_scope: '产品设计',
    formal_artifact_kinds: ['material-wiki-document'],
    authority_scopes: [{ object: 'product', purpose: 'canonical', scope: 'design' }],
    responsibility_model: { required_roles: ['execution', 'content', 'maintenance'] },
    adapter_ref: { kind: 'adapter', ref: 'omnicompany:material-wiki' },
  },
}

afterEach(() => vi.unstubAllGlobals())

describe('formal domain client', () => {
  it('reads the same-port registry and preserves exact domain references', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok', domains: [domain] }) })
    vi.stubGlobal('fetch', fetch)
    await expect(listFormalDomains()).resolves.toEqual([domain])
    expect(fetch).toHaveBeenCalledWith('/api/boss-sight/material-domains/')
  })

  it('uses product language instead of a global promotion label', () => {
    expect(formalDomainActionLabel(domain)).toBe('写入 OmniCompany 设计')
    const spark = structuredClone(domain)
    spark.formal_domain.adapter_ref.ref = 'demogame:spark'
    expect(formalDomainActionLabel(spark)).toBe('做进 Spark 游戏')
    const bside = structuredClone(domain)
    bside.formal_domain.adapter_ref.ref = 'bside:formal-wiki'
    expect(formalDomainActionLabel(bside)).toBe('发布为 BSide 正式正文')
  })
})
