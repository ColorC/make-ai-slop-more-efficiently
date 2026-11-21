# [OMNI] origin=claude-code domain=services/domain_scout ts=2026-04-24T00:00:00Z
# [OMNI] material_id="material:diagnosis.domain_scout.material_definitions.formats.py"
"""domain_scout Material 定义 (F-19 kind 必填).

7 种 Material, 覆盖从 scout 请求到 digest 产出的全链路. 每条 Material
带 kind.source / kind.internal / kind.sink 精确标注.
"""
from omnifactory.packages.services._core.omnicompany import Material


SCOUT_REQUEST = Material(
    id="domain_scout.scout_request",
    name="Scout Request",
    description=(
        "启动单 topic 单轮调研的入口请求. 携带 topic_id / since / source_whitelist. "
        "source kind: 外部输入, 由 run_scout() 或 schedule 触发产出, 无 producer 合法."
    ),
    parent="requirement",
    tags=["domain_scout", "kind.source"],
    examples=[{
        "topic_id": "llm_agent_engineering",
        "since": "2026-04-17T00:00:00Z",
        "source_whitelist": ["github.com", "arxiv.org"],
    }],
)

FETCH_BATCH = Material(
    id="domain_scout.fetch_batch",
    name="Fetch Batch",
    description=(
        "SourceFetcher 抓取结果. list[{url, title, published_at, raw_html, "
        "fetch_ts, source_hash}]. 已抓但未去重未筛选. internal kind: 内部中转."
    ),
    parent="requirement",
    tags=["domain_scout", "kind.internal"],
    examples=[{
        "topic_id": "llm_agent_engineering",
        "fetched_at": "2026-04-24T10:00:00Z",
        "items": [{
            "url": "https://github.com/anthropics/claude-code",
            "title": "Claude Code release",
            "published_at": "2026-04-20T00:00:00Z",
            "source_hash": "sha256:...",
        }],
    }],
)

DEDUP_CANDIDATES = Material(
    id="domain_scout.dedup_candidates",
    name="Dedup Candidates",
    description=(
        "DedupFilter 去重后剩余的候选. 按 url+source_hash 指纹对比 index.jsonl "
        "剔除已报告项. 带 novelty_score. internal kind."
    ),
    parent="requirement",
    tags=["domain_scout", "kind.internal"],
    examples=[{
        "topic_id": "llm_agent_engineering",
        "candidates": [{
            "url": "https://...",
            "title": "...",
            "content_excerpt": "...",
            "source_hash": "sha256:...",
            "novelty_score": 0.9,
        }],
    }],
)

EVIDENCE_BUNDLE = Material(
    id="domain_scout.evidence_bundle",
    name="Evidence Bundle",
    description=(
        "EvidenceExtractor 对每条候选抽取的引用片段. D4 可验证性硬约束之一. "
        "保证 quoted_evidence 在原文中可精确定位. internal kind."
    ),
    parent="requirement",
    tags=["domain_scout", "kind.internal"],
    examples=[{
        "topic_id": "llm_agent_engineering",
        "bundles": [{
            "url": "https://...",
            "quoted_evidence": "原文引用 >= 1 句...",
            "quoted_spans": [[120, 280]],
            "relevance": "high",
        }],
    }],
)

RAW_FINDINGS = Material(
    id="domain_scout.raw_findings",
    name="Raw Findings",
    description=(
        "LLMSummarizer 提炼的 finding 草稿. 每条含 title / insight / source_url / "
        "quoted_evidence / confidence. 未过 D4 验证, 可能含幻觉. internal kind."
    ),
    parent="requirement",
    tags=["domain_scout", "kind.internal"],
    examples=[{
        "topic_id": "llm_agent_engineering",
        "findings": [{
            "title": "...",
            "insight": "...",
            "source_url": "https://...",
            "quoted_evidence": "...",
            "confidence": 0.8,
        }],
    }],
)

VERIFIED_FINDINGS = Material(
    id="domain_scout.verified_findings",
    name="Verified Findings",
    description=(
        "VerifiabilityCheck 过 D4 硬约束后的 findings. source_url 可达 + "
        "quoted_evidence 在原文中 + source_hash 匹配 三项齐备. internal kind."
    ),
    parent="requirement",
    tags=["domain_scout", "kind.internal"],
    examples=[{
        "topic_id": "llm_agent_engineering",
        "findings": [{
            "title": "...",
            "insight": "...",
            "source_url": "https://...",
            "quoted_evidence": "...",
            "source_hash": "sha256:...",
            "verified_at": "2026-04-24T10:05:00Z",
        }],
    }],
)

DIGEST = Material(
    id="domain_scout.digest",
    name="Domain Scout Digest",
    description=(
        "DigestWriter 合成的 digest markdown 文件路径 + index.jsonl 更新. "
        "sink kind: 最终输出, 由 privacy_publish 主动读, 本包不 push."
    ),
    parent="requirement",
    tags=["domain_scout", "kind.sink"],
    examples=[{
        "topic_id": "llm_agent_engineering",
        "digest_path": "data/services/domain_scout/llm_agent_engineering/digests/2026-04-24.md",
        "finding_count": 5,
        "index_updated": True,
    }],
)


ALL_MATERIALS = [
    SCOUT_REQUEST,
    FETCH_BATCH,
    DEDUP_CANDIDATES,
    EVIDENCE_BUNDLE,
    RAW_FINDINGS,
    VERIFIED_FINDINGS,
    DIGEST,
]

__all__ = [
    "SCOUT_REQUEST", "FETCH_BATCH", "DEDUP_CANDIDATES", "EVIDENCE_BUNDLE",
    "RAW_FINDINGS", "VERIFIED_FINDINGS", "DIGEST", "ALL_MATERIALS",
]
