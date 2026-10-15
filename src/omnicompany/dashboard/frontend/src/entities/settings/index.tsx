import React, { useEffect, useState } from 'react'
import { HardDrive, LayoutPanelTop, Settings, TerminalSquare, Type } from 'lucide-react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import { usePanels } from '../../stores/panelsStore'
import { TAB_TITLE_LIMITS, useUiSettings, type TabLayout, type TerminalRenderer } from '../../stores/uiSettingsStore'
import ClientDownloadsTab from './ClientDownloadsTab'
import ContentSharesTab from './ContentSharesTab'
import SecurityNodeTab from './SecurityNodeTab'
import SubscriptionsTab from './SubscriptionsTab'
import './settings.css'

export interface SettingsEntity extends Entity {
  type: 'settings'
}

const SINGLE: SettingsEntity = { type: 'settings' as any, id: 'main', title: '设置' }

const resolver: EntityResolver<SettingsEntity> = {
  type: 'settings',
  async fetch(id) {
    if (id === 'main') return SINGLE
    if (id === 'subscriptions') return { type: 'settings' as const, id: 'subscriptions', title: '订阅配额' }
    throw new Error(`settings: unknown id ${id}`)
  },
  async list() { return [SINGLE] },
}

interface SystemInfo {
  version: string
}

function SystemTab({ onOpenFileBridge }: { onOpenFileBridge: () => void }) {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetch('/api/system/info')
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<SystemInfo>
      })
      .then((payload) => { if (alive) setInfo(payload) })
      .catch((reason) => { if (alive) setError(String(reason)) })
    return () => { alive = false }
  }, [])

  return (
    <>
      <div className="st-sec">本机入口</div>
      <section className="st-group">
        <div className="st-gr">
          <HardDrive size={17} aria-hidden="true" />
          <span className="st-row-t">
            <span className="t1">Agent 暂存区</span>
            <span className="t2">查看上传记录；也可以向任意页面拖入或粘贴文件。</span>
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

      <div className="st-sec">当前版本</div>
      <section className="st-group">
        <div className="st-gr">
          <span className="st-k">Dashboard</span>
          {info ? (
            <span className="st-v">{info.version}</span>
          ) : error ? (
            <span className="st-err">暂时无法读取版本</span>
          ) : (
            <span className="st-muted">正在读取…</span>
          )}
        </div>
      </section>
    </>
  )
}

function InterfaceTab() {
  const tabLayout = useUiSettings((state) => state.tabLayout)
  const tabTitleMaxLength = useUiSettings((state) => state.tabTitleMaxLength)
  const setTabLayout = useUiSettings((state) => state.setTabLayout)
  const setTabTitleMaxLength = useUiSettings((state) => state.setTabTitleMaxLength)
  const terminalRenderer = useUiSettings((state) => state.terminalRenderer)
  const setTerminalRenderer = useUiSettings((state) => state.setTerminalRenderer)
  const layouts: Array<{ value: TabLayout; label: string }> = [
    { value: 'single-row', label: '单行' },
    { value: 'stacked', label: '堆叠' },
  ]
  // gpu = WebGL(快, 但画布不透明, 玻璃只能做在窗框); glass = DOM 渲染器(真透, 更吃 CPU)
  const renderers: Array<{ value: TerminalRenderer; label: string }> = [
    { value: 'gpu', label: '高性能' },
    { value: 'glass', label: '玻璃' },
  ]

  return (
    <>
      <div className="st-sec">页签</div>
      <section className="st-group">
        <div className="st-gr">
          <LayoutPanelTop size={17} aria-hidden="true" />
          <span className="st-row-t">
            <span className="t1">页签排列</span>
            <span className="t2">选择单行横向滚动，或允许页签自动换行堆叠。</span>
          </span>
          <span className="v2-seg" role="radiogroup" aria-label="页签排列">
            {layouts.map((item) => (
              <button
                key={item.value}
                type="button"
                role="radio"
                aria-checked={tabLayout === item.value}
                className={`seg-i${tabLayout === item.value ? ' on' : ''}`}
                onClick={() => setTabLayout(item.value)}
              >
                {item.label}
              </button>
            ))}
          </span>
        </div>
        <div className="st-gr st-gr-separated">
          <Type size={17} aria-hidden="true" />
          <span className="st-row-t">
            <span className="t1">标题最大长度</span>
            <span className="t2">超过长度后以省略号显示，完整标题仍会保留。</span>
          </span>
          <label className="st-number-field">
            <input
              type="number"
              min={TAB_TITLE_LIMITS.min}
              max={TAB_TITLE_LIMITS.max}
              step={1}
              aria-label="页签标题最大长度"
              value={tabTitleMaxLength}
              onChange={(event) => setTabTitleMaxLength(Number(event.target.value))}
            />
            <span>字</span>
          </label>
        </div>
      </section>

      <div className="st-sec">终端</div>
      <section className="st-group">
        <div className="st-gr">
          <TerminalSquare size={17} aria-hidden="true" />
          <span className="st-row-t">
            <span className="t1">终端底</span>
            <span className="t2">
              玻璃能让蓝图格纸从文字底下透上来，代价是退回 CPU 渲染，大量输出滚动时更吃性能。
              切换后需重开终端页签才生效。
            </span>
          </span>
          <span className="v2-seg" role="radiogroup" aria-label="终端底">
            {renderers.map((item) => (
              <button
                key={item.value}
                type="button"
                role="radio"
                aria-checked={terminalRenderer === item.value}
                className={`seg-i${terminalRenderer === item.value ? ' on' : ''}`}
                onClick={() => setTerminalRenderer(item.value)}
              >
                {item.label}
              </button>
            ))}
          </span>
        </div>
      </section>
    </>
  )
}

type SettingsTab = 'downloads' | 'subscriptions' | 'content-shares' | 'security' | 'interface' | 'system'

const TABS: Array<{ key: SettingsTab; label: string }> = [
  { key: 'downloads', label: '客户端下载' },
  { key: 'subscriptions', label: '订阅配额' },
  { key: 'content-shares', label: '内容分享' },
  { key: 'security', label: 'Remote Node' },
  { key: 'interface', label: '界面' },
  { key: 'system', label: '系统' },
]

const Editor: React.FC<{ entity: SettingsEntity }> = ({ entity }) => {
  const [tab, setTab] = useState<SettingsTab>(entity.id === 'subscriptions' ? 'subscriptions' : 'downloads')
  const openTab = usePanels((state) => state.openTab)

  return (
    <div className="st-page" data-testid="settings-page">
      <div className="st-tools">
        <span className="v2-seg" role="radiogroup" aria-label="设置视图" data-testid="settings-view-tabs">
          {TABS.map((item) => {
            const selected = tab === item.key
            return (
              <button
                key={item.key}
                type="button"
                role="radio"
                aria-checked={selected}
                className={`seg-i${selected ? ' on' : ''}`}
                onClick={() => setTab(item.key)}
              >
                {item.label}
              </button>
            )
          })}
        </span>
      </div>
      <div className="st-wrap">
        {tab === 'downloads' && <ClientDownloadsTab onOpenRemoteNode={() => setTab('security')} />}
        {tab === 'subscriptions' && <SubscriptionsTab />}
        {tab === 'content-shares' && <ContentSharesTab />}
        {tab === 'security' && <SecurityNodeTab />}
        {tab === 'interface' && <InterfaceTab />}
        {tab === 'system' && (
          <SystemTab onOpenFileBridge={() => openTab({ type: 'file_bridge', id: 'main' }, 'Agent 暂存区')} />
        )}
      </div>
    </div>
  )
}

export const settingsRegistration: EntityRegistration<SettingsEntity> = {
  resolver,
  renderer: { type: 'settings' as any, Editor },
  label: '设置',
  icon: React.createElement(Settings, { size: 14 }),
}
