/**
 * 脚本型插件的运行器：Node 权限模型子进程。
 *
 * 门是 Node 自己的 `--permission`：只放清单映射出来的目录可读 / 可写；child_process、worker、addons、wasi 默认全封
 * （2026-09-22 在 Node 24 上实测：越界读 ERR_ACCESS_DENIED，spawn 与 Worker 同样 ERR_ACCESS_DENIED）。
 * Node 不管 CPU 与内存：墙钟由这里杀；没有 worker / 子进程时 CPU ≤ 墙钟，所以墙钟就是 CPU 的硬顶，
 * launcher 的 CPU 自查只在插件让出事件循环时提前生效；堆上限用 --max-old-space-size（超了进程自杀，按 stderr 认出来）。
 * 业界同款：Deno 的 --allow-read/--allow-write、GitHub Actions 的声明式权限。防君子不防小人。
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const LAUNCHER = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'plugins-runtime', 'launcher.mjs');

export interface ScriptRunOptions {
    entry: string;
    readDirs: string[];
    writeDirs: string[];
    input: unknown;
    budget: { wallMs: number; cpuMs: number; memoryMb: number; scratchBytes?: number };
    dirs: Record<string, string>;
    /** Override the launcher when the runner is bundled into the downloadable CLI. */
    launcherPath?: string;
}

export interface ScriptRunResult {
    ok: boolean;
    output?: unknown;
    logs: string[];
    error?: string;
    detail?: string;
    usage: { wallMs: number; cpuMs: number | null; exitCode: number | null; signal: string | null; scratchBytes: number };
    budgetExceeded: boolean;
}

export function launcherPath(): string { return LAUNCHER; }

function scratchUsage(root: string): number {
    let bytes = 0;
    const visit = (current: string): void => {
        const stat = fs.lstatSync(current);
        if (stat.isSymbolicLink()) throw new Error(`scratch 目录不能包含符号链接：${current}`);
        if (stat.isDirectory()) {
            for (const name of fs.readdirSync(current)) visit(path.join(current, name));
        } else if (stat.isFile()) bytes += stat.size;
    };
    visit(root);
    return bytes;
}

export function runScript(options: ScriptRunOptions): Promise<ScriptRunResult> {
    const startedAt = Date.now();
    const launcher = options.launcherPath ? path.resolve(options.launcherPath) : LAUNCHER;
    const scratchRoot = options.dirs.scratch ? path.resolve(options.dirs.scratch) : null;
    const scratchLimit = options.budget.scratchBytes ?? 16 * 1024 * 1024;
    let initialScratch = 0;
    try { if (scratchRoot) initialScratch = scratchUsage(scratchRoot); } catch (error) {
        return Promise.resolve({ ok: false, logs: [], error: 'scratch_path_invalid', detail: error instanceof Error ? error.message : String(error), usage: { wallMs: 0, cpuMs: null, exitCode: null, signal: null, scratchBytes: 0 }, budgetExceeded: false });
    }
    if (initialScratch > scratchLimit) return Promise.resolve({ ok: false, logs: [], error: 'budget_exceeded', detail: `scratch 已有 ${initialScratch} 字节，超过预算 ${scratchLimit} 字节`, usage: { wallMs: 0, cpuMs: null, exitCode: null, signal: null, scratchBytes: initialScratch }, budgetExceeded: true });
    const readDirs = [...new Set([launcher, path.dirname(options.entry), ...options.readDirs, ...options.writeDirs])];
    const args = [
        '--permission',
        ...readDirs.map((dir) => `--allow-fs-read=${dir}`),
        ...options.writeDirs.map((dir) => `--allow-fs-write=${dir}`),
        // 绝不加 --allow-worker：实测 Worker 里的 fs 读会绕过权限模型（Node 自己也警告）。没有 worker 与子进程，
        // 插件最多吃满一个核，CPU ≤ 墙钟，所以 CPU 预算实际由墙钟兜底，launcher 的自查只是提前一步。
        `--max-old-space-size=${Math.max(16, Math.floor(options.budget.memoryMb))}`,
        launcher, options.entry, String(options.budget.cpuMs),
    ];
    // 环境变量只给最小集：不把服务端的密钥、令牌、代理配置带进插件进程。
    const env: Record<string, string> = {
        PATH: process.env['PATH'] ?? '', SystemRoot: process.env['SystemRoot'] ?? process.env['SYSTEMROOT'] ?? '', TEMP: process.env['TEMP'] ?? '', TMP: process.env['TMP'] ?? '',
        WORKSHOP_PLUGIN_DIRS: JSON.stringify(options.dirs), NODE_OPTIONS: '',
    };
    return new Promise((resolve) => {
        const child = spawn(process.execPath, args, { env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
        let stdout = ''; let stderr = ''; let timedOut = false; let scratchExceeded = false; let scratchInvalid: string | null = null; let settled = false;
        const finish = (result: ScriptRunResult): void => { if (settled) return; settled = true; clearTimeout(timer); clearInterval(scratchTimer); resolve(result); };
        const timer = setTimeout(() => { timedOut = true; try { child.kill(); } catch { /* 已退出 */ } }, options.budget.wallMs);
        const scratchTimer = setInterval(() => {
            if (!scratchRoot || settled) return;
            try { const bytes = scratchUsage(scratchRoot); if (bytes > scratchLimit) { scratchExceeded = true; try { child.kill(); } catch {} } }
            catch (error) { scratchInvalid = error instanceof Error ? error.message : String(error); try { child.kill(); } catch {} }
        }, 25);
        child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString('utf8'); if (stdout.length > 4 * 1024 * 1024) { timedOut = false; try { child.kill(); } catch { /* */ } } });
        child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString('utf8'); if (stderr.length > 64 * 1024) stderr = stderr.slice(-64 * 1024); });
        child.on('error', (error) => finish({ ok: false, logs: [], error: 'spawn_failed', detail: error.message, usage: { wallMs: Date.now() - startedAt, cpuMs: null, exitCode: null, signal: null, scratchBytes: initialScratch }, budgetExceeded: false }));
        child.on('close', (exitCode, signal) => {
            const wallMs = Date.now() - startedAt;
            let finalScratch = initialScratch;
            if (scratchRoot) { try { finalScratch = scratchUsage(scratchRoot); } catch (error) { scratchInvalid = scratchInvalid ?? (error instanceof Error ? error.message : String(error)); } }
            const lines = stdout.trim().split('\n').filter(Boolean);
            let payload: Record<string, unknown> | null = null;
            for (let i = lines.length - 1; i >= 0 && !payload; i -= 1) { try { const parsed = JSON.parse(lines[i]!); if (parsed && typeof parsed === 'object') payload = parsed as Record<string, unknown>; } catch { /* 不是结果行 */ } }
            const logs = Array.isArray(payload?.['logs']) ? (payload!['logs'] as unknown[]).map(String) : [];
            const cpuMs = typeof payload?.['cpuMs'] === 'number' ? payload['cpuMs'] as number : null;
            if (scratchInvalid) return finish({ ok: false, logs, error: 'scratch_path_invalid', detail: scratchInvalid, usage: { wallMs, cpuMs, exitCode, signal, scratchBytes: finalScratch }, budgetExceeded: false });
            if (scratchExceeded || finalScratch > scratchLimit) return finish({ ok: false, logs, error: 'budget_exceeded', detail: `scratch 使用 ${finalScratch} 字节，超过预算 ${scratchLimit} 字节`, usage: { wallMs, cpuMs, exitCode, signal, scratchBytes: finalScratch }, budgetExceeded: true });
            if (timedOut) return finish({ ok: false, logs, error: 'budget_exceeded', detail: `墙钟超过预算 ${options.budget.wallMs}ms，已终止`, usage: { wallMs, cpuMs, exitCode, signal, scratchBytes: finalScratch }, budgetExceeded: true });
            if (payload && payload['ok'] === true) return finish({ ok: true, output: payload['output'] ?? null, logs, usage: { wallMs, cpuMs, exitCode, signal, scratchBytes: finalScratch }, budgetExceeded: false });
            const heapOom = /heap out of memory|Allocation failed|OOM/i.test(stderr);
            const error = typeof payload?.['error'] === 'string' ? payload['error'] : heapOom ? 'budget_exceeded' : exitCode === 0 ? 'no_result' : 'plugin_crashed';
            const detail = typeof payload?.['detail'] === 'string' ? payload['detail'] : heapOom ? `内存超过预算 ${options.budget.memoryMb}MB，进程已终止` : stderr.trim().split('\n').slice(-6).join('\n') || `进程退出码 ${exitCode ?? signal}`;
            finish({ ok: false, logs, error, detail: detail.slice(0, 2000), usage: { wallMs, cpuMs, exitCode, signal, scratchBytes: finalScratch }, budgetExceeded: error === 'budget_exceeded' });
        });
        child.stdin.on('error', () => undefined);
        child.stdin.end(JSON.stringify(options.input ?? null));
    });
}
