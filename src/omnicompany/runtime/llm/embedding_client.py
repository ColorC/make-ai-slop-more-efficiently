# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:43Z
# [OMNI] material_id="material:runtime.llm.embedding_client.bge_adapter.implementation.py"
import logging
import math
from typing import List
import asyncio

import httpx

logger = logging.getLogger(__name__)

class TextEmbeddingClient:
    """提供和 WSL 内部 Graph-RAG (BGE 模型) 通信的嵌入计算客户端"""

    def __init__(self, endpoint_url: str = "http://localhost:8000/api/embeddings"):
        self.endpoint_url = endpoint_url

    async def get_embedding(self, text: str) -> List[float]:
        """异步调用 GraphRAG 取回 BGE embedding，带有简单的重试机制"""
        if not text.strip():
            return []

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        self.endpoint_url,
                        json={"input": text}
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    # 兼容返回结构 {"data": [{"embedding": [...]}]}
                    if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
                        return data["data"][0].get("embedding", [])
                    
                    logger.warning(f"Unexpected embedding response format: {data}")
                    return []
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"Embedding API attempt {attempt+1} failed ({e}), waiting {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Failed to fetch embedding from {self.endpoint_url}: {e}")
                    raise

    def cosine_sim(self, v1: List[float], v2: List[float]) -> float:
        """纯数学算子：计算夹角余弦相似度"""
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
            
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm_v1 = math.sqrt(sum(a * a for a in v1))
        norm_v2 = math.sqrt(sum(b * b for b in v2))
        
        if norm_v1 == 0.0 or norm_v2 == 0.0:
            return 0.0
            
        # 防止浮点精度溢出
        return max(-1.0, min(1.0, dot_product / (norm_v1 * norm_v2)))

# 暴露单例或者可直接用的方式
_client_instance = None

def get_embedding_client() -> TextEmbeddingClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = TextEmbeddingClient()
    return _client_instance
