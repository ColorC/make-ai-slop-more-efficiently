// 类型化 API 客户端。前后端共同契约:后端 api.py 必须实现这些端点。
import type {
  Project, HealthIssue, SearchHit, EmptyItem, Completeness, PlaythroughResult,
} from "./types";

// API 基址从本模块 URL 派生:站点既能跑在根(:8330 直连),也能被审阅台同源反向代理到
// /narrative-studio/ 下(此时 API 落在 /narrative-studio/api)。assets 在 <base>/assets/*,
// 故其父目录 + "api" 即正确 API 根,两种部署都成立。
const BASE = (() => {
  try {
    return new URL("../api", import.meta.url).pathname.replace(/\/+$/, "");
  } catch {
    return "/api";
  }
})();

async function get<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}
async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

const q = (params: Record<string, string>) =>
  "?" + new URLSearchParams(params).toString();

export const api = {
  // 项目真源
  getProject: () => get<Project>("/project"),
  saveProject: (p: Project) => send<{ ok: boolean }>("PUT", "/project", p),

  // 单实体编辑(检查器)
  updateEntity: (carrier: string, id: string, patch: unknown) =>
    send<{ ok: boolean; entity: unknown }>("PUT", `/entity/${carrier}/${encodeURIComponent(id)}`, patch),
  createEntity: (carrier: string, entity: unknown) =>
    send<{ ok: boolean; entity: unknown }>("POST", `/entity/${carrier}`, entity),
  deleteEntity: (carrier: string, id: string) =>
    send<{ ok: boolean }>("DELETE", `/entity/${carrier}/${encodeURIComponent(id)}`),

  // 投影
  timeline: () => get<any>("/projections/timeline"),
  outline: () => get<any>("/projections/outline"),
  routeGraph: () => get<any>("/projections/route-graph"),
  relationshipGraph: () => get<any>("/projections/relationship-graph"),
  characterScenes: (charId: string) => get<any>("/projections/character-scenes" + q({ char_id: charId })),
  variableRefs: (varKey: string) => get<any>("/projections/variable-refs" + q({ var_key: varKey })),
  tagOccurrences: (tagId: string) => get<any>("/projections/tag-occurrences" + q({ tag_id: tagId })),
  ideaAlignment: (idea: string) => get<any>("/projections/idea-alignment" + q({ idea })),
  drilldown: (sceneId: string) => get<any>("/projections/drilldown" + q({ scene_id: sceneId })),
  distribution: () => get<any>("/projections/distribution"),
  provenanceForward: (source: string) => get<any>("/projections/provenance-forward" + q({ source })),

  // 健康 / 完成度 / 搜索 / 演练
  health: () => get<HealthIssue[]>("/health"),
  completeness: () => get<Completeness>("/completeness"),
  empties: () => get<EmptyItem[]>("/empties"),
  search: (text: string) => get<SearchHit[]>("/search" + q({ q: text })),
  playthrough: (choices?: string[], start?: string) =>
    send<PlaythroughResult>("POST", "/playthrough", { choices: choices ?? [], start: start ?? null }),

  // 查找替换
  replace: (find: string, replace: string, dryRun: boolean) =>
    send<{ count: number; hits: { path: string; before: string }[] }>("POST", "/replace", { find, replace, dry_run: dryRun }),

  // 批量改字段/状态
  batchUpdate: (carrier: string, ids: string[], patch: any) =>
    send<{ ok: boolean; updated: number }>("POST", "/batch-update", { carrier, ids, patch }),

  // 场景拆分 / 合并
  sceneSplit: (sceneId: string, at: number) =>
    send<{ ok: boolean; warnings: string[] }>("POST", `/scene/${encodeURIComponent(sceneId)}/split`, { at }),
  sceneMerge: (a: string, b: string) =>
    send<{ ok: boolean; warnings: string[] }>("POST", "/scene/merge", { a, b }),

  // 修订历史
  history: () => get<string[]>("/history"),
  restoreHistory: (ts: string) => send<{ ok: boolean }>("POST", "/history/restore", { ts }),

  // 草稿转正式(落地层:wiki/drafts → wiki/cards|events)
  draftPromote: (id: string) => send<{ ok: boolean; wiki_path?: string }>("POST", "/draft/promote", { id }),

  // 具名版本 + 对照
  versions: () => get<string[]>("/versions"),
  saveVersion: (name: string) => send<{ ok: boolean; versions: string[] }>("POST", "/versions/save", { name }),
  activateVersion: (name: string) => send<{ ok: boolean }>("POST", "/versions/activate", { name }),
  diff: (a: string, b: string) => get<any>("/diff" + q({ a, b })),
};
