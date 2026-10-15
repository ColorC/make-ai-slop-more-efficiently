import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { cn } from '../../../lib/utils';

type TooltipPosition = 'top' | 'bottom' | 'left' | 'right';

type TooltipProps = {
  children: ReactNode;
  content?: ReactNode;
  position?: TooltipPosition;
  className?: string;
  delay?: number;
  /** 容器(包裹 children 的 div)附加样式/类。放进 flex 行内时传 {minWidth:0} 之类保住省略号收缩。 */
  containerStyle?: React.CSSProperties;
  containerClassName?: string;
};

function getArrowClasses(position: TooltipPosition): string {
  switch (position) {
    // 2026-08-06: 箭头原本跟着 gray-900/gray-100 走(暗色下是白箭头), 改为跟随描图纸底色。
    case 'top':
      return 'top-full left-1/2 transform -translate-x-1/2 border-t-[color:var(--fp-bp-solid)]';
    case 'bottom':
      return 'bottom-full left-1/2 transform -translate-x-1/2 border-b-[color:var(--fp-bp-solid)]';
    case 'left':
      return 'left-full top-1/2 transform -translate-y-1/2 border-l-[color:var(--fp-bp-solid)]';
    case 'right':
      return 'right-full top-1/2 transform -translate-y-1/2 border-r-[color:var(--fp-bp-solid)]';
    default:
      return 'top-full left-1/2 transform -translate-x-1/2 border-t-[color:var(--fp-bp-solid)]';
  }
}

function Tooltip({
  children,
  content,
  position = 'top',
  className = '',
  delay = 350,
  containerStyle,
  containerClassName = '',
}: TooltipProps) {
  const [isVisible, setIsVisible] = useState(false);
  // Store the timer id without forcing re-renders while hovering.
  const timeoutRef = useRef<number | null>(null);
  const longPressTriggeredRef = useRef(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [tooltipStyle, setTooltipStyle] = useState<React.CSSProperties | null>(null);

  const updateTooltipPosition = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    const rect = container.getBoundingClientRect();
    const spacing = 8;
    const style: React.CSSProperties = {
      position: 'fixed',
      zIndex: 9999,
    };

    // Calculate tooltip position based on the specified position prop.
    switch (position) {
      case 'bottom':
        style.left = rect.left + rect.width / 2;
        style.top = rect.bottom + spacing;
        style.transform = 'translateX(-50%)';
        break;
      case 'left':
        style.left = rect.left - spacing;
        style.top = rect.top + rect.height / 2;
        style.transform = 'translate(-100%, -50%)';
        break;
      case 'right':
        style.left = rect.right + spacing;
        style.top = rect.top + rect.height / 2;
        style.transform = 'translateY(-50%)';
        break;
      case 'top':
      default:
        style.left = rect.left + rect.width / 2;
        style.top = rect.top - spacing;
        style.transform = 'translate(-50%, -100%)';
        break;
    }

    setTooltipStyle(style);
  }, [position]);

  const clearTooltipTimer = () => {
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  };

  const handleMouseEnter = () => {
    clearTooltipTimer();
    timeoutRef.current = window.setTimeout(() => {
      setIsVisible(true);
    }, delay);
  };

  const handleMouseLeave = () => {
    clearTooltipTimer();
    setIsVisible(false);
  };

  const handleTouchStart = () => {
    clearTooltipTimer();
    longPressTriggeredRef.current = false;
    timeoutRef.current = window.setTimeout(() => {
      longPressTriggeredRef.current = true;
      setIsVisible(true);
    }, delay);
  };

  const handleTouchEnd = () => {
    clearTooltipTimer();
    if (longPressTriggeredRef.current) {
      return;
    }
    setIsVisible(false);
  };

  useEffect(() => {
    // Avoid delayed updates after unmount.
    return () => {
      clearTooltipTimer();
    };
  }, []);

  useEffect(() => {
    if (!isVisible || typeof document === 'undefined') {
      return;
    }

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && containerRef.current?.contains(target)) {
        return;
      }
      setIsVisible(false);
      longPressTriggeredRef.current = false;
    };

    document.addEventListener('pointerdown', handlePointerDown, true);
    return () => document.removeEventListener('pointerdown', handlePointerDown, true);
  }, [isVisible]);

  useEffect(() => {
    if (!isVisible) {
      setTooltipStyle(null);
      return;
    }

    const rafId = window.requestAnimationFrame(updateTooltipPosition);
    const handleViewportChange = () => updateTooltipPosition();

    window.addEventListener('resize', handleViewportChange);
    window.addEventListener('scroll', handleViewportChange, true);

    return () => {
      window.cancelAnimationFrame(rafId);
      window.removeEventListener('resize', handleViewportChange);
      window.removeEventListener('scroll', handleViewportChange, true);
    };
  }, [isVisible, updateTooltipPosition]);

  if (!content) {
    return <>{children}</>;
  }

  return (
    <div
      ref={containerRef}
      className={cn('relative inline-block', containerClassName)}
      style={containerStyle}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      onTouchCancel={handleTouchEnd}
    >
      {children}
      {isVisible && typeof document !== 'undefined' && createPortal(
        <div
          ref={tooltipRef}
          style={tooltipStyle || { position: 'fixed', top: '-9999px', left: '-9999px', opacity: 0 }}
          className={cn(
            // 2026-08-06 蓝图: 原本 `dark:bg-gray-100 dark:text-gray-900` —— 暗色下是个白底提示框,
            // 悬浮在蓝图图板上格外扎眼。悬浮物在 G 里一律走描图纸族: 深图板底 + 白色绘图线边
            // + 纸叠纸硬错位影 + 打字机小字, 锐角不圆。
            'px-2.5 py-1 text-xs whitespace-nowrap pointer-events-none',
            'font-[family-name:var(--fp-font-mono)] tracking-[.06em]',
            'text-[color:var(--fp-bp-text)] bg-[color:var(--fp-bp-solid)]',
            'border border-[color:var(--fp-border)] rounded-[2px]',
            'shadow-[var(--fp-bp-shadow-sm)]',
            'mo-drop',
            className
          )}
        >
          {content}
          {/* Arrow */}
          <div className={cn('absolute w-0 h-0 border-4 border-transparent', getArrowClasses(position))} />
        </div>,
        document.body
      )}
    </div>
  );
}

export default Tooltip;
