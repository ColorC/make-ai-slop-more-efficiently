// 段落评论：锚点模型 + CommentStore 接口 + dashboard 审阅台(reviewstage)适配器。
// 锚点 = { para_hash, snippet, selected_text }，hash/snippet 算法与 dashboard
// entities/note/annotations.ts 逐字一致（FNV-1a 32bit，norm 后截 200），两边互认。

/** FNV-1a 32-bit hash of normalized paragraph text.（与 dashboard paragraphHash 一致） */
export function paragraphHash(text) {
  const norm = (text || "").toLowerCase().replace(/\s+/g, " ").trim().slice(0, 200);
  let h = 0x811c9dc5;
  for (let i = 0; i < norm.length; i++) {
    h ^= norm.charCodeAt(i);
    h = (h * 0x01000193) >>> 0;
  }
  return h.toString(16);
}

/**（与 dashboard snippetOf 一致）*/
export function snippetOf(text) {
  return (text || "").replace(/\s+/g, " ").trim().slice(0, 60);
}

/**
 * 审阅台 CommentStore：评论进 boss_sight reviewstage 材料（与圈选批注同一审阅流，AI 可消费）。
 * target = { kind: "wiki_paragraph", page, para_hash, snippet, selected_text }
 */
export function createReviewstageCommentStore({ endpoint = "/api/boss-sight/reviewstage", materialId }) {
  if (!materialId) throw new Error("reviewstage comment store requires materialId");
  return {
    async list(page) {
      const resp = await fetch(`${endpoint}/${materialId}`);
      if (!resp.ok) throw new Error(`load comments: HTTP ${resp.status}`);
      const material = await resp.json();
      return (material.comments || []).filter(
        (c) => c.target && c.target.kind === "wiki_paragraph" && (!page || c.target.page === page),
      );
    },
    async add({ page, paraText, selectedText, content }) {
      const resp = await fetch(`${endpoint}/${materialId}/comment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          author: "user",
          target: {
            kind: "wiki_paragraph",
            page,
            para_hash: paragraphHash(paraText),
            snippet: snippetOf(paraText),
            selected_text: snippetOf(selectedText || ""),
          },
        }),
      });
      if (!resp.ok) throw new Error(`add comment: HTTP ${resp.status}`);
      return resp.json();
    },
  };
}

/**
 * 演示步评论 CommentStore：每步评论进同一审阅台材料，target.kind="demo_step"。
 * 与段落评论同一传输/同一材料；审批后这些评论即"修改意见"(读侧派生)。
 * add({ target, content }) 的 target 由覆盖层用 stepAnchor(tour, step) 预构。
 */
export function createDemoCommentStore({ endpoint = "/api/boss-sight/reviewstage", materialId }) {
  if (!materialId) throw new Error("demo comment store requires materialId");
  return {
    async list() {
      const resp = await fetch(`${endpoint}/${materialId}`);
      if (!resp.ok) throw new Error(`load demo comments: HTTP ${resp.status}`);
      const material = await resp.json();
      return (material.comments || []).filter((c) => c.target && c.target.kind === "demo_step");
    },
    async add({ target, content }) {
      const resp = await fetch(`${endpoint}/${materialId}/comment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, author: "user", target }),
      });
      if (!resp.ok) throw new Error(`add demo comment: HTTP ${resp.status}`);
      return resp.json();
    },
  };
}
