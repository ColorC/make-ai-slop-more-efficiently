# [OMNI] origin=claude-code domain=services/_core/semantic_fs ts=2026-06-27T00:00:00Z type=router
# [OMNI] summary="语义检索投影层(语义文件系统里程碑三, 唯一真缺设施)。对每个 material 建整单元 embedding + chunk 两级索引, 存 SQLite 旁路库;检索走 hybrid(向量召回 + semantic_tags/type/domain meta 硬过滤 + 重排);自语料 benchmark 选模型。只读投影, 真源不动, 可重建。"
# [OMNI] why="赢家=真源不动+旁路索引(Spotlight)。索引可丢可重建=IO 单向铁律。chunk 用 recursive 不做纯语义切分(Vectara 实测纯语义切碎片掉准)。"
# [OMNI] tags=semantic-os,embedding,semantic-search,hybrid,sqlite
# [OMNI] material_id="material:core.semantic_fs.index.py"
"""语义检索投影层(里程碑三)。embed + chunk 两级索引 + hybrid 检索 + 自语料 benchmark。

存储: data/semantic_fs/index.db(SQLite 旁路, 不动真源)。向量存 JSON TEXT(可移植)。
模型默认 gemini-embedding-001(dim 3072), 经 benchmark 后可换。
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

from . import schema as SC

_DEFAULT_EMBED_MODEL = "gemini-embedding-001"
_CHUNK_CHARS = 500
_CHUNK_OVERLAP = 80
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "; ", ". ", "，", " "]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path(root: Path | None = None) -> Path:
    d = (root or omni_workspace_root()) / "data" / "semantic_fs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "index.db"


def _conn(root: Path | None = None) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path(root)))
    c.execute("""CREATE TABLE IF NOT EXISTS materials(
        entity_id TEXT PRIMARY KEY, file_path TEXT, summary TEXT,
        semantic_tags TEXT, model TEXT, vec TEXT, indexed_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS chunks(
        entity_id TEXT, chunk_idx INTEGER, text TEXT, vec TEXT,
        PRIMARY KEY(entity_id, chunk_idx))""")
    return c


# ── embedding ──────────────────────────────────────────────────────────
def embed_texts(texts: list[str], *, model: str = _DEFAULT_EMBED_MODEL) -> list[list[float]]:
    """批量取 embedding(走 the_company proxy 的 OpenAI 兼容 /embeddings)。"""
    from omnicompany.runtime.llm.llm import LLMClient
    client = LLMClient(model=model)._openai_client
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        batch = [t[:8000] if t.strip() else " " for t in texts[i:i + 64]]
        r = client.embeddings.create(model=model, input=batch)
        out.extend([d.embedding for d in r.data])
    return out


# ── chunking(recursive, 非纯语义切分) ──────────────────────────────────
def chunk_text(text: str, size: int = _CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    # 先按分隔符切成小片, 再贪心累积到 ~size, 带 overlap
    pieces = [text]
    for sep in _SEPARATORS:
        nxt: list[str] = []
        for p in pieces:
            if len(p) <= size:
                nxt.append(p)
            else:
                parts = p.split(sep)
                nxt.extend(parts[0] if len(parts) == 1 else (x + sep) for x in parts[:-1])
                if parts:
                    nxt.append(parts[-1])
        pieces = [x for x in nxt if x]
        if all(len(p) <= size for p in pieces):
            break
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if len(cur) + len(p) <= size:
            cur += p
        else:
            if cur:
                chunks.append(cur)
            cur = (cur[-overlap:] if cur else "") + p
            while len(cur) > size:
                chunks.append(cur[:size])
                cur = cur[size - overlap:]
    if cur.strip():
        chunks.append(cur)
    return [c.strip() for c in chunks if c.strip()]


# ── cosine ─────────────────────────────────────────────────────────────
def _cos(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb + 1e-9)


# ── build index ────────────────────────────────────────────────────────
def _readable_materials(limit: int | None, root: Path) -> list[Any]:
    from omnicompany.packages.services._core.registry import get_registry
    reg = get_registry()
    out = []
    for e in reg.list_all():
        sf = e.source_file or ""
        if not sf or any(sf.endswith(ext) for ext in (".png", ".jpg", ".xlsm", ".pdf", ".zip")):
            continue
        p = root / sf
        if p.is_file():
            out.append(e)
        if limit and len(out) >= limit:
            break
    return out


def build_index(*, entity_ids: list[str] | None = None, model: str = _DEFAULT_EMBED_MODEL,
                limit: int | None = None, rebuild: bool = False,
                root: Path | None = None, echo: Any = None) -> dict[str, Any]:
    """对已注册 material 建两级索引(整单元 + chunk)。只读真源, 可重建。"""
    base = root or omni_workspace_root()
    from omnicompany.packages.services._core.registry import get_registry
    reg = get_registry()
    if entity_ids:
        ents = [reg.read(eid) for eid in entity_ids]
        ents = [e for e in ents if e and e.source_file and (base / e.source_file).is_file()]
    else:
        ents = _readable_materials(limit, base)
    c = _conn(base)
    if rebuild:
        c.execute("DELETE FROM materials"); c.execute("DELETE FROM chunks")
    indexed = 0
    chunk_total = 0
    for e in ents:
        try:
            text = (base / e.source_file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sem = (e.attrs or {}).get("semantic", {})
        summary = sem.get("summary") or (e.attrs or {}).get("omnimark_header", {}).get("summary", "") or e.name
        unit_text = f"{summary}\n{text[:4000]}"
        chunks = chunk_text(text)
        vecs = embed_texts([unit_text] + chunks, model=model)
        unit_vec, chunk_vecs = vecs[0], vecs[1:]
        c.execute("INSERT OR REPLACE INTO materials VALUES(?,?,?,?,?,?,?)",
                  (e.entity_id, e.source_file, summary,
                   json.dumps(sem.get("semantic_tags", []), ensure_ascii=False),
                   model, json.dumps(unit_vec), _now()))
        c.execute("DELETE FROM chunks WHERE entity_id=?", (e.entity_id,))
        for idx, (ch, v) in enumerate(zip(chunks, chunk_vecs)):
            c.execute("INSERT OR REPLACE INTO chunks VALUES(?,?,?,?)",
                      (e.entity_id, idx, ch[:1000], json.dumps(v)))
        indexed += 1
        chunk_total += len(chunks)
        if echo:
            echo(f"  indexed {e.entity_id} (+{len(chunks)} chunks)")
    c.commit(); c.close()
    return {"ok": True, "indexed": indexed, "chunks": chunk_total, "model": model,
            "db": str(db_path(base))}


# ── hybrid search ──────────────────────────────────────────────────────
def search(query: str, *, top_k: int = 5, tags: list[str] | None = None,
           model: str = _DEFAULT_EMBED_MODEL, root: Path | None = None) -> list[dict]:
    """hybrid: 向量召回(整单元 + chunk 取 max)+ semantic_tags meta 硬过滤 + 重排。"""
    base = root or omni_workspace_root()
    qv = embed_texts([query], model=model)[0]
    c = _conn(base)
    mats = c.execute("SELECT entity_id, file_path, summary, semantic_tags, vec FROM materials").fetchall()
    # chunk 最高分(单元内最相关片段)
    chunk_best: dict[str, float] = {}
    for eid, _i, _t, cv in c.execute("SELECT entity_id, chunk_idx, text, vec FROM chunks").fetchall():
        s = _cos(qv, json.loads(cv))
        if s > chunk_best.get(eid, -1):
            chunk_best[eid] = s
    rows = []
    for eid, fp, summ, tags_json, vec in mats:
        mt = json.loads(tags_json or "[]")
        if tags and not all(t in mt for t in tags):  # meta 硬过滤: 要求含全部指定 tag
            continue
        unit_s = _cos(qv, json.loads(vec))
        ch_s = chunk_best.get(eid, 0.0)
        score = 0.5 * unit_s + 0.5 * ch_s  # 重排: 整单元 + 最佳 chunk 各半
        rows.append({"entity_id": eid, "file_path": fp, "summary": summ,
                     "semantic_tags": mt, "unit": round(unit_s, 4),
                     "chunk": round(ch_s, 4), "score": round(score, 4)})
    c.close()
    rows.sort(key=lambda r: -r["score"])
    return rows[:top_k]


# ── 自语料 benchmark(选模型用) ─────────────────────────────────────────
def benchmark(pairs: list[dict], *, models: list[str] | None = None,
              entity_ids: list[str] | None = None, top_k: int = 3,
              root: Path | None = None, echo: Any = None) -> dict[str, Any]:
    """pairs=[{query, expect_entity}]。对每个候选模型测 recall@k(命中=expect 在 top_k)。给证据列表不打分。

    entity_ids: 只在这组语料上重建索引测(不传则全量, 慎用)。
    """
    base = root or omni_workspace_root()
    models = models or [_DEFAULT_EMBED_MODEL]
    results = {}
    for m in models:
        build_index(model=m, entity_ids=entity_ids, rebuild=True, root=base)  # 用该模型在指定语料上重建后测
        hits = []
        for pr in pairs:
            got = search(pr["query"], top_k=top_k, model=m, root=base)
            ids = [g["entity_id"] for g in got]
            ok = any(pr["expect_entity"] in g for g in ids)
            hits.append({"query": pr["query"], "expect": pr["expect_entity"],
                         "hit": ok, "top": ids[:top_k]})
        recall = sum(1 for h in hits if h["hit"])
        results[m] = {"recall_at_k": f"{recall}/{len(pairs)}", "evidence": hits}
        if echo:
            echo(f"  [{m}] recall@{top_k} = {recall}/{len(pairs)}")
    return {"top_k": top_k, "models": results}
