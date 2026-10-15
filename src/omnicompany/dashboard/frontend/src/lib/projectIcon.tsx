// 项目 icon — 正式项目全部映射到开源 Lucide 矢量图标。
// 未知 id 使用 FolderGit2 兜底，不再用字母方块伪装成项目图标。
import React from 'react'
import {
  Blocks,
  BookOpen,
  BrainCircuit,
  ChartCandlestick,
  CodeXml,
  DatabaseBackup,
  Factory,
  Feather,
  FileCheck,
  FishSymbol,
  FolderGit2,
  Gamepad2,
  GitBranch,
  Globe,
  Image as ImageIcon,
  Layers,
  LayoutDashboard,
  LayoutTemplate,
  Music,
  Package,
  Palette,
  PanelsTopLeft,
  Presentation,
  ScanSearch,
  Send,
  ShieldCheck,
  Smartphone,
  Stethoscope,
  Swords,
  Table2,
  TestTube,
  TvMinimalPlay,
  type LucideIcon,
} from 'lucide-react'

export const PROJECT_ICON_IDS = [
  'quant-lab', 'vilo', 'walker', 'voxelcraft', 'omnidashboard', 'omni-guard',
  'omni-teambuilder-doctor', 'demogame-config', 'demogame-unity', 'demogame-prefab',
  'demogame-demo', 'personal-site', 'aigc-image', 'aigc-music', 'publish-backup',
  'decisions', 'omni-productization', 'omni-sw-engineering', 'narrative',
  'omnidashboard-os', 'slidecast', 'frontend-design', 'demogame-design',
  'demogame-publishing', 'game-observatory', 'ai-learning-observatory',
  'agent-office', 'lofa', 'bside',
  'spark-webtwin', 'bilibili-publish',
] as const

const BY_ID: Record<(typeof PROJECT_ICON_IDS)[number], LucideIcon> = {
  'quant-lab': ChartCandlestick,
  vilo: BookOpen,
  walker: Swords,
  voxelcraft: Blocks,
  omnidashboard: LayoutDashboard,
  'omni-guard': ShieldCheck,
  'omni-teambuilder-doctor': Stethoscope,
  'demogame-config': Table2,
  'demogame-unity': TestTube,
  'demogame-prefab': LayoutTemplate,
  'demogame-demo': Gamepad2,
  'personal-site': Globe,
  'aigc-image': ImageIcon,
  'aigc-music': Music,
  'publish-backup': DatabaseBackup,
  decisions: GitBranch,
  'omni-productization': Package,
  'omni-sw-engineering': CodeXml,
  narrative: Feather,
  'omnidashboard-os': PanelsTopLeft,
  slidecast: Presentation,
  'frontend-design': Palette,
  'demogame-design': FileCheck,
  'demogame-publishing': Send,
  'game-observatory': ScanSearch,
  'ai-learning-observatory': BrainCircuit,
  'agent-office': Factory,
  lofa: Smartphone,
  bside: Layers,
  'spark-webtwin': FishSymbol,
  'bilibili-publish': TvMinimalPlay,
}

export function projectColor(id: string): string {
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0
  return `hsl(${h % 360}, 52%, 54%)`
}

/** 项目 icon: 注册项目用专属语义图标，未知 id 明确回落文件夹。 */
export function ProjectIcon({ id, size = 18 }: { id: string; size?: number }) {
  const Icon = BY_ID[id as keyof typeof BY_ID] || FolderGit2
  const color = projectColor(id)
  const box: React.CSSProperties = {
    width: size, height: size, borderRadius: 3, flexShrink: 0,
    boxSizing: 'border-box',
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  }
  return (
    <span
      data-project-icon={id}
      data-registered={BY_ID[id as keyof typeof BY_ID] ? '1' : '0'}
      style={{ ...box, background: `${color}26`, border: `1px solid ${color}66`, color }}
    >
      <Icon size={Math.round(size * 0.62)} />
    </span>
  )
}
