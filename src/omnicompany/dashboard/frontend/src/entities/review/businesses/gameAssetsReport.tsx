/**
 * businesses/gameAssetsReport — walker 素材治理聚合审阅报告渲染器（game_assets 域 04 层·采用与报告）。
 *
 * 材料契约: kind=markdown + extra.data_schema_id="game.report.aggregate-v1"
 *   + extra.citations（注册表 ID 数组）+ extra.report_since。
 * 报告由 walker-game tools/ops/ops-game-report.mjs 生成，经 reviewstage REST 提交。
 *
 * 形态: 引用徽章墙（按素材种类分组着色）+ 常驻 asset-stage 内嵌展示壳
 * （点徽章经 postMessage 无刷新热切换 —— 与展示壳自身的嵌入协议同一套）；
 * 报告正文续以通用 markdown 渲染（更新列表表格）。
 * 纪律: 渲染器正文不画第二条顶栏（业务身份走 toolbar 工厂并入审阅顶栏）。
 */
import { useMemo, useRef, useState } from 'react'
import type { MaterialRendererProps } from '../rendererRegistry'
import { MarkdownMaterialView } from '../MaterialViews'
import './gameAssetsReport.css'

const KIND_META: Record<string, { label: string; color: string }> = {
  fx: { label: '特效', color: 'var(--fp-accent-2)' },
  bg: { label: '背景', color: 'var(--fp-link)' },
  layer: { label: '拆层', color: 'var(--fp-link)' },
  standee: { label: '立绘', color: 'var(--fp-err)' },
  token: { label: '棋子', color: 'var(--fp-warn)' },
  avatar: { label: '头像', color: 'var(--fp-err)' },
  portrait: { label: '肖像', color: 'var(--fp-accent-2)' },
  'key-art': { label: '关键图', color: 'var(--fp-warn)' },
  card: { label: '卡图', color: 'var(--fp-warn)' },
  ui: { label: 'UI', color: 'var(--fp-accent-2)' },
  prop: { label: '道具', color: 'var(--fp-ok)' },
  blocker: { label: '阻挡物', color: 'var(--fp-text)' },
}

function kindOf(id: string): string {
  return id.split(':')[0] ?? 'misc'
}

function shortId(id: string): string {
  return id.replace(/^[a-z-]+:/, '')
}

interface AggregateExtra {
  citations?: unknown
  report_since?: unknown
}

export function GameAssetsReportView({ m }: MaterialRendererProps) {
  const extra = (m.extra ?? {}) as AggregateExtra
  const citations = useMemo(
    () =>
      Array.isArray(extra.citations)
        ? (extra.citations as unknown[]).filter((c): c is string => typeof c === 'string')
        : [],
    [m.id],
  )
  const groups = useMemo(() => {
    const byKind = new Map<string, string[]>()
    for (const id of citations) {
      const k = kindOf(id)
      if (!byKind.has(k)) byKind.set(k, [])
      byKind.get(k)!.push(id)
    }
    return [...byKind.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [citations])
  const since = typeof extra.report_since === 'string' ? extra.report_since : null
  const stageRef = useRef<HTMLIFrameElement>(null)
  const [active, setActive] = useState<string | null>(citations[0] ?? null)

  const navigate = (assetId: string) => {
    setActive(assetId)
    stageRef.current?.contentWindow?.postMessage({ type: 'governance/navigate', asset: assetId }, '*')
  }

  if (citations.length === 0) {
    return (
      <div className="gar-root">
        <div className="gar-empty">本报告未携带注册表引用 —— 正文如下。</div>
        <MarkdownMaterialView m={m} />
      </div>
    )
  }

  return (
    <div className="gar-root" data-testid="game-assets-report">
      <header className="gar-head">
        <div>
          <span className="gar-kicker">素材治理 · 聚合审阅</span>
          {since && <span className="gar-since">{since} 以来变更</span>}
        </div>
        <span className="gar-count">{citations.length} 件引用</span>
      </header>

      <div className="gar-chips" aria-label="引用素材">
        {groups.map(([kind, ids]) => {
          const meta = KIND_META[kind] ?? { label: kind, color: 'var(--fp-text)' }
          return (
            <div className="gar-group" key={kind}>
              <span className="gar-glabel" style={{ color: meta.color }}>
                <i aria-hidden style={{ background: meta.color }} />
                {meta.label} · {ids.length}
              </span>
              <span className="gar-gchips">
                {ids.map((id) => (
                  <span
                    key={id}
                    className={`gar-chip${active === id ? ' is-active' : ''}`}
                    style={{ ['--gar-accent' as string]: meta.color }}
                  >
                    <button
                      type="button"
                      title={`${id} — 点击在下方展示壳切换`}
                      data-testid={`gar-chip-${id}`}
                      onClick={() => navigate(id)}
                    >
                      {shortId(id)}
                    </button>
                    <a
                      href={`/walker-game/asset-stage.html?asset=${encodeURIComponent(id)}`}
                      target="_blank"
                      rel="noopener"
                      title="新页签打开"
                    >
                      ↗
                    </a>
                  </span>
                ))}
              </span>
            </div>
          )
        })}
      </div>

      <iframe
        ref={stageRef}
        className="gar-stage"
        title="素材展示壳"
        src={`/walker-game/asset-stage.html?asset=${encodeURIComponent(active ?? citations[0])}`}
        data-testid="gar-stage-frame"
      />

      <details className="gar-body" open>
        <summary>报告正文（更新列表与备注）</summary>
        <MarkdownMaterialView m={m} />
      </details>
    </div>
  )
}
