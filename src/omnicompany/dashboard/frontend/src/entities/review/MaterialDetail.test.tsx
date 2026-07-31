/**
 * MaterialDetail 合并顶栏测试(件一 DEC-2026-07-06-082/083)。
 * 覆盖: ①有 toolbar 的材料 → 单条合并栏(business 身份 + 审阅动作永不丢, 无第二条顶栏);
 *       ②无 toolbar 的材料 → 顶栏照旧(默认栏, 无合并栏 testid), 零回归。
 *
 * MaterialContentView 的富渲染不在本测范围, 用一个只回显的探针 kind 渲染器占位。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { Material } from '../../api/reviewstageClient'
import { registerKindRenderer, type BusinessToolbarSpec } from './rendererRegistry'
import { MaterialDetail } from './MaterialDetail'

// 探针 kind 渲染器: 一个带 toolbar 工厂(with-tb), 一个不带(no-tb)。都只回显正文占位。
const Probe = () => <div data-testid="probe-body" />
const withToolbar = (m: Material): BusinessToolbarSpec => ({
  icon: <span data-testid="biz-icon">◆</span>,
  title: '业务标题占位',
  sub: '副标题占位',
  actions: [{ label: '业务动作', onClick: () => {} }],
})
registerKindRenderer('probe-with-tb', { Component: Probe, toolbar: withToolbar })
registerKindRenderer('probe-no-tb', { Component: Probe })

function mat(over: Partial<Material>): Material {
  return {
    id: 'mat_x', kind: 'probe-no-tb', tier: 'important', title: '原材料标题', status: 'pending',
    source_subagent_id: null, source_plan_id: null, file_relpath: null,
    inline_content: 'hi', annotations: [], comments: [], annotations_allowed: true,
    created_at: '', updated_at: '', history: [], pushed_to_user: false,
    pushed_reason: null, pushed_at: null, extra: {},
    ...over,
  } as Material
}

const noop = async () => {}
function renderDetail(m: Material) {
  return render(
    <MaterialDetail
      material={m}
      onVerdict={noop}
      onCommentSubmit={noop}
      onFeedbackChange={noop}
      onTierChange={noop}
      source={null}
      onReturnToSource={() => {}}
    />,
  )
}

beforeEach(() => { vi.restoreAllMocks() })

describe('MaterialDetail 合并顶栏(件一)', () => {
  it('① 有 toolbar 的材料 → 渲染合并栏, 业务身份 + 审阅动作(通过/驳回/⋯)都在, 无第二条顶栏', () => {
    renderDetail(mat({ kind: 'probe-with-tb' as never, extra: {} }))
    const merged = screen.getByTestId('material-detail-merged-toolbar')
    expect(merged).toBeTruthy()
    // 业务身份: 标题取代材料标题区(材料原标题以 title 属性保留可查)。
    expect(screen.getByTestId('material-title').textContent).toBe('业务标题占位')
    expect(merged.getAttribute('title')).toContain('原材料标题')
    expect(screen.getByTestId('biz-icon')).toBeTruthy()
    // 审阅动作永不丢: 通过/驳回两键 + tier/status + 更多。
    expect(screen.getByTestId('verdict-accept')).toBeTruthy()
    expect(screen.getByTestId('verdict-reject')).toBeTruthy()
    expect(screen.getByTestId('material-tier-status')).toBeTruthy()
    expect(screen.getByTestId('material-detail-more')).toBeTruthy()
  })

  it('② 无 toolbar 的材料 → 顶栏照旧(无合并栏 testid, 材料标题原样显示), 零回归', () => {
    renderDetail(mat({ kind: 'probe-no-tb' as never }))
    expect(screen.queryByTestId('material-detail-merged-toolbar')).toBeNull()
    expect(screen.getByTestId('material-title').textContent).toBe('原材料标题')
    // 审阅动作仍在。
    expect(screen.getByTestId('verdict-accept')).toBeTruthy()
    expect(screen.getByTestId('verdict-reject')).toBeTruthy()
  })
})
