import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { HtmlMaterialView } from './MaterialViews'

function material(overrides: Partial<Material>): Material {
  return {
    id: 'mat_parent',
    kind: 'html',
    tier: 'important',
    title: '复合网页',
    status: 'pending',
    source_subagent_id: null,
    source_plan_id: 'dashboard/test',
    file_relpath: null,
    inline_content: '<html><body></body></html>',
    annotations: [],
    comments: [],
    annotations_allowed: true,
    created_at: '',
    updated_at: '',
    history: [],
    pushed_to_user: false,
    pushed_reason: null,
    pushed_at: null,
    extra: {},
    ...overrides,
  } as Material
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('HTML composite review embedding', () => {
  it('mounts the canonical MaterialEmbed into a same-origin declarative placeholder', async () => {
    vi.spyOn(reviewstageApi, 'get').mockResolvedValue(material({
      id: 'mat_table',
      kind: 'custom_web_template',
      title: '经济表格差异审阅',
      version: 4,
      review_context: {
        profile_id: 'spreadsheet-review',
        profile_version: 1,
        schema_id: 'table_diff_v1',
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
    }))

    render(<HtmlMaterialView m={material({})} />)
    const iframe = screen.getByTestId('material-html') as HTMLIFrameElement
    const embeddedDocument = document.implementation.createHTMLDocument('embedded review')
    const placeholder = embeddedDocument.createElement('section')
    placeholder.dataset.reviewMaterialEmbed = 'omni://review/mat_table'
    placeholder.dataset.reviewMaterialLabel = '表格变更'
    embeddedDocument.body.appendChild(placeholder)
    Object.defineProperty(iframe, 'contentDocument', {
      configurable: true,
      value: embeddedDocument,
    })

    fireEvent.load(iframe)

    await waitFor(() => expect(placeholder.textContent).toContain('表格变更'))
    expect(placeholder.textContent).toContain('spreadsheet-review')
    expect(placeholder.textContent).toContain('打开审阅')
  })
})
