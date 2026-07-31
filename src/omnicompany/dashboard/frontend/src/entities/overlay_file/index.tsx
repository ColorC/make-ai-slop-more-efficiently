import React, { Suspense, useEffect, useMemo, useState } from 'react'
import {
  Check,
  Copy,
  File,
  FileCode2,
  FileQuestion,
  Film,
  Folder,
  HardDrive,
  Image as ImageIcon,
  Music,
  RefreshCw,
  Search,
} from 'lucide-react'
import type { Entity } from '../types'
import type { EntityRegistration, EntityResolver } from '../registry'
import {
  inspectOverlayFile,
  overlayFileContentUrl,
  type OverlayFileDetail,
  type OverlayFileEntry,
} from '../../api/overlayClient'
import { copyText } from '../../lib/copyText'
import {
  openOverlayFileInDashboard,
  type OverlayFileOpenTarget,
} from '../../lib/overlayFileNavigation'
import WebFileContextMenu, {
  type WebFileMenuState,
} from '../../shared/view/ui/WebFileContextMenu'
import './overlayFile.css'

const MonacoEditor = React.lazy(() =>
  import('@monaco-editor/react').then((module) => ({ default: module.default })),
)

export interface OverlayFileEntity extends Entity {
  type: 'overlay_file'
  detail: OverlayFileDetail
}

function detectLanguage(path: string): string {
  const extension = path.split('.').pop()?.toLowerCase() || ''
  const languages: Record<string, string> = {
    bat: 'bat', c: 'c', cc: 'cpp', cmd: 'bat', cpp: 'cpp', cs: 'csharp',
    css: 'css', csv: 'plaintext', go: 'go', h: 'cpp', hpp: 'cpp',
    html: 'html', ini: 'ini', java: 'java', js: 'javascript', json: 'json',
    jsx: 'javascript', log: 'plaintext', lua: 'lua', md: 'markdown', mjs: 'javascript',
    ps1: 'powershell', py: 'python', rs: 'rust', scss: 'scss', sh: 'shell',
    sql: 'sql', svg: 'xml', toml: 'ini', ts: 'typescript', tsx: 'typescript',
    txt: 'plaintext', uxml: 'xml', vue: 'html', xml: 'xml', yaml: 'yaml', yml: 'yaml',
  }
  return languages[extension] || 'plaintext'
}

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

function iconFor(entry: Pick<OverlayFileEntry, 'kind' | 'mime'>, size = 16) {
  if (entry.kind === 'folder') return <Folder size={size} />
  if (entry.mime.startsWith('image/')) return <ImageIcon size={size} />
  if (entry.mime.startsWith('audio/')) return <Music size={size} />
  if (entry.mime.startsWith('video/')) return <Film size={size} />
  if (entry.mime.startsWith('text/') || /json|javascript|xml/.test(entry.mime)) return <FileCode2 size={size} />
  return <File size={size} />
}

function toOpenTarget(entry: OverlayFileEntry): OverlayFileOpenTarget {
  return {
    name: entry.name,
    path: entry.path,
    kind: entry.kind,
    open_token: entry.open_token,
  }
}

function DirectoryView({ detail }: { detail: OverlayFileDetail }) {
  const [filter, setFilter] = useState('')
  const [menu, setMenu] = useState<WebFileMenuState | null>(null)
  const items = useMemo(() => {
    const query = filter.trim().toLowerCase()
    const all = detail.items || []
    if (!query) return all
    return all.filter((entry) =>
      entry.name.toLowerCase().includes(query) || entry.path.toLowerCase().includes(query))
  }, [detail.items, filter])

  return (
    <div className="ov-file-directory" data-testid="overlay-directory-view">
      <div className="ov-file-directory-tools">
        <label className="ov-file-filter">
          <Search size={14} aria-hidden />
          <input
            aria-label="筛选当前目录"
            placeholder="筛选当前目录…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </label>
        <span>{items.length}{detail.truncated ? '+' : ''} 项</span>
      </div>
      <div className="ov-file-list" role="list">
        {items.map((entry) => {
          const target = toOpenTarget(entry)
          return (
            <button
              type="button"
              role="listitem"
              className="ov-file-row"
              key={entry.open_token}
              title={`${entry.name}\n${entry.path}`}
              onClick={() => openOverlayFileInDashboard(target)}
              onAuxClick={(event) => {
                if (event.button !== 1) return
                event.preventDefault()
                openOverlayFileInDashboard(target, true)
              }}
              onContextMenu={(event) => {
                event.preventDefault()
                setMenu({ x: event.clientX, y: event.clientY, target })
              }}
            >
              <span className="ov-file-row-icon">{iconFor(entry)}</span>
              <span className="ov-file-row-name">{entry.name}</span>
              <span className="ov-file-row-kind">{entry.kind === 'folder' ? '目录' : entry.mime}</span>
              <span className="ov-file-row-size">{entry.kind === 'folder' ? '' : formatSize(entry.size)}</span>
              <span className="ov-file-row-time">{entry.modified_at.replace('T', ' ')}</span>
            </button>
          )
        })}
        {items.length === 0 && (
          <div className="ov-file-empty">{filter ? '当前目录没有匹配项' : '空目录'}</div>
        )}
        {detail.truncated && (
          <div className="ov-file-truncated">目录条目过多，仅显示前 500 项</div>
        )}
      </div>
      <WebFileContextMenu
        menu={menu}
        onClose={() => setMenu(null)}
        onOpen={openOverlayFileInDashboard}
      />
    </div>
  )
}

function FilePreview({ detail }: { detail: OverlayFileDetail }) {
  const streamUrl = overlayFileContentUrl(detail.open_token)
  if (detail.preview === 'text') {
    return (
      <div className="ov-file-code" data-testid="overlay-text-preview">
        {detail.truncated && (
          <div className="ov-file-truncated">文件较大，网页中只显示前 2 MB</div>
        )}
        <div className="ov-file-code-editor">
          <Suspense fallback={<div className="ov-file-loading">正在加载网页编辑器…</div>}>
            <MonacoEditor
              value={detail.content || ''}
              language={detectLanguage(detail.path)}
              theme="vs-dark"
              options={{
                readOnly: true,
                domReadOnly: false,
                automaticLayout: true,
                minimap: { enabled: false },
                fontSize: 13,
                lineHeight: 20,
                scrollBeyondLastLine: false,
                wordWrap: detail.mime === 'text/markdown' ? 'on' : 'off',
              }}
            />
          </Suspense>
        </div>
      </div>
    )
  }
  if (detail.preview === 'image') {
    return (
      <div className="ov-file-media ov-file-image" data-testid="overlay-image-preview">
        <img src={streamUrl} alt={detail.name} />
      </div>
    )
  }
  if (detail.preview === 'pdf') {
    return (
      <iframe
        className="ov-file-pdf"
        title={detail.name}
        src={streamUrl}
        data-testid="overlay-pdf-preview"
      />
    )
  }
  if (detail.preview === 'audio') {
    return (
      <div className="ov-file-media">
        <Music size={42} />
        <audio controls src={streamUrl} data-testid="overlay-audio-preview" />
      </div>
    )
  }
  if (detail.preview === 'video') {
    return (
      <div className="ov-file-media">
        <video controls src={streamUrl} data-testid="overlay-video-preview" />
      </div>
    )
  }
  return (
    <div className="ov-file-empty ov-file-unpreviewable" data-testid="overlay-unpreviewable">
      <FileQuestion size={48} />
      <strong>这个文件暂时不能在网页中预览</strong>
      <span>{detail.mime || '未知文件类型'} · {formatSize(detail.size)}</span>
      <span>Dashboard 不会调用本机应用打开它。</span>
    </div>
  )
}

const Editor: React.FC<{ entity: OverlayFileEntity }> = ({ entity }) => {
  const [detail, setDetail] = useState(entity.detail)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    setDetail(entity.detail)
    setError('')
  }, [entity.id, entity.detail])

  const refresh = async () => {
    setRefreshing(true)
    setError('')
    try {
      setDetail(await inspectOverlayFile(entity.id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setRefreshing(false)
    }
  }

  const copyPath = async () => {
    const ok = await copyText(detail.path)
    setCopied(ok)
    if (ok) window.setTimeout(() => setCopied(false), 900)
  }

  return (
    <section className="ov-file-page" data-testid="overlay-file-page">
      <header className="ov-file-header">
        <span className="ov-file-main-icon">
          {detail.kind === 'folder' ? <HardDrive size={18} /> : iconFor(detail, 18)}
        </span>
        <div className="ov-file-identity">
          <strong title={detail.name}>{detail.name}</strong>
          <span title={detail.path}>{detail.path}</span>
        </div>
        <span className="ov-file-meta">
          {detail.kind === 'folder' ? '目录' : `${detail.mime} · ${formatSize(detail.size)}`}
          {detail.modified_at ? ` · ${detail.modified_at.replace('T', ' ')}` : ''}
        </span>
        <button
          type="button"
          className="ov-file-tool"
          title="复制文件路径"
          onClick={() => { void copyPath() }}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {copied ? '已复制' : '复制路径'}
        </button>
        <button
          type="button"
          className="ov-file-tool"
          title="刷新网页内容"
          disabled={refreshing}
          onClick={() => { void refresh() }}
        >
          <RefreshCw size={14} className={refreshing ? 'is-spinning' : undefined} />
          刷新
        </button>
      </header>
      {error && <div className="ov-file-error">{error}</div>}
      <div className="ov-file-body">
        {detail.preview === 'directory'
          ? <DirectoryView detail={detail} />
          : <FilePreview detail={detail} />}
      </div>
    </section>
  )
}

const resolver: EntityResolver<OverlayFileEntity> = {
  type: 'overlay_file',
  async fetch(id) {
    const detail = await inspectOverlayFile(id)
    return {
      type: 'overlay_file',
      id,
      title: detail.name,
      tags: ['overlay', detail.kind],
      meta: { path: detail.path, mime: detail.mime },
      detail,
    }
  },
  async list() {
    // 文件实体只来自实时 Overlay 搜索/目录浏览，不把短期签名令牌塞进实体总表。
    return []
  },
}

export const overlayFileRegistration: EntityRegistration<OverlayFileEntity> = {
  resolver,
  renderer: { type: 'overlay_file', Editor },
  label: '本机文件',
  icon: <HardDrive size={14} />,
}

