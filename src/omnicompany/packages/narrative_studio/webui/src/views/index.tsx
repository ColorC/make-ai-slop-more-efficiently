// 视图注册表(v2 四层 IA)。导航只列内容四层 + 归档;工具/子视图 nav:false,经顶栏/上下文进入。
import type React from "react";
// 叙事指导层
import { PremiseView } from "./PremiseView";
import { BackgroundView } from "./BackgroundView";
import { StructureView } from "./StructureView";
import { AudienceView } from "./AudienceView";
import { RevealView } from "./RevealView";
// 叙事事实层
import { ScenesView } from "./ScenesView";          // 情节(客观事实)
import { ReasoningView } from "./ReasoningView";     // 情节推理
import { CharactersView } from "./CharactersView";
import { WorldView } from "./WorldView";
import { RelationshipsView } from "./RelationshipsView";
// 落地指导层
import { StyleView } from "./StyleView";             // 文风矩阵
import { EngineView } from "./EngineView";           // 落地结构演算引擎(路线+数值+演练)
import { RouteGraphView } from "./RouteGraphView";
import { VariablesView } from "./VariablesView";
import { PlaythroughView } from "./PlaythroughView";
// 落地层
import { GameTextView } from "./GameTextView";       // 游戏内文本(写回 wiki)
import { DraftsView } from "./DraftsView";           // 草稿看板
// 归档
import { RejectedArchiveView } from "./RejectedArchiveView";
// 工具(非导航,折进上下文)
import { HealthView } from "./HealthView";
import { CompletenessView } from "./CompletenessView";
import { TraceView } from "./TraceView";
import { DistributionView } from "./DistributionView";
import { ProvenanceView } from "./ProvenanceView";
import { DiffView } from "./DiffView";
import { CommentsView } from "./CommentsView";
import { TagsView } from "./TagsView";

export interface ViewDef {
  id: string;
  label: string;
  group: string;
  countKey?: string;
  nav?: boolean;     // false=不在左侧导航(经顶栏/上下文进入)
  Component: React.ComponentType<{ onChanged?: () => void }>;
}

export const VIEW_GROUPS: { id: string; label: string; collapsible?: boolean }[] = [
  { id: "guidance", label: "① 叙事指导层" },
  { id: "fact", label: "② 叙事事实层" },
  { id: "realize-guide", label: "③ 落地指导层" },
  { id: "realize", label: "④ 落地层" },
  { id: "archive", label: "归档" },
  { id: "tools", label: "工具", collapsible: true },  // 折叠组:默认收起,不再塞顶栏常驻按钮
];

export const VIEWS: ViewDef[] = [
  // ① 叙事指导层
  { id: "premise", label: "立意", group: "guidance", Component: PremiseView, nav: true },
  { id: "background", label: "背景 / 思考", group: "guidance", Component: BackgroundView, nav: true },
  { id: "audience", label: "受众与预期管理", group: "guidance", Component: AudienceView, nav: true },
  { id: "structure", label: "大纲", group: "guidance", countKey: "beats", Component: StructureView, nav: true },
  { id: "reveal", label: "揭示层", group: "guidance", countKey: "reveal_layers", Component: RevealView, nav: true },
  // ② 叙事事实层
  { id: "plot", label: "情节", group: "fact", countKey: "scenes", Component: ScenesView, nav: true },
  { id: "reasoning", label: "情节推理", group: "fact", Component: ReasoningView, nav: true },
  { id: "characters", label: "设定 · 人设", group: "fact", countKey: "characters", Component: CharactersView, nav: true },
  { id: "world", label: "设定 · 世界", group: "fact", countKey: "world", Component: WorldView, nav: true },
  { id: "relationships", label: "设定 · 关系", group: "fact", countKey: "relationships", Component: RelationshipsView, nav: true },
  // ③ 落地指导层
  { id: "style", label: "文风矩阵", group: "realize-guide", Component: StyleView, nav: true },
  { id: "engine", label: "落地结构演算引擎", group: "realize-guide", countKey: "nodes", Component: EngineView, nav: true },
  // ④ 落地层
  { id: "gametext", label: "游戏内文本", group: "realize", countKey: "game_texts", Component: GameTextView, nav: true },
  { id: "drafts", label: "草稿看板", group: "realize", Component: DraftsView, nav: true },
  // 归档
  { id: "archive", label: "否决案归档", group: "archive", countKey: "rejected_archive", Component: RejectedArchiveView, nav: true },

  // —— 非导航:演算引擎子视图(经 engine 内切 / jumpTo)——
  { id: "route", label: "路线节点图", group: "realize-guide", Component: RouteGraphView, nav: false },
  { id: "variables", label: "数值 / 状态", group: "realize-guide", Component: VariablesView, nav: false },
  { id: "playthrough", label: "演练", group: "realize-guide", Component: PlaythroughView, nav: false },
  // —— 工具组(左侧可折叠"工具",默认收起;不再以顶栏常驻按钮形式存在)——
  { id: "health", label: "健康检查", group: "tools", Component: HealthView, nav: true },
  { id: "completeness", label: "完成度", group: "tools", Component: CompletenessView, nav: true },
  { id: "trace", label: "贯穿追踪", group: "tools", Component: TraceView, nav: true },
  { id: "distribution", label: "分布对照", group: "tools", Component: DistributionView, nav: true },
  { id: "provenance", label: "出处钻取", group: "tools", Component: ProvenanceView, nav: true },
  { id: "diff", label: "版本对照", group: "tools", Component: DiffView, nav: true },
  { id: "comments", label: "圈选评论", group: "tools", Component: CommentsView, nav: true },
  { id: "tags", label: "标签 / 伏笔", group: "tools", Component: TagsView, nav: true },
];
