import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { usePanels } from '../../stores/panelsStore'
import { MaterialEmbed } from './MaterialEmbed'

function material(): Material {
  return {
    id: 'mat_aigc_compare',
    kind: 'html',
    tier: 'important',
    title: 'AIGC 三候选比较',
    status: 'pending',
    source_subagent_id: null,
    source_plan_id: 'PLAN-A',
    file_relpath: 'files/compare.html',
    inline_content: '<html></html>',
    annotations: [],
    comments: [],
    annotations_allowed: true,
    created_at: '',
    updated_at: '',
    history: [],
    pushed_to_user: false,
    pushed_reason: null,
    pushed_at: null,
    project: 'walker',
    track: '战场表现',
    version: 3,
    version_family: 'compare',
    extra: {},
    review_context: {
      profile_id: 'aigc-comparison',
      profile_version: 1,
      schema_id: '',
      references: [
        { target: 'omni://review/mat_a', relation: 'comparison_member', label: 'A' },
        { target: 'omni://review/mat_b', relation: 'comparison_member', label: 'B' },
        { target: 'omni://review/mat_c', relation: 'comparison_member', label: 'C' },
      ],
      resolution: {
        selected_by: 'explicit',
        routing_level: 'L1',
        confidence: 1,
        reason: 'explicit review context',
        candidates: [],
      },
      reminders: [],
    },
    context_spine: {
      schema_version: 1,
      material_id: 'mat_aigc_compare',
      canonical_ref: 'omni://review/mat_aigc_compare',
      authority: {
        material: 'MaterialStore',
        review: 'ReviewContext',
        session: 'session_binding_ledger',
        legacy_extra: 'Material.extra',
      },
      sections: [
        {
          id: 'scope',
          label: '归属',
          fields: [
            {
              key: 'project', label: '项目', value: 'walker', status: 'recorded',
              source: 'material.project', authority: 'MaterialStore', authoritative: true,
            },
            {
              key: 'plan', label: '计划',
              value: { id: 'PLAN-A', ref: 'omni://plan/PLAN-A' },
              status: 'recorded', source: 'material.source_plan_id',
              authority: 'MaterialStore', authoritative: true,
            },
          ],
        },
        {
          id: 'producer',
          label: '生产过程',
          fields: [{
            key: 'declared_producer',
            label: '模型 / 框架 / 运行声明',
            value: { model: 'seedream', run_id: 'run-42' },
            status: 'recorded',
            source: 'material.extra',
            authority: 'Material.extra (legacy)',
            authoritative: false,
          }],
        },
      ],
      relationships: [],
      completeness: {
        recorded: 8,
        expected: 10,
        ratio: 0.8,
        missing: ['producer.session', 'scope.subject'],
        delivery: 'on_material_open',
        emits_reminders: false,
      },
    },
  } as Material
}

beforeEach(() => {
  vi.restoreAllMocks()
  usePanels.getState().setTabs([], null)
})

describe('MaterialEmbed', () => {
  it('shows AIGC background and opens the canonical material in the review surface', async () => {
    vi.spyOn(reviewstageApi, 'get').mockResolvedValue(material())
    const contextSpy = vi.spyOn(reviewstageApi, 'context')

    render(<MaterialEmbed reference="omni://review/mat_aigc_compare" />)

    await screen.findByText('AIGC 三候选比较')
    expect(await screen.findByTestId('material-embed-aigc-comparison')).toBeTruthy()
    expect(screen.getByTestId('material-embed').getAttribute('data-embed-renderer'))
      .toBe('profile:aigc-comparison')
    expect(screen.getByText(/aigc-comparison · 3 个候选 · v3/)).toBeTruthy()
    expect(screen.getByText(/model: seedream · run_id: run-42/)).toBeTruthy()
    expect(screen.getByText(/8\/10 已记录，缺 2/)).toBeTruthy()
    expect(contextSpy).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /打开审阅/ }))
    await waitFor(() => {
      expect(usePanels.getState().activeId).toBe('review_material:mat_aigc_compare')
    })
  })

  it('lets a standalone embed provide a reliable full-review fallback', async () => {
    vi.spyOn(reviewstageApi, 'get').mockResolvedValue(material())
    const onOpen = vi.fn()

    render(
      <MaterialEmbed
        reference="omni://review/mat_aigc_compare"
        onOpen={onOpen}
      />,
    )

    await screen.findByText('AIGC 三候选比较')
    fireEvent.click(screen.getByRole('button', { name: /打开审阅/ }))
    expect(onOpen).toHaveBeenCalledWith('mat_aigc_compare', 'AIGC 三候选比较')
  })

  it('selects a compact renderer by carrier and keeps an explicit generic-card fallback', async () => {
    const image = material()
    image.id = 'mat_image'
    image.kind = 'image'
    image.title = '战斗截图'
    image.file_relpath = 'files/battle.png'
    image.review_context = {
      ...image.review_context!,
      profile_id: 'generic-image',
      references: [],
    }
    vi.spyOn(reviewstageApi, 'get').mockResolvedValue(image)

    const { unmount } = render(<MaterialEmbed reference="omni://review/mat_image" />)
    expect(await screen.findByTestId('material-embed-image')).toBeTruthy()
    expect(screen.getByTestId('material-embed').getAttribute('data-embed-renderer'))
      .toBe('kind:image')
    unmount()

    const web = material()
    web.id = 'mat_web'
    web.title = '普通网页'
    web.review_context = {
      ...web.review_context!,
      profile_id: 'generic-web',
      references: [],
    }
    vi.mocked(reviewstageApi.get).mockResolvedValue(web)

    render(<MaterialEmbed reference="omni://review/mat_web" />)
    await screen.findByText('普通网页')
    expect(screen.getByTestId('material-embed').getAttribute('data-embed-renderer'))
      .toBe('generic-card')
  })
})
