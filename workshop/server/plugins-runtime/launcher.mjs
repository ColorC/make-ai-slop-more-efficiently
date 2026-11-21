// 脚本型插件的启动器。由服务端以 `node --permission --allow-fs-read=… --allow-fs-write=…` 起子进程运行；
// 它自己也在白名单里（只读）。职责：读 stdin 的任务输入 → 动态 import 插件入口 → 调 default(input, ctx)
// → 把结果按一行 JSON 写到 stdout。
//
// 单线程，故意不用 Worker：2026-09-22 实测 `--allow-worker` 下 Worker 里的 fs 读**绕过**权限模型
// （Node 自己也打 SecurityWarning「could invalidate the permission model」）。没有 worker、没有子进程，
// 一个插件进程最多吃满一个核，CPU 时间 ≤ 墙钟——墙钟由父进程杀，这里的 CPU 自查只是插件让出事件循环时的
// 提前一步。堆上限用 --max-old-space-size（由父进程传）。业主：防君子不防小人——挡失控脚本，不挡有心人。
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const entry = process.argv[2];
const cpuBudgetMs = Number(process.argv[3] ?? '0') || 0;
const readStdin = () => new Promise((resolve) => { let data = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', (c) => { data += c; }); process.stdin.on('end', () => resolve(data)); });
const emit = (payload) => { process.stdout.write(JSON.stringify(payload) + '\n'); };
const cpuMs = () => { const u = process.cpuUsage(); return Math.round((u.user + u.system) / 1000); };

const logs = [];
if (cpuBudgetMs > 0) {
    setInterval(() => {
        if (cpuMs() > cpuBudgetMs) { emit({ ok: false, error: 'budget_exceeded', detail: `CPU 时间超过预算 ${cpuBudgetMs}ms`, logs, cpuMs: cpuMs() }); process.exit(75); }
    }, 200).unref();
}

const ctx = {
    input: undefined,
    // 插件能碰的目录由父进程按清单映射后经 env 传入；这里只是把它们摆到顺手的位置，真正的门是 --allow-fs-*。
    dirs: JSON.parse(process.env.WORKSHOP_PLUGIN_DIRS || '{}'),
    log: (...parts) => { logs.push(parts.map((p) => (typeof p === 'string' ? p : JSON.stringify(p))).join(' ').slice(0, 2000)); if (logs.length > 200) logs.shift(); },
    readText: (file) => fs.readFileSync(file, 'utf8'),
    writeText: (file, text) => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, text); },
};

try {
    const raw = await readStdin();
    ctx.input = raw.trim() ? JSON.parse(raw) : null;
    const mod = await import(pathToFileURL(path.resolve(entry)).href);
    const main = typeof mod.default === 'function' ? mod.default : typeof mod.main === 'function' ? mod.main : null;
    if (!main) throw new Error('插件入口没有导出 default 函数');
    const output = await main(ctx.input, ctx);
    emit({ ok: true, output: output === undefined ? null : output, logs, cpuMs: cpuMs() });
    process.exit(0);
} catch (error) {
    const code = error && typeof error === 'object' && 'code' in error ? String(error.code) : '';
    emit({ ok: false, error: code === 'ERR_ACCESS_DENIED' ? 'permission_denied' : 'plugin_error', detail: `${error instanceof Error ? error.message : String(error)}`.slice(0, 2000), logs, cpuMs: cpuMs() });
    process.exit(code === 'ERR_ACCESS_DENIED' ? 77 : 1);
}
