/**
 * Design tokens — 组件侧消费口(colors.* 引用 var(--fp-*),单一真源=frostpane.css)。
 *
 * 2026-06 重做: 标准深色主题(参考collab platform深色 / Linear / Notion 深色)——底色分层、文字对比度 AA、字号地板硬化。
 * 2026-07-19 主题 G「蓝图精制」: 值全部改由 frostpane.css 的 G token 提供(scene/paper/tracing/
 * 白线 accent/seal 朱红/黄铜),本文件不再承载任何 hex,调主题只改 frostpane.css 一处(真源=theme.css)。
 *
 * 字号标准(地板硬化, 杜绝 11/12px 小字; G 全域功能文字 ≥12px):
 *   small:   13px — 辅助信息 / 时间戳 / 计数 (UI 文字地板, 不再更小)
 *   body:    15px — 正文基准 / 列表项 / 按钮 (整体抬到 15)
 *   doc:     16px — 文档 / 写作正文 (collab platform文档手感)
 *   title:   17px — 段落标题 / 面板标题
 *   heading: 22px — 页面标题
 *   caption: 13px — 仅 1~2 字 badge (历史保留, 已不再用于正文)
 *
 * 字体: UI=Inter→中文回落; 代码/数据=等宽(mono 栈尾挂 LXGW/Noto/YaHei CJK 回落, G.6)。
 */

// ── 颜色 (frostpane 冷色玻璃, 单一真源) ─────────────────────────────────────
// 2026-06-29 通用化: 每个值都引用 styles/frostpane.css 的 CSS 变量(--fp-*), 不再写死 hex。
// 调主题只改 frostpane.css 一处, 全 dashboard(凡消费 colors.* 或内联 var(--fp-*) 者)跟随。
// 文本按 DESIGN.md 收敛成 3 阶(text/2/3), 5 个历史字段映射到这 3 阶。
export const colors = {
  // 背景分层
  bg:           'var(--fp-bg)',          // App 底
  bgPanel:      'var(--fp-solid)',       // 面板 / 表格 / 长列表(实色, 避免大面积模糊)
  bgCard:       'var(--fp-card)',        // 悬浮卡片 / 输入框
  bgDoc:        'var(--fp-bg-doc)',      // 文档 / 写作区
  bgOverlay:    'var(--fp-bg-overlay)',  // 次级表面

  border:       'var(--fp-border)',      // 主边框(超薄, 透出背景)
  borderSubtle: 'var(--fp-border-subtle)', // 更淡分割线

  // 文本 3 阶(全部 AA on --fp-bg)
  text:         'var(--fp-text)',        // 主文本
  textSecondary:'var(--fp-text-2)',      // 次要文本
  textMuted:    'var(--fp-text-2)',      // 标签 / 描述
  textFaint:    'var(--fp-text-3)',      // 时间戳 / 低优先
  textGhost:    'var(--fp-text-3)',      // 占位符

  // 语义强调
  accent:       'var(--fp-accent)',      // 主强调
  accentBg:     'var(--fp-accent-weak)', // 强调弱底(半透, 同 demo 选中态)
  accentLime:   'var(--fp-accent)',      // 主操作 → 冷蓝
  link:         'var(--fp-link)',        // 链接
  wikilink:     'var(--fp-link)',

  // 状态色
  success:      'var(--fp-ok)',
  warning:      'var(--fp-err)',         // 错误/失败红(statusColor.fail 用它)
  info:         'var(--fp-accent-2)',    // cyan
} as const

// ── 域标识色 (白名单 map) ──────────────────────────────────────────────────
// 域标识色：有限、非语义、禁渐变。仅用于"这是哪个域/哪种资产类型"的身份标记(色点/chip/图标) ——
// 不表达状态(状态走 success/warning/info)、不做强调(强调走 accent)。值只取既有 --fp-* 实色 token,
// 新增条目必须先在此登记, 别开新彩虹。(2026-07-18 W2 violet 退位: 原 --fp-violet 消费归并到此)
export const domainColor = {
  cyan: 'var(--fp-accent-2)',  // 青 — 富媒体/可视资产(image/demo/html/video/custom_web_template/aigc)、Multica、agent.llm 事件、业务域 chip
  blue: 'var(--fp-accent)',    // 蓝 — 文档/文本类资产(markdown 等)
  gray: 'var(--fp-idle)',      // 灰 — 未登记域兜底
} as const

/** Status semantic color map. */
export const statusColor: Record<string, string> = {
  ok: colors.success, pass: colors.success, done: colors.success,
  finished: colors.success, success: colors.success,
  active: 'var(--fp-link)', running: 'var(--fp-warn)',
  pending: colors.textMuted, planned: colors.textMuted,
  paused: 'var(--fp-warn)', warn: 'var(--fp-warn)', warning: 'var(--fp-warn)',
  fail: colors.warning, failed: colors.warning, error: colors.warning,
  cancelled: colors.textMuted, unknown: colors.textFaint,
}

export function statusColorOf(status: string | null | undefined): string {
  if (!status) return statusColor.unknown
  return statusColor[status.toLowerCase()] || statusColor.unknown
}

// ── 间距 (4px 基准, 行内可点区纵向不低于 xs) ───────────────────────────────
export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const

// ── 字体 ───────────────────────────────────────────────────────────────
export const fonts = {
  // UI 正文: Inter 优先, 中文回落等线/微软雅黑
  ui: "'Inter', 'Inter Variable', '等线', 'DengXian', '微软雅黑', 'Microsoft YaHei', system-ui, -apple-system, sans-serif",
  // 等宽: Berkeley Mono 优先, 回落 Consolas/Menlo
  mono: "'Berkeley Mono', 'Consolas', 'Menlo', 'Monaco', 'IBM Plex Mono', monospace",
} as const

// ── 字号 (地板硬化) ─────────────────────────────────────────────────────
export const fontSize = {
  caption: 13,    // 仅 1~2 字 badge (历史保留)
  small:   13,    // 辅助信息地板 — 时间戳 / 计数
  body:    15,    // 正文基准 — 列表项 / 按钮 / kv 值
  doc:     16,    // 文档 / 写作正文
  title:   17,    // 段落标题 / 面板标题
  heading: 22,    // 页面标题
} as const

// ── 行高 ───────────────────────────────────────────────────────────────
export const lineHeight = {
  tight: 1.4,   // 密集列表 / 表格下限
  ui:    1.5,   // UI 文本
  doc:   1.6,   // 文档 / 写作
} as const

// ── 圆角 ───────────────────────────────────────────────────────────────
export const radius = {
  tags: 3,
  badges: 4,
  default: 6,   // 卡片 / 按钮 / 输入框
  xl: 12,
} as const
