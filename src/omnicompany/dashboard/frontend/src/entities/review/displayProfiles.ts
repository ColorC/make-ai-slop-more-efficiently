/**
 * entities/review/displayProfiles — 业务展示区配置(统一设计工作室 v2 F2, DEC-2026-07-05-030)。
 *
 * 每业务一份配置。前三项来自一期；第四项 subjectHierarchy 是 2026-07-20 用户明确提出的
 * 内容运营失效边界：同项目包含多期内容时，必须按主体/整期版本组织，不能继续把标题平铺。
 *   1. 结构排布   structure: 材料按业务的流水线顺序或结构分区展示。
 *      只允许引用数据源名('domain-tree'=域层级注册投影 GET /domain-tree; 'track'=按材料 track 陈列),
 *      **禁止内联层级数组**(双重权威 A13: 业务展示结构唯一真源=域层级注册, 这里只引用)。
 *   2. 版本策略   versionPolicy: 三型(纯展示层策略, 底层 version_family 链一条不动):
 *      coexist          多版本并存(全部版本片可见)
 *      latest-collapse  只有最新, 其余全部折叠在历史
 *      latest-selective 最新为主, 其余选择性展示(材料 extra.display_pinned 为真的版本保持可见)
 *      可按层(track)覆写 —— 仍属"版本策略"这一项配置。
 *   3. 类型渲染器清单 schemaRenderers: 该业务的专属材料类型(data_schema_id), 渲染器本体
 *      在 entities/review/businesses 懒加载登记进 rendererRegistry, 这里只列清单。
 *   4. 主体层级 subjectHierarchy: project → subject → revision → track → material。
 *      只声明显示方式；subject/revision 真值来自 Material 正式字段，不从标题猜。
 *
 * 未配置的项目回落 DEFAULT_PROFILE(按 track 陈列 + 多版本并存), 不白屏。
 *
 * 项目级自定义面纪律(件一 DEC-2026-07-06-082/083): 一个业务只有两处可定制展示 ——
 *   ①侧栏结构 = 这里的 structure(域层级注册投影, 唯一真源), 承载材料分区顺序;
 *   ②审阅顶栏 = rendererRegistry.RendererEntry.toolbar(业务顶栏并入审阅顶栏的唯一协议)。
 * 除此之外, **禁止渲染器在正文里自画第二条顶栏**(双顶栏问题的根治); 渲染器只管正文内容。
 */

export type VersionPolicy = 'coexist' | 'latest-collapse' | 'latest-selective'

export interface DisplayProfile {
  /** 结构排布数据源: 'domain-tree'=复用域层级注册(唯一真源), 'track'=按材料 track。 */
  structure: 'domain-tree' | 'track'
  versionPolicy: VersionPolicy
  /** 按层(track)覆写版本策略, 键=层级名。 */
  trackVersionPolicy?: Record<string, VersionPolicy>
  /** 业务专属材料类型清单(data_schema_id, 渲染器在 businesses 登记)。 */
  schemaRenderers?: string[]
  subjectHierarchy?: {
    label: string
    subjectType: string
    includeArchivedHistory?: boolean
  }
}

export const DEFAULT_PROFILE: DisplayProfile = {
  structure: 'track',
  versionPolicy: 'coexist',
}

const PROFILES: Record<string, DisplayProfile> = {
  // 叙事(vilo, 二期首个业务定制): 排布=叙事域层级注册(工作室四层 IA 原词,
  // 用户 07-05 裁决 DEC-2026-07-05-039:忠实迁移工作室结构,「世界圣经」弃用);
  // 创作稿多轮迭代 → 只看最新, 旧版折叠进历史(用户 07-05:"只有最新,其余的全部折叠在历史")。
  vilo: {
    structure: 'domain-tree',
    versionPolicy: 'latest-collapse',
    schemaRenderers: [
      'narrative_outline_v1',
      'narrative_premise_v1',
      'narrative_characters_v1',
      'narrative_drafts_v1',
      'narrative_scenes_v1',
      'narrative_setting_v1',
      'narrative_guidance_v1',
      'narrative_style_engine_v1',
      'narrative_gametext_v1',
    ],
  },
  // 前端设计(walker, 对照回归): 沿用轨迹画布口径, 多版本并存(样张 v1/v2 版本片并排)。
  walker: {
    structure: 'track',
    versionPolicy: 'coexist',
  },
  // demogame 策划案(G1 样板, DEC-2026-07-05-005 收编落点): 三层排布=细化案域层级注册;
  // 策划案以最新为主, 被钉住的旧版选择性展示(第三型)。
  'demogame-design': {
    structure: 'domain-tree',
    versionPolicy: 'latest-selective',
  },
  // 前端设计域本体(G2 样板): 四层排布(信息审计/交互审计/设计稿/实际稿, DEC-2026-07-04-230)。
  'frontend-design': {
    structure: 'domain-tree',
    versionPolicy: 'coexist',
  },
  // voxelcraft(资产同化管线重建, DEC-2026-07-06-081): 四层排布=资产域层级注册
  //   (资产管线批次/资产库/生产样例/工作报告); 批次/库总览多轮迭代 → 只看最新, 旧版折叠进历史。
  voxelcraft: {
    structure: 'domain-tree',
    versionPolicy: 'latest-collapse',
    schemaRenderers: [
      'voxelcraft_asset_library_v1',
      'voxelcraft_asset_batch_v1',
    ],
  },
  // B 站发布(bilibili-publish, 发布工作台 MVP 2026-07-19): 九层排布=视频发布域层级注册
  //   (选题/母稿/台本/写稿/配音/素材/成片/发布包/专栏); 文稿类多轮迭代 → 只看最新, 旧版折叠进历史。
  'bilibili-publish': {
    structure: 'domain-tree',
    versionPolicy: 'latest-collapse',
    subjectHierarchy: {
      label: '视频',
      subjectType: 'episode',
      includeArchivedHistory: true,
    },
  },
}

export function getDisplayProfile(project: string): DisplayProfile {
  return PROFILES[project] ?? DEFAULT_PROFILE
}

/** 某层生效的版本策略(层覆写 > 业务默认)。 */
export function versionPolicyFor(profile: DisplayProfile, track: string): VersionPolicy {
  return profile.trackVersionPolicy?.[track] ?? profile.versionPolicy
}

/** 测试/核查用: 全部已配置项目 id。 */
export function configuredProjects(): string[] {
  return Object.keys(PROFILES)
}
