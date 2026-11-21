import React from 'react';

type OpenCodeLogoProps = {
  className?: string;
};

// 内联 SVG(不引外部图片): 深色圆角方块 + 尖括号, 取终端/CLI 意象。
const OpenCodeLogo = ({ className = 'w-5 h-5' }: OpenCodeLogoProps) => {
  return (
    <svg viewBox="0 0 20 20" className={className} role="img" aria-label="OpenCode">
      <rect width="20" height="20" rx="4" fill="#334155" />
      <text
        x="10"
        y="13.5"
        textAnchor="middle"
        fontSize="9"
        fontWeight="700"
        fill="#ffffff"
        fontFamily="ui-monospace, monospace"
      >
        {'</>'}
      </text>
    </svg>
  );
};

export default OpenCodeLogo;
