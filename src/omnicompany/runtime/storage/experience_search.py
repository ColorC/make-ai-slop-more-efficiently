# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.storage.experience_search_engine.pipeline.py"
"""experience_search — 执行前搜索（Pre-execution Search）

在 agent 开始实际工作之前，搜索已有的 pipeline 和知识节点，
避免重复劳动。支持两种搜索后端：

1. 语义向量 + 关键词混合搜索（embedding 可用时）
2. 纯关键词 + LLM 重排序（embedding 不可用时降级）

数据源：
- pipeline_index 表（SQLite，存储已注册 pipeline 的语义信息）
- KnowledgeRouter 子类（通过模块发现，PASSTHROUGH=True 的 Router）

设计来源: DESIGN-trace-induction.md §执行前搜索
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from omnicompany.runtime.storage.db_access import open_db

logger = logging.getLogger(__name__)

# ── pipeline_index 表定义 ─────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS pipeline_index (
    pipeline_name     TEXT PRIMARY KEY,
    purpose           TEXT NOT NULL,
    purpose_embedding BLOB,
    domain            TEXT,
    tags              TEXT NOT NULL DEFAULT '[]',
    source            TEXT NOT NULL DEFAULT 'manual',
    test_status       TEXT NOT NULL DEFAULT 'untested',
    created_at        TEXT NOT NULL,
    usage_count       INTEGER NOT NULL DEFAULT 0,
    last_used_at      TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pi_domain ON pipeline_index (domain);",
    "CREATE INDEX IF NOT EXISTS idx_pi_source ON pipeline_index (source);",
    "CREATE INDEX IF NOT EXISTS idx_pi_test_status ON pipeline_index (test_status);",
]


def ensure_pipeline_index_table(db_path: str) -> None:
    """确保 pipeline_index 表存在。"""
    with open_db(db_path) as conn:
        conn.executescript(_CREATE_TABLE + "\n".join(_CREATE_INDEXES))


# ── 数据结构 ──────────────────────────────────────────────────────────────

@dataclass
class PipelineMatch:
    """搜索命中的 pipeline 信息。"""
    pipeline_name: str
    purpose: str
    domain: str | None
    tags: list[str]
    source: str
    test_status: str
    score: float          # 匹配得分（0~1，越高越匹配）
    match_method: str     # "embedding" | "keyword" | "hybrid"


@dataclass
class KnowledgeMatch:
    """搜索命中的知识节点信息。"""
    class_name: str
    module: str
    format_in: str
    description: str
    score: float


@dataclass
class ExperienceSearchResult:
    """Pre-execution Search 的返回结果。"""
    pipelines: list[PipelineMatch] = field(default_factory=list)
    knowledge: list[KnowledgeMatch] = field(default_factory=list)

    @property
    def has_matches(self) -> bool:
        return bool(self.pipelines or self.knowledge)

    def best_pipeline(self) -> PipelineMatch | None:
        """返回得分最高的 pipeline（如有）。"""
        if not self.pipelines:
            return None
        return max(self.pipelines, key=lambda p: p.score)


# ── Pipeline 索引操作 ─────────────────────────────────────────────────────

def register_pipeline_to_index(
    db_path: str,
    *,
    pipeline_name: str,
    purpose: str,
    domain: str | None = None,
    tags: list[str] | None = None,
    source: str = "manual",
    test_status: str = "untested",
    purpose_embedding: bytes | None = None,
) -> None:
    """向 pipeline_index 注册一条 pipeline 元信息。

    重复注册同名 pipeline 会更新（UPSERT）。
    """
    ensure_pipeline_index_table(db_path)
    now = datetime.now(timezone.utc).isoformat()
    tags_json = json.dumps(tags or [], ensure_ascii=False)

    with open_db(db_path) as conn:
        conn.execute(
            """INSERT INTO pipeline_index
               (pipeline_name, purpose, purpose_embedding, domain, tags,
                source, test_status, created_at, usage_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
               ON CONFLICT(pipeline_name) DO UPDATE SET
                 purpose = excluded.purpose,
                 purpose_embedding = excluded.purpose_embedding,
                 domain = excluded.domain,
                 tags = excluded.tags,
                 source = excluded.source,
                 test_status = excluded.test_status
            """,
            (pipeline_name, purpose, purpose_embedding, domain,
             tags_json, source, test_status, now),
        )
    logger.info("Registered pipeline '%s' to index (source=%s)", pipeline_name, source)


def record_pipeline_usage(db_path: str, pipeline_name: str) -> None:
    """记录 pipeline 被使用一次（增加 usage_count，更新 last_used_at）。"""
    now = datetime.now(timezone.utc).isoformat()
    with open_db(db_path) as conn:
        conn.execute(
            """UPDATE pipeline_index
               SET usage_count = usage_count + 1, last_used_at = ?
               WHERE pipeline_name = ?""",
            (now, pipeline_name),
        )


def update_pipeline_test_status(
    db_path: str, pipeline_name: str, status: str,
) -> None:
    """更新 pipeline 的测试状态。

    status: "untested" | "passed" | "failed"
    """
    with open_db(db_path) as conn:
        conn.execute(
            "UPDATE pipeline_index SET test_status = ? WHERE pipeline_name = ?",
            (status, pipeline_name),
        )


# ── 搜索实现 ──────────────────────────────────────────────────────────────

def _extract_keywords(text: str) -> list[str]:
    """从文本中提取关键词（简单分词，去停用词）。"""
    # 简单实现：按空格/标点分割，过滤短词
    import re
    tokens = re.split(r'[\s,;，；。！？\-_/\\()\[\]{}]+', text)
    stopwords = {
        "的", "了", "是", "在", "和", "与", "一个", "这个", "那个",
        "the", "a", "an", "is", "are", "in", "on", "for", "to", "of",
        "it", "this", "that", "with",
    }
    return [t.lower() for t in tokens if len(t) >= 2 and t.lower() not in stopwords]


def search_pipeline_index_by_keywords(
    db_path: str,
    keywords: list[str],
    *,
    domain: str | None = None,
    top_k: int = 10,
) -> list[PipelineMatch]:
    """关键词搜索 pipeline_index 表。

    对每条 pipeline 的 purpose + tags 做关键词匹配，
    匹配词数越多得分越高。
    """
    ensure_pipeline_index_table(db_path)

    with open_db(db_path, readonly=True) as conn:
        if domain:
            rows = conn.execute(
                "SELECT * FROM pipeline_index WHERE domain = ?", (domain,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM pipeline_index").fetchall()

    if not rows or not keywords:
        return []

    results: list[PipelineMatch] = []
    for row in rows:
        purpose = row["purpose"].lower()
        tags_raw = row["tags"]
        try:
            tags_list = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags_list = []
        tags_text = " ".join(str(t).lower() for t in tags_list)
        searchable = f"{purpose} {tags_text}"

        hit_count = sum(1 for kw in keywords if kw in searchable)
        if hit_count == 0:
            continue

        score = hit_count / len(keywords)
        results.append(PipelineMatch(
            pipeline_name=row["pipeline_name"],
            purpose=row["purpose"],
            domain=row["domain"],
            tags=tags_list,
            source=row["source"],
            test_status=row["test_status"],
            score=score,
            match_method="keyword",
        ))

    results.sort(key=lambda m: m.score, reverse=True)
    return results[:top_k]


async def search_pipeline_index_by_embedding(
    db_path: str,
    query_embedding: list[float],
    *,
    top_k: int = 10,
) -> list[PipelineMatch]:
    """语义向量搜索 pipeline_index 表。

    对 purpose_embedding 非空的条目做余弦相似度计算。
    """
    from omnicompany.runtime.llm.embedding_client import get_embedding_client

    client = get_embedding_client()
    ensure_pipeline_index_table(db_path)

    with open_db(db_path, readonly=True) as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_index WHERE purpose_embedding IS NOT NULL"
        ).fetchall()

    if not rows or not query_embedding:
        return []

    results: list[PipelineMatch] = []
    for row in rows:
        stored_emb_blob = row["purpose_embedding"]
        try:
            stored_emb = json.loads(stored_emb_blob)
        except (json.JSONDecodeError, TypeError):
            continue

        score = client.cosine_sim(query_embedding, stored_emb)
        if score < 0.5:
            continue

        try:
            tags_list = json.loads(row["tags"]) if row["tags"] else []
        except (json.JSONDecodeError, TypeError):
            tags_list = []

        results.append(PipelineMatch(
            pipeline_name=row["pipeline_name"],
            purpose=row["purpose"],
            domain=row["domain"],
            tags=tags_list,
            source=row["source"],
            test_status=row["test_status"],
            score=score,
            match_method="embedding",
        ))

    results.sort(key=lambda m: m.score, reverse=True)
    return results[:top_k]


async def search_available_experience(
    task_description: str,
    *,
    db_path: str = "data/intent_traces.db",
    domain: str | None = None,
    top_k: int = 5,
) -> ExperienceSearchResult:
    """混合搜索 pipeline + 知识节点。

    优先尝试 embedding 搜索，不可用时降级为关键词搜索。

    Args:
        task_description: 任务描述（自然语言）
        db_path: pipeline_index 所在的数据库路径
        domain: 可选领域过滤
        top_k: 返回最多几条结果

    Returns:
        ExperienceSearchResult 包含匹配的 pipelines 和 knowledge 列表
    """
    results = ExperienceSearchResult()
    keywords = _extract_keywords(task_description)

    # 1. Pipeline 搜索
    embedding_results: list[PipelineMatch] = []
    keyword_results: list[PipelineMatch] = []

    # 1a. 尝试 embedding 搜索
    try:
        from omnicompany.runtime.llm.embedding_client import get_embedding_client
        client = get_embedding_client()
        query_vec = await client.get_embedding(task_description)
        if query_vec:
            embedding_results = await search_pipeline_index_by_embedding(
                db_path, query_vec, top_k=top_k * 2,
            )
    except Exception:
        logger.debug("Embedding search unavailable, falling back to keywords")

    # 1b. 关键词搜索（补充）
    keyword_results = search_pipeline_index_by_keywords(
        db_path, keywords, domain=domain, top_k=top_k * 2,
    )

    # 1c. 合并结果（RRF 简化版：去重 + 取较高分）
    seen: dict[str, PipelineMatch] = {}
    for m in embedding_results:
        seen[m.pipeline_name] = m
    for m in keyword_results:
        if m.pipeline_name in seen:
            existing = seen[m.pipeline_name]
            if m.score > existing.score:
                m.match_method = "hybrid"
                seen[m.pipeline_name] = m
            else:
                existing.match_method = "hybrid"
        else:
            seen[m.pipeline_name] = m

    merged = sorted(seen.values(), key=lambda m: m.score, reverse=True)
    results.pipelines = merged[:top_k]

    # 2. 知识节点搜索（通过 PASSTHROUGH Router 发现）
    # 注意：这里不扫描 RouterRegistry，而是用与 pipeline 相同的降级策略
    # 知识节点的发现留给 agent 自行在 FormatRegistry 中按 FORMAT_IN 匹配
    # （避免在搜索阶段拉入所有 Router 模块的重依赖）

    return results
