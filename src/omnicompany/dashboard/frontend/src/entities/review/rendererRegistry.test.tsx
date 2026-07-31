/**
 * rendererRegistry + displayProfiles — 材料展示框架契约测试(v2 一期)。
 * 覆盖: 唯一分发解析优先级 / 未注册显式回退 / 三项配置契约(A13: 结构只引用数据源名) /
 * 业务清单与注册表一致(vilo 声明的 schemaId 全部真实登记)。
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { Material } from '../../api/reviewstageClient'
import {
  registerKindRenderer, registerSchemaRenderer, resolveMaterialRenderer,
  registeredSchemaIds, registeredKinds, schemaIdOf,
} from './rendererRegistry'
// MaterialViews 顶层登记内建 kind; businesses 登记叙事业务类型(懒加载)。
import { MaterialContentView } from './MaterialViews'
import { registerReviewBusinessRenderers } from './businesses'
import { getDisplayProfile, DEFAULT_PROFILE, configuredProjects, versionPolicyFor } from './displayProfiles'

registerReviewBusinessRenderers()

function m(over: Partial<Material>): Material {
  return {
    id: 'mat_t', kind: 'markdown', tier: 'important', title: 't', status: 'pending',
    source_subagent_id: null, source_plan_id: null, file_relpath: null,
    inline_content: 'hello', annotations: [], comments: [], annotations_allowed: true,
    created_at: '', updated_at: '', history: [], pushed_to_user: false,
    pushed_reason: null, pushed_at: null, extra: {},
    ...over,
  } as Material
}

describe('rendererRegistry 唯一分发(F3/A12)', () => {
  it('内建 kind 全部在注册表(七类 + 五型工作报告)', () => {
    const kinds = registeredKinds()
    for (const k of ['image', 'markdown', 'html', 'key_question', 'custom_web_template', 'video',
      'webgame-spec', 'plan', 'agent-workflow-report', 'static-report', 'demo', 'aigc-image']) {
      expect(kinds, `kind ${k} 应已登记`).toContain(k)
    }
  })

  it('解析优先级: 注册过的 data_schema_id > kind; 未注册 schema 落回 kind', () => {
    const Probe = () => <div data-testid="probe-schema" />
    registerSchemaRenderer('probe_schema_v1', { Component: Probe })
    const withSchema = resolveMaterialRenderer(m({ kind: 'custom_web_template', extra: { data_schema_id: 'probe_schema_v1' } }))
    expect(withSchema?.Component).toBe(Probe)
    const unknownSchema = resolveMaterialRenderer(m({ kind: 'custom_web_template', extra: { data_schema_id: 'nope_v9' } }))
    expect(unknownSchema).toBeTruthy() // 落回 custom_web_template 的 kind 渲染器
    expect(unknownSchema?.Component).not.toBe(Probe)
  })

  it('schemaIdOf 只认非空字符串', () => {
    expect(schemaIdOf(m({ extra: { data_schema_id: 'x' } }))).toBe('x')
    expect(schemaIdOf(m({ extra: { data_schema_id: '' } }))).toBeUndefined()
    expect(schemaIdOf(m({ extra: {} }))).toBeUndefined()
  })

  it('错误样本: 未注册 kind → MaterialContentView 显式回退, 不白屏', () => {
    render(<MaterialContentView m={m({ kind: 'totally_unknown' as never })} onElementSelect={() => {}} />)
    expect(screen.getByTestId('material-unknown-kind').textContent).toContain('totally_unknown')
    expect(screen.getByTestId('material-unknown-kind').textContent).toContain('hello')
  })

  it('registerKindRenderer 可被业务扩展且解析可见', () => {
    const K = () => <div />
    registerKindRenderer('probe_kind', { Component: K, fullBleed: true })
    expect(resolveMaterialRenderer(m({ kind: 'probe_kind' as never }))?.fullBleed).toBe(true)
  })

  it('件一: 叙事业务渲染器挂有 toolbar 工厂, 产出合并栏 spec(icon/title/决策历程 action)', () => {
    const entry = resolveMaterialRenderer(m({ kind: 'custom_web_template', extra: { data_schema_id: 'narrative_outline_v1' } }))
    expect(typeof entry?.toolbar).toBe('function')
    const spec = entry!.toolbar!(m({ project: 'vilo', track: '主旨与情感弧', extra: { data_schema_id: 'narrative_outline_v1' } }))
    expect(spec.title).toContain('大纲')
    expect(spec.icon).toBeTruthy()
    expect((spec.actions ?? []).some((a) => a.label === '决策历程')).toBe(true)
  })

  it('件一: 无 toolbar 的普通 kind 渲染器 → entry.toolbar 为 undefined(默认栏零回归)', () => {
    const entry = resolveMaterialRenderer(m({ kind: 'markdown' }))
    expect(entry).toBeTruthy()
    expect(entry?.toolbar).toBeUndefined()
  })
})

describe('displayProfiles 三项配置契约(F2/A13)', () => {
  it('未配置项目回落默认: 按 track 陈列 + 多版本并存', () => {
    expect(getDisplayProfile('no-such-project')).toBe(DEFAULT_PROFILE)
    expect(DEFAULT_PROFILE.structure).toBe('track')
    expect(DEFAULT_PROFILE.versionPolicy).toBe('coexist')
  })

  it('A13: 结构排布只引用数据源名(domain-tree/track), 无内联层级数组', () => {
    for (const p of configuredProjects()) {
      const prof = getDisplayProfile(p)
      expect(['domain-tree', 'track']).toContain(prof.structure)
      // 三项之外无配置(一期锁死): 只允许 structure/versionPolicy/trackVersionPolicy/schemaRenderers
      const keys = Object.keys(prof)
      for (const k of keys) {
        expect(['structure', 'versionPolicy', 'trackVersionPolicy', 'schemaRenderers', 'subjectHierarchy']).toContain(k)
      }
    }
  })

  it('版本策略三型都有真实业务在用(场景丙全型)', () => {
    const used = new Set(configuredProjects().map((p) => getDisplayProfile(p).versionPolicy))
    expect(used).toContain('coexist')
    expect(used).toContain('latest-collapse')
    expect(used).toContain('latest-selective')
  })

  it('业务类型渲染器清单与注册表一致: vilo 声明的 schemaId 全部真实登记', () => {
    const declared = getDisplayProfile('vilo').schemaRenderers ?? []
    expect(declared.length).toBeGreaterThan(0)
    const registered = registeredSchemaIds()
    for (const id of declared) expect(registered, `schemaId ${id} 未登记`).toContain(id)
  })

  it('层级覆写: versionPolicyFor 层覆写优先于业务默认', () => {
    const prof = { structure: 'track' as const, versionPolicy: 'coexist' as const, trackVersionPolicy: { 成稿: 'latest-collapse' as const } }
    expect(versionPolicyFor(prof, '成稿')).toBe('latest-collapse')
    expect(versionPolicyFor(prof, '其他')).toBe('coexist')
  })
})
