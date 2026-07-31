import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import ReviewCanvas from './ReviewCanvas'
import type { CanvasResponse } from '../../api/reviewstageClient'

// 自绘画布(绝对定位卡片 + SVG 曲线, 无 reactflow): jsdom 直接渲染卡片, 不依赖布局引擎测量。
// 保留恒非零尺寸打底, 避免个别环境下 getBoundingClientRect/offset* 返回 0 影响断言。
let rectSpy: ReturnType<typeof vi.spyOn>
let offsetWidthSpy: ReturnType<typeof vi.spyOn>
let offsetHeightSpy: ReturnType<typeof vi.spyOn>

// walker 形状夹具: 3 轨(界面一览/可玩演示/设计评审) + 设计评审轨内 v1→v2 版本链(supersedes)
// + 界面一览 parent 挂靠可玩演示; 设计评审 v2 上带一条已有版本级评论。
const CANVAS: CanvasResponse = {
  tracks: [
    {
      track: '界面一览',
      families: [{
        family: 'walker-ui-v2-六屏一览',
        materials: [{
          id: 'mat_img1', kind: 'image', tier: 'important', title: '行者无乡 UI v2 六屏一览',
          status: 'pending', source_subagent_id: null, source_plan_id: 'walker/x', file_relpath: 'files/a.png',
          inline_content: null, annotations: [], comments: [], annotations_allowed: true,
          created_at: '2026-07-04T07:11:27Z', updated_at: '2026-07-04T11:45:12Z', history: [],
          pushed_to_user: false, pushed_reason: null, pushed_at: null, archived: false, extra: {},
          project: 'walker', track: '界面一览', version: 1, version_family: 'walker-ui-v2-六屏一览',
          links: { parent: 'mat_demo1' },
        }],
      }],
    },
    {
      track: '可玩演示',
      families: [{
        family: 'walker-ui-v2-可玩本体',
        materials: [{
          id: 'mat_demo1', kind: 'demo', tier: 'important', title: '行者无乡 UI v2 游戏本体·点开即玩',
          status: 'pending', source_subagent_id: null, source_plan_id: 'walker/x', file_relpath: null,
          inline_content: 'demo', annotations: [], comments: [], annotations_allowed: true,
          created_at: '2026-07-04T07:11:27Z', updated_at: '2026-07-04T11:45:12Z', history: [],
          pushed_to_user: false, pushed_reason: null, pushed_at: null, archived: false,
          extra: { live_url: '/walker-game/' },
          project: 'walker', track: '可玩演示', version: 1, version_family: 'walker-ui-v2-可玩本体', links: {},
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
            status: 'accepted', source_subagent_id: null, source_plan_id: 'walker/x', file_relpath: 'docs/rev1.md',
            inline_content: null, annotations: [], comments: [], annotations_allowed: true,
            created_at: '2026-07-04T05:00:00Z', updated_at: '2026-07-04T05:00:00Z', history: [],
            pushed_to_user: false, pushed_reason: null, pushed_at: null, archived: false, extra: {},
            project: 'walker', track: '设计评审', version: 1, version_family: 'walker-ui-v2-重建评审', links: {},
          },
          {
            id: 'mat_rev2', kind: 'markdown', tier: 'important', title: 'walker UI v2 五步全部完成·评审',
            status: 'accepted', source_subagent_id: null, source_plan_id: 'walker/x', file_relpath: 'docs/rev2.md',
            inline_content: null, annotations: [],
            comments: [{
              id: 'note_c1', content: 'v2 这版通过, 但大世界浮卡再收一版', author: 'user',
              target: { kind: 'material_version', material_id: 'mat_rev2', version: 2 },
              created_at: '2026-07-04T06:00:00Z', feedback_status: 'delivered', feedback_history: [], version: 2,
            }],
            annotations_allowed: true,
            created_at: '2026-07-04T06:00:00Z', updated_at: '2026-07-04T06:00:00Z', history: [],
            pushed_to_user: false, pushed_reason: null, pushed_at: null, archived: false, extra: {},
            project: 'walker', track: '设计评审', version: 2, version_family: 'walker-ui-v2-重建评审',
            links: { supersedes: ['mat_rev1'] },
          },
        ],
      }],
    },
  ],
  links: [
    { source: 'mat_demo1', target: 'mat_img1', rel: 'parent' },
    { source: 'mat_rev2', target: 'mat_rev1', rel: 'supersedes' },
  ],
  unassigned: [],
  stats: { total: 4, tracks: 3, unassigned: 0, links: 2 },
}

// adopted 裁决(决策库投影): 一条 decision, 用于 C4 适用规范 + C5 上下文包裁决 id。
const GRAPH_ADOPTED = {
  nodes: [
    { id: 'DEC-walker-001', record_kind: 'decision', label: '战场全屏 HUD 悬浮', status: 'adopted',
      statement: '战场必须全屏, HUD 悬浮, 禁网页组件', anchor: { excerpt: '游戏屏=战场全屏+悬浮HUD', ref: 'docs/walker/spec.md#3' } },
  ],
  edges: [],
}

// 路由 fetch: /review-canvas → CANVAS; /material-graph → GRAPH_ADOPTED; /notes(POST) → 新建 note。
function installFetch(opts: { canvas?: CanvasResponse | null } = {}) {
  const canvas = opts.canvas === undefined ? CANVAS : opts.canvas
  globalThis.fetch = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/review-canvas')) {
      return Promise.resolve({ ok: canvas !== null, json: async () => canvas } as Response)
    }
    if (url.includes('/api/v2/material-graph')) {
      return Promise.resolve({ ok: true, json: async () => GRAPH_ADOPTED } as Response)
    }
    if (url.includes('/api/boss-sight/notes')) {
      return Promise.resolve({ ok: true, json: async () => ({ id: 'note_new', content: '', author: 'user', target: {}, uses: ['comment'], feedback_status: 'delivered', project_id: '', created_at: '' }) } as Response)
    }
    return Promise.resolve({ ok: false, json: async () => ({}) } as Response)
  }) as unknown as typeof fetch
}

beforeEach(() => {
  rectSpy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
    width: 244, height: 92, top: 0, left: 0, right: 244, bottom: 92, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect)
  offsetWidthSpy = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(244)
  offsetHeightSpy = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(92)
})
afterEach(() => {
  vi.restoreAllMocks()
  rectSpy.mockRestore(); offsetWidthSpy.mockRestore(); offsetHeightSpy.mockRestore()
})

describe('ReviewCanvas 材料轨迹', () => {
  it('① 渲染轨道泳道与版本节点', async () => {
    installFetch()
    render(<ReviewCanvas project="walker" />)
    await waitFor(() => expect(screen.getByTestId('canvas-node-mat_img1')).toBeTruthy())
    // 三轨四版本都在(设计评审轨含 v1→v2)
    expect(screen.getByTestId('canvas-node-mat_demo1')).toBeTruthy()
    expect(screen.getByTestId('canvas-node-mat_rev1')).toBeTruthy()
    expect(screen.getByTestId('canvas-node-mat_rev2')).toBeTruthy()
    // 泳道名可见(视图标题=材料轨迹, 绝不叫决策树 —— A10)
    const root = screen.getByTestId('review-canvas')
    expect(root.textContent).toContain('材料轨迹')
    expect(root.textContent).toContain('设计评审')
    expect(root.textContent).not.toContain('决策树')
  })

  it('② 点版本节点 → 详情栏出现, 列出该版本已有评论', async () => {
    installFetch()
    render(<ReviewCanvas project="walker" />)
    await waitFor(() => expect(screen.getByTestId('canvas-node-mat_rev2')).toBeTruthy())
    fireEvent.click(screen.getByTestId('canvas-node-mat_rev2'))
    const detail = await screen.findByTestId('canvas-detail')
    expect(detail.textContent).toContain('walker UI v2 五步全部完成')
    // 版本级评论(带 version=2)水合出现
    expect(detail.textContent).toContain('大世界浮卡再收一版')
    // 评论输入 + 提交按钮就位
    expect(screen.getByTestId('canvas-comment-input')).toBeTruthy()
    expect(screen.getByTestId('canvas-comment-submit')).toBeTruthy()
  })

  it('③ 无带 track 材料时降级空态, 不白屏', async () => {
    installFetch({ canvas: { tracks: [], links: [], unassigned: [], stats: { total: 0, tracks: 0, unassigned: 0, links: 0 } } })
    render(<ReviewCanvas project="walker" />)
    await waitFor(() => expect(screen.getByTestId('canvas-empty')).toBeTruthy())
    expect(screen.getByTestId('canvas-empty').textContent).toContain('还没有带 track/version 标签的材料')
  })

  it('④ 发起下一步: 上下文包含材料标题与 adopted 裁决 id(mock clipboard)', async () => {
    installFetch()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(<ReviewCanvas project="walker" />)
    await waitFor(() => expect(screen.getByTestId('canvas-node-mat_rev2')).toBeTruthy())
    fireEvent.click(screen.getByTestId('canvas-node-mat_rev2'))
    await screen.findByTestId('canvas-detail')
    // 适用规范面拉到 adopted 裁决(异步)
    await waitFor(() => expect(screen.getByTestId('canvas-rules').textContent).toContain('战场必须全屏'))
    fireEvent.click(screen.getByTestId('canvas-next-step'))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    const pkg = writeText.mock.calls[0][0] as string
    expect(pkg).toContain('walker UI v2 五步全部完成')  // 材料标题
    expect(pkg).toContain('DEC-walker-001')             // 适用裁决 id
    expect(pkg).toContain('walker')                     // 项目
  })

  it('⑤ 点承袭连线的决策过程徽章 → 详情栏切到决策过程面, 列适用规范', async () => {
    installFetch()
    render(<ReviewCanvas project="walker" />)
    await waitFor(() => expect(screen.getByTestId('canvas-node-mat_rev2')).toBeTruthy())
    // supersedes 边(mat_rev2→mat_rev1)中点挂着决策过程徽章
    fireEvent.click(screen.getByTestId('canvas-decision-badge'))
    const detail = await screen.findByTestId('canvas-detail')
    expect(detail.textContent).toContain('决策过程')
    await waitFor(() => expect(screen.getByTestId('canvas-rules').textContent).toContain('战场必须全屏'))
    // 决策过程面不是节点面: 没有评论输入框
    expect(screen.queryByTestId('canvas-comment-input')).toBeNull()
  })

  it('⑥ onOpenReader 提供时渲染阅读视图按钮并可点', async () => {
    installFetch()
    const onOpenReader = vi.fn()
    render(<ReviewCanvas project="walker" onOpenReader={onOpenReader} />)
    await waitFor(() => expect(screen.getByTestId('canvas-node-mat_img1')).toBeTruthy())
    const btn = screen.getByTestId('canvas-open-reader')
    fireEvent.click(btn)
    expect(onOpenReader).toHaveBeenCalledTimes(1)
  })

  it('⑦ 不提供 onOpenReader 时不渲染阅读视图按钮', async () => {
    installFetch()
    render(<ReviewCanvas project="walker" />)
    await waitFor(() => expect(screen.getByTestId('canvas-node-mat_img1')).toBeTruthy())
    expect(screen.queryByTestId('canvas-open-reader')).toBeNull()
  })
})
