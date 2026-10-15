import type { ReactNode } from 'react';

import { cn } from '../../../lib/utils';

/* ── Container ─────────────────────────────────────────────────── */
type PillBarProps = {
  children: ReactNode;
  className?: string;
};

export function PillBar({ children, className }: PillBarProps) {
  return (
    // 2026-08-06 蓝图: 药丸分段控件 → 蓝图玻璃按钮组(与 blueprint.css 的 .v2-seg 同一形态)。
    <div
      className={cn(
        'inline-flex items-center gap-[2px] rounded-[2px] p-[2px]',
        'border border-[color:var(--fp-border-subtle)] bg-[color:var(--fp-bp-panel)] backdrop-blur-[6px]',
        className,
      )}
    >
      {children}
    </div>
  );
}

/* ── Individual pill button ────────────────────────────────────── */
type PillProps = {
  isActive: boolean;
  onClick: () => void;
  children: ReactNode;
  className?: string;
};

export function Pill({ isActive, onClick, children, className }: PillProps) {
  return (
    <button
      onClick={onClick}
      aria-pressed={isActive}
      className={cn(
        'flex touch-manipulation items-center gap-1.5 rounded-[2px] px-3 py-1.5',
        // 打字机标签: mono + 大写 + .09em 字距(G 的 chrome 文字口径)
        'text-xs font-medium uppercase tracking-[.09em] font-[family-name:var(--fp-font-mono)]',
        'transition-colors duration-150',
        isActive
          // 选中 = 45° 剖面 hatch + 白线内描 + 制图角缺口。制图里"这块选中了"是画剖面线,
          // 不是刷一块实底 —— 这条与 .v2-seg[aria-checked=true] 完全同构。
          ? 'text-foreground bg-[image:var(--fp-bp-hatch)] bg-[color:var(--fp-surface)] shadow-[inset_0_0_0_1px_var(--fp-border-strong)] [clip-path:polygon(0_0,calc(100%-6px)_0,100%_6px,100%_100%,6px_100%,0_calc(100%-6px))]'
          : 'text-muted-foreground hover:text-foreground',
        className,
      )}
    >
      {children}
    </button>
  );
}
