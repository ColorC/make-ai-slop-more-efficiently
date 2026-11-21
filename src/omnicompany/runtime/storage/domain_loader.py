# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:runtime.storage.domain_node_loader.ingestion.py"
"""私域节点加载器：扫描 domain 目录下的 YAML 节点定义，upsert 到 semantic_nodes。

用法：
    from omnicompany.runtime.storage.domain_loader import load_domain
    load_domain("config/domains/local", db_path="data/private_domain_nodes.db")

    # 或批量加载所有激活的 domain
    load_all_domains(config_path="config/domains.yaml", db_path=...)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _parse_yaml(path: Path) -> dict:
    if _HAS_YAML:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    # 极简 YAML 回退：只支持 key: value 和列表（够用于节点定义）
    result: dict = {}
    current_key = None
    current_list: list | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("  - "):
            if current_list is not None:
                item = line.strip()[2:].strip()
                if item.startswith("{"):
                    try:
                        current_list.append(json.loads(item))
                    except Exception:
                        current_list.append(item)
                elif ":" in item:
                    k, _, v = item.partition(":")
                    current_list.append({k.strip(): v.strip()})
                else:
                    current_list.append(item)
        elif ":" in line and not line.startswith(" "):
            if current_list is not None and current_key:
                result[current_key] = current_list
            k, _, v = line.partition(":")
            current_key = k.strip()
            v = v.strip()
            if v == "":
                current_list = []
                result[current_key] = current_list
            else:
                current_list = None
                if v.lower() == "true":
                    result[current_key] = True
                elif v.lower() == "false":
                    result[current_key] = False
                else:
                    result[current_key] = v.strip('"').strip("'")
        elif line.startswith("  ") and current_key and not line.startswith("   "):
            # multiline string value (processing_prompt etc)
            if isinstance(result.get(current_key), str):
                result[current_key] += "\n" + line.strip()
    return result


def _types_to_json(types: Any) -> str:
    """将 YAML 里的 types 字段序列化为 JSON 字符串。"""
    if types is None:
        return "[]"
    if isinstance(types, str):
        return types
    normalized = []
    for t in types:
        if isinstance(t, str):
            normalized.append({"type_id": t})
        elif isinstance(t, dict):
            normalized.append(t)
        else:
            normalized.append({"type_id": str(t)})
    return json.dumps(normalized, ensure_ascii=False)


_SEMANTIC_NODES_SCHEMA = """\
CREATE TABLE IF NOT EXISTS semantic_nodes (
    node_id            TEXT PRIMARY KEY,
    description        TEXT,
    processing_prompt  TEXT,
    impl_kind          TEXT DEFAULT 'soft',
    input_types        TEXT,
    output_types       TEXT,
    source_channel     TEXT DEFAULT '',
    active             INTEGER DEFAULT 1,
    created_at         REAL,
    pain_score         REAL DEFAULT 0
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """确保 semantic_nodes 表存在。

    历史上这张表由 _graveyard/runtime/route_graph.py 创建，依赖
    data/autonomous/semantic_network.db 这个特定文件。S3b.4 (2026-04-08)
    起 domain_loader 自带 schema，可以写到任何 db_path 而不依赖 graveyard。
    """
    conn.execute(_SEMANTIC_NODES_SCHEMA)
    conn.commit()


def _upsert_node(conn: sqlite3.Connection, defn: dict, source_channel: str = "") -> str:
    """将一个节点定义 upsert 到 semantic_nodes，返回 node_id。"""
    node_id = defn["node_id"]
    now = time.time()

    # 检查节点是否已存在
    existing = conn.execute(
        "SELECT node_id FROM semantic_nodes WHERE node_id=?",
        (node_id,)
    ).fetchone()

    input_types = _types_to_json(defn.get("input_types"))
    output_types = _types_to_json(defn.get("output_types"))
    impl_kind = defn.get("impl_kind", "soft")
    description = defn.get("description", "")
    processing_prompt = defn.get("processing_prompt", None)
    tool_ref = defn.get("tool_ref", None)

    if existing:
        # 已存在：只更新描述、prompt、类型声明
        conn.execute("""
            UPDATE semantic_nodes SET
                description=?, processing_prompt=?, impl_kind=?,
                input_types=?, output_types=?, active=1
            WHERE node_id=?
        """, (description, processing_prompt, impl_kind,
              input_types, output_types, node_id))
        logger.debug("domain_loader: updated node %s", node_id)
    else:
        conn.execute("""
            INSERT INTO semantic_nodes
                (node_id, description, processing_prompt, impl_kind,
                 input_types, output_types, active, created_at)
            VALUES (?,?,?,?,?,?,1,?)
        """, (node_id, description, processing_prompt, impl_kind,
              input_types, output_types, now))
        logger.info("domain_loader: registered new node %s", node_id)

    return node_id


def load_domain(domain_dir: str | Path, db_path: str | Path) -> list[str]:
    """加载单个 domain 目录下的所有节点定义。

    返回成功加载的 node_id 列表。
    """
    domain_dir = Path(domain_dir)
    domain_yaml = domain_dir / "domain.yaml"
    if not domain_yaml.exists():
        raise FileNotFoundError(f"domain.yaml not found in {domain_dir}")

    domain_def = _parse_yaml(domain_yaml)
    if not domain_def.get("active", True):
        logger.info("domain_loader: domain %s is inactive, skipping", domain_dir.name)
        return []

    source_channel = domain_def.get("source_channel", f"private:{domain_dir.name}")
    node_dir = domain_dir / domain_def.get("node_dir", "nodes/")

    # 确保父目录存在
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)

    loaded: list[str] = []
    for yaml_file in sorted(node_dir.glob("*.yaml")):
        try:
            defn = _parse_yaml(yaml_file)
            if not defn.get("node_id"):
                logger.warning("domain_loader: %s missing node_id, skipping", yaml_file.name)
                continue
            nid = _upsert_node(conn, defn, source_channel)
            loaded.append(nid)
        except Exception as e:
            logger.error("domain_loader: failed to load %s: %s", yaml_file.name, e)

    conn.commit()
    conn.close()
    logger.info("domain_loader: loaded %d nodes from domain '%s'", len(loaded), domain_dir.name)
    return loaded


def load_domains_from_env(db_path: str | Path) -> dict[str, list[str]]:
    """从环境变量加载私域节点，供 dashboard / CLI 启动时调用。

    环境变量：
      OMNI_DOMAINS   逗号分隔的 domain 目录路径，每个目录下须有 domain.yaml
                     例：OMNI_DOMAINS=/home/user/my-domain,/opt/company-domain
                     不设置则静默跳过（不报错）。

      OMNI_DOMAINS_CONFIG  domains.yaml 路径（替代 OMNI_DOMAINS 列举方式）
                           例：OMNI_DOMAINS_CONFIG=/home/user/config/domains.yaml

    两种方式可以同时使用，结果合并。
    """
    import os
    results: dict[str, list[str]] = {}

    # 方式一：OMNI_DOMAINS 逐目录加载
    raw_dirs = os.environ.get("OMNI_DOMAINS", "")
    for domain_dir in [d.strip() for d in raw_dirs.split(",") if d.strip()]:
        p = Path(domain_dir)
        if not p.exists():
            logger.warning("domain_loader: OMNI_DOMAINS path not found: %s", p)
            continue
        try:
            loaded = load_domain(p, db_path)
            results[p.name] = loaded
            logger.info("domain_loader: OMNI_DOMAINS loaded '%s' (%d nodes)", p.name, len(loaded))
        except Exception as e:
            logger.error("domain_loader: OMNI_DOMAINS '%s' failed: %s", p, e)

    # 方式二：OMNI_DOMAINS_CONFIG 批量加载
    config_path = os.environ.get("OMNI_DOMAINS_CONFIG", "")
    if config_path and Path(config_path).exists():
        try:
            extra = load_all_domains(config_path, db_path)
            results.update(extra)
        except Exception as e:
            logger.error("domain_loader: OMNI_DOMAINS_CONFIG failed: %s", e)

    return results


def load_all_domains(config_path: str | Path, db_path: str | Path,
                     base_dir: str | Path | None = None) -> dict[str, list[str]]:
    """根据 config/domains.yaml 加载所有激活的 domain。

    domain 节点 YAML 现在位于 config/domains/<domain_id>/nodes/（S3b.2 之前是
    仓库根的 domains/<domain_id>/nodes/）。base_dir 默认设为 config_path 的
    上一级（即仓库根）以保持路径前缀 config/domains/<domain_id> 完整。
    """
    config_path = Path(config_path)
    # base_dir 默认 = 仓库根（config_path = "<root>/config/domains.yaml" → parent.parent = root）
    base_dir = Path(base_dir) if base_dir else config_path.parent.parent

    if not config_path.exists():
        logger.info("domain_loader: no domains.yaml at %s, skipping", config_path)
        return {}

    cfg = _parse_yaml(config_path)
    results: dict[str, list[str]] = {}
    for domain_id, domain_cfg in (cfg.get("domains") or {}).items():
        if not domain_cfg.get("active", True):
            continue
        node_dir_rel = domain_cfg.get("node_dir", f"config/domains/{domain_id}/nodes/")
        domain_dir = base_dir / f"config/domains/{domain_id}"
        try:
            loaded = load_domain(domain_dir, db_path)
            results[domain_id] = loaded
        except Exception as e:
            logger.error("domain_loader: domain '%s' failed: %s", domain_id, e)
    return results
