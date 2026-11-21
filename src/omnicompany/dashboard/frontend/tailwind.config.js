// shadcn 语义槽里存的是完整颜色值(hex / rgb / oklch 都行, 由皮肤决定), 不再是 HSL 三元组。
// 透明度修饰符(bg-card/50)用 color-mix 实现, 所以任何颜色格式都能带透明度。
const slot = (name) => `color-mix(in srgb, var(${name}) calc(<alpha-value> * 100%), transparent)`

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      // 2026-06 重做: 字号地板硬化 —— xs/sm 物理映射到 ≥14px, text-xs 不可能再渲染 12px。
      // 这是"字看不清"的配置层根治(原来 floor 只在注释里, Tailwind 实际仍输出 12px)。
      fontSize: {
        xs: ['14px', { lineHeight: '1.5' }],
        sm: ['14px', { lineHeight: '1.5' }],
        base: ['15px', { lineHeight: '1.6' }],
        lg: ['17px', { lineHeight: '1.5' }],
        xl: ['20px', { lineHeight: '1.4' }],
        '2xl': ['24px', { lineHeight: '1.3' }],
        '3xl': ['30px', { lineHeight: '1.2' }],
        '4xl': ['36px', { lineHeight: '1.15' }],
      },
      colors: {
        border: slot("--border"),
        input: slot("--input"),
        ring: slot("--ring"),
        background: slot("--background"),
        foreground: slot("--foreground"),
        primary: {
          DEFAULT: slot("--primary"),
          foreground: slot("--primary-foreground"),
        },
        secondary: {
          DEFAULT: slot("--secondary"),
          foreground: slot("--secondary-foreground"),
        },
        destructive: {
          DEFAULT: slot("--destructive"),
          foreground: slot("--destructive-foreground"),
        },
        muted: {
          DEFAULT: slot("--muted"),
          foreground: slot("--muted-foreground"),
        },
        accent: {
          DEFAULT: slot("--accent"),
          foreground: slot("--accent-foreground"),
        },
        popover: {
          DEFAULT: slot("--popover"),
          foreground: slot("--popover-foreground"),
        },
        card: {
          DEFAULT: slot("--card"),
          foreground: slot("--card-foreground"),
        },
      },
      // 2026-08-06 蓝图收敛: 制图感 = 锐利。G 的圆角刻度只有 2/3/5(--fp-bp-r1/r2/r3)。
      // --radius 已由 8px 降到 5px, lg/md/sm 自动落到 5/3/1;
      // xl 及以上是 Tailwind 内置默认(12/16/24px), 不封顶就会有组件继续渲染大圆角 ——
      // 这里一并压回刻度内, full 保留(色点/头像等真·圆形件仍需要)。
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xl: "var(--omni-skin-radius-xl, 5px)",
        "2xl": "var(--omni-skin-radius-xl, 5px)",
        "3xl": "var(--omni-skin-radius-xl, 5px)",
      },
      spacing: {
        'safe-area-inset-bottom': 'env(safe-area-inset-bottom)',
        'mobile-nav': 'var(--mobile-nav-total)',
      },
      keyframes: {
        shimmer: {
          '0%': { backgroundPosition: '200% 0' },
          '100%': { backgroundPosition: '-200% 0' },
        },
        'dialog-overlay-show': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'dialog-content-show': {
          from: { opacity: '0', transform: 'translate(-50%, -48%) scale(0.96)' },
          to: { opacity: '1', transform: 'translate(-50%, -50%) scale(1)' },
        },
      },
      animation: {
        shimmer: 'shimmer 2s linear infinite',
        'dialog-overlay-show': 'dialog-overlay-show 150ms ease-out',
        'dialog-content-show': 'dialog-content-show 150ms ease-out',
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
}