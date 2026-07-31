import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import type { CanvasResponse } from '../../api/reviewstageClient'
import type { Mock } from 'vitest'

// ReviewMaterialPanel 拉起 MaterialDetail 全链路 + WS streamStore, 本测只验阅读视图外壳
// (左列/选择/切轨迹), 故把主区面板替换为轻量探针, 只回显收到的材料 id + embedded。
vi.mock('../review_material', () => ({
  ReviewMaterialPanel: ({ id, embedded }: { id: string; embedded?: boolean }) => (
    <div data-testid="mock-review-material">material:{id}:{embedded ? 'embedded' : 'full'}</div>
  ),
}))

// reviewstageApi.canvas → CANVAS 夹具; 其余保留(测里用不到)。
const CANVAS: CanvasResponse = {
  tracks: [
    {
      track: '界面一览',
      families: [{
        family: 'walker-ui-v2-六屏一览',
        materials: [{
          id: 'mat_img1', kind: 'image', tier: 'important', title: '行者无乡 UI v2 六屏一览',
          status: 'pending', source_subagent_id: null, source_plan_id: null, file_relpath: 'files/a.png',
          inline_content: null, annotations: [], comments: [], annotations_allowed: true,
          created_at: '2026-07-04T07:11:27Z', updated_at: '2026-07-04T11:45:12Z', history: [],
          pushed_to_user: false, pushed_reason: null, pushed_at: null, archived: false, extra: {},
          project: 'walker', track: '界面一览', version: 1, version_family: 'walker-ui-v2-六屏一览', links: {},
        }],
      }],
    },
    {
      track: '设计评审',
      families: [{
        family: 'walker-ui-v2-重建评审',
        materials: [
          {
            id: 'mat_rev1', kind: 'markdown', tier: 'important', title: 'walker UI v2 重建·设计提案评审门',
            status: 'accepted', source_subagent_id: null, source_plan_id: null, file_relpath: 'docs/rev1.md',
            inline_content: null, annotations: [], comments: [], annotations_allowed: true,
            created_at: '2026-07-04T05:00:00Z', updated_at: '2026-07-04T05:00:00Z', history: [],
            pushed_to_user: false, pushed_reason: null, pushed_at: null, archived: false, extra: {},
            project: 'walker', track: '设计评审', version: 1, version_family: 'walker-ui-v2-重建评审', links: {},
          },
          {
            id: 'mat_rev2', kind: 'markdown', tier: 'important', title: 'walker UI v2 五步全部完成·评审',
            status: 'accepted', source_subagent_id: null, source_plan_id: null, file_relpath: 'docs/rev2.md',
            inline_content: null, annotations: [], comments: [], annotations_allowed: true,
            created_at: '2026-07-04T06:00:00Z', updated_at: '2026-07-04T06:00:00Z', history: [],
            pushed_to_user: false, pushed_reason: null, pushed_at: null, archived: false, extra: {},
            project: 'walker', track: '设计评审', version: 2, version_family: 'walker-ui-v2-重建评审',
            links: { supersedes: ['mat_rev1'] },
          },
        ],
      }],
    },
  ],
  links: [{ source: 'mat_rev2', target: 'mat_rev1', rel: 'supersedes' }],
  unassigned: [],
  stats: { total: 3, tracks: 2, unassigned: 0, links: 1 },
}

const GRAPH = {
  nodes: [
    { id: 'DEC-walker-001', record_kind: 'decision', label: '战场全屏 HUD 悬浮', status: 'adopted',
      statement: '战场必须全屏, HUD 悬浮, 禁网页组件', anchor: { excerpt: '游戏屏=战场全屏+悬浮HUD', ref: 'docs/walker/spec.md#3' } },
    { id: 'DEC-walker-002', record_kind: 'decision', label: '指挥台三件合一', status: 'proposed',
      statement: '战斗屏中枢=指挥台三件合一', anchor: { excerpt: '每单位一条按拍对齐的多轨时间面', ref: 'docs/walker/ui.md#1' } },
  ],
  edges: [],
}

// 工厂被 hoist 到顶, 不能引用外层常量; 故在工厂内自建 vi.fn(), 测里再取回 mock 设返回值。
vi.mock('../../api/reviewstageClient', async (orig) => {
  const mod = await orig<typeof import('../../api/reviewstageClient')>()
  return { ...mod, reviewstageApi: { ...mod.reviewstageApi, canvas: vi.fn(), domainTree: vi.fn() } }
})

import { StudioReaderPanel } from './index'
import { reviewstageApi } from '../../api/reviewstageClient'
import { usePanels } from '../../stores/panelsStore'

const mockedCanvas = reviewstageApi.canvas as unknown as Mock
const mockedDomainTree = reviewstageApi.domainTree as unknown as Mock

beforeEach(() => {
  mockedCanvas.mockResolvedValue(CANVAS)
  mockedDomainTree.mockResolvedValue({ domains: [] })
  globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/api/v2/material-graph')) return Promise.resolve({ ok: true, json: async () => GRAPH } as Response)
    return Promise.resolve({ ok: false, json: async () => ({}) } as Response)
  }) as unknown as typeof fetch
})
afterEach(() => { vi.restoreAllMocks() })

describe('StudioReaderPanel 阅读视图', () => {
  it('① 左列按轨分组列出材料, 默认落在最新一条 markdown 材料上', async () => {
    render(<StudioReaderPanel project="walker" />)
    // 材料入列
    await waitFor(() => expect(screen.getByTestId('reader-material-mat_img1')).toBeTruthy())
    const rail = screen.getByTestId('studio-reader-rail')
    expect(rail.textContent).toContain('界面一览')
    expect(rail.textContent).toContain('设计评审')
    // 默认主区 = 最新 markdown(mat_rev2), 且 embedded
    await waitFor(() => expect(screen.getByTestId('mock-review-material').textContent).toBe('material:mat_rev2:embedded'))
  })

  it('② 同族多版本以 v1/v2 小片内联, 点 v1 切到该版本材料', async () => {
    render(<StudioReaderPanel project="walker" />)
    await waitFor(() => expect(screen.getByTestId('reader-version-mat_rev1')).toBeTruthy())
    expect(screen.getByTestId('reader-version-mat_rev2')).toBeTruthy()
    fireEvent.click(screen.getByTestId('reader-version-mat_rev1'))
    await waitFor(() => expect(screen.getByTestId('mock-review-material').textContent).toBe('material:mat_rev1:embedded'))
  })

  it('③ 点「决策过程」→ 主区渲染裁决面板(adopted 列表 + proposed 高亮)', async () => {
    render(<StudioReaderPanel project="walker" />)
    await waitFor(() => expect(screen.getByTestId('reader-goto-decisions')).toBeTruthy())
    fireEvent.click(screen.getByTestId('reader-goto-decisions'))
    const dec = await screen.findByTestId('studio-reader-decisions')
    // adopted 带 statement + anchor 原话摘录
    await waitFor(() => expect(screen.getByTestId('studio-reader-adopted').textContent).toContain('战场必须全屏'))
    expect(dec.textContent).toContain('游戏屏=战场全屏+悬浮HUD')
    // proposed 高亮为待你裁决
    expect(screen.getByTestId('studio-reader-proposed').textContent).toContain('指挥台三件合一')
  })

  it('④ 「切到轨迹视图」→ openTab(project) 复用既有项目页签(不带 facet)', async () => {
    usePanels.setState({ tabs: [], activeId: null })
    render(<StudioReaderPanel project="walker" />)
    await waitFor(() => expect(screen.getByTestId('reader-goto-canvas')).toBeTruthy())
    fireEvent.click(screen.getByTestId('reader-goto-canvas'))
    await waitFor(() => {
      const tab = usePanels.getState().tabs.find((t) => t.ref.type === 'project' && t.ref.id === 'walker')
      expect(tab).toBeTruthy()
      expect(tab?.facet).toBeUndefined()
    })
  })

  it('⑤ 收起/展开细条切换', async () => {
    render(<StudioReaderPanel project="walker" />)
    await waitFor(() => expect(screen.getByTestId('reader-collapse')).toBeTruthy())
    fireEvent.click(screen.getByTestId('reader-collapse'))
    await waitFor(() => expect(screen.getByTestId('reader-expand')).toBeTruthy())
    fireEvent.click(screen.getByTestId('reader-expand'))
    await waitFor(() => expect(screen.getByTestId('reader-collapse')).toBeTruthy())
  })

  it('⑥ 待你裁决计数徽章 = 库中真 proposed 条数', async () => {
    render(<StudioReaderPanel project="walker" />)
    await waitFor(() => expect(screen.getByTestId('reader-pending-count').textContent).toBe('1'))
  })

  it('⑥b 件二: initialMaterialId(深链)优先选中该材料, 而非默认最新 markdown', async () => {
    // 默认会落在最新 markdown(mat_rev2); 传 initialMaterialId=mat_img1 应改选中 mat_img1。
    render(<StudioReaderPanel project="walker" initialMaterialId="mat_img1" />)
    await waitFor(() => expect(screen.getByTestId('mock-review-material').textContent).toBe('material:mat_img1:embedded'))
  })

  it('⑥c 件二: initialMaterialId 不在列表 → 回落默认最新 markdown(不白屏)', async () => {
    render(<StudioReaderPanel project="walker" initialMaterialId="does-not-exist" />)
    await waitFor(() => expect(screen.getByTestId('mock-review-material').textContent).toBe('material:mat_rev2:embedded'))
  })
})

// ── v2 材料展示框架: 业务展示区(结构排布 × 版本策略三型) ──────────────

function mat(id: string, over: Record<string, unknown>) {
  return {
    id, kind: 'markdown', tier: 'important', title: id, status: 'pending',
    source_subagent_id: null, source_plan_id: null, file_relpath: null,
    inline_content: null, annotations: [], comments: [], annotations_allowed: true,
    created_at: '2026-07-05T00:00:00Z', updated_at: '2026-07-05T00:00:00Z', history: [],
    pushed_to_user: false, pushed_reason: null, pushed_at: null, archived: false, extra: {},
    version: 1, version_family: 'fam', links: {}, ...over,
  }
}

const VILO_CANVAS = {
  tracks: [
    {
      track: '主旨与情感弧',
      families: [{
        family: 'vilo-大纲',
        materials: [
          mat('mat_o1', { project: 'vilo', track: '主旨与情感弧', version: 1, version_family: 'vilo-大纲', title: 'vilo 大纲 v1' }),
          mat('mat_o2', { project: 'vilo', track: '主旨与情感弧', version: 2, version_family: 'vilo-大纲', title: 'vilo 大纲 v2', links: { supersedes: ['mat_o1'] } }),
        ],
      }],
    },
    {
      track: '散落轨',
      families: [{ family: 'vilo-零散', materials: [mat('mat_x1', { project: 'vilo', track: '散落轨', version_family: 'vilo-零散' })] }],
    },
  ],
  links: [], unassigned: [], stats: { total: 3, tracks: 2, unassigned: 0, links: 0 },
} as unknown as CanvasResponse

const VILO_TREE = {
  domains: [{
    domain: 'narrative',
    steps: [
      { name: '世界圣经', order: 1, desc: '冻结层', expected_kinds: [], gate: { enforcer: '' }, samples: [], adopted_rulings: [], next: '主旨与情感弧' },
      { name: '主旨与情感弧', order: 2, desc: '立意与情感弧', expected_kinds: [], gate: { enforcer: '' }, samples: [], adopted_rulings: [], next: null },
    ],
  }],
}

describe('StudioReaderPanel 业务展示区(v2)', () => {
  it('⑦ vilo: 按域层级排布(序号+层名+空层显式), 装不进层级的轨附在后面', async () => {
    mockedCanvas.mockResolvedValue(VILO_CANVAS)
    mockedDomainTree.mockResolvedValue(VILO_TREE)
    render(<StudioReaderPanel project="vilo" />)
    await waitFor(() => expect(screen.getByTestId('reader-section-世界圣经')).toBeTruthy())
    // 空层显式"暂无材料", 不留白
    expect(screen.getByTestId('reader-section-世界圣经').textContent).toContain('该层暂无材料')
    // 有材料的层照常; 未入层的轨保底附后
    expect(screen.getByTestId('reader-section-主旨与情感弧')).toBeTruthy()
    expect(screen.getByTestId('reader-section-散落轨')).toBeTruthy()
    // 顺序: 世界圣经(1) 在 主旨与情感弧(2) 前
    const rail = screen.getByTestId('studio-reader-rail')
    const html = rail.innerHTML
    expect(html.indexOf('世界圣经')).toBeLessThan(html.indexOf('主旨与情感弧'))
  })

  it('⑧ vilo: 版本策略=仅最新+历史折叠 —— 默认无版本片, 点「历史」展开全链(底层一条不丢)', async () => {
    mockedCanvas.mockResolvedValue(VILO_CANVAS)
    mockedDomainTree.mockResolvedValue(VILO_TREE)
    render(<StudioReaderPanel project="vilo" />)
    await waitFor(() => expect(screen.getByTestId('reader-material-mat_o2')).toBeTruthy())
    // 默认只见最新(行本体), 旧版折叠 → 无 v1 版本片, 有历史开关
    expect(screen.queryByTestId('reader-version-mat_o1')).toBeNull()
    const toggle = screen.getByTestId('reader-history-toggle-vilo-大纲')
    fireEvent.click(toggle)
    await waitFor(() => expect(screen.getByTestId('reader-version-mat_o1')).toBeTruthy())
    expect(screen.getByTestId('reader-version-mat_o2')).toBeTruthy()
    // 点旧版仍可打开(回访历史)
    fireEvent.click(screen.getByTestId('reader-version-mat_o1'))
    await waitFor(() => expect(screen.getByTestId('mock-review-material').textContent).toBe('material:mat_o1:embedded'))
  })

  it('⑨ vilo: 域层级不可达 → 显式降级横条 + 回落按 track 陈列(不白屏)', async () => {
    mockedCanvas.mockResolvedValue(VILO_CANVAS)
    mockedDomainTree.mockRejectedValue(new Error('boom'))
    render(<StudioReaderPanel project="vilo" />)
    await waitFor(() => expect(screen.getByTestId('reader-structure-fallback')).toBeTruthy())
    expect(screen.getByTestId('reader-section-主旨与情感弧')).toBeTruthy()
    expect(screen.queryByTestId('reader-section-世界圣经')).toBeNull()
  })

  it('⑩ demogame-design: 版本策略=最新为主+选择性 —— 被钉住的旧版保持可见, 其余折叠', async () => {
    const demogame_CANVAS = {
      tracks: [{
        track: '成稿',
        families: [{
          family: 'demogame-案',
          materials: [
            mat('mat_g1', { project: 'demogame-design', track: '成稿', version: 1, version_family: 'demogame-案' }),
            mat('mat_g2', { project: 'demogame-design', track: '成稿', version: 2, version_family: 'demogame-案', extra: { display_pinned: true } }),
            mat('mat_g3', { project: 'demogame-design', track: '成稿', version: 3, version_family: 'demogame-案' }),
          ],
        }],
      }],
      links: [], unassigned: [], stats: { total: 3, tracks: 1, unassigned: 0, links: 0 },
    } as unknown as CanvasResponse
    mockedCanvas.mockResolvedValue(demogame_CANVAS)
    mockedDomainTree.mockResolvedValue({ domains: [] }) // 域层级空 → 结构回落, 但版本策略仍生效
    render(<StudioReaderPanel project="demogame-design" />)
    await waitFor(() => expect(screen.getByTestId('reader-material-mat_g3')).toBeTruthy())
    // 可见 = 最新 v3 + 钉住的 v2; v1 折叠
    expect(screen.getByTestId('reader-version-mat_g3')).toBeTruthy()
    expect(screen.getByTestId('reader-version-mat_g2')).toBeTruthy()
    expect(screen.queryByTestId('reader-version-mat_g1')).toBeNull()
    fireEvent.click(screen.getByTestId('reader-history-toggle-demogame-案'))
    await waitFor(() => expect(screen.getByTestId('reader-version-mat_g1')).toBeTruthy())
  })
})
