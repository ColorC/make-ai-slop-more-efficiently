/**
 * DiskDomainAdapter — 硬盘接入形态的参考实现（node:fs）。
 *
 * 存储布局与 workshop MaterialNotesStore / overlay-note-store 兼容：
 *   <root>/canvases/<document_id>.json   画布文档（StoredDocument 同形）
 *   <root>/texts/<text_id>.md            材料正文（分居链）
 *
 * 并发语义与 workshop store 完全一致（单调 revision / base_revision CAS /
 * 幂等 mutation id / 实体级 _entityRevisions 冲突检测 / tmp+rename 原子写），
 * 因此本 adapter 可直接指向 workshop 的 data/material/notes 目录做只读或读写感知，
 * 也可作为 dashboard builtin 域的落盘实现。
 *
 * 进程内对同一 document 的 mutation 串行化；跨进程安全由文件系统原子 rename 兜底。
 */

import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import {
  applyCanvasMutation,
  emptyCanvasDocument,
  parseCanvasDocument,
  type CanvasMutationPatch,
  type CanvasMutationRequest,
  type MaterialCanvasDocument,
} from "../material-core/note.ts";
import {
  assertWritable,
  type CanvasDomainAdapter,
  type CanvasHead,
  type CanvasSummary,
  type DomainDescriptor,
  type MaterialSummary,
} from "./index.ts";

export interface DiskDomainOptions {
  id: string;
  title?: string;
  root: string;
  access?: "readonly" | "readwrite";
  capabilities?: DomainDescriptor["capabilities"];
  schema_version?: number;
}

export class DiskDomainConflict extends Error {
  readonly conflicts: string[];
  constructor(message: string, conflicts: string[] = []) {
    super(message);
    this.name = "DiskDomainConflict";
    this.conflicts = conflicts;
  }
}

interface StoredMutation { id: string; actor: string; sequence: number; at: string }
interface StoredDocument extends MaterialCanvasDocument {
  _workspaceRef: { kind: string; ref: string };
  _entityRevisions: { title: number; catalog: number; display: number; behavior: number; view: number; cards: Record<string, number>; relations: Record<string, number> };
  _mutations: StoredMutation[];
}

const SAFE_ID = /^[A-Za-z0-9._-]{1,220}$/;
const MUTATION_HISTORY_CAP = 500;

const safeId = (value: unknown, label: string): string => {
  if (typeof value !== "string" || !SAFE_ID.test(value)) throw new TypeError(`${label} 非法`);
  return value;
};

const now = (): string => new Date().toISOString();

export class DiskDomainAdapter implements CanvasDomainAdapter {
  private readonly options: Required<Pick<DiskDomainOptions, "id" | "root">> & DiskDomainOptions;
  private readonly locks = new Map<string, Promise<unknown>>();

  constructor(options: DiskDomainOptions) {
    this.options = { ...options, root: resolve(options.root) };
  }

  descriptor(): DomainDescriptor {
    return {
      id: this.options.id,
      title: this.options.title ?? this.options.id,
      transport: "disk",
      capabilities: this.options.capabilities ?? ["canvas"],
      access: this.options.access ?? "readwrite",
      schema_version: this.options.schema_version ?? 1,
      realtime: false,
    };
  }

  private get canvasRoot(): string { return join(this.options.root, "canvases"); }
  private get textRoot(): string { return join(this.options.root, "texts"); }

  private canvasPath(id: string): string {
    const file = join(this.canvasRoot, `${id}.json`);
    this.assertInRoot(file);
    return file;
  }

  private textPath(id: string): string {
    const file = join(this.textRoot, `${id}.md`);
    this.assertInRoot(file);
    return file;
  }

  private assertInRoot(file: string): void {
    const absolute = resolve(file);
    if (absolute !== this.options.root && !absolute.startsWith(`${this.options.root}${sep}`)) {
      throw new TypeError("域存储路径越界");
    }
  }

  private atomicText(file: string, value: string): void {
    mkdirSync(dirname(file), { recursive: true });
    const temporary = `${file}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
    writeFileSync(temporary, value, "utf8");
    renameSync(temporary, file);
  }

  private readStored(id: string): StoredDocument | null {
    const file = this.canvasPath(id);
    if (!existsSync(file)) return null;
    const raw = readFileSync(file, "utf8");
    const untrusted = JSON.parse(raw) as Partial<StoredDocument>;
    const parsed = parseCanvasDocument(raw, typeof untrusted.sessionId === "string" ? untrusted.sessionId : "");
    if (!parsed) throw new DiskDomainConflict(`Canvas 数据损坏：${id}`);
    const workspaceRef = untrusted._workspaceRef && typeof untrusted._workspaceRef.kind === "string"
      ? { kind: untrusted._workspaceRef.kind, ref: String(untrusted._workspaceRef.ref ?? "") }
      : { kind: "workspace", ref: parsed.sessionId };
    return {
      ...parsed,
      _workspaceRef: workspaceRef,
      _entityRevisions: {
        title: untrusted._entityRevisions?.title ?? 0,
        catalog: untrusted._entityRevisions?.catalog ?? 0,
        display: untrusted._entityRevisions?.display ?? 0,
        behavior: untrusted._entityRevisions?.behavior ?? 0,
        view: untrusted._entityRevisions?.view ?? 0,
        cards: untrusted._entityRevisions?.cards ?? {},
        relations: untrusted._entityRevisions?.relations ?? {},
      },
      _mutations: Array.isArray(untrusted._mutations) ? untrusted._mutations.slice(-MUTATION_HISTORY_CAP) : [],
    };
  }

  // ── canvas 面 ──

  async listCanvases(): Promise<CanvasSummary[]> {
    if (!existsSync(this.canvasRoot)) return [];
    return readdirSync(this.canvasRoot)
      .filter((name) => name.endsWith(".json"))
      .map((name) => {
        try {
          const document = this.readStored(name.slice(0, -5));
          if (!document) return null;
          return {
            document_id: name.slice(0, -5),
            title: document.title ?? document.sessionId,
            revision: document.revision,
            updated_at: document.updatedAt,
            cards: document.cards.length,
            relations: document.relations.length,
          } satisfies CanvasSummary;
        } catch {
          return null; // 损坏文件跳过，list 永不整体失败
        }
      })
      .filter((summary): summary is CanvasSummary => Boolean(summary))
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
  }

  async getCanvas(documentId: string): Promise<StoredDocument | null> {
    return this.readStored(safeId(documentId, "document_id"));
  }

  async headCanvas(documentId: string): Promise<CanvasHead | null> {
    const document = this.readStored(safeId(documentId, "document_id"));
    if (!document) return null;
    return { revision: document.revision, updatedAt: document.updatedAt, lastMutation: document.lastMutation };
  }

  async mutateCanvas(request: CanvasMutationRequest): Promise<{ revision: number; duplicate: boolean; merged: boolean }> {
    assertWritable(this.descriptor());
    const id = safeId(request.document_id, "document_id");
    const previous = this.locks.get(id) ?? Promise.resolve();
    const run = previous.catch(() => undefined).then(() => this.mutateSync(request, id));
    this.locks.set(id, run);
    return run;
  }

  private mutateSync(request: CanvasMutationRequest, id: string): { revision: number; duplicate: boolean; merged: boolean } {
    const mutationId = safeId(request.idempotency_key, "idempotency_key");
    const workspace = request.workspace_ref;
    const actor = request.actor_ref;
    if (!Number.isInteger(request.base_revision) || request.base_revision < 0) {
      throw new TypeError("base_revision 必须是非负整数");
    }
    if (!request.patch || typeof request.patch !== "object") throw new TypeError("patch 必填");
    const current = this.readStored(id) ?? {
      ...emptyCanvasDocument(workspace.ref),
      _workspaceRef: workspace,
      _entityRevisions: { title: 0, catalog: 0, display: 0, behavior: 0, view: 0, cards: {}, relations: {} },
      _mutations: [],
    };
    if (current._workspaceRef.kind !== workspace.kind || current._workspaceRef.ref !== workspace.ref) {
      throw new DiskDomainConflict("Canvas workspace 引用不匹配", ["workspace_ref"]);
    }
    if (current._mutations.some((mutation) => mutation.id === mutationId)) {
      return { revision: current.revision, duplicate: true, merged: request.base_revision < current.revision };
    }
    if (request.base_revision > current.revision) throw new DiskDomainConflict("base revision 比当前版本更新");
    const sequence = current.revision + 1;
    const patch: CanvasMutationPatch = { ...request.patch };
    const currentCards = new Map(current.cards.map((card) => [card.id, card]));
    const lockedPositionConflicts = (patch.upsert_cards ?? []).flatMap((card) => {
      const previous = currentCards.get(card.id);
      if (!previous?.positionLock?.locked || request.position_lock_override === true) return [];
      const moved = previous.x !== card.x
        || previous.y !== card.y
        || previous.parentId !== card.parentId;
      return moved ? ['card:' + card.id + ':position'] : [];
    });
    if (lockedPositionConflicts.length) {
      throw new DiskDomainConflict('Locked card position cannot change without position_lock_override', lockedPositionConflicts);
    }
    const touchedCards = new Set([
      ...(patch.upsert_cards ?? []).map((card) => safeId(card.id, "card.id")),
      ...(patch.remove_card_ids ?? []).map((value) => safeId(value, "remove_card_ids")),
    ]);
    const touchedRelations = new Set([
      ...(patch.upsert_relations ?? []).map((relation) => safeId(relation.id, "relation.id")),
      ...(patch.remove_relation_ids ?? []).map((value) => safeId(value, "remove_relation_ids")),
    ]);
    const conflicts = [...touchedCards]
      .filter((cardId) => (current._entityRevisions.cards[cardId] ?? 0) > request.base_revision)
      .map((cardId) => `card:${cardId}`);
    conflicts.push(...[...touchedRelations]
      .filter((relationId) => (current._entityRevisions.relations[relationId] ?? 0) > request.base_revision)
      .map((relationId) => `relation:${relationId}`));
    if (patch.set_title && current._entityRevisions.title > request.base_revision) conflicts.push("title");
    if (patch.set_catalog && current._entityRevisions.catalog > request.base_revision) conflicts.push("catalog");
    if (patch.set_display && current._entityRevisions.display > request.base_revision) conflicts.push("display");
    if (patch.set_behavior && current._entityRevisions.behavior > request.base_revision) conflicts.push("behavior");
    if (patch.set_view && current._entityRevisions.view > request.base_revision) conflicts.push("view");
    if (conflicts.length) throw new DiskDomainConflict("Canvas 修改与较新修改冲突", conflicts);

    const at = now();
    const mutation: StoredMutation = { id: mutationId, actor: `${actor.kind}:${actor.ref}`, sequence, at };
    const applied = applyCanvasMutation(current, patch);
    const next: StoredDocument = {
      ...applied,
      revision: sequence,
      updatedAt: at,
      lastMutation: mutation,
      _workspaceRef: workspace,
      _entityRevisions: {
        title: patch.set_title ? sequence : current._entityRevisions.title,
        catalog: patch.set_catalog ? sequence : current._entityRevisions.catalog,
        display: patch.set_display ? sequence : current._entityRevisions.display,
        behavior: patch.set_behavior ? sequence : current._entityRevisions.behavior,
        view: patch.set_view ? sequence : current._entityRevisions.view,
        cards: { ...current._entityRevisions.cards },
        relations: { ...current._entityRevisions.relations },
      },
      _mutations: [...current._mutations, mutation].slice(-MUTATION_HISTORY_CAP),
    };
    for (const cardId of touchedCards) next._entityRevisions.cards[cardId] = sequence;
    for (const relationId of touchedRelations) next._entityRevisions.relations[relationId] = sequence;
    this.atomicText(this.canvasPath(id), `${JSON.stringify(next, null, 2)}\n`);
    return { revision: sequence, duplicate: false, merged: request.base_revision < current.revision };
  }

  // ── 材料正文面 ──

  async getText(textId: string): Promise<string | null> {
    const file = this.textPath(safeId(textId, "text_id"));
    if (!existsSync(file)) return null;
    return readFileSync(file, "utf8");
  }

  async putText(textId: string, content: string, _actor: string): Promise<{ path: string; modified_at: string }> {
    assertWritable(this.descriptor());
    if (typeof content !== "string") throw new TypeError("正文必须是字符串");
    const file = this.textPath(safeId(textId, "text_id"));
    this.atomicText(file, content);
    return { path: file, modified_at: statSync(file).mtime.toISOString() };
  }

  // ── material / wiki 面：纯 disk 形态暂不提供索引（宿主按需升级为 protocol） ──

  async listMaterials(): Promise<MaterialSummary[]> {
    return [];
  }
}
