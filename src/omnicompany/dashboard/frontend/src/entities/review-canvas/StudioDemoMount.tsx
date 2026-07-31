/**
 * StudioDemoMount — 统一设计工作室引导演示的挂载钩(计划 §8.3 第 1 条)。
 *
 * URL 带 ?demo=<tourId> 时,在整个驾驶舱之上挂 wiki-core 的引导演示覆盖层,驱动**真实 dashboard UI**
 * 走完对应 TourScript(见 demo-tours.ts)。不带 ?demo= 时什么都不渲染——玩家/日常面零泄漏,不加任何
 * 新导航入口。?demo= 与 ?dev=1 / ?open_type= 同族,只由带参地址(审阅台 live_url)触发。
 *
 * 运行时设施唯一 = wiki-core `demo` 模块(mountDemoTour + createDemoCommentStore),这里不 fork、不改内容,
 * 只按现有 @wiki-core vendor 引入方式(同 WebgameSpecView 的 @wiki-core/render)消费。
 *
 * 跨路由导航:wiki-core 内置 action 集(click/waitFor/waitMs/clickCell/eval)不含“打开某项目页”,
 * 故由本组件注册一个 `openProject` eval 钩子——用既有 panelsStore.openTab 打开项目页签(和用户点项目卡
 * 同一条真实路径),再等 project-detail 挂载。TourScript 里用 { type:'eval', ref:'openProject', project, facet }
 * 声明式调用它,不内联函数(纯 JSON)。
 */
import { useEffect, useRef } from 'react'
import { mountDemoTour, type DemoTourHandle } from '@wiki-core/demo'
import { createDemoCommentStore } from '@wiki-core/comments'
import '@wiki-core/demo.css'
import { usePanels } from '../../stores/panelsStore'
import { getStudioTour, STUDIO_DEMO_MATERIAL_ID } from './demo-tours'

function waitFor(sel: string, timeoutMs = 8000): Promise<void> {
  return new Promise((resolve) => {
    const start = Date.now()
    const tick = () => {
      if (document.querySelector(sel) || Date.now() - start > timeoutMs) return resolve()
      window.setTimeout(tick, 60)
    }
    tick()
  })
}

/** 打开某项目页签(和点项目卡同一条真实路径),等 project-detail 与其内容真出现后返回。 */
async function openProject(action: { project?: string; facet?: string }): Promise<void> {
  const project = String(action.project || '')
  if (!project) return
  const facet = action.facet ? String(action.facet) : undefined
  usePanels.getState().openTab({ type: 'project', id: project }, project, facet)
  await waitFor('[data-testid="project-detail"]')
  // 页面壳出现≠内容就绪:再等主视图容器(画布/决策树)挂上,叙述才有的可指。
  if (facet === 'tree') await waitFor('[data-testid="structure-view"]', 12000)
  else await waitFor('[data-testid="review-canvas"]', 12000)
}

// openDecisionTree 钩子已撤(裸 DAG 决策树视图被用户裁决 DEC-2026-07-04-240 封禁;
// 新形态=具象化管线,DEC-233/239,回归时另立入口)。

/** 是否运行在 iframe 里(审阅台材料把 live_url 嵌 iframe 时,驾驶舱会嵌进驾驶舱=套娃)。 */
function isNested(): boolean {
  try {
    return window.self !== window.top
  } catch {
    return true
  }
}

export default function StudioDemoMount() {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const handleRef = useRef<DemoTourHandle | null>(null)
  const params = new URLSearchParams(window.location.search)
  const wantsTour = !!getStudioTour(params.get('demo'))
  const nested = wantsTour && isNested()

  useEffect(() => {
    if (nested) return   // 套娃场景不挂演示,由下方引导面板接管
    const params = new URLSearchParams(window.location.search)
    const tour = getStudioTour(params.get('demo'))
    if (!tour || !rootRef.current) return

    // 应用刚启动时项目名录/面板还在异步水合,立刻开演示会抢跑:第一步打开的项目页是降级壳
    // (标题裸名/工作选项未注册/画布载入中)。等驾驶舱壳可见后再安定一段,才开场。
    let cancelled = false
    ;(async () => {
      await waitFor('[data-testid="cockpit-shell"]', 20000)
      await new Promise((r) => window.setTimeout(r, 2500))
      if (cancelled || !rootRef.current) return

      const reducedMotion =
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches

      handleRef.current = mountDemoTour(rootRef.current, {
        tour,
        appRoot: document,
        // 每步评论落到总验收演示材料(target.kind='demo_step');材料未就绪时评论提交会报错但不影响演示走查。
        comments: createDemoCommentStore({ materialId: STUDIO_DEMO_MATERIAL_ID }),
        hooks: { openProject },
        autoplay: false,
        reducedMotion,
      })
    })()
    return () => {
      cancelled = true
      handleRef.current?.destroy()
      handleRef.current = null
    }
  }, [nested])

  // 套娃守卫:审阅台 iframe 里打开演示地址时,不在嵌套驾驶舱里播(视觉套娃+目标错位),
  // 改为整屏引导面板,让用户一键在顶层新标签页打开。
  if (nested) {
    return (
      <div data-testid="studio-demo-nested-guard" style={{
        position: 'fixed', inset: 0, zIndex: 9999, display: 'flex', alignItems: 'center',
        justifyContent: 'center', background: 'var(--fp-bg, #0b0e14)',
      }}>
        <div style={{
          maxWidth: 460, padding: '28px 32px', borderRadius: 12, textAlign: 'center',
          background: 'var(--fp-glass, rgba(255,255,255,.04))', border: '1px solid var(--fp-border, rgba(255,255,255,.12))',
          color: 'var(--fp-text, #dde3ee)', fontFamily: 'var(--fp-font-sans, sans-serif)',
        }}>
          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 10 }}>引导演示要在独立标签页里播放</div>
          <div style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--fp-text-2, #9aa3b5)', marginBottom: 18 }}>
            这份材料嵌在审阅台里打开时,驾驶舱会嵌进驾驶舱,演示没法正常指引。
            点下面的按钮在新标签页打开,演示会带着你走完全部步骤。
          </div>
          <a href={window.location.href} target="_blank" rel="noreferrer"
            data-testid="studio-demo-open-top"
            style={{
              display: 'inline-block', padding: '9px 22px', borderRadius: 8, textDecoration: 'none',
              background: 'var(--fp-accent, #4c8dff)', color: '#fff', fontSize: 14, fontWeight: 600,
            }}>
            在新标签页打开演示
          </a>
        </div>
      </div>
    )
  }

  // 覆盖层 root:不带 ?demo= 时是空 div(zero-cost);带参时 wiki-core 往里挂 spotlight/card/comment-panel。
  return <div ref={rootRef} data-testid="studio-demo-mount" />
}
