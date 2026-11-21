// 皮肤的「部位表」—— 驾驶舱界面按部位拆开, 每个部位有若干槽位, 每个槽位说明两件事:
//   from: 这个槽位的值从皮肤文件的哪个 token 来(缺了怎么推导);
//   vars: 这个槽位驱动驾驶舱的哪些 CSS 变量(--fp-* 组件变量与 shadcn 语义槽)。
// 换皮肤 = 换一份皮肤文件, 这张表把它翻译成全站变量; 组件只认变量, 不认皮肤。
// 新增可换的部位: 在这里加槽位, 组件里引用它的变量, 不在组件里写死颜色。
import type { SkinArtifact, SkinBase } from './skinArtifact'

export interface SlotContext {
  base: SkinBase
  /** 读皮肤文件里的 token; 没有就是 undefined。 */
  token: (name: string) => string | undefined
}

export interface SkinSlot {
  /** 部位内的槽位名, 与部位 id 组成 `scene.color` 这样的全名(覆盖值按全名写)。 */
  slot: string
  label: string
  vars: readonly string[]
  /** 返回 undefined = 这个槽位沿用宿主默认值(蓝图)。 */
  from: (context: SlotContext) => string | undefined
}

export interface SkinPart {
  id: string
  label: string
  slots: readonly SkinSlot[]
}

const mix = (a: string, percent: number, b: string) => `color-mix(in srgb, ${a} ${percent}%, ${b})`
const fade = (color: string, percent: number) => mix(color, percent, 'transparent')
const scale = (length: string, factor: number) => `calc(${length} * ${factor})`
const NO_PATTERN = 'linear-gradient(transparent, transparent)'
const NO_SHADOW = '0 0 0 0 rgba(0,0,0,0)'

function both<T>(a: T | undefined, b: T | undefined, combine: (a: T, b: T) => string): string | undefined {
  return a !== undefined && b !== undefined ? combine(a, b) : undefined
}

export const SKIN_PARTS: readonly SkinPart[] = [
  {
    id: 'scene',
    label: '底图',
    slots: [
      { slot: 'color', label: '底色', vars: ['--fp-bg', '--fp-bp-scene', '--background'],
        from: ({ token }) => token('--background') },
      { slot: 'pattern', label: '底纹', vars: ['--fp-bg-grad', '--fp-bp-scene-grad'],
        from: ({ token }) => token('--appearance-canvas-background-image') ?? NO_PATTERN },
      // 边缘刻度尺是蓝图专属装饰, 其它皮肤默认不画。
      { slot: 'rulers', label: '边缘刻度尺', vars: ['--omni-skin-rulers'], from: () => 'none' },
    ],
  },
  {
    id: 'surface',
    label: '纸面',
    slots: [
      { slot: 'paper', label: '文字面(卡片、行、正文)', vars: ['--fp-surface', '--fp-card', '--fp-bp-paper', '--card', '--secondary'],
        from: ({ token }) => token('--card') },
      { slot: 'raised', label: '次级表面', vars: ['--fp-bg-overlay', '--muted'],
        from: ({ token }) => token('--muted') },
      { slot: 'hover', label: '悬停面', vars: ['--fp-bp-paper-2', '--accent'],
        from: ({ token }) => token('--appearance-state-hover') ?? token('--accent') },
      { slot: 'hoverInk', label: '悬停面上的字', vars: ['--accent-foreground'],
        from: ({ token }) => token('--appearance-state-hover-foreground') ?? token('--accent-foreground') },
      { slot: 'board', label: '深板(表格、长列表、输入框)', vars: ['--fp-solid', '--fp-bp-solid', '--fp-bg-doc'],
        from: ({ token }) => token('--editor-surface') ?? token('--popover') },
      { slot: 'overlay', label: '弹层面', vars: ['--popover'],
        from: ({ token }) => token('--popover') },
    ],
  },
  {
    id: 'glass',
    label: '面板与悬浮物',
    slots: [
      { slot: 'panel', label: '内容面板', vars: ['--fp-bp-panel', '--fp-bp-panel-text'],
        from: ({ token }) => token('--card') },
      { slot: 'panelHover', label: '内容面板悬停', vars: ['--fp-bp-panel-hover'],
        from: ({ token }) => both(token('--card'), token('--foreground'), (card, ink) => mix(card, 92, ink)) },
      { slot: 'panelBlur', label: '内容面板模糊', vars: ['--fp-bp-panel-blur'],
        from: ({ token }) => token('--appearance-state-backdrop-filter') ?? 'none' },
      { slot: 'floating', label: '悬浮物(rail、页签条、弹层)', vars: ['--fp-glass', '--fp-glass-2', '--fp-bp-tracing', '--fp-bp-tracing-strong'],
        from: ({ token }) => token('--popover') },
      { slot: 'floatingBlur', label: '悬浮物模糊', vars: ['--fp-blur', '--fp-bp-tracing-blur'],
        from: () => 'none' },
    ],
  },
  {
    id: 'line',
    label: '线条',
    slots: [
      { slot: 'base', label: '主边线', vars: ['--fp-border', '--fp-bp-line', '--border', '--input'],
        from: ({ token }) => token('--border') },
      { slot: 'strong', label: '加重边线', vars: ['--fp-border-strong', '--fp-bp-line-strong'],
        from: ({ token }) => both(token('--border'), token('--foreground'), (line, ink) => mix(line, 70, ink)) },
      { slot: 'subtle', label: '淡分割线', vars: ['--fp-border-subtle', '--fp-bp-line-subtle'],
        from: ({ token }) => { const line = token('--border'); return line ? fade(line, 45) : undefined } },
      { slot: 'ink', label: '线的墨色(装饰线、刻度)', vars: ['--fp-bp-line-ink'],
        from: ({ token }) => token('--foreground') },
      { slot: 'frameWidth', label: '外框线宽', vars: ['--fp-bp-frame-w'],
        from: ({ token }) => token('--appearance-border-width') },
    ],
  },
  {
    id: 'text',
    label: '文字',
    slots: [
      { slot: 'primary', label: '主文字',
        vars: ['--fp-text', '--fp-bp-text', '--foreground', '--card-foreground', '--popover-foreground', '--secondary-foreground'],
        from: ({ token }) => token('--foreground') },
      { slot: 'secondary', label: '次要文字', vars: ['--fp-text-2', '--fp-bp-text-2', '--muted-foreground'],
        from: ({ token }) => token('--muted-foreground') },
      { slot: 'tertiary', label: '弱文字', vars: ['--fp-text-3', '--fp-bp-text-3'],
        from: ({ token }) => both(token('--muted-foreground'), token('--background'), (ink, ground) => mix(ink, 72, ground)) },
    ],
  },
  {
    id: 'emphasis',
    label: '强调',
    slots: [
      // 驾驶舱的强调是「更亮的线和字」, 所以默认跟主文字走; 套件的主色放在「主操作」部位。
      { slot: 'base', label: '强调色', vars: ['--fp-accent', '--fp-bp-accent'],
        from: ({ token }) => token('--foreground') },
      { slot: 'ink', label: '强调色上的字', vars: ['--fp-accent-fg', '--fp-bp-accent-fg'],
        from: ({ token }) => token('--background') },
      { slot: 'wash', label: '强调弱底', vars: ['--fp-accent-weak', '--fp-bp-accent-soft'],
        from: ({ token }) => { const ink = token('--foreground'); return ink ? fade(ink, 10) : undefined } },
      { slot: 'info', label: '信息色', vars: ['--fp-accent-2'],
        from: ({ token }) => token('--chart-2') },
      { slot: 'link', label: '链接', vars: ['--fp-link'],
        from: ({ base, token }) => both(token('--chart-1'), token('--foreground'),
          (hue, ink) => mix(hue, base === 'light' ? 55 : 80, ink)) },
      { slot: 'violet', label: '域标识紫', vars: ['--fp-violet'],
        from: ({ token }) => token('--chart-1') },
    ],
  },
  {
    id: 'action',
    label: '主操作与焦点',
    slots: [
      { slot: 'primary', label: '主操作色(主按钮、在跑标记)', vars: ['--fp-bp-seal', '--primary'],
        from: ({ token }) => token('--primary') },
      { slot: 'primaryInk', label: '主操作色上的字', vars: ['--fp-bp-seal-fg', '--primary-foreground'],
        from: ({ token }) => token('--primary-foreground') },
      { slot: 'primaryEdge', label: '主按钮描边', vars: ['--fp-bp-seal-edge'],
        from: ({ token }) => token('--border') },
      { slot: 'primaryWash', label: '主操作弱底', vars: ['--fp-bp-seal-soft'],
        from: ({ token }) => { const primary = token('--primary'); return primary ? fade(primary, 22) : undefined } },
      { slot: 'idle', label: '退稿与待机标记', vars: ['--fp-bp-stamp-idle', '--fp-idle', '--fp-bp-idle'],
        from: ({ token }) => token('--muted-foreground') },
      { slot: 'focus', label: '焦点环与点缀', vars: ['--fp-bp-brass', '--ring'],
        from: ({ token }) => token('--ring') },
      // 这一档会被当成小标题的字色用, 所以往主文字方向压一些保证读得清。
      { slot: 'focusText', label: '点缀色文字', vars: ['--fp-bp-brass-hi'],
        from: ({ base, token }) => both(token('--ring'), token('--foreground'),
          (ring, ink) => mix(ring, base === 'light' ? 45 : 75, ink)) },
      { slot: 'focusWash', label: '点缀弱底', vars: ['--fp-bp-brass-soft'],
        from: ({ token }) => { const ring = token('--ring'); return ring ? fade(ring, 16) : undefined } },
      { slot: 'danger', label: '危险操作', vars: ['--destructive'],
        from: ({ token }) => token('--destructive') },
      { slot: 'dangerInk', label: '危险操作上的字', vars: ['--destructive-foreground'],
        from: ({ token }) => token('--destructive-foreground') },
    ],
  },
  {
    id: 'status',
    label: '状态',
    slots: [
      { slot: 'ok', label: '成功', vars: ['--fp-ok', '--fp-bp-ok'],
        from: ({ base }) => (base === 'light' ? '#1f9d6b' : '#7fd4a8') },
      { slot: 'warn', label: '注意', vars: ['--fp-warn', '--fp-bp-warn'],
        from: ({ base }) => (base === 'light' ? '#a87814' : '#e2c05a') },
      { slot: 'err', label: '失败', vars: ['--fp-err', '--fp-bp-err'],
        from: ({ token }) => token('--destructive') },
    ],
  },
  {
    id: 'shape',
    label: '圆角',
    slots: [
      { slot: 'small', label: '小件(标签、徽章、按钮)', vars: ['--fp-r1', '--fp-bp-r1'],
        from: ({ token }) => { const r = token('--appearance-control-radius'); return r ? scale(r, 0.5) : undefined } },
      { slot: 'medium', label: '中件(卡片、输入框)', vars: ['--fp-r2', '--fp-bp-r2'],
        from: ({ token }) => { const r = token('--appearance-control-radius'); return r ? scale(r, 0.75) : undefined } },
      { slot: 'large', label: '大件(面板、弹层)', vars: ['--fp-r3', '--fp-bp-r3', '--omni-skin-radius-xl'],
        from: ({ token }) => token('--appearance-panel-radius') },
      { slot: 'control', label: '语义类圆角基准', vars: ['--radius'],
        from: ({ token }) => token('--radius') },
      { slot: 'pill', label: '胶囊', vars: ['--fp-r-pill'],
        from: ({ token }) => token('--appearance-pill-radius') },
      { slot: 'sheet', label: '抽屉与大弹层', vars: ['--fp-r-sheet'],
        from: ({ token }) => token('--appearance-overlay-radius') },
    ],
  },
  {
    id: 'elevation',
    label: '阴影',
    slots: [
      { slot: 'small', label: '控件影', vars: ['--fp-shadow-sm', '--fp-bp-shadow-sm'],
        from: ({ token }) => token('--appearance-shadow-control') },
      { slot: 'base', label: '面板影', vars: ['--fp-shadow', '--fp-bp-shadow'],
        from: ({ token }) => token('--appearance-shadow-subtle') },
      { slot: 'pop', label: '弹层影', vars: ['--fp-shadow-pop', '--fp-bp-shadow-pop'],
        from: ({ token }) => token('--appearance-shadow-floating') },
      { slot: 'ink', label: '影的墨色', vars: ['--fp-bp-shadow-ink'],
        from: ({ base, token }) => (base === 'light' ? token('--foreground') : '#000000') },
      { slot: 'rim', label: '边缘高光', vars: ['--fp-spec'], from: () => NO_SHADOW },
    ],
  },
  {
    id: 'motion',
    label: '动效',
    slots: [
      { slot: 'ease', label: '缓动曲线', vars: ['--fp-ease'], from: ({ token }) => token('--motion-ease-out') },
      { slot: 'fast', label: '快速', vars: ['--fp-t-fast'], from: ({ token }) => token('--motion-fast') },
      { slot: 'pop', label: '弹出', vars: ['--fp-t-pop'], from: ({ token }) => token('--motion-base') },
      { slot: 'over', label: '入场', vars: ['--fp-t-over'], from: ({ token }) => token('--motion-enter') },
    ],
  },
  {
    id: 'reading',
    label: '阅读区',
    slots: [
      { slot: 'doc', label: '文档卡', vars: ['--fp-bp-doc'],
        from: ({ token }) => token('--editor-surface') ?? token('--card') },
      { slot: 'docInk', label: '文档卡上的字', vars: ['--fp-bp-doc-ink'],
        from: ({ token }) => token('--foreground') },
      { slot: 'vellum', label: '目录卡', vars: ['--fp-bp-vellum'],
        from: ({ token }) => token('--muted') },
      { slot: 'vellumInk', label: '目录卡上的字', vars: ['--fp-bp-vellum-ink'],
        from: ({ token }) => token('--foreground') },
    ],
  },
  {
    id: 'marks',
    label: '结构标记',
    slots: [
      { slot: 'hatch', label: '选中剖面线', vars: ['--fp-bp-hatch'],
        from: ({ token }) => { const ink = token('--foreground'); return ink
          ? `repeating-linear-gradient(45deg, ${fade(ink, 9)} 0 2px, transparent 2px 7px)` : undefined } },
    ],
  },
  {
    id: 'terminal',
    label: '终端',
    // 终端是嵌在界面上的一块深色观察窗, 各皮肤默认沿用宿主的黑玻璃; 皮肤可以按全名覆盖。
    slots: [
      { slot: 'pane', label: '窗体', vars: ['--fp-term-pane'], from: () => undefined },
      { slot: 'paneBlur', label: '窗体模糊', vars: ['--fp-term-pane-blur'], from: () => undefined },
      { slot: 'ground', label: '画布底', vars: ['--fp-term-bg'], from: () => undefined },
      { slot: 'edge', label: '窗缘', vars: ['--fp-term-edge'], from: () => undefined },
      { slot: 'glow', label: '压边', vars: ['--fp-term-glow'], from: () => undefined },
    ],
  },
]

export type SkinSlotName = string

/** 全部槽位全名(`部位.槽位`), 覆盖值只能写这些名字。 */
export const SKIN_SLOT_NAMES: readonly string[] = SKIN_PARTS.flatMap(
  (part) => part.slots.map((slot) => `${part.id}.${slot.slot}`),
)

/** 把一份皮肤文件翻译成驾驶舱 CSS 变量。overrides 按槽位全名覆盖推导值。 */
export function compileSkin(
  artifact: SkinArtifact,
  overrides: Readonly<Record<string, string>> = {},
): Record<string, string> {
  const context: SlotContext = {
    base: artifact.base,
    token: (name) => artifact.tokens[name],
  }
  const variables: Record<string, string> = {}
  for (const part of SKIN_PARTS) {
    for (const slot of part.slots) {
      const value = overrides[`${part.id}.${slot.slot}`] ?? slot.from(context)
      if (value === undefined) continue
      for (const name of slot.vars) variables[name] = value
    }
  }
  return variables
}
