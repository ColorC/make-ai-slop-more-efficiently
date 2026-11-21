/**
 * entities/review/rendererRegistry — 材料类型 → 渲染器的唯一分发注册表。
 *
 * 统一设计工作室 v2(材料展示框架, DEC-2026-07-05-030)F3:
 * "分材料类型各自展示"的机制层 —— kind 与业务专属类型(extra.data_schema_id)都在这一张表里
 * 登记与解析, 前端只允许这一处类型分发实现(双重权威 A12, 由 studio_authority_audit.py 机械核查)。
 *
 * - 内建 kind 渲染器由 MaterialViews.tsx 在模块顶层登记(七类 kind + 五型工作报告别名)。
 * - 业务专属渲染器(叙事大纲/立意/角色卡…)由 entities/review/businesses 用 React.lazy 懒加载登记,
 *   业务加类型不改框架代码(display profile 的"类型渲染器清单"只引用这里的 schemaId)。
 * - 解析优先级: extra.data_schema_id > review_context.schema_id > kind;
 *   都不中返回 undefined, 消费方给显式回退。
 */
import type { ComponentType, LazyExoticComponent, ReactNode } from 'react'
import type { Material } from '../../api/reviewstageClient'

export interface MaterialRendererProps { m: Material }
export interface MaterialEmbedRendererProps { m: Material }

/**
 * 业务顶栏并入审阅顶栏的唯一协议(件一, DEC-2026-07-06-082/083)。
 *
 * 背景: 过去业务渲染器(如叙事九视图)会在正文里自画"第二条工具条"(NarrativeToolbar),
 * 与外层审阅顶栏(MaterialDetail)并存 → 双顶栏。纪律: **禁止渲染器自画第二条顶栏**,
 * 业务想在顶栏露出自己的图标/标题/适用裁决/跳转按钮, 一律经此声明式协议交给 MaterialDetail,
 * 由它合并成单条顶栏(左=业务身份, 中=业务 chips/aux, 右=业务 actions + 审阅动作永不丢)。
 *
 * 项目级自定义面只有两处: 侧栏结构=displayProfiles.structure(域层级注册),
 * 顶栏=这里的 RendererEntry.toolbar。除此之外渲染器只管正文。
 */
export interface BusinessToolbarSpec {
  /** 业务身份图标(左侧, ReactNode 或组件返回的元素)。 */
  icon?: ReactNode
  /** 业务标题(左侧, 替代顶栏默认的材料标题区; 原材料标题仍以 title 属性/悬浮可查)。 */
  title: string
  /** 副标题(标题旁的弱字说明)。 */
  sub?: string
  /** 中间信息 chip(如"适用裁决 N"); title=悬浮说明。 */
  chips?: Array<{ label: string; title?: string; onClick?: () => void }>
  /**
   * 业务自带的活体附加件(可选): 需异步取数或带自身状态的中间区小部件
   * (如叙事"适用裁决"chip + 可展开原话面板)整体作为一个自洽 React 节点塞进合并栏中间区。
   * 仍属"业务顶栏"范畴、由 MaterialDetail 挂载, 不构成第二条顶栏。
   */
  aux?: ReactNode
  /** 业务动作按钮(右侧, 排在审阅动作之前; 如"决策历程"跳转)。 */
  actions?: Array<{ label: string; icon?: ReactNode; title?: string; onClick: () => void }>
}

export interface RendererEntry {
  Component: ComponentType<MaterialRendererProps> | LazyExoticComponent<ComponentType<MaterialRendererProps>>
  /** 占满型(内含 iframe/复合视图): 容器用 flex 撑满, 让内部 iframe 拉满高度。 */
  fullBleed?: boolean
  /** 文档型(文字内容): 内容区挂"选中即评论"层。 */
  document?: boolean
  /**
   * 业务顶栏工厂(可选): 有则 MaterialDetail 把它并入审阅顶栏, 渲染成单条合并栏
   * (业务身份/chips/actions + 审阅 tier/status/verdict/kebab)。无则顶栏一切照旧(零回归)。
   * 唯一的"业务顶栏"入口 —— 渲染器正文内绝不许再画第二条工具条。
   */
  toolbar?: (m: Material) => BusinessToolbarSpec
}

/**
 * 紧凑嵌入视图是同一材料 renderer 注册表的一个投影槽，不是第二套 Material/profile 语义注册表。
 *
 * 完整视图和嵌入视图的容器约束不同，不能把任意 fullBleed iframe 直接塞进 Markdown 段落；
 * 因而 capability 可以为同一 kind/schema/profile 登记一个安全的 compact renderer。缺失时
 * MaterialEmbed 必须回退到统一背景卡和完整审阅入口。
 */
export interface MaterialEmbedRendererEntry {
  Component: ComponentType<MaterialEmbedRendererProps>
    | LazyExoticComponent<ComponentType<MaterialEmbedRendererProps>>
  rendererId: string
}

const kindRenderers = new Map<string, RendererEntry>()
const schemaRenderers = new Map<string, RendererEntry>()
const kindEmbedRenderers = new Map<string, MaterialEmbedRendererEntry>()
const schemaEmbedRenderers = new Map<string, MaterialEmbedRendererEntry>()
const profileEmbedRenderers = new Map<string, MaterialEmbedRendererEntry>()

export function registerKindRenderer(kind: string, entry: RendererEntry): void {
  kindRenderers.set(kind, entry)
}

export function registerSchemaRenderer(schemaId: string, entry: RendererEntry): void {
  schemaRenderers.set(schemaId, entry)
}

export function registerKindEmbedRenderer(
  kind: string,
  entry: MaterialEmbedRendererEntry,
): void {
  kindEmbedRenderers.set(kind, entry)
}

export function registerSchemaEmbedRenderer(
  schemaId: string,
  entry: MaterialEmbedRendererEntry,
): void {
  schemaEmbedRenderers.set(schemaId, entry)
}

export function registerProfileEmbedRenderer(
  profileId: string,
  entry: MaterialEmbedRendererEntry,
): void {
  profileEmbedRenderers.set(profileId, entry)
}

/** 材料声明的载体/审阅 schema；一等 review_context 不再需要复制到 extra。 */
export function schemaIdOf(m: Pick<Material, 'extra' | 'review_context'>): string | undefined {
  const raw = (m.extra as Record<string, unknown> | undefined)?.data_schema_id
  if (typeof raw === 'string' && raw) return raw
  const reviewSchema = m.review_context?.schema_id
  return typeof reviewSchema === 'string' && reviewSchema ? reviewSchema : undefined
}

/** 唯一解析入口: 声明式 schema 优先, 其次 kind; 未注册显式回退, 不白屏。 */
export function resolveMaterialRenderer(
  m: Pick<Material, 'kind' | 'extra' | 'review_context'>,
): RendererEntry | undefined {
  const schemaId = schemaIdOf(m)
  if (schemaId) {
    const hit = schemaRenderers.get(schemaId)
    if (hit) return hit
  }
  return kindRenderers.get(m.kind as string)
}

/**
 * 嵌入解析优先级：schema > review profile > carrier kind。
 *
 * schema 是最精确的内容合同；profile 表示专门审阅场景（例如 image carrier 上的 AIGC 候选）；
 * kind 最后提供安全的基础预览。三者都没有时返回 undefined，由 MaterialEmbed 显式通用回退。
 */
export function resolveMaterialEmbedRenderer(
  m: Pick<Material, 'kind' | 'extra' | 'review_context'>,
): MaterialEmbedRendererEntry | undefined {
  const schemaId = schemaIdOf(m)
  if (schemaId) {
    const schemaHit = schemaEmbedRenderers.get(schemaId)
    if (schemaHit) return schemaHit
  }
  const profileId = m.review_context?.profile_id
  if (profileId) {
    const profileHit = profileEmbedRenderers.get(profileId)
    if (profileHit) return profileHit
  }
  return kindEmbedRenderers.get(m.kind as string)
}

/** 已注册的业务类型 id 清单(display profile 校验/测试用)。 */
export function registeredSchemaIds(): string[] {
  return [...schemaRenderers.keys()]
}

/** 已注册的 kind 清单(测试用)。 */
export function registeredKinds(): string[] {
  return [...kindRenderers.keys()]
}

/** 嵌入 renderer 清单只用于能力投影/测试，不作为 Material 语义真源。 */
export function registeredEmbedRendererIds(): string[] {
  return [
    ...schemaEmbedRenderers.values(),
    ...profileEmbedRenderers.values(),
    ...kindEmbedRenderers.values(),
  ].map((entry) => entry.rendererId)
}
