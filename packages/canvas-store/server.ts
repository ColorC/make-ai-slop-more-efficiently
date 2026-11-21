/**
 * @omni/canvas-store 独立服务端 —— 一个存储根一个进程，宿主自持，零跨软件依赖。
 *
 * 2026-08-16 裁决修正：workshop 只服务自己的画布（可单独部署）；omnidashboard 的
 * builtin 画布由 dashboard 自己起本服务实例（root=overlay-note-store），不得挂
 * 在 workshop 上。两个软件各跑同一包的一个实例 = "运行同一个包"的正解。
 *
 * 用法:
 *   node --experimental-strip-types server.ts \
 *     --root <存储根> --product <产品ref> [--text-dir texts|materials] \
 *     [--workspace-check strict|off] [--port 8212] [--token-file <path>]
 *
 * 鉴权：GET 公开只读；写操作必须带 Authorization: Bearer <token>（token-file 提供；
 * 未给 token-file 时写也放行，仅限本机回环自测）。
 */

import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFileSync } from 'node:fs';
import { CanvasFileStore, CanvasStoreConflict, safeId } from './store.ts';

interface CliOptions {
    root: string;
    product: string;
    textDir: string;
    workspaceCheck: 'strict' | 'off';
    port: number;
    tokenFile?: string;
}

function parseArgs(argv: string[]): CliOptions {
    const options: Partial<CliOptions> = { textDir: 'texts', workspaceCheck: 'strict', port: 8212 };
    for (let i = 0; i < argv.length; i += 1) {
        const key = argv[i];
        const next = argv[i + 1];
        if (key === '--root') options.root = next;
        else if (key === '--product') options.product = next;
        else if (key === '--text-dir') options.textDir = next;
        else if (key === '--workspace-check') options.workspaceCheck = next === 'off' ? 'off' : 'strict';
        else if (key === '--port') options.port = Number(next);
        else if (key === '--token-file') options.tokenFile = next;
    }
    if (!options.root || !options.product) {
        throw new TypeError('--root 与 --product 必填');
    }
    return options as CliOptions;
}

function send(res: ServerResponse, status: number, body: unknown): void {
    const text = JSON.stringify(body);
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(text);
}

function readBody(req: IncomingMessage): Promise<string> {
    return new Promise((resolvePromise, rejectPromise) => {
        // 必须累积 Buffer 后一次性解码: 之前是 `raw += chunk`(每个 TCP chunk 各自
        // 隐式 toString('utf8')), payload 大到分块时多字节字符会被块边界劈开,
        // 落盘变成 U+FFFD。实测 1.3MB 的画布 mutate 里出现「真」→「��」、「依」→「���」,
        // 小请求单块所以一直没暴露。上限也从字符数改成字节数(HTTP body 的正确口径)。
        const chunks: Buffer[] = [];
        let size = 0;
        req.on('data', (chunk) => {
            const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as string);
            size += buf.byteLength;
            if (size > 8 * 1024 * 1024) {
                rejectPromise(new TypeError('body too large'));
                return;
            }
            chunks.push(buf);
        });
        req.on('end', () => resolvePromise(Buffer.concat(chunks).toString('utf8')));
        req.on('error', rejectPromise);
    });
}

function main(): void {
    const options = parseArgs(process.argv.slice(2));
    const store = new CanvasFileStore(options.root, {
        productRef: options.product,
        textDir: options.textDir,
        workspaceCheck: options.workspaceCheck,
    });
    let token = '';
    const reloadToken = () => {
        if (!options.tokenFile) return;
        try { token = readFileSync(options.tokenFile, 'utf8').trim(); } catch { token = ''; }
    };
    reloadToken();
    setInterval(reloadToken, 30_000).unref();

    const authorized = (req: IncomingMessage): boolean => {
        if (!token) return true; // 未配置令牌 = 本机回环自测口径
        const header = String(req.headers.authorization ?? '');
        return header.startsWith('Bearer ') && header.slice(7).trim() === token;
    };

    const server = createServer(async (req, res) => {
        const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
        const path = decodeURIComponent(url.pathname);
        const method = req.method ?? 'GET';
        try {
            if (path === '/api/health') return send(res, 200, { ok: true, service: 'canvas-store', product: options.product });

            if (path === '/api/canvases' && method === 'GET') {
                const canvases = store.listIds().map((id) => {
                    const document = store.get(id);
                    if (!document) return null;
                    return {
                        document_id: id,
                        title: document.title,
                        revision: document.revision,
                        updated_at: document.updatedAt,
                        cards: document.cards.length,
                        relations: document.relations.length,
                        workspace_ref: document._workspaceRef,
                        migrated_from: document._migratedFrom,
                    };
                }).filter((item): item is NonNullable<typeof item> => Boolean(item));
                return send(res, 200, { canvases });
            }

            const canvasMatch = /^\/api\/canvases\/([A-Za-z0-9._-]{1,220})$/.exec(path);
            if (canvasMatch && method === 'GET') {
                const document = store.get(canvasMatch[1]);
                if (!document) return send(res, 404, { error: 'canvas_not_found' });
                return send(res, 200, { document, workspace_ref: document._workspaceRef, migrated_from: document._migratedFrom });
            }
            const mutateMatch = /^\/api\/canvases\/([A-Za-z0-9._-]{1,220})\/mutations$/.exec(path);
            if (mutateMatch && method === 'GET') {
                // 写入历史（身份 / 目的 / 触碰的卡）。?card=<id>&since=<seq>&limit=<n>
                try {
                    const mutations = store.listMutations(mutateMatch[1], {
                        card: url.searchParams.get('card') ?? undefined,
                        since: url.searchParams.get('since') ? Number(url.searchParams.get('since')) : undefined,
                        limit: url.searchParams.get('limit') ? Number(url.searchParams.get('limit')) : undefined,
                    });
                    return send(res, 200, { document_id: mutateMatch[1], mutations });
                } catch (error) {
                    if (error instanceof TypeError && error.message.startsWith('Canvas 不存在')) return send(res, 404, { error: 'canvas_not_found' });
                    throw error;
                }
            }
            const annotateMatch = /^\/api\/canvases\/([A-Za-z0-9._-]{1,220})\/mutations\/annotate$/.exec(path);
            if (annotateMatch && method === 'POST') {
                if (!authorized(req)) return send(res, 401, { error: 'unauthorized' });
                const body = JSON.parse(await readBody(req)) as Record<string, unknown>;
                try {
                    return send(res, 200, store.annotateMutations(annotateMatch[1], body as never));
                } catch (error) {
                    if (error instanceof TypeError && error.message.startsWith('Canvas 不存在')) return send(res, 404, { error: 'canvas_not_found' });
                    throw error;
                }
            }
            if (mutateMatch && method === 'POST') {
                if (!authorized(req)) return send(res, 401, { error: 'unauthorized' });
                const body = JSON.parse(await readBody(req)) as Record<string, unknown>;
                try {
                    const result = store.mutate({ ...(body as never), document_id: mutateMatch[1] });
                    return send(res, 200, { ...result, workspace_ref: result.document._workspaceRef });
                } catch (error) {
                    if (error instanceof CanvasStoreConflict) {
                        return send(res, 409, { error: 'canvas_conflict', detail: error.message, conflicts: error.conflicts });
                    }
                    throw error;
                }
            }

            const textMatch = /^\/api\/material-notes\/text\/([A-Za-z0-9._-]{1,220})$/.exec(path);
            if (textMatch && method === 'GET') {
                try {
                    return send(res, 200, store.getText(textMatch[1]));
                } catch {
                    return send(res, 404, { error: 'text_not_found' });
                }
            }
            if (textMatch && method === 'PUT') {
                if (!authorized(req)) return send(res, 401, { error: 'unauthorized' });
                const body = JSON.parse(await readBody(req)) as { content?: unknown };
                return send(res, 200, store.putText(textMatch[1], body.content));
            }

            return send(res, 404, { error: 'not_found' });
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            if (message.includes('非法') || message.includes('必填') || message.includes('must')) {
                return send(res, 400, { error: 'bad_request', detail: message });
            }
            return send(res, 500, { error: 'internal', detail: message });
        }
    });

    server.listen(options.port, '127.0.0.1', () => {
        console.log(JSON.stringify({
            event: 'canvas-store-listen',
            port: options.port,
            root: store.root,
            product: options.product,
            textDir: options.textDir,
            workspaceCheck: options.workspaceCheck,
        }));
    });
}

main();
