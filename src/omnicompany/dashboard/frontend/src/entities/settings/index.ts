import React, { useEffect, useState } from 'react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import CcInstallCard from './CcInstallCard'
import BossSightControlCard from './BossSightControlCard'
import { usePanels } from '../../stores/panelsStore'

export interface SettingsEntity extends Entity {
  type: 'settings'
}

const SINGLE: SettingsEntity = { type: 'settings' as any, id: 'main', title: '设置 / 系统信息' }

const resolver: EntityResolver<SettingsEntity> = {
  type: 'settings',
  async fetch(id) {
    if (id === 'main') return SINGLE
    throw new Error(`settings: only 'main' available`)
  },
  async list() { return [SINGLE] },
}

interface SystemInfo {
  version: string
  project_root: string
  packages_root: string
  stats: { worker_count: number; package_count: number }
  databases: Record<string, { path: string; exists: boolean; size?: number; error?: string }>
  endpoints: Record<string, string>
}

const S: Record<string, any> = {
  root: { padding: 24, height: '100%', overflow: 'auto', background: '#0f0f0f', color: '#e0e0e0', fontFamily: 'Consolas, Menlo, monospace', fontSize: 14 },
  title: { color: '#90caf9', fontSize: 15, marginBottom: 12 },
  section: { color: '#90caf9', marginTop: 20, marginBottom: 8, fontSize: 14, textTransform: 'uppercase' as const },
  card: { background: 'var(--fp-bg)', border: '1px solid #1f1f1f', borderRadius: 4, padding: 12, marginBottom: 8 },
  row: { display: 'flex', gap: 12, marginBottom: 4 },
  k: { color: '#666', minWidth: 120 },
  v: { color: '#ccc', wordBreak: 'break-all' as const },
  button: { marginLeft: 'auto', padding: '6px 10px', borderRadius: 4, cursor: 'pointer', color: '#90caf9', background: '#1a2a3a', border: '1px solid #2a4a6a' },
  badge: (ok: boolean): React.CSSProperties => ({ display: 'inline-block', padding: '1px 6px', borderRadius: 3, fontSize: 14, color: ok ? '#4caf50' : '#ef5350', background: '#1a1a1a' }),
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

const Editor: React.FC<{ entity: SettingsEntity }> = () => {
  const openTab = usePanels((state) => state.openTab)
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    fetch('/api/system/info')
      .then((r) => r.json())
      .then(setInfo)
      .catch((e) => setError(String(e)))
  }, [])

  if (error) return React.createElement('div', { style: { ...S.root, color: '#ef5350' } }, error)
  if (!info) return React.createElement('div', { style: { ...S.root, color: '#666' } }, 'loading…')

  return React.createElement('div', { style: S.root }, [
    React.createElement('div', { key: 't', style: S.title }, '设置 / 系统信息'),

    React.createElement('div', { key: 'upload-title', style: S.section }, '文件投递'),
    React.createElement('div', { key: 'upload-card', style: S.card }, [
      React.createElement('div', { key: 'upload-row', style: { ...S.row, alignItems: 'center', marginBottom: 0 } }, [
        React.createElement('span', { key: 'upload-copy', style: { display: 'grid', gap: 3 } }, [
          React.createElement('strong', { key: 'upload-name' }, 'Agent 暂存区'),
          React.createElement('span', { key: 'upload-help', style: { color: '#888' } }, '查看上传记录；平时可向任意页面拖入文件，或粘贴剪贴板中的文件。'),
        ]),
        React.createElement('button', {
          key: 'upload-open',
          type: 'button',
          style: S.button,
          'data-testid': 'settings-file-bridge',
          onClick: () => openTab({ type: 'file_bridge', id: 'main' }, 'Agent 暂存区'),
        }, '查看记录'),
      ]),
    ]),

    React.createElement('div', { key: 'boss-title', style: S.section }, 'BOSS SIGHT'),
    React.createElement(BossSightControlCard, { key: 'boss-sight-control' }),

    React.createElement('div', { key: 's1', style: S.section }, '版本 + 路径'),
    React.createElement('div', { key: 'c1', style: S.card }, [
      React.createElement('div', { key: 'v', style: S.row }, [React.createElement('span', { key: 'k', style: S.k }, 'version'), React.createElement('span', { key: 'val', style: S.v }, info.version)]),
      React.createElement('div', { key: 'p', style: S.row }, [React.createElement('span', { key: 'k', style: S.k }, 'project_root'), React.createElement('span', { key: 'val', style: S.v }, info.project_root)]),
      React.createElement('div', { key: 'pk', style: S.row }, [React.createElement('span', { key: 'k', style: S.k }, 'packages_root'), React.createElement('span', { key: 'val', style: S.v }, info.packages_root)]),
    ]),

    React.createElement('div', { key: 's2', style: S.section }, '统计'),
    React.createElement('div', { key: 'c2', style: S.card }, [
      React.createElement('div', { key: 'w', style: S.row }, [React.createElement('span', { key: 'k', style: S.k }, 'worker 数'), React.createElement('span', { key: 'val', style: S.v }, String(info.stats.worker_count))]),
      React.createElement('div', { key: 'p', style: S.row }, [React.createElement('span', { key: 'k', style: S.k }, 'DESIGN.md 数'), React.createElement('span', { key: 'val', style: S.v }, String(info.stats.package_count))]),
    ]),

    React.createElement('div', { key: 's3', style: S.section }, '数据库'),
    ...Object.entries(info.databases).map(([name, db]) =>
      React.createElement('div', { key: `db-${name}`, style: S.card }, [
        React.createElement('div', { key: 'h', style: { ...S.row, marginBottom: 8 } }, [
          React.createElement('span', { key: 'n', style: { color: '#90caf9' } }, name),
          React.createElement('span', { key: 'b', style: S.badge(db.exists) }, db.exists ? 'OK' : '缺'),
          db.exists && db.size !== undefined && React.createElement('span', { key: 's', style: { color: '#888' } }, fmtBytes(db.size)),
        ]),
        React.createElement('div', { key: 'p', style: S.row }, [React.createElement('span', { key: 'k', style: S.k }, 'path'), React.createElement('span', { key: 'val', style: S.v }, db.path || db.error || '')]),
      ])
    ),

    React.createElement('div', { key: 's4', style: S.section }, 'API 端点'),
    React.createElement('div', { key: 'c4', style: S.card },
      Object.entries(info.endpoints).map(([k, v]) =>
        React.createElement('div', { key: `ep-${k}`, style: S.row }, [
          React.createElement('span', { key: 'k', style: S.k }, k),
          React.createElement('span', { key: 'val', style: S.v }, v),
        ])
      )
    ),

    React.createElement('div', { key: 's5', style: S.section }, 'Claude Code 集成'),
    React.createElement(CcInstallCard, { key: 'cc-install' }),
  ])
}

export const settingsRegistration: EntityRegistration<SettingsEntity> = {
  resolver,
  renderer: { type: 'settings' as any, Editor },
  label: '系统信息',
  icon: '◴',
}
