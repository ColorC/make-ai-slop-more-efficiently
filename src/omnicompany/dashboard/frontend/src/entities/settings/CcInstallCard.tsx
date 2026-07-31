import React, { useEffect, useState } from 'react'
import { ccApi, type AgentIntegrationProvider } from '../../api/ccClient'
import KebabMenu, { type KebabItem } from '../../shared/view/ui/KebabMenu'
import { RefreshCw } from 'lucide-react'

type Scope = 'project' | 'user'

interface Status {
  provider: AgentIntegrationProvider
  settings_path: string
  installed: boolean
  mcp_command?: string | null
  hook_events?: string[]
  requires_trust?: boolean
  trust_command?: string
}

const glassCard: React.CSSProperties = {
  background: 'var(--fp-glass)',
  backdropFilter: 'var(--fp-blur)',
  WebkitBackdropFilter: 'var(--fp-blur)',
  border: '1px solid var(--fp-border)',
  borderRadius: 11,
  boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)',
}

const S: Record<string, any> = {
  // root 透明: 吃 body 全局冷渐变。内容直接从分组玻璃卡开始(无重复标题头, index 节标题已标识)。
  root: { background: 'transparent', color: 'var(--fp-text)' },
  group: { ...glassCard, padding: 14, display: 'grid', gap: 12 },
  head: { display: 'flex', alignItems: 'center', gap: 10 },
  groupTitle: { color: 'var(--fp-text)', fontSize: 15, fontWeight: 650, letterSpacing: '-0.01em' },
  pill: (ok: boolean): React.CSSProperties => ({
    display: 'inline-block', padding: '1px 8px', borderRadius: 999, fontSize: 12, fontWeight: 600,
    color: ok ? 'var(--fp-ok)' : 'var(--fp-text-3)',
    background: ok ? 'color-mix(in srgb, var(--fp-ok) 14%, transparent)' : 'var(--fp-surface)',
    border: `1px solid ${ok ? 'color-mix(in srgb, var(--fp-ok) 38%, transparent)' : 'var(--fp-border)'}`,
  }),
  kv: { display: 'grid', gap: 6 },
  row: { display: 'flex', gap: 12, alignItems: 'baseline' as const },
  k: { color: 'var(--fp-text-3)', minWidth: 112, fontSize: 13, fontWeight: 600, flexShrink: 0 },
  v: { color: 'var(--fp-text-2)', wordBreak: 'break-all' as const, fontSize: 13, fontFamily: 'var(--fp-font-mono)' },
  hookList: { color: 'var(--fp-violet)', fontSize: 13, fontFamily: 'var(--fp-font-mono)', wordBreak: 'break-all' as const },
  scopeSel: {
    background: 'var(--fp-card)', color: 'var(--fp-text)', border: '1px solid var(--fp-border)',
    padding: '4px 8px', borderRadius: 6, fontSize: 14, fontFamily: 'var(--fp-font-mono)',
  },
  controls: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' as const },
  primary: {
    padding: '8px 14px', borderRadius: 6, cursor: 'pointer', fontSize: 14, fontWeight: 600,
    background: 'var(--fp-accent)', color: 'var(--fp-accent-fg)', border: '1px solid var(--fp-accent)', whiteSpace: 'nowrap' as const,
  },
  danger: {
    padding: '8px 12px', borderRadius: 6, cursor: 'pointer', fontSize: 14,
    background: 'color-mix(in srgb, var(--fp-err) 14%, transparent)', color: 'var(--fp-err)',
    border: '1px solid color-mix(in srgb, var(--fp-err) 40%, transparent)', whiteSpace: 'nowrap' as const,
  },
  msg: (ok: boolean): React.CSSProperties => ({ color: ok ? 'var(--fp-ok)' : 'var(--fp-err)', fontSize: 13 }),
  cli: { color: 'var(--fp-text-3)', fontSize: 13, lineHeight: 1.6, fontFamily: 'var(--fp-font-mono)' },
  cliCmd: { color: 'var(--fp-link)', background: 'var(--fp-card)', padding: '2px 6px', borderRadius: 4 },
}

export default function CcInstallCard() {
  const [scope, setScope] = useState<Scope>('project')
  const [provider, setProvider] = useState<AgentIntegrationProvider>('claude_code')
  const [status, setStatus] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const load = (s: Scope, p: AgentIntegrationProvider) => {
    setStatus(null)
    ccApi.installStatus(s, p).then(setStatus).catch((e) => setMsg({ ok: false, text: String(e) }))
  }
  useEffect(() => { load(scope, provider) }, [scope, provider])

  const onInstall = async () => {
    setBusy(true); setMsg(null)
    try {
      const r = await ccApi.install(scope, provider)
      setMsg({ ok: true, text: `已写入 ${r.settings_path}${r.backup ? ` (备份: ${r.backup.split(/[\\/]/).pop()})` : ''}` })
      load(scope, provider)
    } catch (e) {
      setMsg({ ok: false, text: String(e) })
    } finally { setBusy(false) }
  }

  const onUninstall = async () => {
    setBusy(true); setMsg(null)
    try {
      const r = await ccApi.uninstall(scope, provider)
      setMsg({ ok: true, text: r.removed ? `已移除 omnicompany 入口 (备份: ${(r.backup || '').split(/[\\/]/).pop()})` : (r.note || '无变化') })
      load(scope, provider)
    } catch (e) {
      setMsg({ ok: false, text: String(e) })
    } finally { setBusy(false) }
  }

  return (
    <div style={S.root} data-cc-install-card>
      <section style={S.group}>
        <div style={S.head}>
          <span style={S.groupTitle}>原生 Agent 集成</span>
          {status && <span style={S.pill(status.installed)} data-cc-install-pill>{status.installed ? '已装' : '未装'}</span>}
        </div>

        <div style={S.kv}>
          <div style={S.row}>
            <span style={S.k}>provider</span>
            <select
              style={S.scopeSel} value={provider} data-cc-provider-select
              onChange={(e) => setProvider(e.target.value as AgentIntegrationProvider)}
            >
              <option value="claude_code">Claude Code</option>
              <option value="codex">Codex</option>
            </select>
          </div>
          <div style={S.row}>
            <span style={S.k}>scope</span>
            <select
              style={S.scopeSel} value={scope} data-cc-scope-select
              onChange={(e) => setScope(e.target.value as Scope)}
            >
              <option value="project">project (仓库级, 推荐)</option>
              <option value="user">user (用户全局)</option>
            </select>
          </div>
          {status && <>
            <div style={S.row}><span style={S.k}>{provider === 'codex' ? 'hooks.json' : 'settings.json'}</span><span style={S.v}>{status.settings_path}</span></div>
            {status.mcp_command && (
              <div style={S.row}><span style={S.k}>MCP server</span><span style={S.v}>{status.mcp_command} -m omnicompany.dashboard.ccdaemon.mcp_server</span></div>
            )}
            {status.hook_events && status.hook_events.length > 0 && (
              <div style={S.row}><span style={S.k}>已挂 hook 事件</span><span style={S.hookList}>{status.hook_events.join(' · ')}</span></div>
            )}
            {provider === 'codex' && scope === 'project' && status.requires_trust && (
              <div style={S.row}>
                <span style={S.k}>项目信任</span>
                <span style={S.v}>新开 Codex 会话后用 {status.trust_command || '/hooks'} 审阅并信任项目 hooks</span>
              </div>
            )}
          </>}
        </div>

        {/* 主操作 = 安装(primary); 移除是低频/危险二级(danger 弱底); 刷新进 ⋯。 */}
        <div style={S.controls}>
          <button data-cc-install style={S.primary} onClick={onInstall} disabled={busy}>
            {status?.installed ? '重装 / 更新' : `安装到 ${provider === 'codex' ? 'hooks.json' : 'settings.json'}`}
          </button>
          <button data-cc-uninstall style={S.danger} onClick={onUninstall} disabled={busy || !status?.installed}>
            移除
          </button>
          <KebabMenu testid="cc-install-actions" items={[
            { label: '刷新现状', icon: <RefreshCw size={15} />, testid: 'cc-install-refresh', disabled: busy, onClick: () => load(scope, provider) },
          ] as KebabItem[]} />
        </div>

        {msg && <div style={S.msg(msg.ok)}>{msg.text}</div>}

        <div style={S.cli}>
          等价命令行 (CI / 远程 ssh 用): <span style={S.cliCmd}>omni cc install --provider {provider} --scope {scope}</span>
          {' '}/ <span style={S.cliCmd}>omni cc status --provider {provider} --scope {scope}</span>
          {' '}/ <span style={S.cliCmd}>omni cc uninstall --provider {provider} --scope {scope}</span>
          <br/>
          装完后，在 omnicompany 仓库下新开 <span style={S.cliCmd}>{provider === 'codex' ? 'codex' : 'claude'}</span> 会话，Omnicompany hooks 自动激活。
        </div>
      </section>
    </div>
  )
}
