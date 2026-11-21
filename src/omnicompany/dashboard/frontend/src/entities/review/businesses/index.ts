/**
 * entities/review/businesses — 业务专属类型渲染器的懒加载登记入口(统一设计工作室 v2 F3)。
 *
 * 业务加材料类型 = 在这里登记一行 data_schema_id → React.lazy 组件, 不改框架代码
 * (MaterialContentView / rendererRegistry 均不动)。display profile 的"类型渲染器清单"
 * (displayProfiles.ts)只引用这里登记过的 schemaId。
 * 渲染器纪律: 只吃业务引擎 API 投影, 禁依赖业务应用的 store(如叙事 useStudio) —— 静态检查项。
 */
import { lazy } from 'react'
import { registerSchemaRenderer } from '../rendererRegistry'
// 业务顶栏工厂(件一): 从渲染器本体剥离的轻模块(2026-07 首屏拆包: 原与渲染器同源 narrative.tsx/
// voxelcraft.tsx, 会把整个渲染器拖进主包), 启动期即挂到各 RendererEntry.toolbar,
// 供 MaterialDetail 同步解析并入审阅顶栏。工厂本身轻量(纯函数); 渲染器正文仍用 React.lazy 引用。
import {
  narrativeOutlineToolbar, narrativePremiseToolbar, narrativeCharactersToolbar, narrativeDraftsToolbar,
  narrativeScenesToolbar, narrativeSettingToolbar, narrativeGuidanceToolbar, narrativeStyleEngineToolbar,
  narrativeGameTextToolbar,
} from './narrativeToolbars'
// voxelcraft 项目级资产审阅视图(前端半边): 库级浏览 + 批次视图。顶栏工厂同源剥离(blockworksToolbars.tsx)。

let registered = false

export function registerReviewBusinessRenderers(): void {
  if (registered) return
  registered = true

  // ── 叙事(vilo 租户, N1/N2): 数据=叙事内容引擎 API 投影(/narrative-studio/api/*) ──
  // toolbar: 业务顶栏并入审阅顶栏(件一), 渲染器正文不再自画第二条工具条。
  registerSchemaRenderer('narrative_outline_v1', {
    Component: lazy(() => import('./narrative').then((m) => ({ default: m.NarrativeOutlineView }))),
    toolbar: narrativeOutlineToolbar,
  })
  registerSchemaRenderer('narrative_premise_v1', {
    Component: lazy(() => import('./narrative').then((m) => ({ default: m.NarrativePremiseView }))),
    toolbar: narrativePremiseToolbar,
  })
  registerSchemaRenderer('narrative_characters_v1', {
    Component: lazy(() => import('./narrative').then((m) => ({ default: m.NarrativeCharactersView }))),
    toolbar: narrativeCharactersToolbar,
  })
  registerSchemaRenderer('narrative_drafts_v1', {
    Component: lazy(() => import('./narrative').then((m) => ({ default: m.NarrativeDraftsView }))),
    toolbar: narrativeDraftsToolbar,
  })
  // 2026-07-05 用户裁决 DEC-2026-07-05-039 后补齐: 覆盖工作室四层 IA 的其余载体
  registerSchemaRenderer('narrative_scenes_v1', {
    Component: lazy(() => import('./narrative').then((m) => ({ default: m.NarrativeScenesView }))),
    toolbar: narrativeScenesToolbar,
  })
  registerSchemaRenderer('narrative_setting_v1', {
    Component: lazy(() => import('./narrative').then((m) => ({ default: m.NarrativeSettingView }))),
    toolbar: narrativeSettingToolbar,
  })
  registerSchemaRenderer('narrative_guidance_v1', {
    Component: lazy(() => import('./narrative').then((m) => ({ default: m.NarrativeGuidanceView }))),
    toolbar: narrativeGuidanceToolbar,
  })
  registerSchemaRenderer('narrative_style_engine_v1', {
    Component: lazy(() => import('./narrative').then((m) => ({ default: m.NarrativeStyleEngineView }))),
    toolbar: narrativeStyleEngineToolbar,
  })
  registerSchemaRenderer('narrative_gametext_v1', {
    Component: lazy(() => import('./narrative').then((m) => ({ default: m.NarrativeGameTextView }))),
    toolbar: narrativeGameTextToolbar,
  })

  // ── voxelcraft(资产同化管线重建, DEC-2026-07-06-081/024): 数据=资产库只读 API 投影
  //    (/voxelcraft-assets/api/*, dashboard 同源反代懒启动)。toolbar 并入审阅顶栏(件一)。 ──

  // ── 游戏素材治理(walker 首个成员, 2026-08-02 治理体系化): 数据=walker-game 注册表投影 ──
  // 聚合审阅报告: 引用徽章墙 + 常驻 asset-stage 内嵌壳(点徽章 postMessage 热切换, 不重载)。
  // toolbar: 业务身份并入审阅顶栏(件一); 渲染器正文不画第二条顶栏。
  registerSchemaRenderer('game.report.aggregate-v1', {
    Component: lazy(() => import('./gameAssetsReport').then((mod) => ({ default: mod.GameAssetsReportView }))),
    fullBleed: true,
    toolbar: (m) => ({
      icon: '🗂️',
      title: '素材治理聚合报告',
      sub: m.version != null ? `v${m.version}` : undefined,
      chips: [
        {
          label: `${(((((m.extra ?? {}) as Record<string, unknown>).citations) as unknown[] | undefined) ?? []).length} 件引用`,
          title: '报告引用的注册表素材 ID 数',
        },
      ],
      actions: [
        {
          label: '展示壳',
          title: '新页签打开 asset-stage（walker 素材标准展示终端）',
          onClick: () => window.open('/walker-game/asset-stage.html', '_blank', 'noopener'),
        },
      ],
    }),
  })
}
