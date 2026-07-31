// settings 实体页 · 蓝图 G 重置(2026-07-19 阶段四第四波;合同=TRIFORM-UX-REDESIGN-V2/demo/MAPPING.md):
//   · 页内「系统信息 / Token 统计」无边框文字钮 → segmented(role=radiogroup/radio,
//     与 dockview 窗口页签形态分层);「BOSS SIGHT」装饰行删除(分组卡 gr-h 已标识身份)。
//   · 硬编码 console 皮(#0f0f0f/#90caf9/Consolas 栈)清零 → scene 格纸底 + 分组厚框纸件卡;
//     内容 max-width 720 居中(消 desktop 1400px 眼动)。
//   · 系统信息各节 → kv 条(mono 大写标签,元数据不泄漏原文);数据库 OK/缺/尺寸 → 状态徽章+mono。
// 数据(/api/system/info)与文案锚点(版本 + 路径 / worker 数 / 数据库 / API 端点 / 系统信息)未动。
import React, { useEffect, useState } from 'react'
import { HardDrive, Settings } from 'lucide-react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { usePanels } from '../../stores/panelsStore'
import CcInstallCard from './CcInstallCard'
import BossSightControlCard from './BossSightControlCard'
import TokenStatsTab from './TokenStatsTab'
import './settings.css'

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

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function SystemInfoView({ info, onOpenFileBridge }: { info: SystemInfo; onOpenFileBridge: () => void }) {
  return (
    <>
      <div className="st-sec">文件投递</div>
      <section className="st-group">
        <div className="st-gr">
          <HardDrive size={17} aria-hidden="true" />
          <span className="st-row-t">
            <span className="t1">Agent 暂存区</span>
            <span className="t2">查看上传记录；平时直接向任意页面拖入文件，或粘贴剪贴板中的文件。</span>
          </span>
          <button
            type="button"
            className="st-btn"
            data-testid="settings-file-bridge"
            onClick={onOpenFileBridge}
          >
            查看记录
          </button>
        </div>
      </section>

      <BossSightControlCard />

      <div className="st-sec">版本 + 路径</div>
      <section className="st-group">
        <div className="st-gr"><span className="st-k">version</span><span className="st-v">{info.version}</span></div>
        <div className="st-gr"><span className="st-k">project_root</span><span className="st-v">{info.project_root}</span></div>
        <div className="st-gr"><span className="st-k">packages_root</span><span className="st-v">{info.packages_root}</span></div>
      </section>

      <div className="st-sec">统计</div>
      <section className="st-group">
        <div className="st-gr"><span className="st-k">worker 数</span><span className="st-v">{info.stats.worker_count}</span></div>
        <div className="st-gr"><span className="st-k">DESIGN.md 数</span><span className="st-v">{info.stats.package_count}</span></div>
      </section>

      <div className="st-sec">数据库</div>
      <section className="st-group">
        {Object.entries(info.databases).map(([name, db]) => (
          <div key={name} className="st-gr st-db">
            <div className="st-db-h">
              <span className="st-db-n">{name}</span>
              <span className={`v2-status ${db.exists ? 'st-ok' : 'st-err'}`}>
                <i className="led" aria-hidden="true" />{db.exists ? 'OK' : '缺'}
              </span>
              {db.exists && db.size !== undefined && <span className="st-db-s">{fmtBytes(db.size)}</span>}
            </div>
            <div className="st-db-p">{db.path || db.error || ''}</div>
          </div>
        ))}
      </section>

      <div className="st-sec">API 端点</div>
      <section className="st-group">
        {Object.entries(info.endpoints).map(([k, v]) => (
          <div key={k} className="st-gr"><span className="st-k">{k}</span><span className="st-v">{v}</span></div>
        ))}
      </section>

      <CcInstallCard />
    </>
  )
}

const TABS: Array<{ key: 'system' | 'tokens'; label: string }> = [
  { key: 'system', label: '系统信息' },
  { key: 'tokens', label: 'Token 统计' },
]

const Editor: React.FC<{ entity: SettingsEntity }> = () => {
  const [tab, setTab] = useState<'system' | 'tokens'>('system')
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const openTab = usePanels((state) => state.openTab)
  useEffect(() => {
    fetch('/api/system/info')
      .then((r) => r.json())
      .then(setInfo)
      .catch((e) => setError(String(e)))
  }, [])

  return (
    <div className="st-page" data-testid="settings-page">
      <div className="st-tools">
        <span className="v2-seg" role="radiogroup" aria-label="设置视图" data-testid="settings-view-tabs">
          {TABS.map((t) => {
            const on = tab === t.key
            return (
              <button
                key={t.key}
                type="button"
                role="radio"
                aria-checked={on}
                className={`seg-i${on ? ' on' : ''}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            )
          })}
        </span>
      </div>
      <div className="st-wrap">
        {tab === 'tokens' ? (
          <TokenStatsTab />
        ) : error ? (
          <div className="st-err">{error}</div>
        ) : !info ? (
          <div className="st-muted">loading…</div>
        ) : (
          <SystemInfoView
            info={info}
            onOpenFileBridge={() => openTab({ type: 'file_bridge', id: 'main' }, 'Agent 暂存区')}
          />
        )}
      </div>
    </div>
  )
}

export const settingsRegistration: EntityRegistration<SettingsEntity> = {
  resolver,
  renderer: { type: 'settings' as any, Editor },
  label: '系统信息',
  icon: React.createElement(Settings, { size: 14 }),
}
