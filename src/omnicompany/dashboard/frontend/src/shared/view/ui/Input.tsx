import * as React from 'react';

import { cn } from '../../../lib/utils';

type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          // 2026-08-06 蓝图: 输入/筛选件 = 虚线测量件(G 的既定口径), 聚焦时转实线 + 黄铜环,
// 与 .v2-search / .bps-btn 同一手感; 去掉柔光 shadow-sm。
'flex h-9 w-full rounded-[2px] border border-dashed border-[color:var(--fp-border)] bg-transparent px-3 py-1 text-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:border-solid focus-visible:border-[color:var(--fp-bp-brass)] focus-visible:shadow-[0_0_0_3px_var(--fp-bp-brass-soft)] disabled:cursor-not-allowed disabled:opacity-50',
          className
        )}
        ref={ref}
        {...props}
      />
    );
  }
);

Input.displayName = 'Input';

export { Input };
