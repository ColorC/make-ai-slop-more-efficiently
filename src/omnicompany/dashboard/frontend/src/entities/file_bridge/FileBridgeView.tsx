import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowUp,
  Check,
  ChevronRight,
  Copy,
  Download,
  File,
  FileCode2,
  FileQuestion,
  Folder,
  HardDrive,
  Image as ImageIcon,
  Music,
  RefreshCw,
  UploadCloud,
  Video,
} from 'lucide-react'
import {
  fileBridgeApi,
  fileBridgeContentUrl,
  type FileBridgeEntry,
  type FileBridgeHistory,
  type FileBridgeListing,
  type FileBridgeRoot,
  type FileBridgeUploadResult,
} from '../../api/fileBridgeClient'
import { copyText } from '../../lib/copyText'
import { FILE_BRIDGE_UPLOAD_EVENT } from './GlobalFileDrop'
import './fileBridge.css'

function formatSize(value: number | null): string {
  if (value == null) return ''
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(1)} GB`
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value.replace('T', ' ')
    : date.toLocaleString(undefined, {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
}

function iconFor(entry: FileBridgeEntry) {
  if (entry.kind === 'folder') return <Folder size={16} />
  if (entry.preview === 'image') return <ImageIcon size={16} />
  if (entry.preview === 'audio') return <Music size={16} />
  if (entry.preview === 'video') return <Video size={16} />
  if (entry.preview === 'text') return <FileCode2 size={16} />
  return <File size={16} />
}

function parentPath(path: string): string {
  const parts = path.split('/').filter(Boolean)
  parts.pop()
  return parts.join('/')
}

function Preview({ detail }: { detail: FileBridgeEntry }) {
  const token = detail.content_token
  const inlineUrl = token ? fileBridgeContentUrl(token, true) : ''
  if (detail.preview === 'text') {
    return (
      <div className="fb-text-preview" data-testid="file-bridge-text-preview">
        {detail.truncated && <div className="fb-preview-note">网页仅显示前 2 MB；下载仍是完整文件。</div>}
        <pre>{detail.content || ''}</pre>
      </div>
    )
  }
  if (detail.preview === 'image' && token) {
    return <div className="fb-media-preview"><img src={inlineUrl} alt={detail.name} /></div>
  }
  if (detail.preview === 'pdf' && token) {
    return <iframe className="fb-pdf-preview" src={inlineUrl} title={detail.name} />
  }
  if (detail.preview === 'audio' && token) {
    return <div className="fb-media-preview"><audio controls src={inlineUrl} /></div>
  }
  if (detail.preview === 'video' && token) {
    return <div className="fb-media-preview"><video controls src={inlineUrl} /></div>
  }
  return (
    <div className="fb-empty fb-preview-empty">
      <FileQuestion size={28} />
      <strong>网页暂不预览这种文件</strong>
      <span>{detail.mime} · {formatSize(detail.size)}</span>
      <span>仍可复制远端路径或下载到当前设备。</span>
    </div>
  )
}

export default function FileBridgeView() {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const requestRef = useRef(0)
  const [roots, setRoots] = useState<FileBridgeRoot[]>([])
  const [rootId, setRootId] = useState('')
  const [listing, setListing] = useState<FileBridgeListing | null>(null)
  const [selected, setSelected] = useState<FileBridgeEntry | null>(null)
  const [uploads, setUploads] = useState<FileBridgeUploadResult | null>(null)
  const [history, setHistory] = useState<FileBridgeHistory | null>(null)
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [copiedPath, setCopiedPath] = useState('')

  const activeRoot = useMemo(
    () => roots.find((root) => root.id === rootId) || null,
    [rootId, roots],
  )

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await fileBridgeApi.history(30))
    } catch {
      // Browsing/upload remains usable if the optional ledger read fails.
    }
  }, [])

  const loadDirectory = useCallback(async (nextRootId: string, path = '') => {
    const request = ++requestRef.current
    setLoading(true)
    setError('')
    setSelected(null)
    try {
      const next = await fileBridgeApi.browse(nextRootId, path)
      if (request !== requestRef.current) return
      setListing(next)
      setRootId(nextRootId)
    } catch (reason) {
      if (request !== requestRef.current) return
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (request === requestRef.current) setLoading(false)
    }
  }, [])

  const selectFile = useCallback(async (entry: FileBridgeEntry, sourceRootId = rootId) => {
    if (entry.kind === 'folder') {
      await loadDirectory(sourceRootId, entry.relative_path)
      return
    }
    setError('')
    setSelected(entry)
    try {
      setSelected(await fileBridgeApi.inspect(sourceRootId, entry.relative_path))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }, [loadDirectory, rootId])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    fileBridgeApi.roots(controller.signal)
      .then((items) => {
        setRoots(items)
        const preferred = items.find((item) => item.id === 'staging') || items[0]
        if (!preferred) {
          setError('当前没有可用的文件桥根目录')
          setLoading(false)
          return
        }
        void loadDirectory(preferred.id)
      })
      .catch((reason) => {
        if (controller.signal.aborted) return
        setError(reason instanceof Error ? reason.message : String(reason))
        setLoading(false)
      })
    void loadHistory()
    return () => controller.abort()
  }, [loadDirectory, loadHistory])

  useEffect(() => {
    const onUploaded = (event: Event) => {
      const result = (event as CustomEvent<FileBridgeUploadResult>).detail
      if (result?.batch_id) setUploads(result)
      void loadHistory()
    }
    window.addEventListener(FILE_BRIDGE_UPLOAD_EVENT, onUploaded)
    return () => window.removeEventListener(FILE_BRIDGE_UPLOAD_EVENT, onUploaded)
  }, [loadHistory])

  const copyPath = useCallback(async (path: string) => {
    const ok = await copyText(path)
    if (!ok) {
      setError('复制失败：当前浏览器没有剪贴板权限')
      return
    }
    setCopiedPath(path)
    window.setTimeout(() => setCopiedPath((current) => current === path ? '' : current), 1200)
  }, [])

  const receiveFiles = useCallback(async (source: FileList | File[]) => {
    const files = Array.from(source)
    if (files.length === 0 || uploading) return
    setUploading(true)
    setError('')
    setNotice('')
    try {
      const result = await fileBridgeApi.upload(files)
      setUploads(result)
      void loadHistory()
      setRoots((current) => current.map((root) => (
        root.id === 'staging' ? { ...root, available: true } : root
      )))
      await loadDirectory('staging', result.batch_id)
      if (result.items.length === 1) {
        const copied = await copyText(result.items[0].path)
        setNotice(copied ? '上传完成，远端绝对路径已复制。' : '上传完成；可点右侧按钮复制远端路径。')
        if (copied) setCopiedPath(result.items[0].path)
        void selectFile(result.items[0], 'staging')
      } else {
        setNotice(`已上传 ${result.items.length} 个文件；每个文件都可以单独复制远端路径。`)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }, [loadDirectory, loadHistory, selectFile, uploading])

  const pathParts = (listing?.relative_path || '').split('/').filter(Boolean)
  const downloadUrl = selected?.content_token
    ? fileBridgeContentUrl(selected.content_token)
    : ''

  return (
    <section className="fb-page" data-testid="file-bridge-page">
      <div className="fb-intro">
        <div>
          <span className="fb-kicker">FILE BRIDGE · CONTROLLED LOCAL I/O</span>
          <strong>Agent 暂存区</strong>
          <p>文件可拖到任意 Dashboard 页面或直接粘贴；上传只写入暂存区，浏览始终只读。</p>
        </div>
        <span className="fb-readonly-mark"><i />浏览只读</span>
      </div>

      <section className="fb-history" data-testid="file-bridge-history">
        <div className="fb-history-head">
          <div className="fb-section-tag"><span>RECENT</span>最近上传</div>
          <code>{history?.query_command || 'omni dashboard uploads --limit 20'}</code>
          <button type="button" onClick={() => { void loadHistory() }} title="刷新上传记录">
            <RefreshCw size={14} />
          </button>
        </div>
        <div className="fb-history-list">
          {history?.items.slice(0, 8).flatMap((batch) => (
            batch.items.map((item, index) => (
              <button
                type="button"
                className="fb-history-row"
                key={`${batch.batch_id}:${item.path}`}
                title={`${item.path}\n${batch.uploaded_at}`}
                onClick={() => { void copyPath(item.path) }}
              >
                {copiedPath === item.path ? <Check size={14} /> : iconFor(item)}
                <span>{item.name}</span>
                <time>{index === 0 ? formatTime(batch.uploaded_at) : ''}</time>
                <code>{item.path}</code>
                <Copy size={13} />
              </button>
            ))
          ))}
          {history && history.items.length === 0 && (
            <div className="fb-history-empty">还没有上传记录；把文件拖进任意页面即可建立第一条记录。</div>
          )}
          {!history && <div className="fb-history-empty">正在读取暂存记录…</div>}
        </div>
      </section>

      <section
        className={`fb-upload${dragging ? ' is-dragging' : ''}`}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true) }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => {
          if (event.currentTarget === event.target) setDragging(false)
        }}
        onDrop={(event) => {
          event.preventDefault()
          setDragging(false)
          void receiveFiles(event.dataTransfer.files)
        }}
      >
        <div className="fb-section-tag"><span>01</span>主动上传</div>
        <div className="fb-upload-copy">
          <UploadCloud size={24} />
          <div>
            <strong>{uploading ? '正在写入远端暂存区…' : '选择文件，或拖到这里'}</strong>
            <span>单文件上传完成后自动复制这台机器上的绝对路径；最多 50 个文件 / 批。</span>
          </div>
        </div>
        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          data-testid="file-bridge-input"
          onChange={(event) => { if (event.target.files) void receiveFiles(event.target.files) }}
        />
        <button
          type="button"
          className="fb-primary"
          disabled={uploading}
          data-testid="file-bridge-choose"
          onClick={() => inputRef.current?.click()}
        >
          {uploading ? <RefreshCw size={15} className="fb-spinning" /> : <UploadCloud size={15} />}
          {uploading ? '上传中' : '选择文件'}
        </button>
      </section>

      {(error || notice) && (
        <div className={`fb-notice${error ? ' is-error' : ''}`} role="status">
          {error || notice}
        </div>
      )}

      {uploads && (
        <section className="fb-upload-result" data-testid="file-bridge-upload-result">
          <div className="fb-section-tag"><span>PATH</span>{uploads.batch_path}</div>
          {uploads.items.map((item) => (
            <div className="fb-result-row" key={item.path}>
              <Check size={14} />
              <span title={item.path}>{item.path}</span>
              <button type="button" onClick={() => { void copyPath(item.path) }}>
                {copiedPath === item.path ? <Check size={13} /> : <Copy size={13} />}
                {copiedPath === item.path ? '已复制' : '复制路径'}
              </button>
            </div>
          ))}
        </section>
      )}

      <section className="fb-browser">
        <div className="fb-browser-head">
          <div className="fb-section-tag"><span>02</span>反向浏览</div>
          <div className="fb-root-tabs" role="radiogroup" aria-label="允许浏览的根目录">
            {roots.map((root, index) => (
              <button
                key={root.id}
                type="button"
                role="radio"
                aria-checked={root.id === rootId}
                data-active={root.id === rootId ? '1' : '0'}
                onClick={() => { void loadDirectory(root.id) }}
                title={root.path}
              >
                <span>{String(index + 1).padStart(2, '0')}</span>
                {root.label}
              </button>
            ))}
          </div>
        </div>

        <div className="fb-browser-layout">
          <div className="fb-directory-pane">
            <div className="fb-breadcrumbs">
              <button type="button" onClick={() => rootId && void loadDirectory(rootId)}>
                <HardDrive size={14} />{activeRoot?.label || 'ROOT'}
              </button>
              {pathParts.map((part, index) => (
                <React.Fragment key={`${part}-${index}`}>
                  <ChevronRight size={12} />
                  <button
                    type="button"
                    onClick={() => { void loadDirectory(rootId, pathParts.slice(0, index + 1).join('/')) }}
                  >
                    {part}
                  </button>
                </React.Fragment>
              ))}
              <span className="fb-breadcrumb-spacer" />
              <button
                type="button"
                className="fb-icon-btn"
                title="上一级"
                disabled={!listing?.relative_path}
                onClick={() => listing && void loadDirectory(rootId, parentPath(listing.relative_path))}
              >
                <ArrowUp size={14} />
              </button>
              <button
                type="button"
                className="fb-icon-btn"
                title="刷新当前目录"
                onClick={() => rootId && void loadDirectory(rootId, listing?.relative_path || '')}
              >
                <RefreshCw size={14} className={loading ? 'fb-spinning' : undefined} />
              </button>
            </div>

            <div className="fb-file-list" role="list">
              {loading && !listing && <div className="fb-loading">正在读取目录…</div>}
              {!loading && listing?.items.map((entry) => (
                <button
                  key={entry.path}
                  type="button"
                  role="listitem"
                  className="fb-file-row"
                  data-kind={entry.kind}
                  data-selected={selected?.path === entry.path ? '1' : undefined}
                  onClick={() => { void selectFile(entry) }}
                >
                  <span className="fb-file-icon">{iconFor(entry)}</span>
                  <span className="fb-file-name" title={entry.name}>{entry.name}</span>
                  <span className="fb-file-kind">{entry.kind === 'folder' ? '目录' : entry.mime}</span>
                  <span className="fb-file-size">{formatSize(entry.size)}</span>
                  <time>{formatTime(entry.modified_at)}</time>
                  <span
                    className="fb-row-action"
                    role="button"
                    tabIndex={0}
                    title="复制远端路径"
                    onClick={(event) => {
                      event.stopPropagation()
                      void copyPath(entry.path)
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== 'Enter' && event.key !== ' ') return
                      event.preventDefault()
                      event.stopPropagation()
                      void copyPath(entry.path)
                    }}
                  >
                    {copiedPath === entry.path ? <Check size={13} /> : <Copy size={13} />}
                  </span>
                  {entry.kind === 'folder' && <ChevronRight size={14} />}
                </button>
              ))}
              {!loading && listing && listing.items.length === 0 && (
                <div className="fb-empty"><Folder size={24} /><strong>空目录</strong></div>
              )}
              {listing?.truncated && <div className="fb-list-limit">条目较多，仅显示前 500 项。</div>}
            </div>
          </div>

          <aside className="fb-preview-pane" data-selected={selected ? '1' : '0'}>
            {!selected ? (
              <div className="fb-empty">
                <File size={26} />
                <strong>选择一个文件查看</strong>
                <span>目录单击进入；文件会尽力预览，无权限或不支持时仍可复制路径。</span>
              </div>
            ) : (
              <>
                <div className="fb-preview-head">
                  <span className="fb-file-icon">{iconFor(selected)}</span>
                  <div>
                    <strong title={selected.name}>{selected.name}</strong>
                    <span title={selected.path}>{selected.path}</span>
                  </div>
                  <button
                    type="button"
                    title="复制远端路径"
                    onClick={() => { void copyPath(selected.path) }}
                  >
                    {copiedPath === selected.path ? <Check size={14} /> : <Copy size={14} />}
                  </button>
                  {downloadUrl && (
                    <a href={downloadUrl} title="下载到当前设备">
                      <Download size={14} />
                    </a>
                  )}
                </div>
                <Preview detail={selected} />
              </>
            )}
          </aside>
        </div>
      </section>
    </section>
  )
}
