import { useEffect, useMemo, useState } from 'react'
import {
  Download,
  ExternalLink,
  Monitor,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
} from 'lucide-react'
import {
  desktopHostBridge,
  type DesktopHostInfo,
  type DesktopUpdateStatus,
} from '../../lib/workspaceNavigation'

interface LofaManifest {
  versionCode: number
  versionName: string
  size: number
  sha256: string
  publishedAt?: string
}

interface LofaVersionResponse {
  ok: boolean
  manifest: LofaManifest | null
}

interface AuthenticodeStatus {
  status: string
  publisher: string | null
  thumbprint: string | null
}

interface DesktopAsset {
  filename: string
  size: number
  sha256: string
  downloadUrl: string
  updaterSignature: string
  signatureVerifiedByClient: boolean
  authenticode: AuthenticodeStatus
}

interface DesktopRelease {
  version: string
  publishedAt: string
  notes: string
  assets: { nsis: DesktopAsset; msi?: DesktopAsset }
}

interface ClientCatalogResponse {
  ok: boolean
  desktop: {
    available: boolean
    reason?: string
    channel?: string
    latest: DesktopRelease | null
    previous: DesktopRelease | null
    updaterSignatureRequired: boolean
  }
}

const LOFA_DOWNLOAD_URL = '/api/android/apk/latest'
const LOFA_INSTALL_URL = '/api/android/install'

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '大小未知'
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function authenticodeLabel(status: AuthenticodeStatus): string {
  if (status.status.toLowerCase() === 'valid') {
    return status.publisher ? `Windows 发布者：${status.publisher}` : 'Windows 发布者签名有效'
  }
  return '未配置 Windows 受信任发布者证书'
}

export default function ClientDownloadsTab({ onOpenRemoteNode }: { onOpenRemoteNode: () => void }) {
  const host = useMemo(() => desktopHostBridge(), [])
  const [lofa, setLofa] = useState<LofaManifest | null>(null)
  const [desktop, setDesktop] = useState<ClientCatalogResponse['desktop'] | null>(null)
  const [hostInfo, setHostInfo] = useState<DesktopHostInfo | null>(null)
  const [lofaLoading, setLofaLoading] = useState(true)
  const [desktopLoading, setDesktopLoading] = useState(true)
  const [lofaError, setLofaError] = useState<string | null>(null)
  const [desktopError, setDesktopError] = useState<string | null>(null)
  const [updateStatus, setUpdateStatus] = useState<DesktopUpdateStatus | null>(null)
  const [updateBusy, setUpdateBusy] = useState(false)
  const [updateMessage, setUpdateMessage] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetch('/api/android/apk/version')
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<LofaVersionResponse>
      })
      .then((payload) => {
        if (!alive) return
        setLofa(payload.ok ? payload.manifest : null)
        setLofaLoading(false)
      })
      .catch((reason) => {
        if (!alive) return
        setLofaError(String(reason))
        setLofaLoading(false)
      })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    let alive = true
    fetch('/api/clients/catalog')
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<ClientCatalogResponse>
      })
      .then((payload) => {
        if (!alive) return
        setDesktop(payload.desktop)
        setDesktopLoading(false)
      })
      .catch((reason) => {
        if (!alive) return
        setDesktopError(String(reason))
        setDesktopLoading(false)
      })
    if (host?.hostInfo) {
      void host.hostInfo().then((info) => {
        if (alive) setHostInfo(info)
      }).catch(() => undefined)
    }
    return () => { alive = false }
  }, [host])

  const latest = desktop?.latest ?? null
  const previous = desktop?.previous ?? null
  const latestInstaller = latest?.assets.nsis ?? null
  const authenticodeValid = latestInstaller?.authenticode.status.toLowerCase() === 'valid'
  const rollbackRelease = previous && (
    host ? (hostInfo ? previous.version !== hostInfo.version : false) : true
  ) ? previous : null

  const checkForUpdate = async () => {
    if (!host?.checkDesktopUpdate) return
    setUpdateBusy(true)
    setUpdateMessage(null)
    try {
      const status = await host.checkDesktopUpdate(null)
      setUpdateStatus(status)
      setUpdateMessage(status.available ? `发现可安装版本 ${status.version}` : '当前已经是发布目录中的最新版本')
    } catch (reason) {
      setUpdateMessage(`检查失败：${String(reason)}`)
    } finally {
      setUpdateBusy(false)
    }
  }

  const applyUpdate = async (version?: string) => {
    if (!host?.applyDesktopUpdate) return
    setUpdateBusy(true)
    setUpdateMessage('正在校验发布目录并等待本机确认…')
    try {
      const action = await host.applyDesktopUpdate(version ?? null)
      if (action.reason === 'cancelled') setUpdateMessage('已取消安装')
      if (action.reason === 'no_update') setUpdateMessage('目标版本与当前版本相同')
    } catch (reason) {
      setUpdateMessage(`安装未启动：${String(reason)}`)
    } finally {
      setUpdateBusy(false)
    }
  }

  return (
    <>
      <div className="st-download-grid">
        <section className="st-client-card" data-testid="lofa-download-card">
          <div className="st-client-head">
            <span className="st-client-icon"><Smartphone size={20} aria-hidden="true" /></span>
            <span className="st-row-t">
              <span className="t1">LOFA</span>
              <span className="t2">Android 远程客户端</span>
            </span>
            <span className="st-status ready">可下载</span>
          </div>
          <div className="st-client-meta" aria-live="polite">
            {lofaLoading ? (
              <span className="st-muted">正在读取最新版本…</span>
            ) : lofa ? (
              <>
                <span>最新版本 <strong>{lofa.versionName}</strong></span>
                <span>{formatSize(lofa.size)}</span>
              </>
            ) : (
              <span className="st-err">{lofaError ? '暂时无法读取发布清单' : '尚未发布 APK'}</span>
            )}
          </div>
          <div className="st-client-actions">
            <a
              className={`st-btn primary${lofa ? '' : ' disabled'}`}
              href={lofa ? LOFA_DOWNLOAD_URL : undefined}
              aria-disabled={!lofa}
              data-testid="lofa-download-link"
            >
              <Download size={15} aria-hidden="true" />下载最新版
            </a>
            <a className="st-btn" href={LOFA_INSTALL_URL} target="_blank" rel="noreferrer">
              手机安装说明<ExternalLink size={14} aria-hidden="true" />
            </a>
          </div>
        </section>

        <section className="st-client-card" data-testid="desktop-download-card">
          <div className="st-client-head">
            <span className="st-client-icon"><Monitor size={20} aria-hidden="true" /></span>
            <span className="st-row-t">
              <span className="t1">Omnicompany Desktop</span>
              <span className="t2">Windows 全屏工作台</span>
            </span>
            <span className={`st-status ${latestInstaller ? 'ready' : 'planned'}`}>
              {latestInstaller ? '可下载' : '未发布'}
            </span>
          </div>
          <div className="st-client-meta" aria-live="polite">
            {desktopLoading ? (
              <span className="st-muted">正在读取桌面版发布目录…</span>
            ) : latest && latestInstaller ? (
              <>
                <span>最新版本 <strong>{latest.version}</strong></span>
                <span>{formatSize(latestInstaller.size)}</span>
                <span>{desktop?.channel ?? 'canary'}</span>
                {hostInfo && <span>本机 {hostInfo.version}</span>}
                {hostInfo && <span>Server {hostInfo.serverUrl}</span>}
                {hostInfo && <span>自动更新</span>}
              </>
            ) : (
              <span className="st-err">{desktopError || desktop?.reason || '尚未发布 Windows 安装包'}</span>
            )}
          </div>
          {latestInstaller && (
            <div className={`st-client-security${authenticodeValid ? '' : ' warning'}`}>
              {authenticodeValid ? <ShieldCheck size={16} /> : <ShieldAlert size={16} />}
              <span>
                <b>更新签名强制校验</b>
                <small>{authenticodeLabel(latestInstaller.authenticode)} · SHA-256 {latestInstaller.sha256.slice(0, 12)}…</small>
              </span>
            </div>
          )}
          <div className="st-client-actions">
            <a
              className={`st-btn primary${latestInstaller ? '' : ' disabled'}`}
              href={latestInstaller?.downloadUrl}
              aria-disabled={!latestInstaller}
              data-testid="desktop-download-link"
            >
              <Download size={15} aria-hidden="true" />下载 Windows 安装包
            </a>
            {host?.checkDesktopUpdate && (
              <button className="st-btn" type="button" disabled={updateBusy} onClick={() => { void checkForUpdate() }}>
                <RefreshCw size={14} aria-hidden="true" />检查更新
              </button>
            )}
            {host?.configureServer && (
              <button className="st-btn" type="button" disabled={updateBusy} onClick={() => { void host.configureServer!() }}>
                更换 Server
              </button>
            )}
            {updateStatus?.available && updateStatus.version && host?.applyDesktopUpdate && (
              <button className="st-btn" type="button" disabled={updateBusy} onClick={() => { void applyUpdate(updateStatus.version ?? undefined) }}>
                安装 {updateStatus.version}
              </button>
            )}
            {rollbackRelease && host?.applyDesktopUpdate ? (
              <button className="st-btn" type="button" disabled={updateBusy} onClick={() => { void applyUpdate(rollbackRelease.version) }}>
                <RotateCcw size={14} aria-hidden="true" />回滚到 {rollbackRelease.version}
              </button>
            ) : rollbackRelease ? (
              <a className="st-btn" href={rollbackRelease.assets.nsis.downloadUrl}>
                <RotateCcw size={14} aria-hidden="true" />下载上一版
              </a>
            ) : null}
          </div>
          {updateMessage && <div className="st-node-message" aria-live="polite">{updateMessage}</div>}
        </section>
      </div>

      <section className="st-node-note">
        <ShieldCheck size={19} aria-hidden="true" />
        <span className="st-row-t">
          <span className="t1">Remote Execute Node</span>
          <span className="t2">需要时在桌面端启用。</span>
        </span>
        <button className="st-btn" type="button" data-testid="open-remote-node" onClick={onOpenRemoteNode}>
          打开
        </button>
      </section>
    </>
  )
}
