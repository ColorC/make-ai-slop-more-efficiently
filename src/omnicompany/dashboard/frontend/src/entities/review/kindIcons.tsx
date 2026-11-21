/**
 * entities/review/kindIcons — 物料类型 icon 映射。
 *
 * 2026-07-18 从 @phosphor-icons/react(duotone) 迁回 lucide-react(单线): 全项目图标收敛到
 * lucide 一套(phosphor 孤岛清退, 见 UNIFIED-FRONTEND-UPGRADE W2.5)。分型靠 domainColor 标志色。
 */
import {
  Image as ImageIcon,
  FileText,
  Globe,
  CircleQuestionMark,
  Puzzle,
  Video,
  ListChecks,
  FileCode,
  Monitor,
  Sparkles,
  Bot,
  Scale,
  File as FileIcon,
} from 'lucide-react'
import type { MaterialKind } from '../../api/reviewstageClient'
import { domainColor } from '../../shell/tokens'

type IconComp = typeof FileText

const KIND_ICON_COMPONENT: Record<MaterialKind, IconComp> = {
  image: ImageIcon,
  markdown: FileText,
  html: Globe,
  key_question: CircleQuestionMark,
  custom_web_template: Puzzle,
  video: Video,
  plan: ListChecks,
  'static-report': FileCode,
  demo: Monitor,
  'aigc-image': Sparkles,
  'agent-workflow-report': Bot,
  'decision-candidate': Scale,
}

// 每型一个标志色, 让一眼能分型: 富媒体/可视资产走 domainColor.cyan(白名单), 其余走既有语义 token。
const KIND_ICON_COLOR: Record<MaterialKind, string> = {
  image: domainColor.cyan,
  markdown: domainColor.blue,
  html: domainColor.cyan,
  key_question: 'var(--fp-warn)',
  custom_web_template: domainColor.cyan,
  video: domainColor.cyan,
  plan: 'var(--fp-accent)',
  'static-report': 'var(--fp-ok)',
  demo: domainColor.cyan,
  'aigc-image': domainColor.cyan,
  'agent-workflow-report': domainColor.cyan,
  'decision-candidate': 'var(--fp-warn)',
}

export function KindIcon({ kind, size = 16 }: { kind: MaterialKind; size?: number }) {
  const Comp = KIND_ICON_COMPONENT[kind] || FileIcon
  const color = KIND_ICON_COLOR[kind] || 'var(--fp-text-3)'
  return <Comp size={size} color={color} aria-hidden style={{ flexShrink: 0 }} />
}
