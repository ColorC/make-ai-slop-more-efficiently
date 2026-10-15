// 文件标签 / 笔记标签共用的 chip 视觉 + 交互。纯展示组件, 不含任何存储 —— 搜索结果、标签输入面板、
// 标签管理台、笔记列表都用它, 配色/圆角/× 移除/置顶★ 一处定义。详见 docs/file-tagging-system.md ⑤。
import type { CSSProperties, DragEvent, MouseEvent } from "react";
import { X } from "lucide-react";

// 与 Rust tags.rs PALETTE 同序; 无显式色时按名字稳定散列取色(notes 标签没有 def 色, 用这个)。
const PALETTE = [
  "#6366f1", "#ef4444", "#f59e0b", "#10b981", "#3b82f6", "#ec4899", "#8b5cf6", "#14b8a6",
  "#f97316", "#84cc16",
];
export function colorOf(name: string, explicit?: string | null): string {
  if (explicit) return explicit;
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export function TagChip({
  name,
  color,
  pinned,
  size = "sm",
  onClick,
  onRemove,
  draggable,
  onDragStart,
}: {
  name: string;
  color?: string | null;
  pinned?: boolean;
  size?: "sm" | "md";
  onClick?: () => void;
  onRemove?: () => void;
  draggable?: boolean;
  onDragStart?: (e: DragEvent) => void;
}) {
  const c = colorOf(name, color);
  return (
    <span
      className={"tagchip" + (pinned ? " pinned" : "") + (size === "md" ? " md" : "") + (onClick ? " clickable" : "")}
      style={{ ["--tc" as string]: c } as CSSProperties}
      onClick={onClick}
      draggable={draggable}
      onDragStart={onDragStart}
      title={pinned ? `#${name}（置顶）` : `#${name}`}
    >
      {pinned && <span className="tagchip-pin">★</span>}
      <span className="tagchip-name">{name}</span>
      {onRemove && (
        <X
          size={11}
          className="tagchip-x"
          onClick={(e: MouseEvent) => {
            e.stopPropagation();
            onRemove();
          }}
        />
      )}
    </span>
  );
}
