import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '../../../lib/utils';

const badgeVariants = cva(
  // 2026-08-06 蓝图: rounded-md 圆角标签 → 锐角细线标注框 + 打字机大写字距,
// 与 blueprint.css 的 .bp-newtag / .st-status 同族(制图里一段标注就是个方框, 不是药丸)。
'inline-flex items-center rounded-[2px] border px-2.5 py-0.5 text-xs font-semibold uppercase tracking-[.09em] font-[family-name:var(--fp-font-mono)] transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground shadow hover:bg-primary/80',
        secondary: 'border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80',
        destructive:
          'border-transparent bg-destructive text-destructive-foreground shadow hover:bg-destructive/80',
        outline: 'text-foreground',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
);

type BadgeProps = React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof badgeVariants>;

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
