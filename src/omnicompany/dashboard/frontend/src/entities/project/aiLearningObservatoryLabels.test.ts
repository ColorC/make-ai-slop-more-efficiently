import { describe, expect, it } from 'vitest'
import {
  humanTextZh,
  projectScopeNoteZh,
  selectionReasonLabelZh,
  sourceCategoryLabelZh,
  statusLabelZh,
  workTypeLabelZh,
} from './aiLearningObservatoryLabels'

describe('AI 长期学习中心中文显示', () => {
  it('不把内部英文状态直接暴露给界面', () => {
    expect(statusLabelZh('inconclusive')).toBe('证据不足，暂无法判断')
    expect(statusLabelZh('not_comparable')).toBe('还没有可比较的前后样本')
    expect(statusLabelZh('future_internal_state')).toBe('未识别状态')
  })

  it('翻译工作类型、选择原因和证据来源', () => {
    expect(workTypeLabelZh('device-and-product-delivery')).toBe('设备与产品交付')
    expect(selectionReasonLabelZh('active_days_7d=4')).toBe('近 7 天活跃 4 天')
    expect(sourceCategoryLabelZh('human_intervention')).toBe('人工介入')
  })

  it('兼容翻译既有英文确定性摘要', () => {
    expect(humanTextZh(
      'Queried 10 evidence categories; 8 available and 2 unresolved. Improvement remains unproven until comparable later evidence exists.',
    )).toContain('暂未提取或关联')
  })

  it('为容易误解的项目解释本次观察范围', () => {
    expect(projectScopeNoteZh('agent-office')).toContain('不是对整个 Agent Office 的总评价')
    expect(projectScopeNoteZh('lofa')).toContain('不是要求 LOFA')
  })
})
