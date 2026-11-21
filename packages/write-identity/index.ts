/**
 * @omni/write-identity — Node 侧写入身份解析（被动采集，会话无感）。
 *
 * 一次写入是「谁、在哪台机器、哪个运行时、哪个会话、为了什么」。这里把能从环境与
 * hook 已落盘的台账里读到的都读出来，读不到的留空，**绝不编**：
 *
 *   machine   C:/workspace/config/machine-identity.yaml（id / label / owner / os）；
 *             没有配置文件时 id 取 Windows MachineGuid，owner 留空。
 *   runtime   OMNI_AGENT_PROVIDER（claude_2 / codex / …），缺省按原生 id 环境变量推断。
 *   session   厂商原生 id：Claude = CLAUDE_CODE_SESSION_ID（= jsonl 文件名），Codex = CODEX_THREAD_ID；
 *             都没有时按 OMNI_CC_PTY_ID 查 omnicompany 的会话绑定台账（SessionStart hook 写的）。
 *             file = 能在磁盘上定位到的那份 jsonl。
 *   purpose / batch  调用方给，或 OMNI_WRITE_PURPOSE / OMNI_WRITE_BATCH 环境变量；可后补。
 */
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { homedir, hostname, platform } from 'node:os';
import { join } from 'node:path';
import {
    normalizeWriteIdentity,
    type WriteCaptureSource,
    type WriteIdentity,
    type WriteMachine,
    type WriteRuntime,
    type WriteSession,
} from '../material-core/note.ts';

export const DEFAULT_MACHINE_IDENTITY_FILE = 'C:/workspace/config/machine-identity.yaml';
export const DEFAULT_OMNI_DATA_DIR = 'C:/workspace/omnicompany/data';

type Env = Record<string, string | undefined>;

/** 只认「key: value」一行一对的扁平 YAML；机器身份文件就这么简单，不引依赖。 */
export function parseFlatYaml(text: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.replace(/\s+#.*$/, '').trim();
        if (!line || line.startsWith('#')) continue;
        const at = line.indexOf(':');
        if (at <= 0) continue;
        const key = line.slice(0, at).trim();
        let value = line.slice(at + 1).trim();
        if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
        if (key) out[key] = value;
    }
    return out;
}

export function machineIdentityFile(env: Env = process.env): string {
    return env['OMNI_MACHINE_IDENTITY_FILE'] || DEFAULT_MACHINE_IDENTITY_FILE;
}

/** 机器身份配置原文（含 owner_label 等附加键）；没有文件返回 null。 */
export function readMachineIdentityFile(env: Env = process.env): Record<string, string> | null {
    const file = machineIdentityFile(env);
    if (!existsSync(file)) return null;
    try {
        return parseFlatYaml(readFileSync(file, 'utf8'));
    } catch {
        return null;
    }
}

/** Windows 机器自身标识：注册表 MachineGuid；非 Windows 或读不到返回 undefined。 */
export function machineGuid(): string | undefined {
    if (platform() !== 'win32') return undefined;
    try {
        const result = spawnSync('reg', ['query', 'HKLM\\SOFTWARE\\Microsoft\\Cryptography', '/v', 'MachineGuid'], { encoding: 'utf8', windowsHide: true, timeout: 5000 });
        const guid = /MachineGuid\s+REG_SZ\s+([0-9a-fA-F-]{36})/.exec(result.stdout || '')?.[1];
        return guid ? guid.toLowerCase() : undefined;
    } catch {
        return undefined;
    }
}

export function readMachineIdentity(env: Env = process.env): WriteMachine {
    const configured = readMachineIdentityFile(env);
    const id = configured?.id?.trim() || machineGuid() || hostname();
    const machine: WriteMachine = { id };
    const label = configured?.label?.trim() || hostname();
    if (label) machine.label = label;
    const owner = configured?.owner?.trim();
    if (owner) machine.owner = owner;
    const os = configured?.os?.trim() || (platform() === 'win32' ? 'windows' : platform());
    if (os) machine.os = os;
    return machine;
}

function providerFromEnv(env: Env): string | undefined {
    const explicit = (env['OMNI_AGENT_PROVIDER'] || env['OMNI_HOOK_PROVIDER'] || '').trim();
    if (explicit) return explicit;
    if (env['CLAUDE_CODE_SESSION_ID']) return 'claude_code';
    if (env['CODEX_THREAD_ID']) return 'codex';
    return undefined;
}

interface LedgerRecord { session_id?: string; provider?: string; model?: string; trace_id?: string; pty_id?: string }

/** 按 pty_id / trace_id 查 SessionStart hook 落的会话绑定台账（omnicompany/data/cc_session_bindings.json）。 */
export function ledgerRecord(env: Env = process.env): LedgerRecord | undefined {
    const dataDir = env['OMNI_DATA_DIR'] || DEFAULT_OMNI_DATA_DIR;
    const file = join(dataDir, 'cc_session_bindings.json');
    if (!existsSync(file)) return undefined;
    let ledger: Record<string, LedgerRecord>;
    try {
        ledger = JSON.parse(readFileSync(file, 'utf8')) as Record<string, LedgerRecord>;
    } catch {
        return undefined;
    }
    const keys = [env['OMNI_CC_TRACE_ID'], env['OMNI_CC_PTY_ID']].filter((value): value is string => Boolean(value));
    for (const key of keys) {
        const direct = ledger[key];
        if (direct && typeof direct === 'object') return direct;
    }
    if (!keys.length) return undefined;
    for (const record of Object.values(ledger)) {
        if (record && typeof record === 'object' && keys.includes(String(record.pty_id ?? ''))) return record;
    }
    return undefined;
}

function claudeConfigDirs(env: Env): string[] {
    const dirs: string[] = [];
    if (env['CLAUDE_CONFIG_DIR']) dirs.push(env['CLAUDE_CONFIG_DIR']);
    const home = homedir();
    try {
        for (const name of readdirSync(home)) {
            if (name === '.claude' || /^\.claude[-_a-z0-9]*$/i.test(name)) dirs.push(join(home, name));
        }
    } catch { /* 无家目录也不报错 */ }
    return [...new Set(dirs)];
}

/** 把原生 id 定位到磁盘上的 jsonl；找不到返回 undefined（不猜）。 */
export function locateSessionFile(provider: string | undefined, nativeId: string, env: Env = process.env): string | undefined {
    const lower = (provider || '').toLowerCase();
    const wantClaude = !lower || lower.startsWith('claude');
    const wantCodex = !lower || lower.startsWith('codex');
    if (wantClaude) {
        for (const dir of claudeConfigDirs(env)) {
            const projects = join(dir, 'projects');
            if (!existsSync(projects)) continue;
            let names: string[] = [];
            try { names = readdirSync(projects); } catch { continue; }
            for (const name of names) {
                const candidate = join(projects, name, `${nativeId}.jsonl`);
                if (existsSync(candidate)) return candidate;
            }
        }
    }
    if (wantCodex) {
        const sessions = join(env['CODEX_HOME'] || join(homedir(), '.codex'), 'sessions');
        if (existsSync(sessions)) {
            let scanned = 0;
            const listDesc = (dir: string): string[] => {
                try { return readdirSync(dir).sort().reverse(); } catch { return []; }
            };
            for (const year of listDesc(sessions)) {
                for (const month of listDesc(join(sessions, year))) {
                    for (const day of listDesc(join(sessions, year, month))) {
                        const dayDir = join(sessions, year, month, day);
                        scanned += 1;
                        if (scanned > 400) return undefined;
                        try {
                            if (!statSync(dayDir).isDirectory()) continue;
                            const hit = readdirSync(dayDir).find((name) => name.endsWith(`${nativeId}.jsonl`));
                            if (hit) return join(dayDir, hit);
                        } catch { /* 跳过坏目录 */ }
                    }
                }
            }
        }
    }
    return undefined;
}

export function resolveRuntime(env: Env = process.env): WriteRuntime | undefined {
    const provider = providerFromEnv(env);
    if (!provider) return undefined;
    const runtime: WriteRuntime = { provider };
    const model = (env['OMNI_AGENT_MODEL'] || ledgerRecord(env)?.model || '').trim();
    if (model) runtime.model = model;
    return runtime;
}

export function resolveSession(env: Env = process.env): WriteSession | undefined {
    const provider = providerFromEnv(env);
    let nativeId = (env['CLAUDE_CODE_SESSION_ID'] || env['CODEX_THREAD_ID'] || '').trim();
    if (!nativeId) nativeId = String(ledgerRecord(env)?.session_id ?? '').trim();
    if (!nativeId) return undefined;
    const session: WriteSession = { native_id: nativeId };
    const file = locateSessionFile(provider, nativeId, env);
    if (file) session.file = file;
    const trace = (env['OMNI_CC_TRACE_ID'] || '').trim();
    if (trace) session.trace_id = trace;
    const pty = (env['OMNI_CC_PTY_ID'] || '').trim();
    if (pty) session.pty_id = pty;
    return session;
}

export interface ResolveWriteIdentityOptions {
    purpose?: string;
    batch?: string;
    captured_by?: WriteCaptureSource;
    env?: Env;
    at?: Date;
}

/** 当前进程的写入身份。跑在 agent 会话里 = agent；没有任何运行时线索 = system（脚本/服务自己）。 */
export function resolveWriteIdentity(options: ResolveWriteIdentityOptions = {}): WriteIdentity {
    const env = options.env ?? process.env;
    const runtime = resolveRuntime(env);
    const session = resolveSession(env);
    const candidate: WriteIdentity = {
        principal: runtime ? 'agent' : 'system',
        machine: readMachineIdentity(env),
        ...(runtime ? { runtime } : {}),
        ...(session ? { session } : {}),
        captured_by: options.captured_by ?? 'cli',
        at: (options.at ?? new Date()).toISOString(),
    };
    const purpose = (options.purpose ?? env['OMNI_WRITE_PURPOSE'] ?? '').trim();
    if (purpose) candidate.purpose = purpose;
    const batch = (options.batch ?? env['OMNI_WRITE_BATCH'] ?? '').trim();
    if (batch) candidate.batch = batch;
    const normalized = normalizeWriteIdentity(candidate);
    if (!normalized) throw new TypeError('write identity 解析结果不合契约（不应发生）');
    return normalized;
}
