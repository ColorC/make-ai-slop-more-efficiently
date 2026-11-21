# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.nodes.route_retrieve.boltzmann_select.semantic_classify.specialized_dispatch.engine.py"
"""路由与分发节点 — 语义分类、玻尔兹曼选路、检索、专用分发

从 semantic.py 拆分。包含系统中最复杂的路由逻辑。
"""

from __future__ import annotations

import logging
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router
from omnicompany.runtime.storage.db_access import open_db_rw

logger = logging.getLogger(__name__)


class RouteRetrieveRouter(Router):
    """路由检索节点 — 从历史路由图中检索相关路径。

    DAG 上下文：基于 tool_calls 和 intents 检索历史路由。
    """

    INPUT_KEYS = ["tool_calls"]

    def __init__(self, route_db_path: str | None = None):
        self.route_db_path = route_db_path

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        from pathlib import Path
        empty_out = {
            **input_data,
            "route_hints": "",
            "candidate_count": 0,
            "route_candidates": [],
        }

        if not self.route_db_path or not Path(self.route_db_path).exists():
            return Verdict(kind=VerdictKind.PASS, output=empty_out)

        try:
            from omnicompany.runtime.routing.route_retriever import RouteRetriever, format_route_hints

            retriever = RouteRetriever(self.route_db_path)

            # 用任务描述（而非 tool_names）做语义检索
            task_text = input_data.get("task", "")
            if not task_text:
                msgs = input_data.get("messages", [])
                for m in reversed(msgs):
                    if m.get("role") == "user":
                        task_text = m.get("content", "")[:500]
                        break
            if not task_text:
                tool_names = [tc.get("tool_name", "") for tc in input_data.get("tool_calls", [])]
                task_text = " ".join(tool_names)

            candidates = await retriever.retrieve(task_text)
            hints = format_route_hints(candidates) if candidates else ""

            structured = []
            for c in candidates:
                structured.append({
                    "steps": c.steps,
                    "node_ids": c.node_ids,
                    "total_weight": c.total_weight,
                    "similarity": c.similarity,
                })

            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "route_hints": hints,
                    "candidate_count": len(candidates),
                    "route_candidates": structured,
                },
            )
        except Exception as e:
            logger.debug("Route retrieval failed: %s", e)
            return Verdict(kind=VerdictKind.PASS, output=empty_out)


class BoltzmannSelectRouter(Router):
    """玻尔兹曼路径选择 — P(c) = exp(-β·P_c)·S_c / Σ exp(-β·P_i)·S_i

    从 route_retrieve 输出的结构化候选中，用 Boltzmann 分布做概率选路。
    选中的路由信息传递给下游（tool_dispatch/pain_classify），用于：
    1. 记录本次执行"走了哪条路"（供 pain 归因）
    2. 更新 route_graph 中对应节点的 pain/success（反馈闭环）
    """

    INPUT_KEYS = ["tool_calls"]

    def __init__(self, route_graph: Any = None, beta: float = 2.0):
        self._route_graph = route_graph
        self._beta = beta

    def _build_candidates(self, route_candidates: list[dict]) -> list:
        """从结构化候选路由构建 RouteCandidate 列表。

        C2 fix: 使用路径聚合 pain — 遍历路径中所有 node_ids，
        按深度衰减 (gamma=0.5) 累加 pain_score，反映整条路径的健康度。
        """
        from omnicompany.runtime.routing.boltzmann_router import RouteCandidate

        GAMMA = 0.5

        if self._route_graph is None:
            return [
                RouteCandidate(
                    node_id=rc["node_ids"][0] if rc.get("node_ids") else f"route_{i}",
                    pain_score=0.0,
                    success_rate=rc.get("similarity", 0.5),
                    hit_count=rc.get("total_weight", 1),
                )
                for i, rc in enumerate(route_candidates) if rc.get("node_ids")
            ]

        candidates = []
        for i, rc in enumerate(route_candidates):
            nids = rc.get("node_ids", [])
            if not nids:
                continue
            primary_nid = nids[0]
            try:
                primary_node = self._route_graph.get_node(primary_nid)
                if primary_node and (primary_node.hard_eliminated or primary_node.deprecated):
                    continue

                path_pain = 0.0
                path_success = 0.0
                total_hit = 0
                valid_count = 0
                for depth, nid in enumerate(nids):
                    node = self._route_graph.get_node(nid)
                    if node is None:
                        continue
                    decay = GAMMA ** depth
                    node_pain = float(node.pain_score) if node.pain_score is not None else 0.0
                    node_sr = float(node.success_rate) if node.success_rate is not None else -1.0
                    node_hc = int(node.hit_count) if node.hit_count is not None else 0
                    path_pain += node_pain * decay
                    if node_sr >= 0:
                        path_success += node_sr * decay
                        valid_count += 1
                    total_hit += node_hc

                avg_success = (path_success / valid_count) if valid_count > 0 else 0.5

                candidates.append(RouteCandidate(
                    node_id=primary_nid,
                    pain_score=path_pain,
                    success_rate=avg_success,
                    hit_count=total_hit if total_hit > 0 else 1,
                    deprecated=primary_node.deprecated if primary_node else False,
                    hard_eliminated=primary_node.hard_eliminated if primary_node else False,
                    embedding_sim=rc.get("similarity", 0.0),
                ))
            except Exception:
                candidates.append(RouteCandidate(
                    node_id=primary_nid,
                    pain_score=0.0,
                    success_rate=rc.get("similarity", 0.5),
                    hit_count=rc.get("total_weight", 1),
                ))
        return candidates

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        route_candidates = input_data.get("route_candidates", [])

        if not route_candidates:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "boltzmann_beta": self._beta,
                    "route_selected": False,
                    "selected_route": None,
                    "selection_probabilities": [],
                },
            )

        from omnicompany.runtime.routing.boltzmann_router import BoltzmannRouter

        candidates = self._build_candidates(route_candidates)
        if not candidates:
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "boltzmann_beta": self._beta,
                    "route_selected": False,
                    "selected_route": None,
                    "selection_probabilities": [],
                },
            )

        router = BoltzmannRouter(beta=self._beta)
        selected = router.select(candidates)
        probabilities = router.compute_probabilities(candidates)

        prob_details = [
            {"node_id": c.node_id, "pain": c.pain_score,
             "success": c.success_rate, "prob": round(p, 4)}
            for c, p in zip(candidates, probabilities)
        ]

        selected_info = None
        if selected:
            matching = [rc for rc in route_candidates
                        if rc.get("node_ids") and rc["node_ids"][0] == selected.node_id]
            if matching:
                selected_info = {
                    "node_id": selected.node_id,
                    "steps": matching[0].get("steps", []),
                    "pain_score": selected.pain_score,
                    "success_rate": selected.success_rate,
                }

        selected_node_id = selected_info["node_id"] if selected_info else ""
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "boltzmann_beta": self._beta,
                "route_selected": selected is not None,
                "selected_route": selected_info,
                "selected_route_node_id": selected_node_id,
                "selection_probabilities": prob_details,
            },
        )


class SemanticTypeClassifierRouter(Router):
    """语义类型分类节点 — 用类型驱动替代规则驱动的上下文增强。

    Uses DB-stored semantic types (via RouteGraph) for classification.
    Also checks if a viable soft node path exists for the matched type.

    分类策略:
        1. keyword 快速匹配（零 LLM 开销）via legacy registry
        2. DB-based type lookup + LLM classification as fallback
        3. If matched, check for soft node path availability

    输出:
        PASS: semantic_type_id matched + optional soft_node_path
        FAIL: no type matched → fall back to agent_loop
    """

    INPUT_KEYS = ["system_prompt"]

    def __init__(self, registry: Any = None, llm_client: Any = None, route_graph: Any = None):
        self._registry = registry
        self._llm_client = llm_client
        self._route_graph = route_graph

    # Reliability threshold: below this, agent_loop paves the road first
    RELIABILITY_THRESHOLD = 0.05

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.PASS, output=input_data)

        context = _build_classification_context(input_data)

        # Extract destination types from parsed intent (dynamic, not hardcoded)
        parsed_intent = input_data.get("parsed_intent") or {}
        desired_output_types = []
        if isinstance(parsed_intent, dict):
            desired_output_types = parsed_intent.get("desired_output_types", [])

        # ── Legacy path: in-memory registry (keyword match, zero DB) ──
        if self._registry is not None:
            matched = self._registry.classify(context)
            if matched is None and self._llm_client is not None:
                matched = self._registry.classify_with_llm(context, self._llm_client)
            if matched is not None:
                enriched_prompt = input_data.get("system_prompt", "")
                if matched.handler_guidance:
                    enriched_prompt += f"\n\n## Context Type: {matched.type_id}\n{matched.handler_guidance}\n"
                return Verdict(kind=VerdictKind.PASS, output={
                    **input_data,
                    "system_prompt": enriched_prompt,
                    "semantic_type_id": matched.type_id,
                    "semantic_type_guidance": matched.handler_guidance,
                    "soft_node_path": None,
                    "path_reliability": 0.5,
                })

        # ── Phase 1: Find matching semantic types with match scores ──
        type_matches = self._score_all_types(context)

        if not type_matches:
            return self._fail_to_agent_loop(input_data, "No semantic types in DB")

        # ── Phase 2: For top candidates, find reliable paths to destination ──
        viable_routes = []  # [(overall_reliability, match_score, type_info, path, reached_dest)]
        MAX_PATH_SEARCHES = 5
        has_destination = bool(desired_output_types)

        for match_score, type_info in type_matches[:MAX_PATH_SEARCHES]:
            tid = type_info["type_id"]
            to_types = desired_output_types if desired_output_types else None
            path = self._route_graph.find_reliable_path(
                from_types=[tid, "user_request"],
                to_types=to_types,
            ) if self._route_graph else None

            if path:
                path_stability = self._route_graph.score_path_reliability(path)
                overall = match_score * path_stability

                # Check if path actually reaches destination types
                path_output_types = set()
                for node in path:
                    path_output_types.update(node.output_types)

                if has_destination:
                    # Exact match first, then prefix match (e.g. fs.path.* ≈ fs.path.*)
                    exact_match = bool(set(desired_output_types) & path_output_types)
                    prefix_match = False
                    if not exact_match:
                        for dest in desired_output_types:
                            dest_prefix = ".".join(dest.split(".")[:2])
                            for out in path_output_types:
                                if ".".join(out.split(".")[:2]) == dest_prefix:
                                    prefix_match = True
                                    break
                            if prefix_match:
                                break
                    reached_dest = exact_match or prefix_match
                else:
                    # No destination specified → any path is acceptable
                    reached_dest = True
                viable_routes.append(
                    (overall, match_score, type_info, path, reached_dest)
                )

        # Prioritize routes that reach destination
        full_routes = [r for r in viable_routes if r[4]]
        partial_routes = [r for r in viable_routes if not r[4]]

        best_route = self._select_route_with_exploration(full_routes) if full_routes else None

        # If we have a complete path to destination → DAG direct (PASS)
        if best_route and best_route[0] >= self.RELIABILITY_THRESHOLD:
            overall_rel, match_score, type_info, soft_path = best_route[:4]
            matched_type_id = type_info["type_id"]
            matched_guidance = type_info.get("handler_guidance", "")

            if self._route_graph:
                self._route_graph.record_type_hit(matched_type_id)
                self._route_graph.record_type_success(matched_type_id)  # full-path PASS = routing success
                # Accumulate this input as a new exemplar for the matched type
                exemplar_text = context[:200] if context else ""
                if exemplar_text:
                    self._route_graph.upsert_semantic_type(
                        type_id=matched_type_id,
                        description="",
                        exemplars=[exemplar_text],
                        source_channel="match_accumulation",
                    )

            enriched_prompt = input_data.get("system_prompt", "")
            if matched_guidance:
                enriched_prompt += f"\n\n## Context Type: {matched_type_id}\n{matched_guidance}\n"

            logger.info(
                "SemanticClassify PASS (full path): type=%s reliability=%.3f "
                "(match=%.2f × path_stab=%.2f) nodes=%d → DAG direct",
                matched_type_id, overall_rel, match_score,
                overall_rel / max(match_score, 0.001), len(soft_path),
            )

            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "system_prompt": enriched_prompt,
                    "semantic_type_id": matched_type_id,
                    "semantic_type_guidance": matched_guidance,
                    "soft_node_path": [n.node_id for n in soft_path] if soft_path else None,
                    "path_reliability": overall_rel,
                    "path_reached_destination": True,
                },
            )

        # No complete path → agent_loop explores (with partial context if available)
        best_partial = self._select_route_with_exploration(partial_routes) if partial_routes else None
        enriched_prompt = input_data.get("system_prompt", "")
        partial_type = ""
        partial_guidance = ""

        if best_partial:
            partial_type = best_partial[2]["type_id"]
            partial_guidance = best_partial[2].get("handler_guidance", "")
            if partial_guidance:
                enriched_prompt += f"\n\n## Partial Context: {partial_type}\n{partial_guidance}\n"
            if self._route_graph:
                self._route_graph.record_type_hit(partial_type)
                exemplar_text = context[:200] if context else ""
                if exemplar_text:
                    self._route_graph.upsert_semantic_type(
                        type_id=partial_type,
                        description="",
                        exemplars=[exemplar_text],
                        source_channel="match_accumulation",
                    )

        # Routing failure notice: tell agent to execute directly, NOT call register_semantic_types
        if has_destination:
            enriched_prompt += (
                "\n\n## Routing Notice\n"
                "Semantic routing could not find a complete path for this task. "
                "**Execute the task directly** using bash, str_replace_editor, think, and finish. "
                "Do NOT call register_semantic_types — routing failures are handled automatically by the system.\n"
            )

        diagnosis = (
            f"No complete path to destination {desired_output_types}. "
            f"Best partial: type={partial_type or 'none'}"
            if has_destination
            else "No destination types specified — agent_loop explores"
        )
        if best_route:
            diagnosis += (
                f" (reliability={best_route[0]:.3f} < threshold {self.RELIABILITY_THRESHOLD})"
            )
        logger.info("SemanticClassify FAIL → agent_loop: %s", diagnosis)

        return Verdict(
            kind=VerdictKind.FAIL,
            output={
                **input_data,
                "system_prompt": enriched_prompt,
                "semantic_type_id": partial_type,
                "semantic_type_guidance": partial_guidance,
                "soft_node_path": None,
                "path_reliability": 0.0,
                "path_reached_destination": False,
            },
            diagnosis=diagnosis,
        )

    def _fail_to_agent_loop(self, input_data: dict, diagnosis: str) -> Verdict:
        return Verdict(kind=VerdictKind.FAIL, output={
            **input_data,
            "semantic_type_id": "",
            "semantic_type_guidance": "",
            "soft_node_path": None,
            "path_reliability": 0.0,
        }, diagnosis="No semantic type matched — falling back to agent_loop probe")

    def _select_route_with_exploration(
        self, routes: list[tuple],
    ) -> tuple | None:
        """Select a route with exploration/exploitation balance."""
        if not routes:
            return None
        if len(routes) == 1:
            return routes[0]

        import random
        epsilon = 0.3

        if random.random() < epsilon:
            weights = []
            for route in routes:
                path = route[3] if len(route) > 3 else []
                total_hits = sum(n.hit_count for n in path) if path else 0
                weights.append(1.0 / (1 + total_hits))
            total_w = sum(weights)
            probs = [w / total_w for w in weights]
            chosen = random.choices(range(len(routes)), weights=probs, k=1)[0]
            return routes[chosen]
        else:
            return max(routes, key=lambda r: r[0])

    def _score_all_types(self, context: str) -> list[tuple[float, dict]]:
        """Two-stage scoring: embedding粗筛 → LLM精读详细描述+案例做精匹配."""
        if self._route_graph is None:
            return []

        import json as _json
        types = self._route_graph.all_semantic_types(active_only=True)
        if not types:
            return []

        # Stage 1: Embedding-based candidate narrowing (top 10)
        candidates = self._embedding_score_types(context, types)
        if not candidates:
            context_lower = context.lower()
            for t in types:
                score = self._keyword_match_score(t, context_lower, _json)
                if score > 0:
                    candidates.append((score, t))

        if not candidates:
            return []

        candidates.sort(key=lambda x: x[0], reverse=True)
        top_candidates = candidates[:10]

        # Stage 2: LLM precise matching with full descriptions + exemplars
        if self._llm_client and len(top_candidates) >= 1:
            refined = self._llm_refine_match(context, top_candidates)
            if refined:
                return refined

        return top_candidates[:5]

    _type_emb_cache: dict[str, list[float]] = {}
    _emb_cache_path: str | None = None

    @classmethod
    def _load_disk_cache(cls, route_graph: Any) -> None:
        """Load type embedding cache from disk (once per process)."""
        if cls._emb_cache_path is not None:
            return
        try:
            from pathlib import Path
            import json as _j
            if route_graph and hasattr(route_graph, "db_path"):
                p = Path(route_graph.db_path).parent / "type_embeddings_cache.json"
            else:
                # S3b.4 (2026-04-08): data/autonomous/ 已归档；type embedding cache
                # 改写到 data/ 根目录（这是数据缓存，不是事件库）
                p = Path("data/type_embeddings_cache.json")
            cls._emb_cache_path = str(p)
            if p.exists():
                cached = _j.loads(p.read_text(encoding="utf-8"))
                cls._type_emb_cache.update(cached)
                logger.info("Loaded %d type embeddings from disk cache", len(cached))
        except Exception as e:
            logger.debug("Failed to load embedding disk cache: %s", e)

    @classmethod
    def _save_disk_cache(cls) -> None:
        """Persist type embedding cache to disk."""
        if not cls._emb_cache_path:
            return
        try:
            import json as _j
            from pathlib import Path
            Path(cls._emb_cache_path).write_text(
                _j.dumps(cls._type_emb_cache, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _embedding_score_types(self, context: str, types: list[dict]) -> list[tuple[float, dict]]:
        """Score types using embedding cosine similarity (sync, cheap)."""
        self._load_disk_cache(self._route_graph)
        try:
            import httpx
            import math

            with httpx.Client(timeout=10.0) as client:
                ctx_resp = client.post(
                    "http://localhost:8000/api/embeddings",
                    json={"input": context[:500]},
                )
                ctx_resp.raise_for_status()
                ctx_data = ctx_resp.json()
                ctx_emb = ctx_data["data"][0]["embedding"] if ctx_data.get("data") else None
                if not ctx_emb:
                    return []

                uncached = [(i, t) for i, t in enumerate(types) if t.get("type_id", "") not in self._type_emb_cache]
                if uncached:
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    def _embed_one(t: dict) -> tuple[str, list[float] | None]:
                        desc = f"{t.get('type_id', '')}: {t.get('description', '')}"[:300]
                        try:
                            import httpx as _hx
                            r = _hx.post("http://localhost:8000/api/embeddings", json={"input": desc}, timeout=8.0)
                            r.raise_for_status()
                            d = r.json()
                            emb = d["data"][0]["embedding"] if d.get("data") else None
                            return t["type_id"], emb
                        except Exception:
                            return t["type_id"], None

                    with ThreadPoolExecutor(max_workers=8) as pool:
                        futs = {pool.submit(_embed_one, t): t for _, t in uncached}
                        for f in as_completed(futs):
                            tid, emb = f.result()
                            if emb:
                                self._type_emb_cache[tid] = emb

                    self._save_disk_cache()
                    logger.info("Embedded %d new types (total cached: %d)", len(uncached), len(self._type_emb_cache))

                results = []
                norm_a = math.sqrt(sum(a * a for a in ctx_emb))
                for t in types:
                    tid = t.get("type_id", "")
                    t_emb = self._type_emb_cache.get(tid)
                    if not t_emb:
                        continue
                    dot = sum(a * b for a, b in zip(ctx_emb, t_emb))
                    norm_b = math.sqrt(sum(b * b for b in t_emb))
                    sim = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
                    if sim > 0.4:
                        results.append((sim, t))

                return results
        except Exception as e:
            logger.debug("Embedding scoring failed: %s", e)
            return []

    def _keyword_match_score(self, t: dict, context_lower: str, _json: Any) -> float:
        """Normalized keyword match score in [0, 1]."""
        tid = t.get("type_id", "")
        desc = str(t.get("description", "")).lower()

        segments = [s for s in tid.replace(".", " ").replace("_", " ").split() if len(s) >= 3]
        seg_hits = sum(1 for s in segments if s.lower() in context_lower)
        seg_score = seg_hits / max(len(segments), 1)

        keywords = _json.loads(t.get("keywords", "[]")) if isinstance(t.get("keywords"), str) else t.get("keywords", [])
        kw_hits = sum(1 for kw in keywords if kw.lower() in context_lower)
        kw_score = kw_hits / max(len(keywords), 1)

        desc_words = [w for w in desc.split() if len(w) >= 4]
        desc_hits = sum(1 for w in desc_words if w in context_lower)
        desc_score = desc_hits / max(len(desc_words), 1)

        score = (seg_score + kw_score + desc_score) / 3.0
        return min(score, 1.0)

    def _llm_refine_match(
        self, context: str, candidates: list[tuple[float, dict]]
    ) -> list[tuple[float, dict]] | None:
        """Stage 2: LLM reads full type descriptions + exemplars for precise matching."""
        import json as _json

        type_details = []
        for i, (emb_score, t) in enumerate(candidates[:8]):
            exemplars = t.get("exemplars", "[]")
            if isinstance(exemplars, str):
                try:
                    exemplars = _json.loads(exemplars)
                except (ValueError, TypeError):
                    exemplars = []
            ex_text = "; ".join(str(e)[:100] for e in exemplars[:5]) if exemplars else "(no examples)"
            type_details.append(
                f"{i+1}. TYPE: {t['type_id']}\n"
                f"   DESC: {t.get('description', '')}\n"
                f"   KEYWORDS: {t.get('keywords', '[]')}\n"
                f"   EXAMPLES: {ex_text}\n"
                f"   GUIDANCE: {t.get('handler_guidance', '')[:150]}"
            )

        prompt = (
            f"You are a semantic type matcher. Given an input context and candidate types, "
            f"evaluate how well each type matches the input.\n\n"
            f"## Input Context:\n{context[:1500]}\n\n"
            f"## Candidate Types:\n" + "\n".join(type_details) + "\n\n"
            "For EACH candidate, assess: Does this input belong to this semantic type?\n"
            "Consider the type's description, keywords, AND examples.\n"
            "A type matches if the input could be an instance of that semantic region.\n\n"
            "Respond in JSON array, sorted by match quality:\n"
            '[{"type_id": "...", "score": 0.0-1.0, "reason": "brief reason"}]'
        )

        try:
            response = self._llm_client.call(
                messages=[{"role": "user", "content": prompt}],
                system="Semantic matcher. Return JSON array only. Score 0-1.",
            )
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
            text = text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [l for l in lines if not l.startswith("```")]
                text = "\n".join(lines).strip()

            matches = _json.loads(text)
            if not isinstance(matches, list):
                return None

            type_lookup = {t["type_id"]: t for _, t in candidates}
            refined = []
            for m in matches:
                tid = m.get("type_id", "")
                score = float(m.get("score", 0))
                if tid in type_lookup and score > 0.1:
                    refined.append((score, type_lookup[tid]))

            refined.sort(key=lambda x: x[0], reverse=True)
            if refined:
                logger.info(
                    "LLM refined match: top=%s (%.2f), %d candidates scored",
                    refined[0][1]["type_id"], refined[0][0], len(refined),
                )
                return refined[:5]

        except Exception as e:
            logger.debug("LLM refine match failed: %s", e)

        return None

    def _classify_from_db(self, context: str) -> dict | None:
        """Classify using DB-stored semantic types."""
        if self._route_graph is None:
            return None

        import json as _json
        types = self._route_graph.all_semantic_types(active_only=True)
        if not types:
            return None

        context_lower = context.lower()
        best = None
        best_score = 0.0

        for t in types:
            score = 0.0
            tid = t.get("type_id", "")
            desc = str(t.get("description", "")).lower()

            for seg in tid.replace(".", " ").replace("_", " ").split():
                if len(seg) >= 3 and seg.lower() in context_lower:
                    score += 0.5

            keywords = _json.loads(t.get("keywords", "[]")) if isinstance(t.get("keywords"), str) else t.get("keywords", [])
            for kw in keywords:
                if kw.lower() in context_lower:
                    score += 1.0

            for word in desc.split():
                if len(word) >= 4 and word in context_lower:
                    score += 0.2

            if score > best_score:
                best_score = score
                best = t

        logger.debug("Classify scores: best=%s score=%.2f (types=%d)",
                     best.get("type_id", "") if best else "none", best_score, len(types))

        if best and best_score >= 0.3:
            return best

        if self._llm_client and types:
            llm_result = self._llm_classify_db(context, types)
            if llm_result:
                logger.info("LLM classified: %s", llm_result.get("type_id", ""))
            return llm_result

        return None

    def _llm_classify_db(self, context: str, types: list[dict]) -> dict | None:
        """LLM classification against DB-stored types."""
        type_desc = "\n".join(
            f"- {t['type_id']}: {t.get('description', '')}" for t in types[:30]
        )
        prompt = (
            f"Classify this context into ONE semantic type. Respond with ONLY the type_id or NONE.\n\n"
            f"## Types:\n{type_desc}\n\n"
            f"## Context:\n{context[:2000]}"
        )
        try:
            response = self._llm_client.call(
                messages=[{"role": "user", "content": prompt}],
                system="Semantic classifier. Respond with only the type_id or NONE.",
            )
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
            text = text.strip().strip("'\"")
            if text == "NONE" or not text:
                return None
            for t in types:
                if t["type_id"] == text or t["type_id"] in text:
                    return t
        except Exception:
            pass
        return None


class SpecializedDispatchRouter(Router):
    """根据 semantic_type_id 和 soft_node_path 分发任务。

    核心原则：如果 DAG 能通过 soft nodes 直达目标类型，就直接执行，
    不进 agent_loop。只有路径不完整或执行失败时才回退到 agent_loop。
    """

    INPUT_KEYS: list[str] = []

    def __init__(
        self,
        registry: Any = None,
        pipelines: dict[str, Any] | None = None,
        route_graph: Any = None,
        llm_client: Any = None,
    ):
        self._registry = registry
        self._pipelines = pipelines or {}
        self._route_graph = route_graph
        self._llm_client = llm_client

    async def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, output=input_data)

        type_id = input_data.get("semantic_type_id", "")
        soft_path_ids = input_data.get("soft_node_path")

        if not type_id:
            return Verdict(
                kind=VerdictKind.FAIL,
                output={**input_data, "dispatch_action": "fallback_no_type"},
                diagnosis="No semantic type to dispatch",
            )

        # Strategy 1: Execute soft node path via SoftNodeExecutor
        if soft_path_ids and self._route_graph and self._llm_client:
            nodes = []
            for nid in soft_path_ids:
                n = self._route_graph.get_semantic_node(nid)
                if n:
                    nodes.append(n)
            if nodes:
                logger.info(
                    "Executing soft node path: %s (%d nodes)",
                    type_id, len(nodes),
                )
                from omnicompany.runtime.routing.soft_node_executor import SoftNodeExecutor
                executor = SoftNodeExecutor(
                    llm_client=self._llm_client,
                    route_graph=self._route_graph,
                )
                result = await executor.execute_path(
                    path=nodes,
                    initial_input={
                        "user_input": input_data.get("user_input", ""),
                        "semantic_type_id": type_id,
                        "parsed_intent": input_data.get("parsed_intent", {}),
                    },
                )
                if result.success:
                    logger.info(
                        "Soft node path completed: %s (%d tokens)",
                        type_id, result.tokens_used,
                    )
                    return Verdict(
                        kind=VerdictKind.PASS,
                        output={
                            **input_data,
                            "dispatch_action": "soft_node_path_executed",
                            "dispatch_type_id": type_id,
                            "soft_node_output": result.output,
                            "soft_node_tokens": result.tokens_used,
                        },
                    )
                else:
                    logger.warning(
                        "Soft node path failed at %s: %s — falling back to agent_loop",
                        result.node_id[:12], result.error,
                    )
                    return Verdict(
                        kind=VerdictKind.FAIL,
                        output={
                            **input_data,
                            "dispatch_action": "soft_node_path_failed",
                            "dispatch_type_id": type_id,
                            "soft_node_error": result.error,
                        },
                        diagnosis=f"Soft node path failed: {result.error}",
                    )

        # Strategy 2: legacy pipeline
        pipeline = self._pipelines.get(type_id)
        if pipeline is not None:
            guidance = input_data.get("semantic_type_guidance", "")
            logger.info("Dispatching to legacy pipeline for type: %s", type_id)
            return Verdict(
                kind=VerdictKind.PASS,
                output={
                    **input_data,
                    "dispatch_action": "specialized_pipeline",
                    "dispatch_type_id": type_id,
                    "dispatch_guidance": guidance,
                    "pipeline_ref": type_id,
                },
            )

        # No path available → agent_loop explores
        logger.debug("No soft path or pipeline for type %s → agent_loop", type_id)
        return Verdict(
            kind=VerdictKind.FAIL,
            output={
                **input_data,
                "dispatch_action": "fallback_no_pipeline",
                "dispatch_type_id": type_id,
            },
            diagnosis=f"No soft node path or pipeline for semantic type '{type_id}'",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

def _build_classification_context(input_data: dict) -> str:
    """从 input_data 构建用于类型分类的上下文文本。"""
    parts = []

    for key in ("task", "user_input", "user_request"):
        val = input_data.get(key, "")
        if val:
            parts.append(str(val)[:500])

    parsed = input_data.get("parsed_intent") or {}
    if isinstance(parsed, dict):
        goals = parsed.get("goals", [])
        for g in goals[:3]:
            if isinstance(g, dict):
                parts.append(g.get("desc", "")[:200])
        for ot in parsed.get("desired_output_types", []):
            parts.append(str(ot))

    messages = input_data.get("messages", [])
    for m in messages[-3:]:
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(content[:300])
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", "")[:300])

    intents = input_data.get("intents", [])
    for intent in intents:
        if isinstance(intent, dict):
            parts.append(intent.get("action_type", ""))

    return " ".join(parts)[:3000]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2.5：路由核心节点注册（meta-evolvable）
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_routing_nodes_registered(db_path: str) -> None:
    """Phase 2.5: 将路由核心决策节点注册到 semantic_nodes，使其对进化系统可见。

    INSERT OR IGNORE 幂等，每次 build_runtime_bindings 调用安全。
    注册后各 Router 在初始化时从 DB 读取 processing_prompt 覆盖默认 system prompt。
    """
    import json as _json
    import time as _time

    if not db_path:
        return
    _NODES = [
        {
            "node_id": "routing.task_intent",
            "impl_kind": "soft",
            "description": "任务意图解析：将用户请求解析为结构化意图（provided_info / desired_output_types / goals）",
            "processing_prompt": (
                "解析用户请求为结构化意图。"
                "提取用户已提供的信息（provided_info）、期望输出类型（desired_output_types）和目标列表（goals）。"
                "输出 JSON：{provided_info, desired_output_types, goals[{desc, depends_on, output_type}]}。"
            ),
            "input_types": ["user_request"],
            "output_types": ["task_intent"],
        },
        {
            "node_id": "routing.boltzmann_select",
            "impl_kind": "hard",
            "description": "路由选择：用玻尔兹曼分布从候选节点中概率选路（beta=温度参数，beta大→确定性强）",
            "processing_prompt": (
                "路由选择策略：对候选节点用玻尔兹曼分布选路。"
                "高 pain_score 节点获得低选择概率，高 success_rate 节点获得高选择概率。"
                "beta 参数控制随机性：beta 大时选择更确定，beta 小时探索性更强。"
                "当 beta=2.0 时系统倾向于选最优路径；beta=0.5 时探索性更强。"
            ),
            "input_types": ["route_candidates"],
            "output_types": ["selected_route"],
        },
        {
            "node_id": "routing.semantic_classify",
            "impl_kind": "soft",
            "description": "语义类型分类：将任务/意图映射到语义类型节点（embedding 相似度 + LLM 验证）",
            "processing_prompt": (
                "语义类型分类：将任务描述映射到最匹配的语义节点。"
                "先用 embedding 相似度召回候选，再用 LLM 验证最佳匹配。"
                "输出 node_id + confidence_score。"
                "当没有高置信度匹配时（score < 0.5），返回 routing_gap_signal 触发盲区处理。"
            ),
            "input_types": ["task_intent"],
            "output_types": ["semantic_node_id"],
        },
        {
            "node_id": "routing.meta_evolution",
            "impl_kind": "hard",
            "description": "元进化判断：当进化系统自身的痛觉持续高位时，调整元进化参数（温度/阈值/策略）",
            "processing_prompt": (
                "元进化判断：评估进化系统自身的健康状态。"
                "当 evo_pain > meta_threshold（默认0.7）连续 N 轮时，触发元进化干预。"
                "可调整的参数：boltzmann.beta（路由探索性）、repair.threshold（修复阈值）、"
                "evolution.force_evolve_threshold（强制进化阈值）。"
                "输出 action: adjust_param / deprioritize_type / no_action。"
            ),
            "input_types": ["evo_pain_history"],
            "output_types": ["meta_evo_action"],
        },
        {
            "node_id": "routing.crystallize",
            "impl_kind": "hard",
            "description": "结晶检测：判断高命中低熵的节点是否应该结晶为 crystallized 状态",
            "processing_prompt": (
                "结晶检测：对高命中率（>10次）且低熵（<0.3）的节点判断是否应结晶。"
                "结晶节点进入 crystallized 状态，不再接受 prompt 修改，仅接受 create_new_version 进化。"
                "触发条件：hit_count >= 10 AND entropy < 0.3 AND success_rate > 0.7 连续 5 轮稳定。"
            ),
            "input_types": ["node_stats"],
            "output_types": ["crystallization_verdict"],
        },
    ]
    try:
        conn = open_db_rw(db_path)
        for nd in _NODES:
            conn.execute(
                "INSERT OR IGNORE INTO semantic_nodes "
                "(node_id, description, impl_kind, processing_prompt, "
                " input_types, output_types, maturity, active, source_channel, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    nd["node_id"],
                    nd["description"],
                    nd["impl_kind"],
                    nd["processing_prompt"],
                    _json.dumps(nd["input_types"], ensure_ascii=False),
                    _json.dumps(nd["output_types"], ensure_ascii=False),
                    "mature",
                    1,
                    "routing_phase25_registration",
                    _time.time(),
                ),
            )
        conn.commit()
        conn.close()
        logger.info("Phase 2.5: Registered %d routing nodes in semantic_nodes", len(_NODES))
    except Exception as e:
        logger.debug("_ensure_routing_nodes_registered failed: %s", e)
