import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '../../../lib/utils';

// Keep visual variants centralized so all button usages stay consistent.
//
// 2026-08-06 蓝图收敛: 本组件原本是 stock shadcn(rounded-md + bg-primary 冷蓝 + 柔光 shadow),
// 与主题 G「蓝图精制」不是一套语言 —— 这是用户说的「朴实的原版按钮、圆角按钮」的直接来源。
// 现在改为 styles/blueprint.css 的 .v2-btn 原子件之上再叠 Tailwind 布局类:
//   · 材质/圆角/边框/按压手感/焦点环 全部由 .v2-btn 承担(单一真源, 改主题只改 CSS);
//   · 这里只负责变体映射 + 尺寸(高度/内距)+ svg 尺寸这些排版层的事。
// variant 映射口径: default→纸面件, destructive→danger(朱红字, hover 才现底),
//   outline→虚线测量件, secondary→纸面件(同 default, 语义保留), ghost→无面件,
//   link→纯文字链(不套 .v2-btn, 按钮语言里它本来就不是按钮)。
// primary(朱红印章)不在 shadcn 变体表里 —— 需要它的地方直接写 className="v2-btn primary"。
const buttonVariants = cva(
  'inline-flex touch-manipulation items-center justify-center gap-2 whitespace-nowrap disabled:pointer-events-none [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'v2-btn',
        destructive: 'v2-btn danger',
        outline: 'v2-btn outline',
        secondary: 'v2-btn',
        ghost: 'v2-btn ghost',
        // link 保持纯文字: 不进 .v2-btn, 免得继承大写字距和边框
        link: 'text-sm text-[color:var(--fp-link)] underline-offset-4 hover:underline',
      },
      size: {
        // 高度对齐 G 的 34px 图标钮刻度(原 h-10=40px 在密集工具行里太胖)
        default: 'h-[34px] px-4',
        sm: 'h-[28px] px-3',
        lg: 'h-[40px] px-8',
        icon: 'h-[34px] w-[34px] px-0',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>;

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => {
    return (
      <button className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  }
);

Button.displayName = 'Button';

export { Button, buttonVariants };
