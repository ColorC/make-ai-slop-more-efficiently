/**
 * @omni/canvas-store — 画布札记存储的唯一 TS 权威实现（2026-08-16 彻底 TS 化裁决）。
 *
 * 职责：material-note 画布文档的文件布局与并发语义 ——
 *   <root>/canvases/<id>.json        画布文档（StoredDocument 同形）
 *   <root>/<textDir>/<id>.md         材料正文（分居链；workshop=texts，dashboard builtin=materials）
 * 语义：单调 revision / base_revision CAS / 幂等 mutation id / 实体级 _entityRevisions
 * 冲突检测 / tmp+rename 原子写。与 @omni/material-core/note 的 applyCanvasMutation 同源。
 *
 * 消费方：workshop server（productRef=workshop, strict workspace 校验）、
 * omnidashboard builtin 域（productRef=omnidashboard, workspace 校验关闭——Python 桥
 * 历史文档无 workspace 绑定）。禁止任何第二份实现（原 Python 复刻已删）。
 */

import {
    existsSync,
    mkdirSync,
    readFileSync,
    readdirSync,
    renameSync,
    statSync,
    unlinkSync,
    writeFileSync,
} from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import {
    applyCanvasMutation,
    canvasStorageId,
    emptyCanvasDocument,
    normalizeWriteIdentity,
    parseCanvasDocument,
    repairDisplayText,
    validateCanvasCommentThread,
    validateWriteIdentity,
    type CanvasCardProvenance,
    type CanvasCardRecord,
    type CanvasLifecycle,
    type CanvasMutationPatch,
    type MaterialCanvasDocument,
    type MaterialNoteRef,
    type WriteIdentity,
} from '../material-core/note.ts';
import { assertMaterialGraph, lintCanvasDocument, lintMaterialGraph } from '../material-core/index.js';

export interface StoredMutation {
    id: string;
    /** 兼容字段：`${actor_ref.kind}:${actor_ref.ref}`。 */
    actor: string;
    sequence: number;
    at: string;
    /** 写入身份（2026-09-12 起；旧记录与未带身份的写入缺省）。 */
    identity?: WriteIdentity;
    /** 写入目的；可由 annotateMutations 后补。 */
    purpose?: string;
    /** 批号；可由 annotateMutations 后补。 */
    batch?: string;
    /** 这次写入触碰的卡 / 关系 id（上限各 200），供按卡查溯源。 */
    cards?: string[];
    relations?: string[];
}

export interface MutationQuery {
    /** 只要触碰过这张卡的写入。 */
    card?: string;
    /** 只要 sequence > since 的写入。 */
    since?: number;
    /** 最多返回多少条（按 sequence 升序取最新的那一段）。缺省 50，上限 500。 */
    limit?: number;
}

export interface MutationAnnotation {
    mutation_ids?: string[];
    /** 闭区间 [from_sequence, to_sequence]；与 mutation_ids 二选一或并用。 */
    from_sequence?: number;
    to_sequence?: number;
    purpose?: string;
    batch?: string;
}

export interface StoredDocument extends MaterialCanvasDocument {
    /** Physical document id; required for catalog links when legacy ids are not derivable from sessionId. */
    _documentId?: string;
    _workspaceRef: MaterialNoteRef;
    _migratedFrom?: string;
    _archiveState?: {
        previousLifecycle: Exclude<CanvasLifecycle, 'archived'>;
        archivedAt: string;
        archivedBy: string;
        /**
         * 归档前这份文档压根没有 catalog。归档必须物化一个 catalog 才能记下
         * lifecycle=archived，但恢复时若把物化出来的默认值留着，文档就不再是原来那份：
         * 缺失 catalog 与 lifecycle=test 在消费侧是两种归属（workshop 的
         * canvasNoteMaterials 读未规范化的 catalog?.lifecycle，前者进「工作记录」、
         * 后者进「程序测试」），于是 archive→restore 一轮后画布会从工作记录里消失。
         * 记下这一位，恢复时连「本来没有」一起还原。
         */
        previousCatalogAbsent?: boolean;
    };
    _entityRevisions: { title: number; catalog: number; display: number; behavior: number; view: number; cards: Record<string, number>; relations: Record<string, number> };
    _mutations: StoredMutation[];
}

export interface MaterialNoteMutationInput {
    document_id: string;
    workspace_ref: MaterialNoteRef;
    actor_ref: MaterialNoteRef;
    base_revision: number;
    idempotency_key: string;
    /** Explicitly allow moving a card whose current position is locked. */
    position_lock_override?: boolean;
    /** 写入身份；给了就必须合契约（不合直接拒绝，不静默丢）。 */
    identity?: unknown;
    patch: CanvasMutationPatch;
}

export interface CanvasDeletionReference {
    document_id: string;
    title: string;
}

export interface CanvasMaterialClassification {
    classification_ref: Record<string, unknown>;
    classification_context_ref: Record<string, unknown>;
}

export type CanvasMaterialClassificationResolver = (card: CanvasCardRecord) => CanvasMaterialClassification;

export interface CanvasDeletionPreview {
    document_id: string;
    title: string;
    lifecycle: CanvasLifecycle;
    cards: number;
    relations: number;
    mutations: number;
    retained_text_ids: string[];
    referenced_by: CanvasDeletionReference[];
}

export interface CanvasStoreOptions {
    /** 材料命名空间与维护责任产品（workshop=workshop，builtin=omnidashboard）。 */
    productRef: string;
    /** 正文目录名：workshop 布局 texts / dashboard overlay-note-store 布局 materials。 */
    textDir?: string;
    /** strict=校验 document_id 必须等于 canvasStorageId(workspace.ref)（workshop）；
     *  off=不校验（builtin：历史文档无 workspace 绑定）。 */
    workspaceCheck?: 'strict' | 'off';
    /** Host-owned classification pointers for newly materialized Canvas cards. */
    materialClassification?: CanvasMaterialClassificationResolver;
}

export class CanvasStoreConflict extends Error {
    readonly conflicts: string[];
    constructor(message: string, conflicts: string[] = []) {
        super(message);
        this.name = 'CanvasStoreConflict';
        this.conflicts = conflicts;
    }
}

export const SAFE_ID = /^[A-Za-z0-9._-]{1,220}$/;

export const safeId = (value: unknown, label = 'Canvas ID'): string => {
    if (typeof value !== 'string' || !SAFE_ID.test(value)) throw new TypeError(`${label} 非法`);
    return value;
};

export const validRef = (value: unknown, field: string): MaterialNoteRef => {
    if (!value || typeof value !== 'object') throw new TypeError(`${field} 必填`);
    const ref = value as Partial<MaterialNoteRef>;
    if (typeof ref.kind !== 'string' || !ref.kind.trim() || typeof ref.ref !== 'string' || !ref.ref.trim()) {
        throw new TypeError(`${field} 必须是有效引用`);
    }
    return { kind: ref.kind.trim(), ref: ref.ref.trim() };
};

const now = (): string => new Date().toISOString();

const MUTATION_HISTORY_CAP = 500;

export const assertNoMojibakeRuns = (value: string, label = 'Canvas 内容'): void => {
    if (/\?{3,}/.test(value)) {
        throw new TypeError(`${label} 含连续 ???，疑似有损编码，拒绝落盘`);
    }
};

export class CanvasFileStore {
    readonly root: string;
    readonly options: Omit<Required<CanvasStoreOptions>, 'materialClassification'> & {
        materialClassification?: CanvasMaterialClassificationResolver;
    };

    constructor(root: string, options: CanvasStoreOptions) {
        this.root = resolve(root);
        this.options = {
            productRef: options.productRef,
            textDir: options.textDir ?? 'texts',
            workspaceCheck: options.workspaceCheck ?? 'strict',
            materialClassification: options.materialClassification,
        };
        mkdirSync(this.canvasRoot, { recursive: true });
        mkdirSync(this.textRoot, { recursive: true });
        mkdirSync(this.tombstoneRoot, { recursive: true });
    }

    get canvasRoot(): string { return join(this.root, 'canvases'); }
    get textRoot(): string { return join(this.root, this.options.textDir); }
    get tombstoneRoot(): string { return join(this.root, 'deleted-canvases'); }
    canvasPath(id: string): string { return join(this.canvasRoot, `${safeId(id)}.json`); }
    textPath(id: string): string { return join(this.textRoot, `${safeId(id)}.md`); }
    tombstonePath(id: string): string { return join(this.tombstoneRoot, `${safeId(id)}.json`); }
    isDeleted(id: string): boolean { return existsSync(this.tombstonePath(id)); }

    atomicText(file: string, value: string): void {
        const absolute = resolve(file);
        if (absolute !== this.root && !absolute.startsWith(`${this.root}${sep}`)) throw new TypeError('Canvas 路径越界');
        assertNoMojibakeRuns(value);
        mkdirSync(dirname(absolute), { recursive: true });
        const temporary = `${absolute}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
        try {
            writeFileSync(temporary, value, 'utf8');
            assertNoMojibakeRuns(readFileSync(temporary, 'utf8'), 'Canvas 落盘内容');
            renameSync(temporary, absolute);
        } catch (error) {
            if (existsSync(temporary)) unlinkSync(temporary);
            throw error;
        }
    }

    atomicJson(file: string, value: unknown): void {
        this.atomicText(file, `${JSON.stringify(value, null, 2)}\n`);
    }

    /** 卡片 material 化：写盘前把每张卡升格为可寻址 material（命名空间=productRef）。 */
    normalizeMaterialCard(
        card: CanvasCardRecord,
        documentId: string,
        workspace: MaterialNoteRef,
        revision: number,
        actor: MaterialNoteRef,
    ): CanvasCardRecord {
        const product = this.options.productRef;
        const previous = card.material && typeof card.material === 'object' ? card.material : {};
        const previousMetadata = previous['metadata'] && typeof previous['metadata'] === 'object'
            ? previous['metadata'] as Record<string, unknown>
            : {};
        // material_type / material_kind 是旧 Canvas 自由文本分类，不能继续写入 Material。
        // 业务语义通过一级 preset_ref 引用项目自持 preset profile；metadata 只保存展开结果与运行数据。
        const {
            material_type: _legacyMaterialType,
            material_type_id: _legacyMaterialTypeId,
            material_kind: _legacyMaterialKind,
            ...governedMetadata
        } = previousMetadata;
        const object = `${documentId}#card=${card.id}`;
        const mediaType = card.kind === 'group' ? 'application/vnd.omni.view+json' : 'text/markdown';
        const classification = this.options.materialClassification?.(card);
        const material = {
            ...previous,
            id: `material:${product}/note/${documentId}/${card.id}`,
            title: card.title || '未命名内容块',
            labels: ['Canvas', card.kind === 'group' ? '分组' : card.kind === 'material' ? '材料引用' : card.kind === 'comment' ? '评论' : '内容块'],
            revision_ref: { provider: 'material-note', object, revision: `r${revision}` },
            representations: [{
                id: 'canvas-card',
                media_type: mediaType,
                role: 'primary',
                source_ref: { kind: 'material-note', ref: object },
                format_tag: card.kind === 'group' ? 'material-view' : 'markdown',
                renderer_hint: card.kind === 'group' ? 'material-note-group' : 'material-note-markdown',
            }],
            metadata: {
                ...governedMetadata,
                ...(classification ? {
                    classification_ref: classification.classification_ref,
                    classification_context_ref: classification.classification_context_ref,
                } : {}),
                submission_purpose: 'Canvas 整理',
                workspace_ref: workspace,
                responsibility: {
                    content: actor,
                    execution: actor,
                    maintenance: { kind: 'product', ref: product },
                },
                ...(typeof previous['material_ref'] === 'string' ? { referenced_material_id: previous['material_ref'] } : {}),
            },
        };
        assertMaterialGraph({
            contract_version: '0.3.0',
            graph_id: `material-graph:${product}/note/${documentId}`,
            generated_at: now(),
            materials: [material],
        });
        const formatLint = lintMaterialGraph({
            contract_version: '0.3.0',
            graph_id: `material-graph:${product}/note/${documentId}`,
            generated_at: now(),
            materials: [material],
        }, { requireClassification: Boolean(this.options.materialClassification) });
        if (!formatLint.ok) throw new TypeError(`Canvas Material format lint failed: ${formatLint.issues.map((item) => `${item.path}: ${item.message}`).join('; ')}`);
        return { ...card, material };
    }

    readStored(id: string): StoredDocument | null {
        const file = this.canvasPath(id);
        if (!existsSync(file)) return null;
        const raw = readFileSync(file, 'utf8');
        const untrusted = JSON.parse(raw) as Partial<StoredDocument>;
        const parsed = parseCanvasDocument(raw, typeof untrusted.sessionId === 'string' ? untrusted.sessionId : '');
        if (!parsed) throw new CanvasStoreConflict(`Canvas 数据损坏：${id}`);
        const workspace = validRef(untrusted._workspaceRef ?? { kind: 'workspace', ref: parsed.sessionId }, '_workspaceRef');
        const migratedFrom = typeof untrusted._migratedFrom === 'string' ? untrusted._migratedFrom : undefined;
        const archiveState = untrusted._archiveState
            && (untrusted._archiveState.previousLifecycle === 'development'
                || untrusted._archiveState.previousLifecycle === 'formal'
                || untrusted._archiveState.previousLifecycle === 'test')
            && typeof untrusted._archiveState.archivedAt === 'string'
            && typeof untrusted._archiveState.archivedBy === 'string'
            ? {
                ...untrusted._archiveState,
                previousCatalogAbsent: untrusted._archiveState.previousCatalogAbsent === true ? true : undefined,
            }
            : undefined;
        return {
            ...parsed,
            _documentId: id,
            title: typeof parsed.title === 'string' && parsed.title.trim() ? repairDisplayText(parsed.title) : parsed.title,
            _workspaceRef: workspace,
            _migratedFrom: migratedFrom,
            _archiveState: archiveState,
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

    list(): StoredDocument[] {
        return readdirSync(this.canvasRoot)
            .filter((name) => name.endsWith('.json'))
            .map((name) => {
                try {
                    return this.readStored(name.slice(0, -5));
                } catch {
                    return null; // 损坏文件跳过，list 永不整体失败
                }
            })
            .filter((document): document is StoredDocument => Boolean(document))
            .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
    }

    /** 画布 id = 文件名(去 .json); builtin 有具名画布(如 review-inbox), 不能从 sessionId 反推。 */
    listIds(): string[] {
        return readdirSync(this.canvasRoot)
            .filter((name) => name.endsWith('.json'))
            .map((name) => name.slice(0, -5))
            .filter((id) => {
                try {
                    return this.readStored(id) !== null;
                } catch {
                    return false;
                }
            });
    }

    get(documentId: string): StoredDocument | null { return this.readStored(safeId(documentId)); }

    archive(documentId: string, actorRef: MaterialNoteRef): StoredDocument {
        const id = safeId(documentId);
        const actor = validRef(actorRef, 'actor_ref');
        const current = this.get(id);
        if (!current) throw new TypeError(`Canvas 不存在：${id}`);
        const lifecycle = current.catalog?.lifecycle ?? 'test';
        if (lifecycle === 'archived') return current;
        const result = this.mutate({
            document_id: id,
            workspace_ref: current._workspaceRef,
            actor_ref: actor,
            base_revision: current.revision,
            idempotency_key: safeId(`archive-${Date.now()}-${id}`.slice(0, 220), 'idempotency_key'),
            patch: {
                set_catalog: true,
                catalog: { ...(current.catalog ?? { purpose: 'product', role: 'working', domain: 'uncategorized' }), lifecycle: 'archived' },
            },
        });
        const next: StoredDocument = {
            ...result.document,
            _archiveState: {
                previousLifecycle: lifecycle,
                archivedAt: now(),
                archivedBy: `${actor.kind}:${actor.ref}`,
                ...(current.catalog ? {} : { previousCatalogAbsent: true as const }),
            },
        };
        this.atomicJson(this.canvasPath(id), next);
        return this.readStored(id)!;
    }

    restore(documentId: string, actorRef: MaterialNoteRef): StoredDocument {
        const id = safeId(documentId);
        const actor = validRef(actorRef, 'actor_ref');
        const current = this.get(id);
        if (!current) throw new TypeError(`Canvas 不存在：${id}`);
        if (current.catalog?.lifecycle !== 'archived') {
            throw new CanvasStoreConflict('只有已归档 Canvas 可以恢复', ['catalog.lifecycle']);
        }
        const result = this.mutate({
            document_id: id,
            workspace_ref: current._workspaceRef,
            actor_ref: actor,
            base_revision: current.revision,
            idempotency_key: safeId(`restore-${Date.now()}-${id}`.slice(0, 220), 'idempotency_key'),
            patch: {
                set_catalog: true,
                catalog: {
                    ...(current.catalog ?? { purpose: 'product', role: 'working', domain: 'uncategorized' }),
                    lifecycle: current._archiveState?.previousLifecycle ?? 'development',
                },
            },
        });
        const { _archiveState: _discarded, ...withoutArchiveState } = result.document;
        // 本来没有 catalog 的，连「没有」一起还原 —— patch 通道清不掉 catalog
        // （applyCanvasPatch 对 falsy catalog 直接跳过），所以在这里落盘时剥掉。
        const { catalog: _materialized, ...withoutCatalog } = withoutArchiveState;
        this.atomicJson(
            this.canvasPath(id),
            current._archiveState?.previousCatalogAbsent ? withoutCatalog : withoutArchiveState,
        );
        return this.readStored(id)!;
    }

    deletionPreview(documentId: string): CanvasDeletionPreview {
        const id = safeId(documentId);
        const current = this.get(id);
        if (!current) throw new TypeError(`Canvas 不存在：${id}`);
        const textIds = [...new Set(current.cards.flatMap((card) => {
            const source = card.source && typeof card.source === 'object' ? card.source as Record<string, unknown> : {};
            return typeof source['editableId'] === 'string' ? [safeId(source['editableId'], 'source.editableId')] : [];
        }))].sort();
        const identifiers = [id, current.sessionId].filter((value) => value.length >= 4);
        const referencedBy = this.list()
            .filter((document) => document._documentId !== id)
            .filter((document) => {
                const content = JSON.stringify({ cards: document.cards, relations: document.relations });
                return identifiers.some((identifier) => content.includes(identifier));
            })
            .map((document) => ({
                document_id: document._documentId ?? canvasStorageId(document.sessionId),
                title: document.title || '未命名画布',
            }));
        return {
            document_id: id,
            title: current.title || '未命名画布',
            lifecycle: current.catalog?.lifecycle ?? 'test',
            cards: current.cards.length,
            relations: current.relations.length,
            mutations: current._mutations.length,
            retained_text_ids: textIds,
            referenced_by: referencedBy,
        };
    }

    deleteArchived(documentId: string, expectedTitle: string, allowReferenced = false): CanvasDeletionPreview {
        const id = safeId(documentId);
        const current = this.get(id);
        if (!current) throw new TypeError(`Canvas 不存在：${id}`);
        if (current.catalog?.lifecycle !== 'archived') {
            throw new CanvasStoreConflict('永久删除前必须先归档 Canvas', ['catalog.lifecycle']);
        }
        const title = current.title || '未命名画布';
        if (expectedTitle !== title) throw new TypeError('画布名称不匹配');
        const preview = this.deletionPreview(id);
        if (preview.referenced_by.length && !allowReferenced) {
            throw new CanvasStoreConflict('Canvas 仍被其它画布引用', preview.referenced_by.map((item) => `canvas:${item.document_id}`));
        }
        this.atomicJson(this.tombstonePath(id), {
            document_id: id,
            deleted_at: now(),
        });
        unlinkSync(this.canvasPath(id));
        return preview;
    }

    putText(id: string, content: unknown): Record<string, unknown> {
        if (typeof content !== 'string') throw new TypeError('Canvas 正文必须是字符串');
        const clean = safeId(id);
        const file = this.textPath(clean);
        this.atomicText(file, content);
        return {
            path: file,
            mime: 'text/markdown',
            modified_at: statSync(file).mtime.toISOString(),
            open_token: `${this.options.productRef}-note:${clean}`,
            editableId: clean,
        };
    }

    getText(id: string): Record<string, unknown> {
        const clean = safeId(id);
        const file = this.textPath(clean);
        if (!existsSync(file)) throw new TypeError(`Canvas 正文不存在：${clean}`);
        return {
            path: file,
            mime: 'text/markdown',
            modified_at: statSync(file).mtime.toISOString(),
            open_token: `${this.options.productRef}-note:${clean}`,
            editableId: clean,
            content: readFileSync(file, 'utf8'),
        };
    }

    mutate(input: MaterialNoteMutationInput): {
        document: StoredDocument; revision: number; duplicate: boolean; merged: boolean;
    } {
        const id = safeId(input.document_id);
        const mutationId = safeId(input.idempotency_key, 'idempotency_key');
        const workspace = validRef(input.workspace_ref, 'workspace_ref');
        const actor = validRef(input.actor_ref, 'actor_ref');
        if (!Number.isInteger(input.base_revision) || input.base_revision < 0) {
            throw new TypeError('base_revision 必须是非负整数');
        }
        if (!input.patch || typeof input.patch !== 'object') throw new TypeError('patch 必填');
        const existing = this.readStored(id);
        if (this.options.workspaceCheck === 'strict') {
            // New documents must use the canonical id. Existing migrated documents
            // keep their physical id and are authorized by their stored workspace ref.
            if (!existing && id !== canvasStorageId(workspace.ref)) {
                throw new CanvasStoreConflict('Canvas 目标不属于声明的 workspace', [
                    `workspace:${workspace.kind}:${workspace.ref}`,
                    `document:${id}`,
                ]);
            }
        }
        const current = existing ?? {
            ...emptyCanvasDocument(workspace.ref),
            _documentId: id,
            _workspaceRef: workspace,
            _entityRevisions: { title: 0, catalog: 0, display: 0, behavior: 0, view: 0, cards: {}, relations: {} },
            _mutations: [],
        };
        if (current._workspaceRef.kind !== workspace.kind || current._workspaceRef.ref !== workspace.ref) {
            // builtin 域历史文档可能缺 workspace 绑定：首写时认领，之后保持一致。
            if (this.options.workspaceCheck === 'off' && current._mutations.length === 0) {
                current._workspaceRef = workspace;
            } else {
                throw new CanvasStoreConflict('Canvas workspace 引用不匹配', ['workspace_ref']);
            }
        }
        const duplicate = current._mutations.some((mutation) => mutation.id === mutationId);
        if (duplicate) {
            return {
                document: current,
                revision: current.revision,
                duplicate: true,
                merged: input.base_revision < current.revision,
            };
        }
        if (input.base_revision > current.revision) throw new CanvasStoreConflict('base revision 比当前版本更新');
        // 写入身份：给了就必须合契约。存储是溯源的唯一盖章方——卡上的 provenance 由这里写，
        // 客户端送来的 provenance 一律不信（没有身份就沿用卡上既有的溯源）。
        let identity: WriteIdentity | undefined;
        if (input.identity !== undefined && input.identity !== null) {
            const problems = validateWriteIdentity(input.identity);
            if (problems.length) throw new TypeError(`identity 不合契约: ${problems.join('; ')}`);
            identity = normalizeWriteIdentity(input.identity);
        }
        const sequence = current.revision + 1;
        // 评论卡在写入边界就校验：线程不合契约直接拒绝，而不是落盘后读回时悄悄消失。
        for (const card of input.patch.upsert_cards ?? []) {
            if (card.kind === 'comment') {
                const problems = validateCanvasCommentThread(card.commentThread, card.id);
                if (problems.length) throw new TypeError(`评论卡 ${card.id} 不合契约: ${problems.join('; ')}`);
            } else if (card.commentThread !== undefined) {
                throw new TypeError(`只有评论卡可以带 commentThread: ${card.id}`);
            }
        }
        const currentCards = new Map(current.cards.map((card) => [card.id, card]));
        const stampProvenance = (card: CanvasCardRecord): CanvasCardRecord => {
            const previous = currentCards.get(card.id);
            const { provenance: _untrusted, ...rest } = card;
            const provenance: CanvasCardProvenance | undefined = identity
                ? { created: previous?.provenance?.created ?? identity, updated: identity }
                : previous?.provenance;
            return provenance ? { ...rest, provenance } : rest;
        };
        const patch: CanvasMutationPatch = {
            ...input.patch,
            upsert_cards: input.patch.upsert_cards?.map((card) => (
                this.normalizeMaterialCard(stampProvenance(card), id, workspace, sequence, actor)
            )),
        };
        const lockedPositionConflicts = (patch.upsert_cards ?? []).flatMap((card) => {
            const previous = currentCards.get(card.id);
            if (!previous?.positionLock?.locked || input.position_lock_override === true) return [];
            const moved = previous.x !== card.x
                || previous.y !== card.y
                || previous.parentId !== card.parentId;
            return moved ? [`card:${card.id}:position`] : [];
        });
        if (lockedPositionConflicts.length) {
            throw new CanvasStoreConflict('Locked card position cannot change without position_lock_override', lockedPositionConflicts);
        }
        const touchedCards = new Set([
            ...(patch.upsert_cards ?? []).map((card) => safeId(card.id, 'card.id')),
            ...(patch.remove_card_ids ?? []).map((value) => safeId(value, 'remove_card_ids')),
        ]);
        const touchedRelations = new Set([
            ...(patch.upsert_relations ?? []).map((relation) => safeId(relation.id, 'relation.id')),
            ...(patch.remove_relation_ids ?? []).map((value) => safeId(value, 'remove_relation_ids')),
        ]);
        const conflicts = [...touchedCards]
            .filter((cardId) => (current._entityRevisions.cards[cardId] ?? 0) > input.base_revision)
            .map((cardId) => `card:${cardId}`);
        conflicts.push(...[...touchedRelations]
            .filter((relationId) => (current._entityRevisions.relations[relationId] ?? 0) > input.base_revision)
            .map((relationId) => `relation:${relationId}`));
        if (patch.set_title && current._entityRevisions.title > input.base_revision) conflicts.push('title');
        if (patch.set_catalog && current._entityRevisions.catalog > input.base_revision) conflicts.push('catalog');
        if (patch.set_display && current._entityRevisions.display > input.base_revision) conflicts.push('display');
        if (patch.set_behavior && current._entityRevisions.behavior > input.base_revision) conflicts.push('behavior');
        if (patch.set_view && current._entityRevisions.view > input.base_revision) conflicts.push('view');
        if (conflicts.length) throw new CanvasStoreConflict('Canvas 修改与较新修改冲突', conflicts);
        const at = now();
        const mutation: StoredMutation = {
            id: mutationId,
            actor: `${actor.kind}:${actor.ref}`,
            sequence,
            at,
            ...(identity ? { identity } : {}),
            ...(identity?.purpose ? { purpose: identity.purpose } : {}),
            ...(identity?.batch ? { batch: identity.batch } : {}),
            ...(touchedCards.size ? { cards: [...touchedCards].slice(0, 200) } : {}),
            ...(touchedRelations.size ? { relations: [...touchedRelations].slice(0, 200) } : {}),
        };
        const applied = applyCanvasMutation(current, patch);
        const next: StoredDocument = {
            ...applied,
            revision: sequence,
            updatedAt: at,
            lastMutation: mutation,
            _workspaceRef: workspace,
            _migratedFrom: current._migratedFrom,
            _archiveState: current._archiveState,
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
        // Existing Canvas records may still carry the retired presentation-only
        // material_kind field. Keep those records readable while strict format
        // tags and all structural Canvas rules remain enforced.
        const canvasLint = lintCanvasDocument(next, {
            allowLegacyMaterialKind: true,
            // A generic Canvas host has no owner profile to route through; a
            // project host (for example a game project) supplies materialClassification and
            // therefore gets the strict classification gate.
            requireClassification: Boolean(this.options.materialClassification),
        });
        if (!canvasLint.ok) throw new TypeError(`Canvas lint failed: ${canvasLint.issues.map((item) => `${item.path}: ${item.message}`).join('; ')}`);
        this.atomicJson(this.canvasPath(id), next);
        if (!existing && this.isDeleted(id)) unlinkSync(this.tombstonePath(id));
        return { document: next, revision: sequence, duplicate: false, merged: input.base_revision < current.revision };
    }

    /** 写入历史（带身份 / 目的 / 触碰的卡），按 sequence 升序；给 card 只回触碰过这张卡的写入。 */
    listMutations(documentId: string, query: MutationQuery = {}): StoredMutation[] {
        const current = this.get(safeId(documentId));
        if (!current) throw new TypeError(`Canvas 不存在：${documentId}`);
        const limit = Math.max(1, Math.min(500, Math.trunc(query.limit ?? 50)));
        const since = Number.isFinite(query.since) ? Number(query.since) : 0;
        const card = typeof query.card === 'string' && query.card ? safeId(query.card, 'card') : undefined;
        const hits = current._mutations
            .filter((mutation) => mutation.sequence > since)
            .filter((mutation) => !card || (mutation.cards ?? []).includes(card));
        return hits.slice(-limit);
    }

    /**
     * 后补写入目的 / 批号。改的是历史记录的注解，不推进 revision、不动实体时钟——
     * 「目的可以后补」就是这条。返回被注解的 mutation 数。
     */
    annotateMutations(documentId: string, annotation: MutationAnnotation): { annotated: number; mutation_ids: string[] } {
        const id = safeId(documentId);
        const current = this.get(id);
        if (!current) throw new TypeError(`Canvas 不存在：${id}`);
        const purpose = typeof annotation.purpose === 'string' ? annotation.purpose.trim().slice(0, 2000) : '';
        const batch = typeof annotation.batch === 'string' ? annotation.batch.trim().slice(0, 200) : '';
        if (!purpose && !batch) throw new TypeError('annotate 至少要给 purpose 或 batch');
        const ids = new Set((annotation.mutation_ids ?? []).map((value) => safeId(value, 'mutation_id')));
        const from = Number.isFinite(annotation.from_sequence) ? Number(annotation.from_sequence) : undefined;
        const to = Number.isFinite(annotation.to_sequence) ? Number(annotation.to_sequence) : undefined;
        if (!ids.size && from === undefined && to === undefined) throw new TypeError('annotate 要给 mutation_ids 或 from_sequence/to_sequence');
        const matched: string[] = [];
        const mutations = current._mutations.map((mutation) => {
            const inRange = (from === undefined || mutation.sequence >= from) && (to === undefined || mutation.sequence <= to);
            const hit = ids.has(mutation.id) || (ids.size === 0 && inRange) || (ids.size > 0 && (from !== undefined || to !== undefined) && inRange);
            if (!hit) return mutation;
            matched.push(mutation.id);
            const identity = mutation.identity
                ? { ...mutation.identity, ...(purpose ? { purpose } : {}), ...(batch ? { batch } : {}) }
                : undefined;
            return {
                ...mutation,
                ...(purpose ? { purpose } : {}),
                ...(batch ? { batch } : {}),
                ...(identity ? { identity } : {}),
            };
        });
        if (matched.length) this.atomicJson(this.canvasPath(id), { ...current, _mutations: mutations });
        return { annotated: matched.length, mutation_ids: matched };
    }
}
