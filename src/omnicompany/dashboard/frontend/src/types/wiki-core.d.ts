// 共用 wiki 核（webworks/packages/wiki-core，经 vite alias @wiki-core 引入）的最小类型壳。
// 正本是纯 JS + 兄弟 .d.ts；这里只声明 dashboard 用到的门面(渲染 / 引导演示 / 评论)。

declare module '@wiki-core/render' {
  export interface WikiRenderer {
    render(markdown: string): string
  }
  export function createRenderer(options?: Record<string, unknown>): WikiRenderer
  export function stripFrontmatter(content: string): string
}

// 引导演示脚本核(TourScript 类型 + 校验 + 锚点 + 共享 action 执行器)。
// 权威 schema = wiki-core/demo-script.d.ts;这里逐字对齐它导出的类型。
declare module '@wiki-core/demo-script' {
  export type TourAction =
    | { type: 'click'; target: string; timeoutMs?: number }
    | { type: 'clickCell'; q: number; r: number }
    | { type: 'waitFor'; target: string; timeoutMs?: number }
    | { type: 'waitMs'; ms: number }
    | { type: 'eval'; ref: string; [k: string]: unknown }

  export interface TourLink {
    rel: 'doc_paragraph' | 'material' | 'demo_step'
    href: string
    label?: string
  }

  export interface TourStep {
    id: string
    title?: string
    narration: string
    target?: string
    action?: TourAction
    autoplayMs?: number
    screenshotRef?: string
    links?: TourLink[]
  }

  export interface TourScript {
    id: string
    title?: string
    app?: string
    version?: number
    steps: TourStep[]
  }

  export interface DemoActionHooks {
    clickCell?: (q: number, r: number) => void | Promise<void>
    [name: string]: ((action: any) => void | Promise<void>) | undefined
  }

  export interface DemoStepAnchor {
    kind: 'demo_step'
    tour_id: string
    step_id: string
    step_index: number
    title: string
  }

  export function validateTour(tour: TourScript): string[]
  export function stepAnchor(tour: TourScript, step: TourStep, index?: number): DemoStepAnchor
  export function executeAction(
    action: TourAction | undefined,
    ctx?: { appRoot?: ParentNode; hooks?: DemoActionHooks },
  ): Promise<void>
}

// 引导演示覆盖层(mountDemoTour)。
declare module '@wiki-core/demo' {
  import type { TourScript, TourStep, DemoActionHooks } from '@wiki-core/demo-script'
  import type { DemoCommentStore } from '@wiki-core/comments'

  export interface MountDemoTourOptions {
    tour: TourScript
    appRoot?: ParentNode
    comments?: DemoCommentStore | null
    hooks?: DemoActionHooks
    autoplay?: boolean
    reducedMotion?: boolean
    onStep?: (s: { index: number; step: TourStep }) => void
  }

  export interface DemoTourHandle {
    goTo(index: number): void
    next(): Promise<void>
    back(): Promise<void>
    play(): void
    pause(): void
    getCurrent(): { index: number; step: TourStep } | null
    stepCount(): number
    destroy(): void
  }

  export function mountDemoTour(rootEl: Element, opts: MountDemoTourOptions): DemoTourHandle
}

// 段落 / 演示步评论存储(reviewstage 适配器)。
declare module '@wiki-core/comments' {
  export function paragraphHash(text: string): string
  export function snippetOf(text: string): string

  export interface WikiComment {
    id: string
    content: string
    author: string
    target: { kind: string; page: string; para_hash: string; snippet: string; selected_text?: string }
  }
  export interface CommentStore {
    list(page?: string): Promise<WikiComment[]>
    add(input: { page: string; paraText: string; selectedText?: string; content: string }): Promise<unknown>
  }
  export function createReviewstageCommentStore(opts: { endpoint?: string; materialId: string }): CommentStore

  export interface DemoCommentStore {
    list(): Promise<unknown[]>
    add(input: { target: unknown; content: string }): Promise<unknown>
  }
  export function createDemoCommentStore(opts: { endpoint?: string; materialId: string }): DemoCommentStore
}

// 覆盖层样式(副作用 import)。
declare module '@wiki-core/demo.css'
declare module '@wiki-core/ui.css'
