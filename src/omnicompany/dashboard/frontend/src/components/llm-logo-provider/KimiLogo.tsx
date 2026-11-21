import React from 'react';

type KimiLogoProps = {
  className?: string;
};

// 内联 SVG(不引外部图片): 圆角方块 + 字母 K, 颜色对齐 TokenStatsTab 的 kimi 色。
const KimiLogo = ({ className = 'w-5 h-5' }: KimiLogoProps) => {
  return (
    <svg viewBox="0 0 20 20" className={className} role="img" aria-label="Kimi">
      <rect width="20" height="20" rx="4" fill="#e11d48" />
      <text
        x="10"
        y="14.5"
        textAnchor="middle"
        fontSize="12"
        fontWeight="700"
        fill="#ffffff"
        fontFamily="system-ui, sans-serif"
      >
        K
      </text>
    </svg>
  );
};

export default KimiLogo;
