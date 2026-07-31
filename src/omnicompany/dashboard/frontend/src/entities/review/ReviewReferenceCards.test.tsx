import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { ReviewReferenceCards } from './ReviewReferenceCards'

describe('ReviewReferenceCards', () => {
  it('hydrates canonical comparison members with AIGC producer background', async () => {
    vi.spyOn(reviewstageApi, 'get').mockResolvedValue({
      id: 'mat_candidate',
      title: '全幅彩色卡图',
      extra: {
        generation_model: 'seedream-5',
        generation_run_id: 'run-42',
        candidate_style: 'fullbleed',
        candidate_id: 5866,
        candidate_index: 1,
        aigc_lab_url: '/api/local/aigc-review/?candidate=5866',
      },
      review_context: {
        profile_id: 'aigc-candidate',
        profile_version: 1,
        schema_id: '',
        references: [],
        resolution: {
          selected_by: 'explicit',
          routing_level: 'L1',
          confidence: 1,
          reason: 'explicit',
          candidates: [],
        },
        reminders: [],
      },
      context_spine: {
        completeness: { recorded: 8, expected: 9 },
      },
    } as unknown as Material)

    render(
      <ReviewReferenceCards references={[{
        target: 'omni://review/mat_candidate',
        relation: 'comparison_member',
        label: '候选 A',
      }]} />,
    )

    await screen.findByText('全幅彩色卡图')
    expect(screen.getByText('seedream-5')).toBeTruthy()
    expect(screen.getByText('run-42')).toBeTruthy()
    expect(screen.getByText('fullbleed')).toBeTruthy()
    expect(screen.getByRole('link', { name: /审阅候选/ }).getAttribute('href'))
      .toBe('/?surface=material&id=mat_candidate')
    expect(screen.getByRole('link', { name: /打开 AIGC Lab/ }).getAttribute('href'))
      .toBe('/api/local/aigc-review/?candidate=5866')
  })
})
