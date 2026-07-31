/**
 * 叙事业务渲染器测试(v2 二期 N1/N2)。
 * 覆盖: 大纲段×线矩阵(段列头/故事线行/拟徽标) / 决策历程互跳 / 引擎不可达显式降级(错误样本) /
 * 租户纪律(零 useStudio —— 由 studio_authority_audit.py A12 静态检查, 这里验行为)。
 *
 * 件一(DEC-2026-07-06-082/083)迁移: 顶栏并入审阅顶栏后, 渲染器正文不再自画 NarrativeToolbar;
 * 图标/标题/适用裁决/决策历程改由 toolbar 工厂产出(narrativeOutlineToolbar 等), 相应断言随协议下移
 * 到工厂产物 + 其 aux 活体件(NarrativeRulings)。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { Material } from '../../../api/reviewstageClient'

vi.mock('../../../api/reviewstageClient', async (orig) => {
  const mod = await orig<typeof import('../../../api/reviewstageClient')>()
  return { ...mod, reviewstageApi: { ...mod.reviewstageApi, domainTree: vi.fn() } }
})

import { NarrativeOutlineView, NarrativePremiseView } from './narrative'
import { narrativeOutlineToolbar, narrativePremiseToolbar } from './narrativeToolbars'
import { reviewstageApi } from '../../../api/reviewstageClient'
import { usePanels } from '../../../stores/panelsStore'
import type { Mock } from 'vitest'

const mockedDomainTree = reviewstageApi.domainTree as unknown as Mock

const PROJ = {
  meta: { name: 'Vilo想知道', version: 'v1' },
  premise: { proposition: '爱而不得也值得', controlling_ideas: ['母题一', '母题二'], stance: '肯定', locked: true },
  storylines: [{ id: 'line-liang', title: '梁奕笙线', color: '#7aa2ff' }],
  beats: [
    { id: 'seg-1', title: '制造相识', position: 1, status: 'done', authority: 'author', review_status: 'accepted', summary: { sentence: '两人如何相识' } },
    { id: 'seg-2', title: '深入期', position: 2, status: 'todo', authority: null, review_status: 'pending' },
    { id: 'b-11', parent: 'seg-1', lane: 'line-liang', title: '初见', position: 1, status: 'done', authority: 'ai_draft', review_status: 'draft' },
  ],
  characters: [], relationships: [], game_texts: [],
}

const MAT: Material = {
  id: 'mat_outline', kind: 'custom_web_template', tier: 'important', title: 'vilo 大纲', status: 'pending',
  source_subagent_id: null, source_plan_id: null, file_relpath: null,
  inline_content: '{"project":"vilo"}', annotations: [], comments: [], annotations_allowed: true,
  created_at: '', updated_at: '', history: [], pushed_to_user: false, pushed_reason: null, pushed_at: null,
  extra: { data_schema_id: 'narrative_outline_v1' }, project: 'vilo', track: '主旨与情感弧',
} as unknown as Material

beforeEach(() => {
  mockedDomainTree.mockResolvedValue({
    domains: [{
      domain: 'narrative',
      steps: [{
        name: '主旨与情感弧', order: 2, desc: '', expected_kinds: [], gate: { enforcer: '' }, samples: [],
        adopted_rulings: [{ id: 'DEC-x', statement: '墨成角色本体裁决', anchor: '原话' }], next: null,
      }],
    }],
  })
  globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/narrative-studio/api/project')) {
      return Promise.resolve({
        ok: true, headers: new Headers({ 'content-type': 'application/json' }), json: async () => PROJ,
      } as unknown as Response)
    }
    if (url.includes('/narrative-studio/api/versions/details')) {
      return Promise.resolve({
        ok: true, headers: new Headers({ 'content-type': 'application/json' }), json: async () => [],
      } as unknown as Response)
    }
    return Promise.resolve({ ok: false, status: 502, statusText: 'Bad Gateway', headers: new Headers(), json: async () => ({}) } as unknown as Response)
  }) as unknown as typeof fetch
})
afterEach(() => { vi.restoreAllMocks() })

describe('NarrativeOutlineView 大纲段×线(N1)', () => {
  it('① 段为列头、故事线为行、子卡按 lane 落格; 认可状态徽标直接长在卡上', async () => {
    render(<NarrativeOutlineView m={MAT} />)
    await waitFor(() => expect(screen.getByTestId('narrative-stage-seg-1')).toBeTruthy())
    expect(screen.getByTestId('narrative-stage-seg-2')).toBeTruthy()
    expect(screen.getByTestId('narrative-beat-b-11')).toBeTruthy()
    const root = screen.getByTestId('narrative-outline')
    expect(root.textContent).toContain('梁奕笙线')
    // 作者=已认可(绿) / 拟=待认可(黄)两种徽标都在
    expect(screen.getAllByTestId('narrative-review-accepted').length).toBeGreaterThan(0)
    expect(screen.getAllByTestId('narrative-review-draft').length).toBeGreaterThan(0)
  })

  it('①b 件一: 正文不再自画第二条工具条(渲染器内无 NarrativeToolbar/适用裁决/决策历程)', async () => {
    render(<NarrativeOutlineView m={MAT} />)
    await waitFor(() => expect(screen.getByTestId('narrative-stage-seg-1')).toBeTruthy())
    // 顶栏已并入审阅顶栏: 渲染器正文里不应再出现适用裁决 chip / 决策历程按钮(旧 NarrativeToolbar DOM)。
    expect(screen.queryByTestId('narrative-rulings-chip')).toBeNull()
    expect(screen.queryByTestId('narrative-goto-decisions')).toBeNull()
  })

  it('② 顶栏工厂: icon/title/决策历程 action(一跳项目轨迹) + 适用裁决 aux(可展开原话)', async () => {
    usePanels.setState({ tabs: [], activeId: null })
    const spec = narrativeOutlineToolbar(MAT)
    // 工厂产物含身份 + 决策历程 action(跳转行为原样保留)。
    expect(spec.title).toContain('大纲')
    expect(spec.icon).toBeTruthy()
    const gotoDecisions = (spec.actions ?? []).find((a) => a.label === '决策历程')
    expect(gotoDecisions).toBeTruthy()
    gotoDecisions!.onClick()
    await waitFor(() => {
      const tab = usePanels.getState().tabs.find((t) => t.ref.type === 'project' && t.ref.id === 'vilo')
      expect(tab).toBeTruthy()
    })
    // aux = 适用裁决活体件: 渲染后异步取域层级, chip 可展开原话面板。
    render(<div data-testid="aux-host">{spec.aux}</div>)
    await waitFor(() => expect(screen.getByTestId('narrative-rulings-chip')).toBeTruthy())
    fireEvent.click(screen.getByTestId('narrative-rulings-chip'))
    await waitFor(() => expect(screen.getByTestId('narrative-rulings-panel').textContent).toContain('墨成角色本体裁决'))
  })

  it('③ 错误样本: 引擎不可达 → 显式降级提示 + 可重试, 不拖垮整页', async () => {
    ;(globalThis.fetch as unknown as Mock).mockImplementation(() =>
      Promise.resolve({ ok: false, status: 503, statusText: 'Service Unavailable', headers: new Headers(), json: async () => ({}) } as unknown as Response))
    render(<NarrativeOutlineView m={MAT} />)
    await waitFor(() => expect(screen.getByTestId('narrative-workspace-error')).toBeTruthy())
    expect(screen.getByTestId('narrative-workspace-error').textContent).toContain('503')
    expect(screen.getByTestId('narrative-workspace-retry')).toBeTruthy()
  })
})

describe('NarrativePremiseView 立意(N2)', () => {
  it('命题/主控思想/锁定态齐全(正文); 唯一权威=wiki/10 标注随件一迁到顶栏工厂 sub', async () => {
    render(<NarrativePremiseView m={MAT} />)
    await waitFor(() => expect(screen.getByTestId('narrative-premise').textContent).toContain('爱而不得也值得'))
    const t = screen.getByTestId('narrative-premise').textContent || ''
    expect(t).toContain('母题一')
    expect(t).toContain('已锁定')
    // "唯一权威=vilo wiki/10" 现由顶栏工厂 sub 承载(顶栏并入审阅顶栏, 不再在正文 NarrativeToolbar)。
    expect(narrativePremiseToolbar(MAT).sub || '').toContain('wiki/10')
  })
})
