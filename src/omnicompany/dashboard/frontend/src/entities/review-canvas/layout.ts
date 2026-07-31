/**
 * entities/review-canvas/layout — 「材料轨迹」画布的纯几何布局(v2 泳道单程向右流)。
 *
 * 形态权威 = docs/reviews/2026-07-04-trajectory-mockup/轨迹样张v2.html:
 *   - 泳道 = track(横向色带, 固定 y 按 track 序);
 *   - 时间从旧到新 = 左→右, 严格列网格(一材料一列, 永不并列堆叠);
 *   - 卡片竖直居中在自己泳道带; links = 卡片右缘流出→下一卡左缘汇入的贝塞尔曲线;
 *   - supersedes 承袭线(实线) / parent·related 派生线(虚线)。
 *
 * 与渲染解耦: 这里只吃 CanvasResponse 吐坐标, 不碰 React/DOM(便于单测与保持组件可读)。
 */
import type { CanvasResponse, CanvasMaterial, CanvasLink } from '../../api/reviewstageClient'

export const LANE_GUTTER = 52     // 左端道名带宽
export const LANE_H = 158         // 每条泳道带高
export const CARD_W = 244
export const CARD_H = 92
export const COL_W = 300          // 时间列间距(卡宽 244 + 列间 56 流线空间)
export const COL_LEFT = LANE_GUTTER + 40   // 第一列卡片左缘
const STAGE_PAD_R = 60
const STAGE_PAD_B = 8

// 泳道冷色系调色板(照样张: 青/靛/亮蓝…), 按 track 序循环取。
const LANE_PALETTE = ['#59b6c9', '#7c8cf8', '#4c8dff', '#5ad0e0', '#8fa0ff', '#3fb6a8']
export function laneColor(laneIdx: number): string {
  return LANE_PALETTE[((laneIdx % LANE_PALETTE.length) + LANE_PALETTE.length) % LANE_PALETTE.length]
}

export interface LaneGeom {
  track: string
  index: number
  top: number
  color: string
}
export interface CardGeom {
  mat: CanvasMaterial
  col: number
  x: number
  y: number
  color: string
}
export interface FlowGeom {
  link: CanvasLink
  /** 贝塞尔 path d(永远向右, x 递增) */
  path: string
  /** 汇入端小箭头三角 path d */
  arrow: string
  color: string
  supersede: boolean
}
export interface CanvasLayout {
  lanes: LaneGeom[]
  cards: CardGeom[]
  cardById: Map<string, CardGeom>
  flows: FlowGeom[]
  width: number
  height: number
  /** supersedes 边的中点(决策过程徽章骑坐处); 无则 null。 */
  decisionAnchor: { link: CanvasLink; x: number; y: number } | null
}

const RELATED_COLOR = '#8790a6'

function rightMid(c: CardGeom) { return { x: c.x + CARD_W, y: c.y + CARD_H / 2 } }
function leftMid(c: CardGeom) { return { x: c.x, y: c.y + CARD_H / 2 } }

// 右缘流出 → 下一卡左缘汇入的圆滑贝塞尔; 永远向右。
function bezier(a: { x: number; y: number }, b: { x: number; y: number }): string {
  const dx = Math.max((b.x - a.x) * 0.5, 46)
  return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`
}
function arrowHead(p: { x: number; y: number }): string {
  const s = 5
  return `M ${p.x} ${p.y} L ${p.x - s * 1.7} ${p.y - s} L ${p.x - s * 1.7} ${p.y + s} Z`
}

/**
 * 从画布投影算出全部坐标。
 * 列序 = 全材料按 created_at 升序(严格列网格, 一材料一列); y = 材料 track 的泳道带。
 */
export function computeLayout(data: CanvasResponse | null): CanvasLayout {
  const empty: CanvasLayout = {
    lanes: [], cards: [], cardById: new Map(), flows: [], width: 0, height: 0, decisionAnchor: null,
  }
  const tracks = data?.tracks ?? []
  if (!tracks.length) return empty

  const laneIndexOf = new Map<string, number>()
  const lanes: LaneGeom[] = tracks.map((t, i) => {
    laneIndexOf.set(t.track, i)
    return { track: t.track, index: i, top: i * LANE_H, color: laneColor(i) }
  })

  // 拍平所有版本材料, 按时间升序定列。
  const flat: CanvasMaterial[] = []
  for (const t of tracks) for (const f of t.families) for (const m of f.materials) flat.push(m)
  flat.sort((a, b) => tsOf(a.created_at) - tsOf(b.created_at))

  const cardById = new Map<string, CardGeom>()
  const cards: CardGeom[] = flat.map((mat, col) => {
    const li = laneIndexOf.get(mat.track) ?? 0
    const g: CardGeom = {
      mat, col,
      x: COL_LEFT + col * COL_W,
      y: lanes[li].top + (LANE_H - CARD_H) / 2,
      color: laneColor(li),
    }
    cardById.set(mat.id, g)
    return g
  })

  const width = COL_LEFT + Math.max(flat.length - 1, 0) * COL_W + CARD_W + STAGE_PAD_R
  const height = tracks.length * LANE_H + STAGE_PAD_B

  const flows: FlowGeom[] = []
  let decisionAnchor: CanvasLayout['decisionAnchor'] = null
  for (const link of data?.links ?? []) {
    const src = cardById.get(link.source)
    const tgt = cardById.get(link.target)
    if (!src || !tgt) continue
    const supersede = link.rel === 'supersedes'
    // 保证向右: 从时间早(列小)的一端流出。
    const [early, late] = src.col <= tgt.col ? [src, tgt] : [tgt, src]
    const a = rightMid(early)
    const b = leftMid(late)
    flows.push({
      link,
      path: bezier(a, b),
      arrow: arrowHead(b),
      color: supersede ? late.color : RELATED_COLOR,
      supersede,
    })
    if (supersede && !decisionAnchor) {
      decisionAnchor = { link, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
    }
  }

  return { lanes, cards, cardById, flows, width, height, decisionAnchor }
}

function tsOf(iso?: string): number {
  const t = iso ? Date.parse(iso) : NaN
  return Number.isNaN(t) ? 0 : t
}
