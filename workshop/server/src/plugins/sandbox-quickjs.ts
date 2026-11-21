/**
 * 转换型插件的沙箱：QuickJS（WASM）。
 *
 * 业界同款：Figma 插件逻辑跑在 WASM 里的 QuickJS，Cloudflare Workers 跑在 V8 isolate——
 * 都是「能力注入」：插件拿不到 fs、net、process，宿主只把允许的接口塞进去。这里注入的只有
 * `workshop.api(method, path, body)`（按清单的 api 白名单、以调用者本人身份打 Workshop 自己的接口）、
 * `workshop.log()` 与 `workshop.input`。内存上限 + 中断器（墙钟）是硬限，超了直接掐。
 *
 * 选 WASM 而不是 isolated-vm：不需要本机编译原生模块，换机器不炸。vm2 已停更、Node 自带 vm 不是安全边界，都不用。
 *
 * 注意：asyncified 的宿主函数在插件里是**同步**的（quickjs-emscripten 的 "Async on host, sync in QuickJS"），
 * 插件写 `const r = workshop.api('GET', '/api/...')` 直接拿返回值；default 返回 Promise 也接受。
 */
import { newQuickJSAsyncWASMModule, shouldInterruptAfterDeadline, type QuickJSAsyncWASMModule, type QuickJSAsyncContext, type QuickJSHandle } from 'quickjs-emscripten';

export interface TransformHost {
    api(method: string, path: string, body: unknown): Promise<unknown>;
    log(line: string): void;
}

export interface TransformRunResult {
    ok: boolean;
    output?: unknown;
    error?: string;
    detail?: string;
    usage: { wallMs: number; memoryBytes: number | null };
    budgetExceeded: boolean;
}

let modulePromise: Promise<QuickJSAsyncWASMModule> | null = null;
const quickjs = (): Promise<QuickJSAsyncWASMModule> => (modulePromise ??= newQuickJSAsyncWASMModule());

function marshal(ctx: QuickJSAsyncContext, value: unknown): QuickJSHandle {
    // 走 JSON：宿主对象永远不会以引用形式漏进沙箱。
    const json = JSON.stringify(value === undefined ? null : value);
    const result = ctx.evalCode(`(${json})`, 'marshal.js');
    return ctx.unwrapResult(result);
}

function classify(message: string): { error: string; budgetExceeded: boolean } {
    if (/interrupted/i.test(message)) return { error: 'budget_exceeded', budgetExceeded: true };
    if (/out of memory|memory limit|InternalError: out of memory/i.test(message)) return { error: 'budget_exceeded', budgetExceeded: true };
    if (/stack overflow|Maximum call stack/i.test(message)) return { error: 'budget_exceeded', budgetExceeded: true };
    if (/^api_forbidden/.test(message)) return { error: 'api_forbidden', budgetExceeded: false };
    if (/^api_budget/.test(message)) return { error: 'budget_exceeded', budgetExceeded: true };
    if (/^api_error/.test(message)) return { error: 'api_error', budgetExceeded: false };
    return { error: 'plugin_error', budgetExceeded: false };
}

export async function runTransform(code: string, input: unknown, host: TransformHost, budget: { wallMs: number; memoryMb: number }): Promise<TransformRunResult> {
    const startedAt = Date.now();
    const QuickJS = await quickjs();
    const runtime = QuickJS.newRuntime();
    runtime.setMemoryLimit(Math.max(4, budget.memoryMb) * 1024 * 1024);
    runtime.setMaxStackSize(1024 * 1024);
    runtime.setInterruptHandler(shouldInterruptAfterDeadline(startedAt + budget.wallMs));
    const ctx = runtime.newContext();
    let memoryBytes: number | null = null;
    const fail = (message: string): TransformRunResult => {
        const kind = classify(message);
        return { ok: false, error: kind.error, detail: message.slice(0, 2000), usage: { wallMs: Date.now() - startedAt, memoryBytes }, budgetExceeded: kind.budgetExceeded };
    };
    try {
        const workshop = ctx.newObject();
        const apiFn = ctx.newAsyncifiedFunction('api', async (methodHandle, pathHandle, bodyHandle) => {
            const method = ctx.getString(methodHandle);
            const apiPath = ctx.getString(pathHandle);
            const body = bodyHandle && ctx.typeof(bodyHandle) !== 'undefined' ? ctx.dump(bodyHandle) : undefined;
            // 宿主抛出的 Error 会变成沙箱里的异常；message 带前缀便于归类。
            const result = await host.api(method, apiPath, body);
            return marshal(ctx, result);
        });
        ctx.setProp(workshop, 'api', apiFn); apiFn.dispose();
        const logFn = ctx.newFunction('log', (...args) => { host.log(args.map((arg) => { const value = ctx.dump(arg); return typeof value === 'string' ? value : JSON.stringify(value); }).join(' ').slice(0, 2000)); });
        ctx.setProp(workshop, 'log', logFn); logFn.dispose();
        const inputHandle = marshal(ctx, input);
        ctx.setProp(workshop, 'input', inputHandle);
        ctx.setProp(ctx.global, '__input', inputHandle); inputHandle.dispose();
        ctx.setProp(ctx.global, 'workshop', workshop); workshop.dispose();

        const program = code.replace(/\bexport\s+default\s+/, 'globalThis.__plugin_main = ')
            + '\n;(() => { if (typeof globalThis.__plugin_main !== "function") throw new Error("插件没有导出 default 函数");'
            + ' const __r = globalThis.__plugin_main(globalThis.__input, globalThis.workshop);'
            + ' const __s = (v) => JSON.stringify(v === undefined ? null : v) ?? "null";'
            + ' return (__r && typeof __r.then === "function") ? __r.then(__s) : __s(__r); })()';
        const evaluated = await ctx.evalCodeAsync(program, 'plugin.js', { type: 'global' });
        if (evaluated.error) { const err = ctx.dump(evaluated.error); evaluated.error.dispose(); return fail(errorMessage(err)); }
        let handle = evaluated.value;
        if (ctx.typeof(handle) !== 'string') {
            // default 返回了 Promise：把待办跑完再解出来。
            const pending = ctx.resolvePromise(handle);
            runtime.executePendingJobs();
            const resolved = await pending;
            handle.dispose();
            if (resolved.error) { const err = ctx.dump(resolved.error); resolved.error.dispose(); return fail(errorMessage(err)); }
            handle = resolved.value;
        }
        const json = ctx.typeof(handle) === 'string' ? ctx.getString(handle) : JSON.stringify(ctx.dump(handle));
        handle.dispose();
        try { const usage = ctx.dump(runtime.computeMemoryUsage()) as { memory_used_size?: number }; memoryBytes = typeof usage.memory_used_size === 'number' ? usage.memory_used_size : null; } catch { /* 统计失败不影响结果 */ }
        return { ok: true, output: json === undefined ? null : JSON.parse(json), usage: { wallMs: Date.now() - startedAt, memoryBytes }, budgetExceeded: false };
    } catch (error) {
        return fail(error instanceof Error ? error.message : String(error));
    } finally {
        try { ctx.dispose(); } catch { /* 已释放 */ }
        try { runtime.dispose(); } catch { /* 已释放 */ }
    }
}

function errorMessage(dumped: unknown): string {
    if (dumped && typeof dumped === 'object') {
        const record = dumped as { name?: unknown; message?: unknown; stack?: unknown };
        const name = typeof record.name === 'string' ? record.name : 'Error';
        const message = typeof record.message === 'string' ? record.message : JSON.stringify(dumped);
        return name === 'Error' || name === 'InternalError' ? message : `${name}: ${message}`;
    }
    return String(dumped);
}
