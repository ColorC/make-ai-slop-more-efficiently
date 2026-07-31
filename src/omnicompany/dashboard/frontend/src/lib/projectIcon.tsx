// 项目 icon — 优先用开源 lucide 图标库里合适的; 找不到 → 纯色字母方块兜底(不用 AIGC/不存图)。
// 用户 2026-06-14: "找个开源的icon库先, 里面找不到合适的再生成纯色icon"。icon 与背景图(bg)是两回事。
import React from 'react'
import {
  TrendingUp, BookOpen, Swords, Boxes, LayoutDashboard, ShieldCheck, Stethoscope,
  Table2, Gamepad2, Puzzle, MonitorPlay, Radio, Globe, FolderGit2, type LucideIcon,
} from 'lucide-react'

// 按项目 id 挑 lucide 图标(开源库里找合适的)。没列到的 id → 纯色字母方块。
const BY_ID: Record<string, LucideIcon> = {
  'quant-lab': TrendingUp,
  vilo: BookOpen,
  walker: Swords,
  voxelcraft: Boxes,
  omnidashboard: LayoutDashboard,
  'omni-guard': ShieldCheck,
  'omni-teambuilder-doctor': Stethoscope,
  'demogame-config': Table2,
  'demogame-unity': Gamepad2,
  'demogame-prefab': Puzzle,
  'demogame-demo': MonitorPlay,
  'omni-remote': Radio,
  'personal-site': Globe,
}

// id 关键词兜底(新项目没在 BY_ID 里时也能挑到大致合适的)
function byKeyword(id: string): LucideIcon | null {
  const s = id.toLowerCase()
  if (s.includes('game') || s.includes('unity')) return Gamepad2
  if (s.includes('guard') || s.includes('secur')) return ShieldCheck
  if (s.includes('dash') || s.includes('board')) return LayoutDashboard
  if (s.includes('site') || s.includes('web')) return Globe
  if (s.includes('config') || s.includes('table')) return Table2
  if (s.includes('quant') || s.includes('lab')) return TrendingUp
  return null
}

export function projectColor(id: string): string {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0
  return `hsl(${h % 360}, 52%, 54%)`
}

/** 项目 icon: lucide 库里有合适的就用(放进同色调圆角方块); 没有 → 纯色字母方块。 */
export function ProjectIcon({ id, size = 18 }: { id: string; size?: number }) {
  const Icon = BY_ID[id] || byKeyword(id) || FolderGit2
  const explicit = !!(BY_ID[id] || byKeyword(id))
  const color = projectColor(id)
  const box: React.CSSProperties = {
    width: size, height: size, borderRadius: 4, flexShrink: 0,
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  }
  if (explicit) {
    return (
      <span style={{ ...box, background: `${color}26`, border: `1px solid ${color}66`, color }}>
        <Icon size={Math.round(size * 0.62)} />
      </span>
    )
  }
  // 库里找不到合适的 → 纯色字母方块
  return (
    <span style={{ ...box, background: color, color: '#fff', fontSize: Math.round(size * 0.5), fontWeight: 700 }}>
      {(id.replace(/[^a-z0-9]/gi, '')[0] || '?').toUpperCase()}
    </span>
  )
}
