import React from 'react';

type CodeBuddyLogoProps = {
  className?: string;
};

const CodeBuddyLogo = ({ className = 'w-5 h-5' }: CodeBuddyLogoProps) => (
  <svg viewBox="0 0 20 20" className={className} role="img" aria-label="CodeBuddy">
    <rect width="20" height="20" rx="4" fill="#2563eb" />
    <text
      x="10"
      y="13.5"
      textAnchor="middle"
      fontSize="8"
      fontWeight="700"
      fill="#ffffff"
      fontFamily="system-ui, sans-serif"
    >
      CB
    </text>
  </svg>
);

export default CodeBuddyLogo;
