import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import type { LLMProvider, Project } from '../../types/app';
import StandaloneShell from '../standalone-shell/view/StandaloneShell';

const PROVIDERS: Array<{ id: LLMProvider; label: string }> = [
  { id: 'claude', label: 'Claude Code' },
  { id: 'codex', label: 'Codex' },
  { id: 'opencode', label: 'OpenCode' },
  { id: 'kimi', label: 'Kimi CLI' },
];

const DEFAULT_WORKSPACE = '';

export default function CliWorkspace() {
  const [params, setParams] = useSearchParams();
  const requestedProvider = params.get('provider') as LLMProvider | null;
  const [provider, setProvider] = useState<LLMProvider>(
    PROVIDERS.some((item) => item.id === requestedProvider) ? requestedProvider! : 'claude',
  );
  const [workspace, setWorkspace] = useState(params.get('cwd') || DEFAULT_WORKSPACE);
  const [terminalKey, setTerminalKey] = useState(0);

  const project = useMemo<Project>(() => ({
    projectId: `cli:${workspace}`,
    displayName: workspace.split(/[\\/]/).filter(Boolean).pop() || '工作区',
    fullPath: workspace,
    path: workspace,
  }), [workspace]);
  const workspaceName = project.displayName;

  useEffect(() => {
    localStorage.setItem('selected-provider', provider);
    setParams({ provider, cwd: workspace }, { replace: true });
  }, [provider, setParams, workspace]);

  const restart = (nextProvider = provider) => {
    localStorage.setItem('selected-provider', nextProvider);
    setProvider(nextProvider);
    setTerminalKey((value) => value + 1);
  };

  return (
    <main className="flex h-dvh min-h-0 flex-col bg-background text-foreground">
      <header className="flex flex-wrap items-center gap-2 border-b border-border/60 bg-card/95 px-2 py-2">
        <strong className="mr-auto text-sm">远程 CLI 工作台</strong>
        {PROVIDERS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`rounded-full px-3 py-1.5 text-xs ${provider === item.id ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'}`}
            onClick={() => restart(item.id)}
          >
            <span className="block font-medium">{item.label}</span>
            <span className="hidden text-[10px] opacity-75 sm:block">{item.label} 编程 · {workspaceName}</span>
          </button>
        ))}
      </header>
      <div className="flex gap-2 border-b border-border/50 p-2">
        <input
          aria-label="工作目录"
          className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 text-xs"
          value={workspace}
          onChange={(event) => setWorkspace(event.target.value)}
        />
        <button className="rounded-md bg-secondary px-3 text-xs" type="button" onClick={() => restart()}>
          重新连接
        </button>
      </div>
      <section className="min-h-0 flex-1">
        <StandaloneShell
          key={`${provider}:${terminalKey}`}
          project={project}
          title={`${PROVIDERS.find((item) => item.id === provider)?.label || provider} · ${project.displayName}`}
          showHeader={false}
          autoConnect
        />
      </section>
    </main>
  );
}
