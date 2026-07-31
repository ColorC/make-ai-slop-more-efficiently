/**
 * WebgameSpecView — webgame-spec 材料的"三位一体"复合视图: 一份材料里整合 文档 / 引导演示 / 文件树,
 * 而非三条独立队列项。演示与文件树作为子材料(extra.demo / extra.filetree_diff)按 id 取来内嵌。
 * 点文件树里的文件 → 开一个"文件页签"看预览(图片/html/diff)。
 *
 * frostpane 重做(2026-06-30): root 透明吃全局冷渐变; 内部页签对齐 shadcn(圆角药丸/选中无缝/无底线);
 * 演示卡浮玻璃其上, 主操作单显, 长说明撤进 tooltip。只改呈现+交互, 数据接线/testid 全保。
 */
import React, { useEffect, useMemo, useState } from 'react'
import { ExternalLink } from 'lucide-react'
import { reviewstageApi, type Material } from '../../api/reviewstageClient'
import { createRenderer, stripFrontmatter } from '@wiki-core/render'
import '@wiki-core/ui.css'
import { FileTreeDiffView, FilePreview, type FileEntry } from './FileTreeDiffView'

const md = createRenderer()

interface FileTab { key: string; file: FileEntry }

const S: Record<string, React.CSSProperties> = {
  // 复合面板 → 底色透明, 吃 CockpitShell/body 的统一冷渐变(不铺实底把渐变顶掉)。
  root: { display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, height: '100%', background: 'transparent', color: 'var(--fp-text)' },
  // 页签条: 安静药丸排(shadcn) —— 无底部分割线, 横向可滚, 顶部留一点呼吸。
  tabBar: { display: 'flex', alignItems: 'center', gap: 4, padding: '8px 10px 4px', overflowX: 'auto', flexShrink: 0 },
  // 内容区: 撑满, 列向。
  body: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' },
  // 文档阅读区: 安静实表面(护眼), 浮在渐变上。
  doc: { flex: 1, minHeight: 0, overflow: 'auto', margin: '0 10px 10px', padding: '20px 24px', background: 'var(--fp-surface)', border: '1px solid var(--fp-border)', borderRadius: 11, boxShadow: 'inset 0 1px 0 rgba(255,255,255,.08)', fontFamily: 'var(--fp-font-sans)' },
  // 演示外壳: 玻璃卡浮其上(磨砂 + 边缘高光 + token 描边)。
  demoShell: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', margin: '0 10px 10px', borderRadius: 11, overflow: 'hidden', background: 'var(--fp-glass)', backdropFilter: 'var(--fp-blur)', WebkitBackdropFilter: 'var(--fp-blur)', border: '1px solid var(--fp-border)', boxShadow: '0 4px 16px rgba(0,0,0,.30), inset 0 1px 0 rgba(255,255,255,.08)' },
  // 演示卡头: 主操作单显(打开本体) + 弱次级状态字; 长说明只进 tooltip。
  demoHead: { display: 'flex', gap: 10, alignItems: 'center', padding: '8px 12px', borderBottom: '1px solid var(--fp-border-subtle)', flexShrink: 0 },
  // 主操作钮: 显眼(primary), 整段说明撤出界面。
  primaryBtn: { display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px', background: 'var(--fp-accent)', color: 'var(--fp-accent-fg)', border: '1px solid var(--fp-accent)', borderRadius: 7, cursor: 'pointer', fontWeight: 550, fontSize: 14, transition: 'filter 150ms var(--fp-ease)' },
  // 次级状态字: 13px 弱灰。
  demoNote: { fontSize: 13, color: 'var(--fp-text-3)' },
  iframe: { flex: 1, minHeight: 0, border: 'none', background: '#fff', width: '100%' },
  // 文件树外壳: 长 mono 列表保持安静实表面, 不盖死渐变。
  treeShell: { flex: 1, minHeight: 0, overflow: 'auto', margin: '0 10px 10px', borderRadius: 11, background: 'var(--fp-surface)', border: '1px solid var(--fp-border)' },
  filePane: { flex: 1, minHeight: 0, overflow: 'auto', margin: '0 10px 10px', padding: 10, borderRadius: 11, background: 'var(--fp-surface)', border: '1px solid var(--fp-border)' },
  empty: { padding: 16, color: 'var(--fp-text-3)', fontSize: 13 },
}

export function WebgameSpecView({ m }: { m: Material }) {
  const extra = (m.extra || {}) as Record<string, unknown>
  const demoRef = typeof extra.demo === 'string' ? extra.demo : null
  const demoId = demoRef && demoRef.startsWith('mat_') ? demoRef : null
  const demoUrlDirect = demoRef && !demoRef.startsWith('mat_') ? demoRef : null
  const filetreeId = typeof extra.filetree_diff === 'string' ? extra.filetree_diff : null

  const [demo, setDemo] = useState<Material | null>(null)
  const [filetree, setFiletree] = useState<Material | null>(null)
  const [tab, setTab] = useState<string>('doc')
  const [fileTabs, setFileTabs] = useState<FileTab[]>([])

  useEffect(() => { if (demoId) reviewstageApi.get(demoId).then(setDemo).catch(() => setDemo(null)) }, [demoId])
  useEffect(() => { if (filetreeId) reviewstageApi.get(filetreeId).then(setFiletree).catch(() => setFiletree(null)) }, [filetreeId])

  const docHtml = useMemo(() => (m.inline_content ? md.render(stripFrontmatter(m.inline_content)) : ''), [m.inline_content])
  const filetreeData = useMemo(() => {
    try { return filetree?.inline_content ? JSON.parse(filetree.inline_content) : null } catch { return null }
  }, [filetree])
  const demoLiveUrl = ((demo?.extra as Record<string, unknown> | undefined)?.live_url as string | undefined) ?? demoUrlDirect ?? undefined

  const openFileTab = (f: FileEntry) => {
    setFileTabs((prev) => (prev.some((t) => t.key === f.path) ? prev : [...prev, { key: f.path, file: f }]))
    setTab(`file:${f.path}`)
  }
  const closeFileTab = (key: string) => {
    setFileTabs((prev) => prev.filter((t) => t.key !== key))
    setTab('tree')
  }

  // shadcn 风药丸页签: 唯独选中页与内容同色无缝(实表面/无底线), 未选中弱底凹陷; hover 浮淡底。
  const TabPill = ({ k, label, onClose }: { k: string; label: string; onClose?: () => void }) => {
    const active = tab === k
    return (
      <div style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>
        <button
          type="button"
          onClick={() => setTab(k)}
          onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'rgba(255,255,255,.05)' }}
          onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent' }}
          style={{
            display: 'inline-flex', alignItems: 'center',
            padding: onClose ? '6px 6px 6px 12px' : '6px 12px',
            background: active ? 'var(--fp-surface)' : 'transparent',
            color: active ? 'var(--fp-text)' : 'var(--fp-text-3)',
            border: active ? '1px solid var(--fp-border)' : '1px solid transparent',
            borderRadius: 8, cursor: 'pointer', fontSize: 14, fontWeight: active ? 550 : 400,
            whiteSpace: 'nowrap', transition: 'background 150ms var(--fp-ease), color 150ms var(--fp-ease)',
          }}
        >
          {label}
          {onClose && (
            <span
              onClick={(e) => { e.stopPropagation(); onClose() }}
              title="关闭"
              style={{ marginLeft: 8, color: 'var(--fp-text-3)', fontSize: 13, lineHeight: 1, cursor: 'pointer' }}
            >✕</span>
          )}
        </button>
      </div>
    )
  }

  const activeFile = tab.startsWith('file:') ? fileTabs.find((t) => `file:${t.key}` === tab)?.file : null
  const demoNoteText = demo || demoUrlDirect ? '嵌入框小, 点"打开网页本体"全屏体验; 每步评论在演示卡上' : demoId ? '加载中…' : '未配置引导演示'

  return (
    <div data-testid="material-webgame-spec" style={S.root}>
      {/* 无标题头(Linear 内容优先): 页签即身份, 不再加重复标题栏。页签对齐 shadcn 药丸。 */}
      <div style={S.tabBar}>
        <TabPill k="doc" label="文档" />
        <TabPill k="demo" label="引导演示" />
        <TabPill k="tree" label="文件树" />
        {fileTabs.map((t) => <TabPill key={t.key} k={`file:${t.key}`} label={`文件: ${t.key.split('/').pop()}`} onClose={() => closeFileTab(t.key)} />)}
      </div>
      <div style={S.body}>
        {tab === 'doc' && (
          <div className="wiki-prose" style={S.doc} dangerouslySetInnerHTML={{ __html: docHtml }} />
        )}
        {tab === 'demo' && (
          <div style={S.demoShell}>
            <div style={S.demoHead}>
              {demoLiveUrl && (
                <button
                  type="button"
                  style={S.primaryBtn}
                  onClick={() => window.open(demoLiveUrl, '_blank', 'noopener')}
                  onMouseEnter={(e) => { e.currentTarget.style.filter = 'brightness(1.08)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.filter = 'none' }}
                  title="嵌入框小, 在新页全屏体验; 每步评论在演示卡上"
                >
                  <ExternalLink size={14} />打开网页本体（新页全屏）
                </button>
              )}
              <span style={S.demoNote} title="嵌入框小, 点&quot;打开网页本体&quot;全屏体验; 每步评论在演示卡上">{demoNoteText}</span>
            </div>
            {demoLiveUrl
              ? <iframe src={demoLiveUrl} sandbox="allow-same-origin allow-scripts" style={S.iframe} title="引导演示" />
              : <div style={S.empty}>未配置引导演示。</div>}
          </div>
        )}
        {tab === 'tree' && (
          <div style={S.treeShell}>
            {filetreeData
              ? <FileTreeDiffView data={filetreeData} material={filetree ?? undefined} onOpenFile={openFileTab} />
              : <div style={S.empty}>{filetreeId ? '文件树 diff 加载中…' : '未配置文件树 diff。'}</div>}
          </div>
        )}
        {activeFile && <div style={S.filePane}><FilePreview file={activeFile} /></div>}
      </div>
    </div>
  )
}
