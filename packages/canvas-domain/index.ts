/**
 * @omni/canvas-domain — 画布域契约（纯契约层，宿主无关）。
 *
 * 设计裁决（2026-08-16，见 workshop 需求文档 §10.3）：
 * - omnidashboard 为主体宿主；workshop 等外部系统注册为"域"被 dashboard 感知。
 * - dashboard 与 workshop 零互相依赖，共同依赖 core 包（@omni/material-core）。
 * - 感知单向：dashboard → workshop；dashboard 可操作域内容（受 access 约束）。
 * - 变更语义（单调 revision / base_revision CAS / 幂等 mutation）由 core 统一定义，
 *   与传输形态无关 —— disk 与 protocol 两种 adapter 因此可互换。
 *
 * 本文件不含任何 IO；磁盘实现见 ./disk.ts，协议实现由宿主提供。
 */

import type { CanvasMutationRequest } from "../material-core/note.ts";

export type DomainCapability =
  | "canvas"
  | "material"
  | "wiki"
  | "review";

export type DomainAccess = "readonly" | "readwrite";

export type DomainTransport = "builtin" | "disk" | "protocol";

export interface DomainDescriptor {
  /** 域唯一 id（注册表内唯一，小写短横线风格，如 builtin / workshop）。 */
  id: string;
  title: string;
  /** builtin = dashboard 自身存储；disk/protocol = 外部域接入形态。 */
  transport: DomainTransport;
  /** 能力协商：域实际开放的面。dashboard 按此渲染与路由，不假设全集。 */
  capabilities: DomainCapability[];
  /** 注册时声明；dashboard 据此操作权限。 */
  access: DomainAccess;
  /** 契约版本（域存储/协议 schema 版本），不匹配时 dashboard 只读降级并告警。 */
  schema_version: number;
  /** 可选实时通道；无则宿主轮询 head。 */
  realtime?: boolean;
}

export interface CanvasSummary {
  document_id: string;
  title: string;
  revision: number;
  updated_at: string;
  cards: number;
  relations: number;
}

export interface CanvasHead {
  revision: number;
  updatedAt: string;
  lastMutation?: { id: string; actor: string; sequence: number; at: string };
}

export interface MaterialSummary {
  id: string;
  title: string;
  labels: string[];
  updated_at?: string;
}

export interface WikiIndexSummary {
  pages: number;
  updated_at?: string;
}

/**
 * 域适配器：dashboard 感知与操作一个域的唯一界面。
 * 所有写操作遵守 core 的 CanvasMutationRequest 语义（base_revision CAS + 幂等键）。
 * 未开放的能力对应方法抛 DomainCapabilityError，宿主必须先经 descriptor 协商。
 */
export interface CanvasDomainAdapter {
  descriptor(): DomainDescriptor;

  // ── canvas 面 ──
  listCanvases(): Promise<CanvasSummary[]>;
  getCanvas(documentId: string): Promise<unknown | null>;
  headCanvas(documentId: string): Promise<CanvasHead | null>;
  mutateCanvas(request: import("../material-core/note.ts").CanvasMutationRequest): Promise<{
    revision: number;
    duplicate: boolean;
    merged: boolean;
  }>;

  // ── 材料正文面（text 卡分居链） ──
  getText(textId: string): Promise<string | null>;
  putText(textId: string, content: string, actor: string): Promise<{ path: string; modified_at: string }>;

  // ── material 面（可选能力） ──
  listMaterials?(): Promise<MaterialSummary[]>;
  getMaterial?(materialId: string): Promise<unknown | null>;

  // ── wiki 面（可选能力） ──
  wikiIndex?(): Promise<WikiIndexSummary | null>;
  wikiPage?(ref: string): Promise<string | null>;

  // ── 实时（可选） ──
  subscribe?(onEvent: (event: DomainEvent) => void): () => void;
}

export interface DomainEvent {
  domain: string;
  kind: "canvas-updated" | "canvas-removed" | "material-updated" | "wiki-updated";
  ref: string;
  revision?: number;
  at: string;
}

export class DomainCapabilityError extends Error {
  constructor(domain: string, capability: DomainCapability) {
    super(`域 ${domain} 未开放能力：${capability}`);
    this.name = "DomainCapabilityError";
  }
}

export class DomainAccessError extends Error {
  constructor(domain: string) {
    super(`域 ${domain} 为只读，拒绝写入`);
    this.name = "DomainAccessError";
  }
}

export function hasCapability(descriptor: DomainDescriptor, capability: DomainCapability): boolean {
  return descriptor.capabilities.includes(capability);
}

export function assertWritable(descriptor: DomainDescriptor): void {
  if (descriptor.access !== "readwrite") throw new DomainAccessError(descriptor.id);
}

// ── 域注册配置（domains.yaml / JSON 解析后的校验） ──

export interface DiskDomainConfig {
  id: string;
  title?: string;
  transport: "disk";
  root: string;
  access?: DomainAccess;
  capabilities?: DomainCapability[];
  schema_version?: number;
  poll_seconds?: number;
}

export interface ProtocolDomainConfig {
  id: string;
  title?: string;
  transport: "protocol";
  endpoint: string;
  token_env?: string;
  access?: DomainAccess;
  capabilities?: DomainCapability[];
  schema_version?: number;
  poll_seconds?: number;
}

export type DomainConfig = DiskDomainConfig | ProtocolDomainConfig;

const CAPABILITY_SET: ReadonlySet<string> = new Set(["canvas", "material", "wiki", "review"]);
const ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/** 校验一条域注册配置；失败抛 TypeError（宿主在启动期调用，快速失败）。 */
export function parseDomainConfig(raw: unknown): DomainConfig {
  if (!raw || typeof raw !== "object") throw new TypeError("域配置必须是对象");
  const value = raw as Record<string, unknown>;
  const id = value["id"];
  if (typeof id !== "string" || !ID_PATTERN.test(id)) throw new TypeError("域 id 非法（小写字母数字短横线，≤64）");
  const transport = value["transport"];
  if (transport !== "disk" && transport !== "protocol") {
    throw new TypeError(`域 ${id}: transport 必须是 disk|protocol`);
  }
  const access = value["access"] ?? "readwrite";
  if (access !== "readonly" && access !== "readwrite") throw new TypeError(`域 ${id}: access 非法`);
  const capabilities = value["capabilities"] ?? ["canvas"];
  if (!Array.isArray(capabilities) || capabilities.length === 0 || !capabilities.every((c) => CAPABILITY_SET.has(String(c)))) {
    throw new TypeError(`域 ${id}: capabilities 非法`);
  }
  const schema_version = value["schema_version"] ?? 1;
  if (typeof schema_version !== 'number' || !Number.isInteger(schema_version) || schema_version < 1) throw new TypeError(`域 ${id}: schema_version 非法`);
  const base = {
    id,
    title: typeof value["title"] === "string" ? value["title"] : id,
    access: access as DomainAccess,
    capabilities: capabilities as DomainCapability[],
    schema_version,
    poll_seconds: typeof value["poll_seconds"] === "number" ? value["poll_seconds"] : 15,
  };
  if (transport === "disk") {
    const root = value["root"];
    if (typeof root !== "string" || !root) throw new TypeError(`域 ${id}: disk 形态必须给 root 路径`);
    return { ...base, transport: "disk", root };
  }
  const endpoint = value["endpoint"];
  if (typeof endpoint !== "string" || !/^https?:\/\//.test(endpoint)) {
    throw new TypeError(`域 ${id}: protocol 形态必须给 http(s) endpoint`);
  }
  return {
    ...base,
    transport: "protocol",
    endpoint: endpoint.replace(/\/+$/, ""),
    token_env: typeof value["token_env"] === "string" ? value["token_env"] : undefined,
  };
}

// ── 跨域寻址 ──

/** omni://domain/<domain_id>/<kind>/<ref> */
export interface DomainUri {
  domain: string;
  kind: "canvas" | "material" | "wiki" | "text";
  ref: string;
}

export function formatDomainUri(uri: DomainUri): string {
  return `omni://domain/${uri.domain}/${uri.kind}/${uri.ref}`;
}

export function parseDomainUri(value: string): DomainUri {
  const match = /^omni:\/\/domain\/([a-z0-9][a-z0-9_-]{0,63})\/(canvas|material|wiki|text)\/(.+)$/.exec(value.trim());
  if (!match) throw new TypeError(`非法域 URI：${value}`);
  return { domain: match[1]!, kind: match[2] as DomainUri["kind"], ref: match[3]! };
}

// ── 注册表（纯内存，宿主持有） ──

export class DomainRegistry {
  private readonly adapters = new Map<string, CanvasDomainAdapter>();

  register(adapter: CanvasDomainAdapter): void {
    const descriptor = adapter.descriptor();
    if (!ID_PATTERN.test(descriptor.id)) throw new TypeError(`域 id 非法：${descriptor.id}`);
    if (this.adapters.has(descriptor.id)) throw new TypeError(`域重复注册：${descriptor.id}`);
    this.adapters.set(descriptor.id, adapter);
  }

  unregister(id: string): boolean {
    return this.adapters.delete(id);
  }

  get(id: string): CanvasDomainAdapter | undefined {
    return this.adapters.get(id);
  }

  require(id: string): CanvasDomainAdapter {
    const adapter = this.adapters.get(id);
    if (!adapter) throw new TypeError(`域未注册：${id}`);
    return adapter;
  }

  descriptors(): DomainDescriptor[] {
    return [...this.adapters.values()].map((adapter) => adapter.descriptor());
  }

  withCapability(capability: DomainCapability): CanvasDomainAdapter[] {
    return [...this.adapters.values()].filter((adapter) => hasCapability(adapter.descriptor(), capability));
  }
}
