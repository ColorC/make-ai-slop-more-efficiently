import { describe, it, expect } from 'vitest'
import { validateTour } from '@wiki-core/demo-script'
import { STUDIO_TOURS, getStudioTour } from './demo-tours'

// 统一设计工作室引导演示脚本的结构 + 规范守卫(计划 §8.3 / 引导演示材料规范)。
// 快确定性测:tour 过 wiki-core 校验、步骤 id 稳定唯一、narration 中文非空、action 纯声明式、
// 跨路由只用注册钩子。真 UI 驱动由 tests/e2e/studio_demo_tour.spec.ts 负责。
// 决策树三跳段已随裸 DAG 视图撤下(DEC-2026-07-04-240),现只剩 walker 一条 tour。

const tours = Object.values(STUDIO_TOURS)

// action 允许的类型集(纯 JSON 声明式,禁内联函数)。
const ALLOWED_ACTIONS = new Set(['click', 'clickCell', 'waitFor', 'waitMs', 'eval'])
// 中文-only gate 抽检:narration 里不得出现英文开发术语(允许命令行报错原文里的 --project 等,
// 那是场景八“真报错照录”,单列白名单)。
const HAS_CJK = /[一-鿿]/

describe('studio demo tours', () => {
  it('注册表含 walker tour;被封禁的决策树段不在册', () => {
    expect(getStudioTour('studio-walker')?.id).toBe('studio-walker')
    expect(getStudioTour('studio-demogame-tree')).toBeNull()   // 裸DAG外观封禁(DEC-240)
    expect(getStudioTour('nope')).toBeNull()
    expect(getStudioTour(null)).toBeNull()
  })

  for (const tour of tours) {
    describe(tour.id, () => {
      it('过 wiki-core validateTour(零警告)', () => {
        expect(validateTour(tour)).toEqual([])
      })

      it('步骤 id 稳定唯一, narration 中文非空', () => {
        const ids = new Set<string>()
        for (const s of tour.steps) {
          expect(s.id, `${tour.id} 有空步骤 id`).toBeTruthy()
          expect(ids.has(s.id), `${tour.id} 步骤 id 重复: ${s.id}`).toBe(false)
          ids.add(s.id)
          expect(s.narration.trim().length, `${s.id} narration 空`).toBeGreaterThan(0)
          expect(HAS_CJK.test(s.narration), `${s.id} narration 非中文`).toBe(true)
        }
      })

      it('action 纯声明式(类型在允许集内, 无内联函数)', () => {
        for (const s of tour.steps) {
          if (!s.action) continue
          expect(ALLOWED_ACTIONS.has(s.action.type), `${s.id} action 类型非法: ${s.action.type}`).toBe(true)
          // 纯 JSON:序列化再解析应完全还原(内联函数会丢)
          expect(JSON.parse(JSON.stringify(s.action))).toEqual(s.action)
        }
      })

      it('target 选择器复用已有 data-testid', () => {
        for (const s of tour.steps) {
          if (!s.target) continue
          expect(s.target, `${s.id} target 未走 data-testid`).toMatch(/\[data-testid=/)
        }
      })
    })
  }

  it('跨路由导航只用注册过的 eval 钩子(内置 action 集不覆盖跨页)', () => {
    // StudioDemoMount 注册的钩子:openProject(项目页)。
    const HOOKS = new Set(['openProject'])
    for (const tour of tours) {
      for (const s of tour.steps) {
        if (s.action?.type === 'eval') {
          expect(HOOKS.has(s.action.ref), `${s.id} eval ref 未注册: ${s.action.ref}`).toBe(true)
          expect(typeof (s.action as Record<string, unknown>).project).toBe('string')
        }
      }
    }
    // walker tour 第一步开 walker 项目页
    const walkerFirst = STUDIO_TOURS['studio-walker'].steps[0].action as Record<string, unknown>
    expect(walkerFirst.ref).toBe('openProject')
    expect(walkerFirst.project).toBe('walker')
  })
})
