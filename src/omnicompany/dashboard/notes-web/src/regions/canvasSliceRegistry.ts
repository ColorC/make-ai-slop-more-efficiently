/**
 * 切片登记表 —— 画布与"材料内部长什么样"之间的唯一接缝。
 *
 * 业主 2026-08-20：「card 之间的连接可以针对 material 内的任意切片，包括文字类的，
 * 交互 demo 类还有图片类的」。做法不是让画布去认识每种材料，而是**反过来**：
 * 每个材料渲染器把自己暴露出的切片登记进来，画布只负责在建边时按 handle 查出切片。
 *
 * 于是新增一类切片（文字段落、demo 步骤）不需要改画布一行代码 —— 与 core 0.3.0
 * "消费者声明能力"是同一条纪律的另一面。
 */
import { canvasSliceHandleId, type CanvasSliceRef } from "./materialCanvasModel.ts";

const slicesByKey = new Map<string, CanvasSliceRef>();

const keyOf = (cardId: string, handleId: string) => `${cardId}::${handleId}`;

/**
 * 登记一张卡片当前暴露的全部切片，返回注销函数（配 useEffect 清理）。
 * 同一张卡重复登记会**整体替换**，避免卡片换阶段后残留上一阶段的切片。
 */
export function publishCanvasSlices(
  cardId: string,
  slices: readonly CanvasSliceRef[],
  legacyAliases: Readonly<Record<string, CanvasSliceRef>> = {},
): () => void {
  const keys: string[] = [];
  for (const slice of slices) {
    const key = keyOf(cardId, canvasSliceHandleId(slice));
    slicesByKey.set(key, slice);
    keys.push(key);
  }
  // 别名只用于让存量落库关系继续查得到切片；它不改变切片身份。
  for (const [handleId, slice] of Object.entries(legacyAliases)) {
    const key = keyOf(cardId, handleId);
    slicesByKey.set(key, slice);
    keys.push(key);
  }
  return () => {
    for (const key of keys) slicesByKey.delete(key);
  };
}

/** 按 (卡片, handle) 查切片。查不到 = 这个端点不是切片端点，走四边语义。 */
export function lookupCanvasSlice(
  cardId: string | null | undefined,
  handleId: string | null | undefined,
): CanvasSliceRef | undefined {
  if (!cardId || !handleId) return undefined;
  return slicesByKey.get(keyOf(cardId, handleId));
}

/** 仅供诊断/测试：当前登记的条数。 */
export function canvasSliceRegistrySize(): number {
  return slicesByKey.size;
}
